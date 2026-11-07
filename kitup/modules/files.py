"""Idempotent file, directory, symlink, and text-file management."""

import re
from collections.abc import Mapping
from pathlib import Path

from .._utils import file_ops
from .._utils import ini as ini_ops
from .._utils.file_helper import mkdir_path, move_path, symlink_path
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult
from ..validation import (
    State,
    require_bool,
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
    state: State = "present",
    content: str | None = None,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> CallResult:
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
        return ctx.changed(
            file_ops.remove(
                ctx,
                target,
                info,
                action="files.file",
                force=False,
                sudo=sudo,
                unchanged_summary=f"ensure {target} is absent",
            )
        )

    info = file_ops.stat(ctx, target, sudo=sudo)
    if content is None:
        if info is not None and info.is_symlink:
            raise TaskFailed(f"refusing to manage symlink as file: {target}")
        if info is not None and info.is_dir:
            raise TaskFailed(f"path is a directory: {target}")
        if info is not None and not info.is_file:
            raise TaskFailed(f"path is not a regular file: {target}")
        data = file_ops.read_bytes(ctx, target, info, sudo=sudo) if info is not None else b""
        summary = f"create {target}" if info is None else f"update metadata for {target}"
    else:
        data = content.encode()
        summary = f"write {target}"

    return ctx.changed(
        file_ops.apply_bytes(
            ctx,
            target,
            data,
            action="files.file",
            mode=mode,
            owner=owner,
            group=group,
            summary=summary,
            unchanged_summary=f"ensure file {target}",
            data={"path": str(target)},
            sudo=sudo,
        )
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
) -> CallResult:
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

    return ctx.changed(
        file_ops.apply_bytes(
            ctx,
            target,
            file_ops.read_bytes(ctx, source, source_info, sudo=sudo),
            action="files.copy",
            mode=source_info.mode if mode is None else mode,
            owner=owner,
            group=group,
            summary=f"copy {source} -> {target}",
            unchanged_summary=f"ensure {target} from {source}",
            data={"path": str(target), "src": str(source)},
            sudo=sudo,
        )
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
) -> CallResult:
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

    def reconcile(*, unchanged_summary: str | None = None) -> CallResult:
        return _reconcile_metadata(
            ctx,
            target,
            target_info,
            action="files.move",
            src=source,
            mode=mode,
            owner=owner,
            group=group,
            sudo=sudo,
            unchanged_summary=unchanged_summary,
        )

    if source_info is None:
        if target_info is not None and not target_info.is_dir and not target_info.is_symlink:
            return ctx.changed(reconcile(unchanged_summary=f"ensure {target} is moved from {source}"))
        raise TaskFailed(f"move source does not exist: {source}")
    if source_info.is_symlink or not source_info.is_file:
        raise TaskFailed(f"move source is not a regular file: {source}")
    if target_info is not None and target_info.is_dir and not target_info.is_symlink:
        raise TaskFailed(f"destination is a directory: {target}")
    if target_info is not None and target_info.is_symlink:
        raise TaskFailed(f"refusing to replace symlink: {target}")
    if source.absolute() == target.absolute():
        return ctx.changed(reconcile())

    file_ops.require_parent(ctx, target, sudo=sudo)
    move_summary = f"move {source} -> {target}"
    metadata_requested = mode is not None or owner is not None or group is not None

    def result(*, metadata: bool) -> CallResult:
        summary = f"{move_summary}; update metadata for {target}" if metadata else move_summary
        return CallResult(
            action="files.move",
            changed=True,
            summary=summary,
            dest=str(target),
            src=str(source),
        )

    if ctx.dry_run:
        if metadata_requested:
            file_ops.resolve_metadata(owner, group)
            return ctx.changed(result(metadata=True))
        return ctx.changed(result(metadata=False))

    _move(ctx, source, target, sudo=sudo)
    if not metadata_requested:
        return ctx.changed(result(metadata=False))
    moved_info = file_ops.stat(ctx, target, sudo=sudo)
    if moved_info is None:
        raise TaskFailed(f"move destination is missing after moving: {target}")
    resolved_mode = moved_info.mode if mode is None else mode
    if file_ops.metadata_differs(moved_info, resolved_mode, owner, group):
        file_ops.apply_metadata_ctx(
            ctx,
            target,
            resolved_mode,
            owner,
            group,
            sudo=sudo,
        )
        return ctx.changed(result(metadata=True))
    return ctx.changed(result(metadata=False))


def line(
    ctx: TaskContext,
    path: str | Path,
    line: str,
    *,
    match: str | None = None,
    state: State = "present",
    sudo: bool = False,
) -> CallResult:
    """Add, replace, or remove a line selected by an exact value or regex."""
    require_state(state, "line")
    require_path(path, "line path")
    if match is not None:
        require_regex(match, "line match")

    target = Path(path)
    text = file_ops.read_text(ctx, target, sudo=sudo)
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
        unchanged_summary = f"ensure line in {target}"
    else:
        new_lines = [existing for existing in lines if not _line_matches(existing, desired, match)]
        summary = f"remove line from {target}"
        unchanged_summary = f"ensure line is absent from {target}"

    new_text = "".join(new_lines)
    if new_text != text:
        return ctx.changed(
            file_ops.apply_text(
                ctx,
                target,
                text,
                new_text,
                action="files.line",
                summary=summary,
                data={"path": str(target)},
                sudo=sudo,
            )
        )
    return ctx.changed(
        action="files.line",
        changed=False,
        summary=unchanged_summary,
        path=str(target),
    )


def block(
    ctx: TaskContext,
    path: str | Path,
    marker: str,
    content: str,
    *,
    state: State = "present",
    sudo: bool = False,
) -> CallResult:
    """Create, update, or remove a marked block in a file."""
    require_state(state, "block")
    require_path(path, "block path")
    require_text(marker, "block marker")

    target = Path(path)
    text = file_ops.read_text(ctx, target, sudo=sudo)
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
        unchanged_summary = f"ensure {marker} block in {target}"
    else:
        if bounds is None:
            return ctx.changed(
                action="files.block",
                changed=False,
                summary=f"ensure {marker} block is absent from {target}",
                path=str(target),
            )
        start, stop = bounds
        new_lines = [*lines[:start], *lines[stop + 1 :]]
        summary = f"remove {marker} block from {target}"
        unchanged_summary = f"ensure {marker} block is absent from {target}"

    new_text = "".join(new_lines)
    if new_text != text:
        return ctx.changed(
            file_ops.apply_text(
                ctx,
                target,
                text,
                new_text,
                action="files.block",
                summary=summary,
                data={"path": str(target)},
                sudo=sudo,
            )
        )
    return ctx.changed(
        action="files.block",
        changed=False,
        summary=unchanged_summary,
        path=str(target),
    )


def ini(
    ctx: TaskContext,
    path: str | Path,
    *,
    section: str | None = None,
    option: str | None = None,
    value: str | None = None,
    match: Mapping[str, str] | None = None,
    state: State = "present",
    create: bool = True,
    sudo: bool = False,
) -> CallResult:
    """Set or remove one option in an INI-style file, section by section.

    Sections are matched case-sensitively as ``[section]`` headers; with
    ``section=None`` the preamble before the first header is edited. Options
    are written canonically as ``option = value``. ``match`` selects one of
    several same-named sections and is never written to the file.
    """
    require_state(state, "ini")
    require_path(path, "ini path")
    require_bool(create, "ini create")
    if section is not None:
        section = ini_ops.require_ini_section(section)
    if option is not None:
        option = ini_ops.require_ini_option(option)
    if match is not None:
        if section is None:
            raise TaskFailed("ini match requires a section")
        match = ini_ops.require_ini_match(match)
    # Validate the state-dependent arguments before any filesystem access.
    set_args: tuple[str, str] | None = None
    if state == "present":
        if option is None:
            raise TaskFailed("ini state 'present' requires an option")
        if value is None:
            raise TaskFailed("ini state 'present' requires a value")
        set_args = (option, ini_ops.require_ini_value(value))
    elif section is None and option is None:
        raise TaskFailed("ini state 'absent' requires a section or an option")

    target = Path(path)
    if not create and file_ops.stat(ctx, target, sudo=sudo) is None:
        raise TaskFailed(f"file does not exist and create=False: {target}")
    text = file_ops.read_text(ctx, target, sudo=sudo)
    lines = text.splitlines(keepends=True)

    where = f"[{section}] of {target}" if section is not None else str(target)
    data: dict[str, object] = {"path": str(target), "section": section, "option": option, "value": value}

    new_lines: list[str]
    summary: str
    unchanged_summary: str
    ini_action: str
    if set_args is not None:
        opt, desired = set_args
        new_lines, ini_action = ini_ops.set_option(lines, section, opt, desired, match)
        summary = f"set {opt} in {where}"
        unchanged_summary = f"ensure {opt} is {desired} in {where}"
    elif option is not None:
        new_lines = ini_ops.remove_option(lines, section, option, match)
        ini_action = "removed"
        summary = f"remove {option} from {where}"
        unchanged_summary = f"ensure {option} is absent from {where}"
    else:
        if section is None:
            raise TaskFailed("ini state 'absent' requires a section or an option")
        new_lines = ini_ops.remove_section(lines, section, match)
        ini_action = "removed"
        summary = f"remove section [{section}] from {target}"
        unchanged_summary = f"ensure section [{section}] is absent from {target}"

    new_text = "".join(new_lines)
    if new_text != text and new_text and not new_text.endswith("\n"):
        new_text += "\n"

    changed = new_text != text
    data["ini_action"] = ini_action if changed else "unchanged"
    if changed:
        return ctx.changed(
            file_ops.apply_text(
                ctx,
                target,
                text,
                new_text,
                action="files.ini",
                summary=summary,
                data=data,
                sudo=sudo,
            )
        )
    return ctx.changed(action="files.ini", changed=False, summary=unchanged_summary, **data)


def directory(
    ctx: TaskContext,
    path: str | Path,
    *,
    state: State = "present",
    force: bool = False,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> CallResult:
    """Ensure a directory exists or is absent."""
    require_state(state, "directory")
    require_path(path, "directory path")
    require_metadata(owner, group)
    require_mode(mode)

    target = Path(path)
    if state == "absent":
        info = file_ops.stat(ctx, target, sudo=sudo)
        if info is None:
            return ctx.changed(
                action="files.directory",
                changed=False,
                summary=f"ensure directory {target} is absent",
                path=str(target),
            )
        if info.is_symlink or not info.is_dir:
            raise TaskFailed(f"path is not a directory: {target}")
        return ctx.changed(
            file_ops.remove(
                ctx,
                target,
                info,
                action="files.directory",
                force=force,
                sudo=sudo,
                unchanged_summary=f"ensure directory {target} is absent",
            )
        )

    info = file_ops.stat(ctx, target, sudo=sudo)
    if info is not None and info.is_symlink:
        raise TaskFailed(f"refusing to manage symlink as directory: {target}")
    if info is not None and not info.is_dir:
        raise TaskFailed(f"path exists and is not a directory: {target}")

    if info is None:
        file_ops.require_parent(ctx, target, sudo=sudo)
        if ctx.dry_run:
            file_ops.resolve_metadata(owner, group)
            return ctx.changed(
                action="files.directory",
                summary=f"mkdir {target}",
                path=str(target),
            )
        _mkdir(
            ctx,
            target,
            0o755 if mode is None else mode,
            owner,
            group,
            sudo=sudo,
        )
        return ctx.changed(
            action="files.directory",
            summary=f"mkdir {target}",
            path=str(target),
        )

    resolved = info.mode if mode is None else mode
    if file_ops.metadata_differs(info, resolved, owner, group):
        if ctx.dry_run:
            file_ops.resolve_metadata(owner, group)
            return ctx.changed(
                action="files.directory",
                summary=f"chmod {target}",
                path=str(target),
            )
        file_ops.apply_metadata_ctx(ctx, target, resolved, owner, group, sudo=sudo)
        return ctx.changed(
            action="files.directory",
            summary=f"chmod {target}",
            path=str(target),
        )
    return ctx.changed(
        action="files.directory",
        changed=False,
        summary=f"ensure directory {target}",
        path=str(target),
    )


def symlink(
    ctx: TaskContext,
    target: str | Path,
    path: str | Path,
    *,
    sudo: bool = False,
) -> CallResult:
    """Create a symlink, replacing an existing file or symlink."""
    require_text(str(target), "symlink target")
    require_path(path, "symlink path")
    link = Path(path)
    desired = str(target)
    info = file_ops.stat(ctx, link, sudo=sudo)
    if info is not None and info.is_symlink and info.link == desired:
        return ctx.changed(
            action="files.symlink",
            changed=False,
            summary=f"ensure symlink {link} -> {desired}",
            path=str(link),
            target=desired,
        )
    if info is not None and info.is_dir and not info.is_symlink:
        raise TaskFailed(f"refusing to replace a directory with a symlink: {link}")

    file_ops.require_parent(ctx, link, sudo=sudo)
    result = CallResult(
        action="files.symlink",
        changed=True,
        summary=f"symlink {link} -> {desired}",
        path=str(link),
        target=desired,
    )
    if ctx.dry_run:
        return ctx.changed(result)
    if file_ops.privileged(ctx, sudo=sudo):
        file_ops.worker_request(ctx, "symlink", path=str(link), target=desired)
        return ctx.changed(result)
    try:
        symlink_path(desired, link)
    except OSError as exc:
        raise TaskFailed(f"could not create symlink {link}: {exc}") from exc
    return ctx.changed(result)


def _move(ctx: TaskContext, source: Path, target: Path, *, sudo: bool) -> None:
    if file_ops.privileged(ctx, sudo=sudo):
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
    action: str,
    src: Path,
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    sudo: bool,
    unchanged_summary: str | None = None,
) -> CallResult:
    data = {"dest": str(target), "src": str(src)}
    if info is None or (mode is None and owner is None and group is None):
        return CallResult(
            action=action,
            changed=False,
            summary=unchanged_summary or f"ensure metadata for {target}",
            **data,
        )
    resolved_mode = info.mode if mode is None else mode
    if not file_ops.metadata_differs(info, resolved_mode, owner, group):
        return CallResult(
            action=action,
            changed=False,
            summary=unchanged_summary or f"ensure metadata for {target}",
            **data,
        )
    if ctx.dry_run:
        file_ops.resolve_metadata(owner, group)
        return CallResult(action=action, changed=True, summary=f"update metadata for {target}", **data)
    file_ops.apply_metadata_ctx(ctx, target, resolved_mode, owner, group, sudo=sudo)
    return CallResult(action=action, changed=True, summary=f"update metadata for {target}", **data)


def _mkdir(
    ctx: TaskContext,
    path: Path,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    if file_ops.privileged(ctx, sudo=sudo):
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
