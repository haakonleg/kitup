"""Context-aware filesystem helpers shared by built-in modules."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._privileged.protocol import Request
from ..context import TaskContext
from ..errors import TaskFailed
from .file_helper import resolve_group, resolve_owner, stat_path


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
