"""Idempotent file, directory, symlink, and move operations."""

from pathlib import Path

from ..._utils import file_actions
from ..._utils.file_actions import PathPolicy, require_path_kind
from ..._utils.file_primitives import move_path, symlink_path
from ..._utils.paths import expand_path
from ..._utils.validation import (
    State,
    normalize_mode,
    require_bool,
    require_metadata,
    require_path,
    require_state,
    require_text,
)
from ...context import TaskContext, current_context
from ...errors import TaskFailed
from ...results import CallResult


def file(
    path: str | Path,
    *,
    state: State = "present",
    content: str | None = None,
    mode: int | str | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> CallResult:
    """Ensure a text file exists with optional content, or is absent.

    ``sudo=True`` performs the operation through the privileged worker.
    """
    ctx = current_context()
    require_state(state, "file")
    require_path(path, "file path")
    require_metadata(owner, group)
    mode = normalize_mode(mode)
    require_bool(sudo, "file sudo")

    target = expand_path(path, error=TaskFailed)
    if state == "absent":
        info = file_actions.stat(ctx, target, sudo=sudo)
        require_path_kind(target, info, policy=PathPolicy.NOT_DIRECTORY)
        return ctx.changed(
            file_actions.remove(
                ctx,
                target,
                info,
                force=False,
                sudo=sudo,
                unchanged_summary=f"ensure {target} is absent",
            )
        )

    if content is None:
        info = file_actions.stat(ctx, target, sudo=sudo)
        require_path_kind(target, info, policy=PathPolicy.FILE)
        if info is not None:
            return ctx.changed(
                file_actions.reconcile_metadata(
                    ctx,
                    target,
                    info,
                    mode=mode,
                    owner=owner,
                    group=group,
                    summary=f"update metadata for {target}",
                    unchanged_summary=f"ensure file {target}",
                    data={"path": str(target)},
                    sudo=sudo,
                )
            )
        snapshot = file_actions.FileSnapshot(None)
        data = b""
        summary = f"create {target}"
    else:
        data = content.encode()
        snapshot = file_actions.read_snapshot(ctx, target, sudo=sudo, content_size=len(data))
        summary = f"write {target}"

    return ctx.changed(
        file_actions.apply_bytes(
            ctx,
            target,
            data,
            snapshot=snapshot,
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
    src: str | Path,
    dest: str | Path,
    *,
    mode: int | str | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> CallResult:
    """Copy one regular file, preserving its mode unless overridden.

    Relative ``src`` paths resolve against the bundle's merged ``files`` view, so
    project overlays apply; absolute paths are used as-is. When ``dest`` ends
    with a slash it is treated as a directory and the source filename is
    appended.
    """
    ctx = current_context()
    require_path(src, "copy source")
    require_path(dest, "copy destination")
    require_metadata(owner, group)
    mode = normalize_mode(mode)
    require_bool(sudo, "copy sudo")
    source = ctx.resolve_source(src)
    target = expand_path(dest, error=TaskFailed)
    if str(dest).endswith("/"):
        target /= source.name

    comparison = file_actions.compare(ctx, source, target, sudo=sudo)
    source_info = comparison.source
    target_info = comparison.target
    require_path_kind(target, target_info, policy=PathPolicy.REPLACEABLE, label="destination")

    resolved_mode = source_info.mode if mode is None else mode
    data: dict[str, object] = {"path": str(target), "src": str(source)}
    if comparison.equal:
        return ctx.changed(
            file_actions.reconcile_metadata(
                ctx,
                target,
                target_info,
                mode=resolved_mode,
                owner=owner,
                group=group,
                summary=f"copy {source} -> {target}",
                unchanged_summary=f"ensure {target} from {source}",
                data=data,
                sudo=sudo,
            )
        )

    source_content: bytes | None = None
    target_content: bytes | None = None
    if ctx.diff:
        source_content = file_actions.read_bytes(ctx, source, source_info, sudo=sudo)
        if target_info is not None and target_info.is_file:
            target_content = file_actions.read_bytes(ctx, target, target_info, sudo=sudo)

    def write() -> None:
        file_actions.copy_file(
            ctx,
            source,
            target,
            mode=resolved_mode,
            owner=owner,
            group=group,
            sudo=sudo,
        )

    return ctx.changed(
        file_actions.apply_change(
            ctx,
            target,
            summary=f"copy {source} -> {target}",
            data=data,
            sudo=sudo,
            old=target_content,
            new=source_content,
            write=write,
            owner=owner,
            group=group,
        )
    )


def move(
    src: str | Path,
    dest: str | Path,
    *,
    mode: int | str | None = None,
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
    ctx = current_context()
    require_path(src, "move source")
    require_path(dest, "move destination")
    require_metadata(owner, group)
    mode = normalize_mode(mode)
    require_bool(sudo, "move sudo")
    source = expand_path(src, error=TaskFailed)
    target = expand_path(dest, error=TaskFailed)
    if str(dest).endswith("/"):
        target /= source.name

    source_info = file_actions.stat(ctx, source, sudo=sudo)
    target_info = file_actions.stat(ctx, target, sudo=sudo)

    def reconcile(*, unchanged_summary: str | None = None) -> CallResult:
        return file_actions.reconcile_metadata(
            ctx,
            target,
            target_info,
            mode=mode,
            owner=owner,
            group=group,
            summary=f"update metadata for {target}",
            unchanged_summary=unchanged_summary or f"ensure metadata for {target}",
            data={"dest": str(target), "src": str(source)},
            sudo=sudo,
        )

    if source_info is None:
        if target_info is not None and not target_info.is_dir and not target_info.is_symlink:
            return ctx.changed(reconcile(unchanged_summary=f"ensure {target} is moved from {source}"))
        raise TaskFailed(f"move source does not exist: {source}")
    require_path_kind(source, source_info, policy=PathPolicy.FILE, label="move source")
    require_path_kind(target, target_info, policy=PathPolicy.FILE, label="destination")
    if source.absolute() == target.absolute():
        return ctx.changed(reconcile())

    move_summary = f"move {source} -> {target}"
    metadata_requested = mode is not None or owner is not None or group is not None

    def result(*, metadata: bool) -> CallResult:
        summary = f"{move_summary}; update metadata for {target}" if metadata else move_summary
        return CallResult(changed=True, summary=summary, dest=str(target), src=str(source))

    if ctx.dry_run:
        if metadata_requested:
            file_actions.resolve_metadata(owner, group)
            return ctx.changed(result(metadata=True))
        return ctx.changed(result(metadata=False))

    file_actions.require_parent(ctx, target, sudo=sudo)
    _move(ctx, source, target, sudo=sudo)
    if not metadata_requested:
        return ctx.changed(result(metadata=False))
    moved_info = file_actions.stat(ctx, target, sudo=sudo)
    if moved_info is None:
        raise TaskFailed(f"move destination is missing after moving: {target}")
    resolved_mode = moved_info.mode if mode is None else mode
    if file_actions.metadata_differs(moved_info, resolved_mode, owner, group):
        file_actions.apply_metadata_ctx(
            ctx,
            target,
            resolved_mode,
            owner,
            group,
            sudo=sudo,
        )
        return ctx.changed(result(metadata=True))
    return ctx.changed(result(metadata=False))


def directory(
    path: str | Path,
    *,
    state: State = "present",
    force: bool = False,
    parents: bool = False,
    mode: int | str | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> CallResult:
    """Ensure a directory exists or is absent.

    ``parents=True`` creates missing parent directories with the same ``mode``,
    ``owner``, and ``group`` as the target. Existing parents are never modified,
    so their mode and ownership are preserved.
    """
    ctx = current_context()
    require_state(state, "directory")
    require_path(path, "directory path")
    require_metadata(owner, group)
    mode = normalize_mode(mode)
    require_bool(force, "directory force")
    require_bool(parents, "directory parents")
    require_bool(sudo, "directory sudo")
    if parents and state == "absent":
        raise TaskFailed("directory parents=True requires state 'present'")

    target = expand_path(path, error=TaskFailed)
    if state == "absent":
        info = file_actions.stat(ctx, target, sudo=sudo)
        if info is None:
            return ctx.changed(
                CallResult(changed=False, summary=f"ensure directory {target} is absent", path=str(target))
            )
        require_path_kind(target, info, policy=PathPolicy.DIRECTORY)
        return ctx.changed(
            file_actions.remove(
                ctx,
                target,
                info,
                force=force,
                sudo=sudo,
                unchanged_summary=f"ensure directory {target} is absent",
            )
        )

    info = file_actions.stat(ctx, target, sudo=sudo)
    require_path_kind(target, info, policy=PathPolicy.DIRECTORY)

    if info is None:
        directory_mode = 0o755 if mode is None else mode
        file_actions.resolve_metadata(owner, group)
        created = (
            file_actions.ensure_parent(
                ctx,
                target,
                sudo=sudo,
                mode=directory_mode,
                owner=owner,
                group=group,
            )
            if parents
            else []
        )
        if not ctx.dry_run:
            if not parents:
                file_actions.require_parent(ctx, target, sudo=sudo)
            file_actions.make_directory(
                ctx,
                target,
                mode=directory_mode,
                owner=owner,
                group=group,
                sudo=sudo,
            )
        summary = f"mkdir -p {target}" if created else f"mkdir {target}"
        return ctx.changed(CallResult(changed=True, summary=summary, path=str(target)))

    return ctx.changed(
        file_actions.reconcile_metadata(
            ctx,
            target,
            info,
            mode=mode,
            owner=owner,
            group=group,
            summary=f"chmod {target}",
            unchanged_summary=f"ensure directory {target}",
            data={"path": str(target)},
            sudo=sudo,
        )
    )


def symlink(target: str | Path, path: str | Path, *, sudo: bool = False) -> CallResult:
    """Create a symlink, replacing an existing file or symlink."""
    ctx = current_context()
    require_text(str(target), "symlink target")
    require_path(path, "symlink path")
    require_bool(sudo, "symlink sudo")
    link = expand_path(path, error=TaskFailed)
    desired = str(expand_path(target, error=TaskFailed))
    info = file_actions.stat(ctx, link, sudo=sudo)
    if info is not None and info.is_symlink and info.link == desired:
        return ctx.changed(changed=False, summary=f"ensure symlink {link} -> {desired}", path=str(link), target=desired)
    require_path_kind(link, info, policy=PathPolicy.NOT_DIRECTORY)

    result = CallResult(changed=True, summary=f"symlink {link} -> {desired}", path=str(link), target=desired)
    if ctx.dry_run:
        return ctx.changed(result)
    file_actions.require_parent(ctx, link, sudo=sudo)
    if file_actions.privileged(ctx, sudo=sudo):
        file_actions.worker_request(ctx, "symlink", path=str(link), target=desired)
        return ctx.changed(result)
    try:
        symlink_path(desired, link)
    except OSError as exc:
        raise TaskFailed(f"could not create symlink {link}: {exc}") from exc
    return ctx.changed(result)


def _move(ctx: TaskContext, source: Path, target: Path, *, sudo: bool) -> None:
    if file_actions.privileged(ctx, sudo=sudo):
        file_actions.worker_request(ctx, "move", source=str(source), path=str(target))
        return
    try:
        move_path(source, target)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not move {source} to {target}: {exc}") from exc
