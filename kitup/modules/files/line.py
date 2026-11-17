"""Idempotent line editing for managed text files."""

import re
from pathlib import Path

from ..._utils import file_actions
from ..._utils.paths import expand_path
from ..._utils.validation import State, require_bool, require_path, require_regex, require_single_line, require_state
from ...context import current_context
from ...errors import TaskFailed
from ...results import CallResult


def line(
    path: str | Path, line: str, *, match: str | None = None, state: State = "present", sudo: bool = False
) -> CallResult:
    """Add, replace, or remove a line selected by an exact value or regex."""
    ctx = current_context()
    require_state(state, "line")
    require_path(path, "line path")
    require_single_line(line, "line")
    require_bool(sudo, "line sudo")
    pattern: re.Pattern[str] | None = None
    if match is not None:
        pattern = require_regex(match, "line match")

    target = expand_path(path, error=TaskFailed)
    snapshot = file_actions.read_snapshot(ctx, target, sudo=sudo)
    text = snapshot.text(target)
    lines = text.splitlines(keepends=True)

    if state == "present":
        index = _matching_line(lines, line, pattern)
        rendered = line + "\n"
        if index is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            new_lines = [*lines, rendered]
        elif lines[index].rstrip("\n") == line:
            new_lines = lines
        else:
            new_lines = list(lines)
            new_lines[index] = rendered
        summary = f"{'replace' if index is not None else 'append'} line in {target}"
        unchanged_summary = f"ensure line in {target}"
    else:
        new_lines = [existing for existing in lines if not _line_matches(existing, line, pattern)]
        summary = f"remove line from {target}"
        unchanged_summary = f"ensure line is absent from {target}"

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


def _matching_line(lines: list[str], desired: str, match: re.Pattern[str] | None) -> int | None:
    for index, existing in enumerate(lines):
        if _line_matches(existing, desired, match):
            return index
    return None


def _line_matches(existing: str, desired: str, match: re.Pattern[str] | None) -> bool:
    candidate = existing.rstrip("\n")
    if match is None:
        return candidate == desired
    return match.search(candidate) is not None
