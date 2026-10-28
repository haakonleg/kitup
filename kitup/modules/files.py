"""Idempotent file, directory, and symlink management."""

from __future__ import annotations

import base64
import difflib
import re
from pathlib import Path

from .._utils import file_ops
from .._utils.file_helper import (
    apply_metadata,
    mkdir_path,
    move_path,
    read_path,
    remove_path,
    symlink_path,
    write_path,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import (
    require_metadata,
    require_mode,
    require_path,
    require_regex,
    require_state,
    require_text,
)


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

    ``sudo=True`` performs the operation through the privileged worker.
    """
    require_state(state, "file")
    require_path(path, "file path")
    require_metadata(owner, group)
    require_mode(mode)

    target = Path(path)
    if state == "absent":
        info = file_ops.stat(ctx, target, sudo=sudo)
        if info is not None and info.is_dir and not info.is_symlink:
            raise TaskFailed(f"path is a directory: {target}")
        _remove_path(ctx, target, force=False, sudo=sudo)
        return

    info = file_ops.stat(ctx, target, sudo=sudo)
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
    """Copy one regular file, preserving its mode unless overridden.

    A bare filename in ``src`` resolves under the bundle's ``files`` directory.
    Other relative paths resolve against the bundle directory, while absolute
    paths are used as-is. When ``dest`` ends with a slash it is treated as a
    directory and the source filename is appended.
    """
    require_path(src, "copy source")
    require_path(dest, "copy destination")
    require_metadata(owner, group)
    require_mode(mode)
    source = ctx.resolve_source(src)
    target = Path(dest)
    if str(dest).endswith("/"):
        target /= source.name
    source_info = file_ops.stat(ctx, source, sudo=sudo)
    if source_info is None or source_info.is_symlink or not source_info.is_file:
        raise TaskFailed(f"copy source is not a regular file: {source}")
    target_info = file_ops.stat(ctx, target, sudo=sudo)
    if target_info is not None and target_info.is_dir:
        raise TaskFailed(f"destination is a directory: {target}")

    _apply_bytes(
        ctx,
        target,
        _read_bytes(ctx, source, source_info, sudo=sudo),
        mode=source_info.mode if mode is None else mode,
        owner=owner,
        group=group,
        summary=f"copy {source} -> {target}",
        data={"path": str(target), "source": str(source), "action": "copy"},
        sudo=sudo,
    )


def move(
    ctx: TaskContext,
    src: str | Path,
    dest: str | Path,
    *,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> None:
    """Move one regular file, preserving its mode and ownership when renamed.

    Unlike :func:`copy`, ``src`` is used as given and is not resolved against
    the bundle. When ``dest`` ends with a slash it is treated as a directory
    and the source filename is appended. If ``src`` is already gone and
    ``dest`` exists, the move is treated as complete. ``mode``, ``owner``, and
    ``group`` are applied to the destination after the move.
    """
    require_path(src, "move source")
    require_path(dest, "move destination")
    require_metadata(owner, group)
    require_mode(mode)
    source = Path(src)
    target = Path(dest)
    if str(dest).endswith("/"):
        target /= source.name

    source_info = file_ops.stat(ctx, source, sudo=sudo)
    target_info = file_ops.stat(ctx, target, sudo=sudo)
    if source_info is None:
        if target_info is not None and not target_info.is_dir and not target_info.is_symlink:
            _reconcile_metadata(
                ctx,
                target,
                target_info,
                mode=mode,
                owner=owner,
                group=group,
                sudo=sudo,
            )
            return
        raise TaskFailed(f"move source does not exist: {source}")
    if source_info.is_symlink or not source_info.is_file:
        raise TaskFailed(f"move source is not a regular file: {source}")
    if target_info is not None and target_info.is_dir and not target_info.is_symlink:
        raise TaskFailed(f"destination is a directory: {target}")
    if target_info is not None and target_info.is_symlink:
        raise TaskFailed(f"refusing to replace symlink: {target}")
    if source.absolute() == target.absolute():
        _reconcile_metadata(
            ctx,
            target,
            target_info,
            mode=mode,
            owner=owner,
            group=group,
            sudo=sudo,
        )
        return

    file_ops.require_parent(ctx, target, sudo=sudo)
    ctx.changed(
        {"path": str(target), "source": str(source), "action": "move"},
        f"move {source} -> {target}",
    )
    if ctx.dry_run:
        if mode is not None or owner is not None or group is not None:
            file_ops.resolve_metadata(owner, group)
        return
    _move(ctx, source, target, sudo=sudo)
    if mode is None and owner is None and group is None:
        return
    moved_info = file_ops.stat(ctx, target, sudo=sudo)
    if moved_info is None:
        raise TaskFailed(f"move destination is missing after moving: {target}")
    resolved_mode = moved_info.mode if mode is None else mode
    if file_ops.metadata_differs(moved_info, resolved_mode, owner, group):
        _apply_metadata_ctx(
            ctx,
            target,
            resolved_mode,
            owner,
            group,
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
    require_state(state, "line")
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
    require_state(state, "block")
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
    require_state(state, "directory")
    require_path(path, "directory path")
    require_metadata(owner, group)
    require_mode(mode)

    target = Path(path)
    if state == "absent":
        info = file_ops.stat(ctx, target, sudo=sudo)
        if info is None:
            return
        if info.is_symlink or not info.is_dir:
            raise TaskFailed(f"path is not a directory: {target}")
        _remove_path(ctx, target, force=force, sudo=sudo)
        return

    info = file_ops.stat(ctx, target, sudo=sudo)
    if info is not None and info.is_symlink:
        raise TaskFailed(f"refusing to manage symlink as directory: {target}")
    if info is not None and not info.is_dir:
        raise TaskFailed(f"path exists and is not a directory: {target}")

    if info is None:
        file_ops.require_parent(ctx, target, sudo=sudo)
        if ctx.dry_run:
            file_ops.resolve_metadata(owner, group)
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
    if file_ops.metadata_differs(info, resolved, owner, group):
        if ctx.dry_run:
            file_ops.resolve_metadata(owner, group)
        ctx.changed({"path": str(target), "action": "chmod"}, f"chmod {target}")
        if not ctx.dry_run:
            _apply_metadata_ctx(ctx, target, resolved, owner, group, sudo=sudo)


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
    info = file_ops.stat(ctx, link, sudo=sudo)
    if info is not None and info.is_symlink and info.link == desired:
        return
    if info is not None and info.is_dir and not info.is_symlink:
        raise TaskFailed(f"refusing to replace a directory with a symlink: {link}")

    file_ops.require_parent(ctx, link, sudo=sudo)
    ctx.changed(
        {"path": str(link), "target": desired, "action": "symlink"},
        f"symlink {link} -> {desired}",
    )
    if ctx.dry_run:
        return
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(ctx, "symlink", path=str(link), target=desired)
        return
    try:
        symlink_path(desired, link)
    except OSError as exc:
        raise TaskFailed(f"could not create symlink {link}: {exc}") from exc


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
    info = file_ops.stat(ctx, path, sudo=sudo)
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
    metadata_differs = (
        info is None
        or is_link
        or file_ops.metadata_differs(info, resolved, owner, group)
    )
    if not content_differs and not metadata_differs:
        return

    file_ops.require_parent(ctx, path, sudo=sudo)
    if content_differs:
        _emit_diff(ctx, path, old, content)
    if ctx.dry_run:
        file_ops.resolve_metadata(owner, group)
    ctx.changed(data, summary)
    if ctx.dry_run:
        return

    if content_differs:
        _write_bytes(ctx, path, content, resolved, owner, group, sudo=sudo)
    else:
        _apply_metadata_ctx(ctx, path, resolved, owner, group, sudo=sudo)


def _rewrite_text(
    ctx: TaskContext,
    path: Path,
    text: str,
    summary: str,
    *,
    sudo: bool,
) -> None:
    info = file_ops.stat(ctx, path, sudo=sudo)
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
    info = file_ops.stat(ctx, path, sudo=sudo)
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


def _read_bytes(
    ctx: TaskContext,
    path: Path,
    info: file_ops.FileInfo | None,
    *,
    sudo: bool,
) -> bytes:
    if info is None:
        return b""
    if file_ops.privileged(ctx, sudo):
        result = file_ops.worker_request(ctx, "read", path=str(path))
        if not result.get("exists"):
            return b""
        return base64.b64decode(result.get("content", ""))
    try:
        return read_path(path)
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc


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
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(
            ctx,
            "write",
            path=str(path),
            content=base64.b64encode(content).decode(),
            mode=mode,
            owner=owner,
            group=group,
        )
        return

    file_ops.require_parent(ctx, path, sudo=False)
    try:
        write_path(path, content, mode=mode, owner=owner, group=group)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not write {path}: {exc}") from exc


def _move(ctx: TaskContext, source: Path, target: Path, *, sudo: bool) -> None:
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(ctx, "move", source=str(source), path=str(target))
        return
    try:
        move_path(source, target)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not move {source} to {target}: {exc}") from exc


def _reconcile_metadata(
    ctx: TaskContext,
    target: Path,
    info: file_ops.FileInfo | None,
    *,
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    sudo: bool,
) -> None:
    if info is None or (mode is None and owner is None and group is None):
        return
    resolved_mode = info.mode if mode is None else mode
    if not file_ops.metadata_differs(info, resolved_mode, owner, group):
        return
    if ctx.dry_run:
        file_ops.resolve_metadata(owner, group)
    ctx.changed(
        {"path": str(target), "action": "metadata"},
        f"update metadata for {target}",
    )
    if ctx.dry_run:
        return
    _apply_metadata_ctx(ctx, target, resolved_mode, owner, group, sudo=sudo)


def _mkdir(
    ctx: TaskContext,
    path: Path,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(
            ctx,
            "mkdir",
            path=str(path),
            mode=mode,
            owner=owner,
            group=group,
        )
        return
    try:
        mkdir_path(path, mode=mode, owner=owner, group=group)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not create directory {path}: {exc}") from exc


def _remove_path(ctx: TaskContext, path: Path, *, force: bool, sudo: bool) -> None:
    info = file_ops.stat(ctx, path, sudo=sudo)
    if info is None:
        return
    if info.is_dir and not info.is_symlink and not force:
        action = "rmdir"
    else:
        action = "remove"
    ctx.changed({"path": str(path), "action": action}, f"{action} {path}")
    if ctx.dry_run:
        return
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(ctx, "remove", path=str(path), force=force)
        return
    try:
        remove_path(path, force=force)
    except OSError as exc:
        if info.is_dir and not info.is_symlink and not force:
            raise TaskFailed(f"directory is not empty: {path}") from exc
        raise TaskFailed(f"could not remove {path}: {exc}") from exc


def _apply_metadata_ctx(
    ctx: TaskContext,
    path: Path,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(
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
