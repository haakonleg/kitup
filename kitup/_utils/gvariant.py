"""Shared GVariant value handling for gsettings and dconf."""

import subprocess
from dataclasses import dataclass
from typing import Any, Final

from ..context import TaskContext
from ..errors import TaskFailed
from .validation import require_encodable, require_text

MISSING: Final[object] = object()

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
class Variant:
    """Verbatim GVariant text used where Python values cannot express a type."""

    text: str


def variant(text: str, label: str) -> Variant:
    """Wrap verbatim GVariant text after validating it can cross argv."""
    require_text(text, f"{label} variant text")
    require_text_value(text, f"{label} variant text")
    return Variant(text)


def format_value(value: Any, *, label: str) -> str:
    """Return the GVariant text for a Python value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Variant):
        return value.text
    if value is None:
        return "nothing"
    if isinstance(value, str):
        require_text_value(value, f"{label} value")
        return f"'{_escape_string(value)}'"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, bytes):
        return _format_bytes(value)
    if isinstance(value, dict):
        items = (
            f"{format_value(item_key, label=label)}: {format_value(item_value, label=label)}"
            for item_key, item_value in value.items()
        )
        return "{" + ", ".join(items) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(format_value(item, label=label) for item in value) + "]"
    if isinstance(value, tuple):
        if len(value) == 1:
            return f"({format_value(value[0], label=label)},)"
        return "(" + ", ".join(format_value(item, label=label) for item in value) + ")"
    raise TaskFailed(
        f"unsupported {label} value type: {type(value).__name__}; use {label}.variant() for verbatim GVariant text"
    )


def require_text_value(value: str, label: str) -> None:
    """Reject text that cannot cross argv as UTF-8 without being truncated."""
    require_encodable(value, label)
    if "\x00" in value:
        raise TaskFailed(f"{label} must not contain NUL characters")


def require_user_session(ctx: TaskContext, name: str) -> None:
    """Reject running a user-session tool as root."""
    if ctx.sudo.is_root:
        raise TaskFailed(f"{name} must run as the target user, not root")


def detail(result: subprocess.CompletedProcess[str]) -> str:
    """Return the most useful captured output for a failed command."""
    return result.stderr.strip() or result.stdout.strip() or "key unavailable"


def _escape_string(value: str) -> str:
    """Escape a Python string for a single-quoted GVariant string."""
    # Escape the backslash first so later replacements are not doubled.
    for literal, escaped in _STRING_ESCAPES:
        value = value.replace(literal, escaped)
    return value


def _format_bytes(value: bytes) -> str:
    """Format bytes as a bytestring when possible, otherwise as a byte array."""
    # A GVariant bytestring always ends in NUL, so it can only represent bytes
    # with a single trailing NUL and no interior NULs (it truncates at the first).
    if value.endswith(b"\x00") and b"\x00" not in value[:-1]:
        return f"b'{_escape_bytestring(value[:-1])}'"
    return "[" + ", ".join(f"0x{byte:02x}" for byte in value) + "]"


def _escape_bytestring(value: bytes) -> str:
    """Escape bytes for a single-quoted GVariant bytestring."""
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
