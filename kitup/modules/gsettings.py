"""GNOME gsettings management."""

from typing import Any

from .._utils.validation import State, require_state, require_text
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult


def key(
    ctx: TaskContext,
    schema: str,
    key: str,
    value: Any = None,
    *,
    state: State = "present",
) -> CallResult:
    """Manage a gsettings key, or reset it to its schema default."""
    require_state(state, "gsettings")
    require_text(schema, "gsettings schema")
    require_text(key, "gsettings key")
    if ctx.sudo.is_root:
        raise TaskFailed("gsettings must run as the target user, not root")

    if state == "absent":
        if value is not None:
            raise TaskFailed("gsettings state 'absent' does not take a value")
        return ctx.changed(_reset(ctx, schema, key))
    if value is None:
        raise TaskFailed("value is required when state is present")

    desired = _format_value(value)
    if _read(ctx, schema, key) == desired:
        return ctx.changed(
            action="gsettings.key",
            changed=False,
            summary=f"ensure {schema} {key}={desired}",
            schema=schema,
            key=key,
            value=value,
        )

    call = CallResult(
        action="gsettings.key",
        changed=True,
        summary=f"set {schema} {key}={desired}",
        schema=schema,
        key=key,
        value=value,
    )
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(
        ["gsettings", "set", schema, key, desired],
        sudo=False,
    )
    return ctx.changed(call)


def _reset(ctx: TaskContext, schema: str, key: str) -> CallResult:
    # Read first so a missing schema or key fails before recording a change.
    current = _read(ctx, schema, key)
    default = ctx.probe(
        ["gsettings", "get", schema, key],
        sudo=False,
        env={"GSETTINGS_BACKEND": "memory"},
    )
    if default.returncode == 0 and current == _normalize(default.stdout):
        return CallResult(
            action="gsettings.key",
            changed=False,
            summary=f"ensure {schema} {key} is reset",
            schema=schema,
            key=key,
        )

    call = CallResult(
        action="gsettings.key",
        changed=True,
        summary=f"reset {schema} {key}",
        schema=schema,
        key=key,
    )
    if ctx.dry_run:
        return call
    ctx.run(
        ["gsettings", "reset", schema, key],
        sudo=False,
    )
    return call


def _read(ctx: TaskContext, schema: str, key: str) -> str:
    result = ctx.probe(["gsettings", "get", schema, key], sudo=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "key unavailable"
        raise TaskFailed(f"gsettings {schema} {key}: {detail}")
    return _normalize(result.stdout)


def _normalize(value: str) -> str:
    value = value.strip()
    if value.startswith("@"):
        _, _, value = value.partition(" ")
    return value.strip()


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace("\b", "\\b")
            .replace("\f", "\\f")
        )
        return f"'{escaped}'"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, tuple):
        if len(value) == 1:
            return f"({_format_value(value[0])},)"
        return "(" + ", ".join(_format_value(item) for item in value) + ")"
    raise TaskFailed(f"unsupported gsettings value type: {type(value).__name__}")
