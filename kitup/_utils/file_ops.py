"""Context-aware filesystem helpers shared by built-in modules."""

import base64
import difflib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .._privileged.protocol import Request
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult
from . import ini as ini_ops
from .file_helper import (
    apply_metadata,
    read_path,
    remove_path,
    resolve_group,
    resolve_owner,
    stat_path,
    write_path,
)


@dataclass(frozen=True, slots=True)
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


def privileged(ctx: TaskContext, *, sudo: bool) -> bool:
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
    return cast(dict[str, Any], ctx.privileged_worker.request(Request(action, params)))


def stat(ctx: TaskContext, path: Path, *, sudo: bool) -> FileInfo | None:
    """Return path metadata, using the privileged worker when needed."""
    if not privileged(ctx, sudo=sudo):
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
    if not privileged(ctx, sudo=sudo):
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
    if privileged(ctx, sudo=sudo):
        result = worker_request(ctx, "read", path=str(path))
        if not result.get("exists"):
            return b""
        return base64.b64decode(result.get("content", ""))
    try:
        return read_path(path)
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc


def read_text(ctx: TaskContext, path: Path, *, sudo: bool) -> str:
    """Read a regular UTF-8 text file, returning ``""`` when it is missing.

    Directories, symlinks, other non-regular files, and non-UTF-8 content
    raise ``TaskFailed``.
    """
    info = stat(ctx, path, sudo=sudo)
    if info is None:
        return ""
    if info.is_dir:
        raise TaskFailed(f"path is a directory: {path}")
    if info.is_symlink:
        raise TaskFailed(f"refusing to edit symlink: {path}")
    if not info.is_file:
        raise TaskFailed(f"path is not a regular file: {path}")
    try:
        return read_bytes(ctx, path, info, sudo=sudo).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TaskFailed(f"not a UTF-8 text file: {path}") from exc


