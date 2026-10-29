"""Context-aware filesystem helpers shared by built-in modules."""

from __future__ import annotations

import base64
import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._privileged.protocol import Request
from ..context import TaskContext
from ..errors import TaskFailed
from .file_helper import (
    apply_metadata,
    read_path,
    resolve_group,
    resolve_owner,
    stat_path,
    write_path,
)


@dataclass(frozen=True)
class FileInfo:
    """Filesystem metadata returned by :func:`stat`."""

    mode: int
    uid: int
    gid: int
    is_dir: bool
    is_symlink: bool
    is_file: bool
    link: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the metadata in the shape used by ``file_helper``."""
        return {
            "mode": self.mode,
            "uid": self.uid,
            "gid": self.gid,
            "is_dir": self.is_dir,
            "is_symlink": self.is_symlink,
            "is_file": self.is_file,
            "link": self.link,
        }


def privileged(ctx: TaskContext, sudo: bool) -> bool:
    """Return whether an operation should use the privileged worker."""
    return sudo and not ctx.sudo.is_root


def worker_request(ctx: TaskContext, action: str, **params: Any) -> dict[str, Any]:
    """Send a worker request, normalizing path parameters to absolute paths."""
    for key in ("path", "source"):
        if key not in params:
            continue
        value = str(params[key])
        if not os.path.isabs(value):
            value = os.path.join(os.getcwd(), value)
        params[key] = value
    return ctx.privileged_worker.request(Request(action, params))


def stat(ctx: TaskContext, path: Path, *, sudo: bool) -> FileInfo | None:
    """Return path metadata, using the privileged worker when needed."""
    if not privileged(ctx, sudo):
        return stat_local(path)
    result = worker_request(ctx, "stat", path=str(path))
    if not result or not result.get("exists"):
        return None
    return _file_info(result)


def stat_local(path: Path) -> FileInfo | None:
    """Return path metadata for the current process."""
    try:
        result = stat_path(path)
    except OSError as exc:
        raise TaskFailed(f"could not stat {path}: {exc}") from exc
    if not result:
        return None
    return _file_info(result)


def _file_info(result: dict[str, Any]) -> FileInfo:
    return FileInfo(
        mode=result["mode"],
        uid=result["uid"],
        gid=result["gid"],
        is_dir=result["is_dir"],
        is_symlink=result["is_symlink"],
        is_file=result["is_file"],
        link=result.get("link"),
    )


def require_parent(ctx: TaskContext, path: Path, *, sudo: bool) -> None:
    """Raise when the parent directory of ``path`` does not exist."""
    if not privileged(ctx, sudo):
        require_parent_local(path)
        return
    result = worker_request(ctx, "stat", path=str(path.parent), follow=True)
    if not result.get("exists") or not result.get("is_dir"):
        raise TaskFailed(f"parent directory does not exist: {path.parent}")


def require_parent_local(path: Path) -> None:
    """Raise when the parent directory of ``path`` does not exist locally."""
    if not path.parent.is_dir():
        raise TaskFailed(f"parent directory does not exist: {path.parent}")


def resolve_metadata(owner: str | int | None, group: str | int | None) -> None:
    """Validate metadata identities without applying them."""
    try:
        resolve_owner(owner)
        resolve_group(group)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc


def metadata_differs(
    info: FileInfo,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
) -> bool:
    """Return whether requested metadata differs from ``info``."""
    if info.mode != mode:
        return True
    try:
        uid = resolve_owner(owner)
        gid = resolve_group(group)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    if uid is not None and info.uid != uid:
        return True
    return gid is not None and info.gid != gid


def read_bytes(
    ctx: TaskContext,
    path: Path,
    info: FileInfo | None,
    *,
    sudo: bool,
) -> bytes:
    """Read a path as bytes, using the privileged worker when needed."""
    if info is None:
        return b""
    if privileged(ctx, sudo):
        result = worker_request(ctx, "read", path=str(path))
        if not result.get("exists"):
            return b""
        return base64.b64decode(result.get("content", ""))
    try:
        return read_path(path)
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc


def apply_bytes(
    ctx: TaskContext,
    path: Path,
    content: bytes,
    *,
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    summary: str,
    data: dict[str, object],
    sudo: bool,
) -> None:
    """Write bytes idempotently, reporting content and metadata changes.

    Existing symlinks are replaced rather than followed. ``mode=None``
    preserves an existing mode and uses ``0o644`` for new files.
    """
    info = stat(ctx, path, sudo=sudo)
    is_link = info.is_symlink if info is not None else False
    if info is not None and info.is_dir and not is_link:
        raise TaskFailed(f"path is a directory: {path}")
    if info is not None and not is_link and not info.is_file:
        raise TaskFailed(f"path is not a regular file: {path}")

    old = read_bytes(ctx, path, info, sudo=sudo) if info is not None and not is_link else None
    content_differs = is_link or old != content
    resolved = mode
    if resolved is None:
        resolved = info.mode if info is not None and not is_link else 0o644
    metadata_changed = (
        info is None
        or is_link
        or metadata_differs(
            info,
            resolved,
            owner,
            group,
        )
    )
    if not content_differs and not metadata_changed:
        return

    require_parent(ctx, path, sudo=sudo)
    if content_differs:
        _emit_diff(ctx, path, old, content)
    if ctx.dry_run:
        resolve_metadata(owner, group)
    ctx.changed(data, summary)
    if ctx.dry_run:
        return

    if content_differs:
        _write_bytes(ctx, path, content, resolved, owner, group, sudo=sudo)
    else:
        apply_metadata_ctx(ctx, path, resolved, owner, group, sudo=sudo)


def apply_metadata_ctx(
    ctx: TaskContext,
    path: Path,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    """Apply file metadata, using the privileged worker when needed."""
    if privileged(ctx, sudo):
        worker_request(
            ctx,
            "metadata",
            path=str(path),
            mode=mode,
            owner=owner,
            group=group,
        )
        return
    try:
        apply_metadata(path, mode, owner, group, preserve=False)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not update metadata for {path}: {exc}") from exc


def _write_bytes(
    ctx: TaskContext,
    path: Path,
    content: bytes,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    if privileged(ctx, sudo):
        worker_request(
            ctx,
            "write",
            path=str(path),
            content=base64.b64encode(content).decode(),
            mode=mode,
            owner=owner,
            group=group,
        )
        return

    require_parent(ctx, path, sudo=False)
    try:
        write_path(path, content, mode=mode, owner=owner, group=group)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not write {path}: {exc}") from exc


def _emit_diff(ctx: TaskContext, path: Path, old: bytes | None, new: bytes) -> None:
    if not ctx.diff or ctx.output is None:
        return
    try:
        old_text = (old or b"").decode()
        new_text = new.decode()
    except UnicodeDecodeError:
        return
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"{path} (current)",
        tofile=f"{path} (new)",
        lineterm="",
    )
    for item in diff:
        ctx.output.write(item)
