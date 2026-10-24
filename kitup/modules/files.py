"""Idempotent file, directory, and symlink management."""

from __future__ import annotations

import base64
import difflib
import grp
import json
import os
import pkgutil
import pwd
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_path, require_regex, require_text


@dataclass(frozen=True)
class _FileInfo:
    mode: int
    uid: int
    gid: int
    is_dir: bool
    is_symlink: bool
    is_file: bool
    link: str | None = None


def file(
    ctx: TaskContext,
    path: str | Path,
    *,
    state: str = "present",
    content: str | None = None,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> None:
    """Ensure a text file exists with optional content, or is absent.

    ``sudo=True`` performs the operation through the privileged helper.
    """
    if state not in {"present", "absent"}:
        raise TaskFailed(f"invalid file state: {state!r}")
    require_path(path, "file path")
    _validate_metadata(owner, group)

    target = Path(path)
    if state == "absent":
        info = _lstat(ctx, target, sudo=sudo)
        if info is not None and info.is_dir and not info.is_symlink:
            raise TaskFailed(f"path is a directory: {target}")
        _remove_path(ctx, target, force=False, sudo=sudo)
        return

    info = _lstat(ctx, target, sudo=sudo)
    if content is None:
        if info is not None and info.is_symlink:
            raise TaskFailed(f"refusing to manage symlink as file: {target}")
        if info is not None and info.is_dir:
            raise TaskFailed(f"path is a directory: {target}")
        if info is not None and not info.is_file:
            raise TaskFailed(f"path is not a regular file: {target}")
        data = _read_bytes(ctx, target, info, sudo=sudo) if info is not None else b""
        action = "ensure"
        summary = f"ensure file {target}"
    else:
        data = content.encode()
        action = "write"
        summary = f"write {target}"

    _apply_bytes(
        ctx,
        target,
        data,
        mode=mode,
        owner=owner,
        group=group,
        summary=summary,
        data={"path": str(target), "action": action},
        sudo=sudo,
    )


def copy(
    ctx: TaskContext,
    src: str | Path,
    dest: str | Path,
    *,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> None:
    """Copy one regular file, preserving its mode unless overridden."""
    require_path(src, "copy source")
    require_path(dest, "copy destination")
    _validate_metadata(owner, group)
    source = Path(src)
    target = Path(dest)
    source_info = _lstat(ctx, source, sudo=sudo)
    if source_info is None or source_info.is_symlink or not source_info.is_file:
        raise TaskFailed(f"copy source is not a regular file: {source}")
    target_info = _lstat(ctx, target, sudo=sudo)
    if target_info is not None and target_info.is_dir:
        raise TaskFailed(f"destination is a directory: {target}")

    _apply_bytes(
        ctx,
        target,
        _read_bytes(ctx, source, source_info, sudo=sudo),
        mode=source_info.mode & 0o777 if mode is None else mode,
        owner=owner,
        group=group,
        summary=f"copy {source} -> {target}",
        data={"path": str(target), "source": str(source), "action": "copy"},
        sudo=sudo,
    )


def line(
    ctx: TaskContext,
    path: str | Path,
    line: str,
    *,
    match: str | None = None,
    state: str = "present",
    sudo: bool = False,
) -> None:
    """Add, replace, or remove a line selected by an exact value or regex."""
    if state not in {"present", "absent"}:
        raise TaskFailed(f"invalid line state: {state!r}")
    require_path(path, "line path")
    if match is not None:
        require_regex(match, "line match")

    target = Path(path)
    text = _read_text(ctx, target, sudo=sudo)
    lines = text.splitlines(keepends=True)
    desired = line.rstrip("\n")

    if state == "present":
        index = _matching_line(lines, desired, match)
        rendered = desired + "\n"
        if index is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            new_lines = [*lines, rendered]
        elif lines[index].rstrip("\n") == desired:
            new_lines = lines
        else:
            new_lines = list(lines)
            new_lines[index] = rendered
        summary = f"{'replace' if index is not None else 'append'} line in {target}"
    else:
        new_lines = [
            existing
            for existing in lines
            if not _line_matches(existing, desired, match)
        ]
        summary = f"remove line from {target}"

    new_text = "".join(new_lines)
    if new_text != text:
        _rewrite_text(ctx, target, new_text, summary, sudo=sudo)


def block(
    ctx: TaskContext,
    path: str | Path,
    marker: str,
    content: str,
    *,
    state: str = "present",
    sudo: bool = False,
) -> None:
    """Create, update, or remove a marked block in a file."""
    if state not in {"present", "absent"}:
        raise TaskFailed(f"invalid block state: {state!r}")
    require_path(path, "block path")
    require_text(marker, "block marker")

    target = Path(path)
    text = _read_text(ctx, target, sudo=sudo)
    lines = text.splitlines(keepends=True)
    begin = f"# BEGIN {marker}\n"
    end = f"# END {marker}\n"
    bounds = _block_bounds(lines, marker)

    block_lines = [begin, *_content_lines(content), end]
    if state == "present":
        if bounds is None:
            prefix = list(lines)
            if prefix and not prefix[-1].endswith("\n"):
                prefix[-1] += "\n"
            new_lines = [*prefix, *block_lines]
            summary = f"add {marker} block to {target}"
        else:
            start, stop = bounds
            new_lines = [*lines[:start], *block_lines, *lines[stop + 1 :]]
            summary = f"update {marker} block in {target}"
    else:
        if bounds is None:
            return
        start, stop = bounds
        new_lines = [*lines[:start], *lines[stop + 1 :]]
        summary = f"remove {marker} block from {target}"

    new_text = "".join(new_lines)
    if new_text != text:
        _rewrite_text(ctx, target, new_text, summary, sudo=sudo)


def directory(
    ctx: TaskContext,
    path: str | Path,
    *,
    state: str = "present",
    force: bool = False,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> None:
    """Ensure a directory exists or is absent."""
    if state not in {"present", "absent"}:
        raise TaskFailed(f"invalid directory state: {state!r}")
    require_path(path, "directory path")
    _validate_metadata(owner, group)

    target = Path(path)
    if state == "absent":
        info = _lstat(ctx, target, sudo=sudo)
        if info is None:
            return
        if info.is_symlink or not info.is_dir:
            raise TaskFailed(f"path is not a directory: {target}")
        _remove_path(ctx, target, force=force, sudo=sudo)
        return

    info = _lstat(ctx, target, sudo=sudo)
    if info is not None and info.is_symlink:
        raise TaskFailed(f"refusing to manage symlink as directory: {target}")
    if info is not None and not info.is_dir:
        raise TaskFailed(f"path exists and is not a directory: {target}")

    if info is None:
        _require_parent(ctx, target, sudo=sudo)
        ctx.changed({"path": str(target), "action": "mkdir"}, f"mkdir {target}")
        if not ctx.dry_run:
            _mkdir(
                ctx,
                target,
                0o755 if mode is None else mode,
                owner,
                group,
                sudo=sudo,
            )
        return

    resolved = info.mode if mode is None else mode
    if _metadata_differs(info, resolved, owner, group):
        ctx.changed({"path": str(target), "action": "chmod"}, f"chmod {target}")
        if not ctx.dry_run:
            _apply_metadata_ctx(ctx, target, resolved, owner, group, info, sudo=sudo)


def symlink(
    ctx: TaskContext,
    target: str | Path,
    path: str | Path,
    *,
    sudo: bool = False,
) -> None:
    """Create a symlink, replacing an existing file or symlink."""
    require_text(str(target), "symlink target")
    require_path(path, "symlink path")
    link = Path(path)
    desired = str(target)
    info = _lstat(ctx, link, sudo=sudo)
    if info is not None and info.is_symlink and info.link == desired:
        return
    if info is not None and info.is_dir and not info.is_symlink:
        raise TaskFailed(f"refusing to replace a directory with a symlink: {link}")

    _require_parent(ctx, link, sudo=sudo)
    ctx.changed(
        {"path": str(link), "target": desired, "action": "symlink"},
        f"symlink {link} -> {desired}",
    )
    if ctx.dry_run:
        return
    if _privileged(ctx, sudo):
        _helper(
            ctx,
            {"action": "symlink", "path": str(link), "target": desired},
            mutates=True,
            sudo=sudo,
        )
        return
    if os.path.lexists(link):
        link.unlink()
    link.symlink_to(desired)


def _apply_bytes(
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
    info = _lstat(ctx, path, sudo=sudo)
    is_link = info.is_symlink if info is not None else False
    if info is not None and info.is_dir and not is_link:
        raise TaskFailed(f"path is a directory: {path}")
    if info is not None and not is_link and not info.is_file:
        raise TaskFailed(f"path is not a regular file: {path}")

    old = _read_bytes(ctx, path, info, sudo=sudo) if info is not None and not is_link else None
    content_differs = is_link or old != content
    resolved = mode
    if resolved is None:
        resolved = info.mode if info is not None and not is_link else 0o644
    metadata_differs = info is None or is_link or _metadata_differs(info, resolved, owner, group)
    if not content_differs and not metadata_differs:
        return

    _require_parent(ctx, path, sudo=sudo)
    if content_differs:
        _emit_diff(ctx, path, old, content)
    ctx.changed(data, summary)
    if ctx.dry_run:
        return

    if content_differs:
        _write_bytes(ctx, path, content, resolved, owner, group, info, sudo=sudo)
    else:
        _apply_metadata_ctx(ctx, path, resolved, owner, group, info, sudo=sudo)


def _rewrite_text(
    ctx: TaskContext,
    path: Path,
    text: str,
    summary: str,
    *,
    sudo: bool,
) -> None:
    info = _lstat(ctx, path, sudo=sudo)
    mode = info.mode if info is not None and info.is_file and not info.is_symlink else 0o644
    _apply_bytes(
        ctx,
        path,
        text.encode(),
        mode=mode,
        owner=None,
        group=None,
        summary=summary,
        data={"path": str(path), "action": "edit"},
        sudo=sudo,
    )


def _read_text(ctx: TaskContext, path: Path, *, sudo: bool) -> str:
    info = _lstat(ctx, path, sudo=sudo)
    if info is None:
        return ""
    if info.is_dir:
        raise TaskFailed(f"path is a directory: {path}")
    if info.is_symlink:
        raise TaskFailed(f"refusing to edit symlink: {path}")
    if not info.is_file:
        raise TaskFailed(f"path is not a regular file: {path}")
    try:
        return _read_bytes(ctx, path, info, sudo=sudo).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TaskFailed(f"not a UTF-8 text file: {path}") from exc


def _lstat(ctx: TaskContext, path: Path, *, sudo: bool) -> _FileInfo | None:
    if _privileged(ctx, sudo):
        result = _helper(
            ctx,
            {"action": "stat", "path": str(path)},
            mutates=False,
            sudo=sudo,
        )
        if not result.get("exists"):
            return None
        return _FileInfo(
            mode=result["mode"],
            uid=result["uid"],
            gid=result["gid"],
            is_dir=result["is_dir"],
            is_symlink=result["is_symlink"],
            is_file=result["is_file"],
            link=result.get("link"),
        )

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TaskFailed(f"could not stat {path}: {exc}") from exc
    return _FileInfo(
        mode=stat.S_IMODE(info.st_mode),
        uid=info.st_uid,
        gid=info.st_gid,
        is_dir=stat.S_ISDIR(info.st_mode),
        is_symlink=stat.S_ISLNK(info.st_mode),
        is_file=stat.S_ISREG(info.st_mode),
        link=os.readlink(path) if stat.S_ISLNK(info.st_mode) else None,
    )


def _read_bytes(ctx: TaskContext, path: Path, info: _FileInfo | None, *, sudo: bool) -> bytes:
    if info is None:
        return b""
    if _privileged(ctx, sudo):
        result = _helper(
            ctx,
            {"action": "read", "path": str(path)},
            mutates=False,
            sudo=sudo,
        )
        if not result.get("exists"):
            return b""
        return base64.b64decode(result.get("content", ""))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc


def _write_bytes(
    ctx: TaskContext,
    path: Path,
    content: bytes,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    info: _FileInfo | None,
    *,
    sudo: bool,
) -> None:
    if _privileged(ctx, sudo):
        _helper(
            ctx,
            {
                "action": "write",
                "path": str(path),
                "content": base64.b64encode(content).decode(),
                "mode": mode,
                "owner": owner,
                "group": group,
            },
            mutates=True,
            sudo=sudo,
        )
        return

    _atomic_write(ctx, path, content, mode, owner, group, info)


def _atomic_write(
    ctx: TaskContext,
    path: Path,
    content: bytes,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    info: _FileInfo | None,
) -> None:
    _require_parent(ctx, path, sudo=False)
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if info is not None and not info.is_symlink:
            try:
                shutil.copystat(path, temp, follow_symlinks=False)
            except OSError:
                pass
        _apply_metadata(temp, mode, owner, group, info, preserve=True)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _mkdir(
    ctx: TaskContext,
    path: Path,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    if _privileged(ctx, sudo):
        _helper(
            ctx,
            {
                "action": "mkdir",
                "path": str(path),
                "mode": mode,
                "owner": owner,
                "group": group,
            },
            mutates=True,
            sudo=sudo,
        )
        return
    path.mkdir()
    _apply_metadata(path, mode, owner, group, None, preserve=False)


def _remove_path(ctx: TaskContext, path: Path, *, force: bool, sudo: bool) -> None:
    info = _lstat(ctx, path, sudo=sudo)
    if info is None:
        return
    if info.is_dir and not info.is_symlink and not force:
        action = "rmdir"
    else:
        action = "remove"
    ctx.changed({"path": str(path), "action": action}, f"{action} {path}")
    if ctx.dry_run:
        return
    if _privileged(ctx, sudo):
        _helper(
            ctx,
            {"action": "remove", "path": str(path), "force": force},
            mutates=True,
            sudo=sudo,
        )
        return
    if info.is_dir and not info.is_symlink:
        if force:
            shutil.rmtree(path)
            return
        try:
            path.rmdir()
        except OSError as exc:
            raise TaskFailed(f"directory is not empty: {path}") from exc
        return
    path.unlink()


def _apply_metadata_ctx(
    ctx: TaskContext,
    path: Path,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    info: _FileInfo | None,
    *,
    sudo: bool,
) -> None:
    if _privileged(ctx, sudo):
        _helper(
            ctx,
            {
                "action": "metadata",
                "path": str(path),
                "mode": mode,
                "owner": owner,
                "group": group,
            },
            mutates=True,
            sudo=sudo,
        )
        return
    _apply_metadata(path, mode, owner, group, info, preserve=False)


def _apply_metadata(
    path: Path,
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    info: _FileInfo | None,
    *,
    preserve: bool,
) -> None:
    uid = _resolve_owner(owner)
    gid = _resolve_group(group)
    if preserve and info is not None:
        if uid is None:
            uid = info.uid
        if gid is None:
            gid = info.gid
    if uid is not None or gid is not None:
        os.chown(path, -1 if uid is None else uid, -1 if gid is None else gid)
    if mode is not None:
        os.chmod(path, mode)


def _metadata_differs(
    info: _FileInfo,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
) -> bool:
    if info.mode != mode:
        return True
    uid = _resolve_owner(owner)
    gid = _resolve_group(group)
    if uid is not None and info.uid != uid:
        return True
    return gid is not None and info.gid != gid


def _validate_metadata(owner: str | int | None, group: str | int | None) -> None:
    if owner is not None and not isinstance(owner, int):
        require_text(owner, "owner")
    if group is not None and not isinstance(group, int):
        require_text(group, "group")


def _resolve_owner(owner: str | int | None) -> int | None:
    if owner is None or isinstance(owner, int):
        return owner
    require_text(owner, "owner")
    try:
        return pwd.getpwnam(owner).pw_uid
    except KeyError as exc:
        raise TaskFailed(f"unknown user: {owner}") from exc


def _resolve_group(group: str | int | None) -> int | None:
    if group is None or isinstance(group, int):
        return group
    require_text(group, "group")
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError as exc:
        raise TaskFailed(f"unknown group: {group}") from exc


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


def _matching_line(lines: list[str], desired: str, match: str | None) -> int | None:
    for index, existing in enumerate(lines):
        if _line_matches(existing, desired, match):
            return index
    return None


def _line_matches(existing: str, desired: str, match: str | None) -> bool:
    candidate = existing.rstrip("\n")
    if match is None:
        return candidate == desired
    return re.search(match, candidate) is not None


def _block_bounds(lines: list[str], marker: str) -> tuple[int, int] | None:
    begin = f"# BEGIN {marker}"
    end = f"# END {marker}"
    starts = [index for index, line in enumerate(lines) if line.rstrip("\n") == begin]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\n") == end]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        raise TaskFailed(f"malformed {marker} block markers")
    return starts[0], ends[0]


def _content_lines(content: str) -> list[str]:
    if not content:
        return []
    normalized = content if content.endswith("\n") else f"{content}\n"
    return normalized.splitlines(keepends=True)


def _require_parent(ctx: TaskContext, path: Path, *, sudo: bool) -> None:
    if _privileged(ctx, sudo):
        result = _helper(
            ctx,
            {"action": "stat", "path": str(path.parent), "follow": True},
            mutates=False,
            sudo=sudo,
        )
        if not result.get("exists") or not result.get("is_dir"):
            raise TaskFailed(f"parent directory does not exist: {path.parent}")
        return
    if not path.parent.is_dir():
        raise TaskFailed(f"parent directory does not exist: {path.parent}")


def _privileged(ctx: TaskContext, sudo: bool) -> bool:
    return sudo and not ctx.sudo.is_root


def _helper(
    ctx: TaskContext,
    request: dict[str, object],
    *,
    mutates: bool,
    sudo: bool,
) -> dict[str, Any]:
    handle, request_name = tempfile.mkstemp(prefix="kitup-file-", suffix=".json")
    request_path = Path(request_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(request, stream)
        command = [sys.executable, "-c", _helper_source(), str(request_path)]
        if mutates:
            result = ctx.run(command, record=False, sudo=sudo)
        else:
            result = ctx.probe(command, sudo=sudo)
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)
    finally:
        request_path.unlink(missing_ok=True)


_HELPER_SOURCE: str | None = None


def _helper_source() -> str:
    global _HELPER_SOURCE
    if _HELPER_SOURCE is None:
        data = pkgutil.get_data(__package__, "_file_helper.py")
        if data is None:
            raise RuntimeError("file helper source is unavailable")
        _HELPER_SOURCE = data.decode()
    return _HELPER_SOURCE
