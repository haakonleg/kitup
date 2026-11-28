"""GNOME gsettings management."""

import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from .._utils.validation import State, require_encodable, require_state, require_text
from ..context import TaskContext, current_context
from ..errors import TaskFailed
from ..registry import module
from ..results import CallResult

module("gsettings", "GNOME settings.")

_MISSING: Final[object] = object()

_STRING_ESCAPES: Final[tuple[tuple[str, str], ...]] = (
    ("\\", "\\\\"),
    ("'", "\\'"),
    ("\a", "\\a"),
    ("\b", "\\b"),
    ("\f", "\\f"),
    ("\n", "\\n"),
    ("\r", "\\r"),
    ("\t", "\\t"),
    ("\v", "\\v"),
)


@dataclass(slots=True, frozen=True)
class _Variant:
    """Verbatim GVariant text used where Python values cannot express a type."""

    text: str


def variant(text: str) -> _Variant:
    """Wrap verbatim GVariant text for values without a native Python mapping.

    The text is validated against the key's type with ``gsettings`` before the
    key is changed. Use it for variants (``<42>``), dictionary values of type
    ``v`` (``<'value'>``), and other GVariant types without a native mapping.
    """
    require_text(text, "gsettings variant text")
    _require_text_value(text, "gsettings variant text")
    return _Variant(text)


def key(schema: str, key: str, value: Any = _MISSING, *, state: State = "present") -> CallResult:
    """Manage a gsettings key, or reset it to its schema default."""
    ctx = current_context()
    require_state(state, "gsettings")
    require_text(schema, "gsettings schema")
    require_text(key, "gsettings key")
    if ctx.sudo.is_root:
        raise TaskFailed("gsettings must run as the target user, not root")

    if state == "absent":
        if value is not _MISSING:
            raise TaskFailed("gsettings state 'absent' does not take a value")
        return ctx.changed(_reset(ctx, schema, key))
    if value is _MISSING:
        raise TaskFailed("value is required when state is present")

    text = _format_value(value)
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
            raise TaskFailed(f"gsettings {schema} {key}: {_detail(written)}")
        return _get(ctx, schema, key, env=env)


def _get(ctx: TaskContext, schema: str, key: str, *, env: Mapping[str, str] | None = None) -> str:
    result = ctx.probe("gsettings", "get", schema, key, sudo=False, env=env)
    if result.returncode != 0:
        raise TaskFailed(f"gsettings {schema} {key}: {_detail(result)}")
    return result.stdout.strip()


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or "key unavailable"


def _require_text_value(value: str, label: str) -> None:
    """Reject text that cannot cross argv as UTF-8 without being truncated."""
    require_encodable(value, label)
    if "\x00" in value:
        raise TaskFailed(f"{label} must not contain NUL characters")


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, _Variant):
        return value.text
    if value is None:
        return "nothing"
    if isinstance(value, str):
        _require_text_value(value, "gsettings value")
        return f"'{_escape_string(value)}'"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, bytes):
        return _format_bytes(value)
    if isinstance(value, dict):
        items = (f"{_format_value(item_key)}: {_format_value(item_value)}" for item_key, item_value in value.items())
        return "{" + ", ".join(items) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, tuple):
        if len(value) == 1:
            return f"({_format_value(value[0])},)"
        return "(" + ", ".join(_format_value(item) for item in value) + ")"
    raise TaskFailed(
        f"unsupported gsettings value type: {type(value).__name__}; use gsettings.variant() for verbatim GVariant text"
    )


def _escape_string(value: str) -> str:
    # Escape the backslash first so later replacements are not doubled.
    for literal, escaped in _STRING_ESCAPES:
        value = value.replace(literal, escaped)
    return value


def _format_bytes(value: bytes) -> str:
    # A GVariant bytestring always ends in NUL, so it can only represent bytes
    # with a single trailing NUL and no interior NULs (it truncates at the first).
    if value.endswith(b"\x00") and b"\x00" not in value[:-1]:
        return f"b'{_escape_bytestring(value[:-1])}'"
    return "[" + ", ".join(f"0x{byte:02x}" for byte in value) + "]"


def _escape_bytestring(value: bytes) -> str:
    parts: list[str] = []
    for byte in value:
        if byte == ord("\\"):
            parts.append("\\\\")
        elif byte == ord("'"):
            parts.append("\\'")
        elif 0x20 <= byte <= 0x7E:
            parts.append(chr(byte))
        else:
            # Bytestrings accept octal escapes only; ``\x`` is not a hex escape.
            parts.append(f"\\{byte:03o}")
    return "".join(parts)
