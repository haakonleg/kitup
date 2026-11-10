"""Linux sysctl parameter management."""

import re
from pathlib import Path
from typing import Final

from .._utils import file_actions, ini
from .._utils.paths import expand_path
from .._utils.validation import State, require_state, require_text
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult

_KEY_PATTERN: Final = r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*"


def key(
    ctx: TaskContext,
    name: str,
    value: str | float | bool | None = None,  # noqa: FBT001 - positional value matches the module API
    *,
    state: State = "present",
    file: str | Path | None = None,
) -> CallResult:
    """Set a sysctl key at runtime and optionally persist it in a file.

    ``file=None`` changes only the runtime value. When ``file`` is an absolute
    path, a ``name = value`` entry is also managed in that file. An existing
    entry with the desired value is left untouched. ``state="absent"``
    requires ``file`` and only edits the file. Boolean values are written as
    ``1`` or ``0``.
    """
    require_state(state, "sysctl")
    key_name = _normalize_name(name)
    if state == "absent" and value is not None:
        raise TaskFailed("sysctl state 'absent' does not take a value")
    target = _resolve_file(file)

    if state == "absent":
        if target is None:
            raise TaskFailed("state 'absent' requires a sysctl file")
        return ctx.changed(_remove_entry(ctx, target, key_name))

    if value is None:
        raise TaskFailed("value is required when state is present")
    desired = _format_value(value)

    current = _read_runtime(ctx, key_name)
    file_call = _write_entry(ctx, target, key_name, desired) if target is not None else None
    runtime_changed = _normalize_ws(current) != _normalize_ws(desired)
    summaries: list[str] = []
    if file_call is not None and file_call.changed:
        summaries.append(file_call.summary)
    if runtime_changed:
        summaries.append(f"set {key_name}={desired}")
    data: dict[str, object] = {
        "key": key_name,
        "value": desired,
        "runtime": runtime_changed,
    }
    if target is not None:
        data["path"] = str(target)
    changed = runtime_changed or (file_call is not None and file_call.changed)
    result = CallResult(
        action="sysctl.key",
        changed=changed,
        summary="; ".join(summaries) if summaries else f"ensure {key_name}={desired}",
        **data,
    )
    if runtime_changed and not ctx.dry_run:
        ctx.run(["sysctl", "-w", f"{key_name}={desired}"], sudo=True)
    return ctx.changed(result)


def _normalize_name(name: str) -> str:
    require_text(name, "sysctl key")
    normalized = name.strip().replace("/", ".")
    if re.fullmatch(_KEY_PATTERN, normalized) is None:
        raise TaskFailed(f"invalid sysctl key: {name!r}")
    return normalized


def _resolve_file(file: str | Path | None) -> Path | None:
    if file is None:
        return None
    target = expand_path(file, error=TaskFailed)
    if not target.is_absolute():
        raise TaskFailed("sysctl file must be an absolute path")
    return target


def _format_value(value: str | float) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if "\n" in value or "\r" in value or "\0" in value:
            raise TaskFailed("sysctl value must not contain newlines or null bytes")
        return value
    raise TaskFailed(f"unsupported sysctl value type: {type(value).__name__}")


def _read_runtime(ctx: TaskContext, name: str) -> str:
    result = ctx.probe(["sysctl", "-n", name], sudo=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "key unavailable"
        raise TaskFailed(f"sysctl {name}: {detail}")
    return result.stdout.strip()


def _normalize_ws(value: str) -> str:
    return " ".join(value.split())


def _write_entry(ctx: TaskContext, target: Path, name: str, desired: str) -> CallResult:
    pattern = ini.option_re(name)
    snapshot = file_actions.read_snapshot(ctx, target, sudo=True)
    text = snapshot.text(target)
    lines = text.splitlines(keepends=True)
    matches = [line for line in lines if pattern.match(line) is not None]
    if len(matches) == 1 and _entry_value(matches[0]) == _normalize_ws(desired):
        # Keep the existing line, including its spacing and position.
        return CallResult(
            action="sysctl.key",
            changed=False,
            summary=f"ensure {name} in {target}",
            path=str(target),
            key=name,
        )
    remaining = [line for line in lines if pattern.match(line) is None]
    if remaining and not remaining[-1].endswith("\n"):
        remaining[-1] += "\n"
    remaining.append(ini.render_option(name, desired))
    return file_actions.apply_text(
        ctx,
        target,
        "".join(remaining),
        snapshot=snapshot,
        action="sysctl.key",
        summary=f"set {name} in {target}",
        unchanged_summary=f"ensure {name} in {target}",
        data={"path": str(target), "key": name},
        sudo=True,
    )


def _entry_value(line: str) -> str:
    parsed = ini.parse_option(line)
    if parsed is None:
        return ""
    return _normalize_ws(parsed[1])


def _remove_entry(ctx: TaskContext, target: Path, name: str) -> CallResult:
    pattern = ini.option_re(name)
    snapshot = file_actions.read_snapshot(ctx, target, sudo=True)
    text = snapshot.text(target)
    new_text = "".join(line for line in text.splitlines(keepends=True) if pattern.match(line) is None)
    return file_actions.apply_text(
        ctx,
        target,
        new_text,
        snapshot=snapshot,
        action="sysctl.key",
        summary=f"remove {name} from {target}",
        unchanged_summary=f"ensure {name} is absent from {target}",
        data={"path": str(target), "key": name},
        sudo=True,
    )
