"""GNOME gsettings management."""

import tempfile
from collections.abc import Mapping
from typing import Any

from .._utils import gvariant
from .._utils.validation import State, require_state, require_text
from ..context import TaskContext, current_context
from ..errors import TaskFailed
from ..registry import module
from ..results import CallResult

module("gsettings", "GNOME settings.")


def variant(text: str) -> gvariant.Variant:
    """Wrap verbatim GVariant text for values without a native Python mapping.

    The text is validated against the key's type with ``gsettings`` before the
    key is changed. Use it for variants (``<42>``), dictionary values of type
    ``v`` (``<'value'>``), and other GVariant types without a native mapping.
    """
    return gvariant.variant(text, "gsettings")


def key(schema: str, key: str, value: Any = gvariant.MISSING, *, state: State = "present") -> CallResult:
    """Manage a gsettings key, or reset it to its schema default."""
    ctx = current_context()
    require_state(state, "gsettings")
    require_text(schema, "gsettings schema")
    require_text(key, "gsettings key")
    gvariant.require_user_session(ctx, "gsettings")

    if state == "absent":
        if value is not gvariant.MISSING:
            raise TaskFailed("gsettings state 'absent' does not take a value")
        return ctx.changed(_reset(ctx, schema, key))
    if value is gvariant.MISSING:
        raise TaskFailed("value is required when state is present")

    text = gvariant.format_value(value, label="gsettings")
    canonical = _canonical(ctx, schema, key, text)
    if _get(ctx, schema, key) == canonical:
        return ctx.changed(
            changed=False,
            summary=f"ensure {schema} {key}={text}",
            schema=schema,
            key=key,
            value=value,
        )

    call = CallResult(changed=True, summary=f"set {schema} {key}={text}", schema=schema, key=key, value=value)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run("gsettings", "set", schema, key, text, sudo=False)
    return ctx.changed(call)


def _reset(ctx: TaskContext, schema: str, key: str) -> CallResult:
    # Read first so a missing schema or key fails before recording a change.
    current = _get(ctx, schema, key)
    default = ctx.probe("gsettings", "get", schema, key, sudo=False, env={"GSETTINGS_BACKEND": "memory"})
    if default.returncode == 0 and current == default.stdout.strip():
        return CallResult(changed=False, summary=f"ensure {schema} {key} is reset", schema=schema, key=key)

    call = CallResult(changed=True, summary=f"reset {schema} {key}", schema=schema, key=key)
    if ctx.dry_run:
        return call
    ctx.run("gsettings", "reset", schema, key, sudo=False)
    return call


def _canonical(ctx: TaskContext, schema: str, key: str, text: str) -> str:
    """Validate value text against the key's type and return its canonical form.

    A throwaway ``keyfile`` backend both rejects invalid values before the key
    is touched and reports the same annotated form that ``gsettings get`` prints
    for the real backend, which makes idempotency a plain string comparison.
    """
    with tempfile.TemporaryDirectory(prefix="kitup-gsettings-") as home:
        env = {"GSETTINGS_BACKEND": "keyfile", "XDG_CONFIG_HOME": home}
        written = ctx.probe("gsettings", "set", schema, key, text, sudo=False, env=env)
        if written.returncode != 0:
            raise TaskFailed(f"gsettings {schema} {key}: {gvariant.detail(written)}")
        return _get(ctx, schema, key, env=env)


def _get(ctx: TaskContext, schema: str, key: str, *, env: Mapping[str, str] | None = None) -> str:
    result = ctx.probe("gsettings", "get", schema, key, sudo=False, env=env)
    if result.returncode != 0:
        raise TaskFailed(f"gsettings {schema} {key}: {gvariant.detail(result)}")
    return result.stdout.strip()