def apply_bytes(
    ctx: TaskContext,
    path: Path,
    content: bytes,
    *,
    action: str,
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    summary: str,
    data: dict[str, object],
    sudo: bool,
    unchanged_summary: str | None = None,
) -> CallResult:
    """Write bytes idempotently, returning the resulting call.

    Existing symlinks are replaced rather than followed. ``mode=None``
    preserves an existing mode and uses ``0o644`` for new files.
    ``unchanged_summary`` describes the desired state when nothing changes.
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
        return CallResult(
            action=action,
            changed=False,
            summary=unchanged_summary or summary,
            **data,
        )

    require_parent(ctx, path, sudo=sudo)
    if content_differs:
        _emit_diff(ctx, path, old, content)
    if ctx.dry_run:
        resolve_metadata(owner, group)
        return CallResult(action=action, changed=True, summary=summary, **data)

    if content_differs:
        _write_bytes(ctx, path, content, resolved, owner, group, sudo=sudo)
    else:
        apply_metadata_ctx(ctx, path, resolved, owner, group, sudo=sudo)
    return CallResult(action=action, changed=True, summary=summary, **data)


def apply_text(
    ctx: TaskContext,
    path: Path,
    old_text: str,
    new_text: str,
    *,
    action: str,
    summary: str,
    data: dict[str, object],
    sudo: bool,
    unchanged_summary: str | None = None,
) -> CallResult:
    """Write text idempotently, preserving the metadata of an existing file.

    Identical text returns a ``changed=False`` call without touching the file;
    otherwise the write goes through :func:`apply_bytes`, which handles
    ``--dry-run``, diffs, and privileged writes. New files get mode ``0o644``.
    """
    if new_text == old_text:
        return CallResult(action=action, changed=False, summary=unchanged_summary or summary, **data)
    try:
        payload = new_text.encode()
    except UnicodeEncodeError as exc:
        raise TaskFailed(f"not UTF-8 encodable: {path}") from exc
    return apply_bytes(
        ctx,
        path,
        payload,
        action=action,
        mode=None,
        owner=None,
        group=None,
        summary=summary,
        data=data,
        sudo=sudo,
        unchanged_summary=unchanged_summary,
    )


def apply_ini(
    ctx: TaskContext,
    target: Path,
    *,
    section: str | None,
    option: str | None,
    value: str | None,
    match: Mapping[str, str] | None,
    create: bool,
    action: str,
    sudo: bool,
) -> CallResult:
    """Apply one INI edit to ``target`` and return the resulting call.

    Shared by modules that edit INI-style files (``files.ini``,
    ``dnf.config``). Callers validate the arguments first: ``value`` is the
    canonical value to write, and when it is ``None`` the ``option`` line —
    or the whole ``section`` when ``option`` is also ``None`` — is removed
    instead. The returned call is not recorded; the caller records it with
    ``ctx.changed``.
    """
    if not create and stat(ctx, target, sudo=sudo) is None:
        raise TaskFailed(f"file does not exist and create=False: {target}")
    text = read_text(ctx, target, sudo=sudo)
    lines = text.splitlines(keepends=True)

    where = f"[{section}] of {target}" if section is not None else str(target)
    data: dict[str, object] = {"path": str(target), "section": section, "option": option, "value": value}

    new_lines: list[str]
    summary: str
    unchanged_summary: str
    ini_action: str
    if value is not None and option is not None:
        new_lines, ini_action = ini_ops.set_option(lines, section, option, value, match)
        summary = f"set {option} in {where}"
        unchanged_summary = f"ensure {option} is {value} in {where}"
    elif option is not None:
        new_lines = ini_ops.remove_option(lines, section, option, match)
        ini_action = "removed"
        summary = f"remove {option} from {where}"
        unchanged_summary = f"ensure {option} is absent from {where}"
    elif section is not None:
        new_lines = ini_ops.remove_section(lines, section, match)
        ini_action = "removed"
        summary = f"remove section [{section}] from {target}"
        unchanged_summary = f"ensure section [{section}] is absent from {target}"
    else:
        raise TaskFailed("ini edit requires a section or an option")

    new_text = "".join(new_lines)
    if new_text != text and new_text and not new_text.endswith("\n"):
        new_text += "\n"

    changed = new_text != text
    data["ini_action"] = ini_action if changed else "unchanged"
    if changed:
        return apply_text(
            ctx,
            target,
            text,
            new_text,
            action=action,
            summary=summary,
            data=data,
            sudo=sudo,
        )
    return CallResult(action=action, changed=False, summary=unchanged_summary, **data)


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
    if privileged(ctx, sudo=sudo):
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


def remove(
    ctx: TaskContext,
    path: Path,
    info: FileInfo | None,
    *,
    action: str,
    force: bool,
    sudo: bool,
    summary: str | None = None,
    data: dict[str, object] | None = None,
    allow_directory: bool = True,
    unchanged_summary: str | None = None,
) -> CallResult:
    """Remove a file, symlink, or directory idempotently.

    ``info`` is the result of :func:`stat`; pass ``None`` when the path is
    missing. When ``summary`` or ``data`` are omitted, use a generic
    ``rmdir``/``remove`` summary and data. ``unchanged_summary`` describes the
    desired state when the path is already absent.
    """
    is_dir = info is not None and info.is_dir and not info.is_symlink
    if info is not None and is_dir and not allow_directory:
        raise TaskFailed(f"path is a directory: {path}")
    action = "rmdir" if is_dir and not force else "remove"
    if data is None:
        data = {"path": str(path)}
    if summary is None:
        summary = f"{action} {path}"
    if info is None:
        return CallResult(
            action=action,
            changed=False,
            summary=unchanged_summary or f"ensure {path} is absent",
            **data,
        )
    if ctx.dry_run:
        return CallResult(action=action, changed=True, summary=summary, **data)
    if privileged(ctx, sudo=sudo):
        worker_request(ctx, "remove", path=str(path), force=force)
        return CallResult(action=action, changed=True, summary=summary, **data)
    try:
        remove_path(path, force=force)
    except OSError as exc:
        if is_dir and not force:
            raise TaskFailed(f"directory is not empty: {path}") from exc
        raise TaskFailed(f"could not remove {path}: {exc}") from exc
    return CallResult(action=action, changed=True, summary=summary, **data)


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
    if privileged(ctx, sudo=sudo):
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
