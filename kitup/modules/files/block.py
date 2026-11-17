"""Idempotent marked-block editing for managed text files."""

from pathlib import Path

from ..._utils import file_actions
from ..._utils.paths import expand_path
from ..._utils.validation import (
    State,
    require_bool,
    require_path,
    require_single_line,
    require_state,
    require_text,
)
from ...context import current_context
from ...errors import TaskFailed
from ...results import CallResult


def block(path: str | Path, marker: str, content: str, *, state: State = "present", sudo: bool = False) -> CallResult:
    """Create, update, or remove a marked block in a file."""
    ctx = current_context()
    require_state(state, "block")
    require_path(path, "block path")
    require_text(marker, "block marker")
    require_single_line(marker, "block marker")
    require_bool(sudo, "block sudo")

    target = expand_path(path, error=TaskFailed)
    snapshot = file_actions.read_snapshot(ctx, target, sudo=sudo)
    text = snapshot.text(target)
    lines = text.splitlines(keepends=True)
    begin_marker, end_marker = _block_markers(marker)
    begin = f"{begin_marker}\n"
    end = f"{end_marker}\n"
    bounds = _block_bounds(lines, marker, begin_marker, end_marker)

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
                changed=False,
                summary=f"ensure {marker} block is absent from {target}",
                path=str(target),
            )
        start, stop = bounds
        new_lines = [*lines[:start], *lines[stop + 1 :]]
        summary = f"remove {marker} block from {target}"
        unchanged_summary = f"ensure {marker} block is absent from {target}"

    new_text = "".join(new_lines)
    return ctx.changed(
        file_actions.apply_text(
            ctx,
            target,
            new_text,
            snapshot=snapshot,
            summary=summary,
            unchanged_summary=unchanged_summary,
            data={"path": str(target)},
            sudo=sudo,
        )
    )


def _block_markers(marker: str) -> tuple[str, str]:
    return f"# BEGIN {marker}", f"# END {marker}"


def _block_bounds(lines: list[str], marker: str, begin: str, end: str) -> tuple[int, int] | None:
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
