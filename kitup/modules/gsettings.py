"""GNOME gsettings management."""

from __future__ import annotations

from typing import Any

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_state, require_text


def key(
    ctx: TaskContext,
    schema: str,
    key: str,
    value: Any = None,
    *,
    state: str = "present",
) -> None:
    """Manage a gsettings key, or reset it to its schema default."""
    require_state(state, "gsettings")
    require_text(schema, "gsettings schema")
    require_text(key, "gsettings key")
    if ctx.sudo.is_root:
        raise TaskFailed("gsettings must run as the target user, not root")

    if state == "absent":
        _reset(ctx, schema, key)
        return
    if value is None:
        raise TaskFailed("value is required when state is present")

    desired = _format_value(value)
    if _read(ctx, schema, key) == desired:
        return

    ctx.changed(
        {"schema": schema, "key": key, "value": value},
        f"{schema} {key}={desired}",
    )
    ctx.run(
        ["gsettings", "set", schema, key, desired],
        sudo=False,
        record=False,
    )


def _reset(ctx: TaskContext, schema: str, key: str) -> None:
    # Read first so a missing schema or key fails before recording a change.
    current = _read(ctx, schema, key)
    default = ctx.probe(
        ["gsettings", "get", schema, key],
        sudo=False,
        env={"GSETTINGS_BACKEND": "memory"},
    )
    if default.returncode == 0 and current == _normalize(default.stdout):
        return

    ctx.changed(
        {"schema": schema, "key": key, "state": "absent"},
        f"reset {schema} {key}",
    )
    ctx.run(
        ["gsettings", "reset", schema, key],
        sudo=False,
        record=False,
    )


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
