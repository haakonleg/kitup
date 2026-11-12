"""Shared argument validation for module APIs."""

import math
import re
import subprocess
import unicodedata
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Final, Literal

from ..errors import TaskFailed
from .text import LINE_BREAKS

type State = Literal["present", "absent"]

_PRESENT_OR_ABSENT: Final[tuple[State, ...]] = ("present", "absent")


def normalize_strings(values: str | Iterable[str]) -> list[str]:
    """Return a list, treating a single string as a one-element sequence."""
    if isinstance(values, str):
        return [values]
    return list(values)


def normalize_extra_args(extra_args: str | Iterable[str] | None, label: str = "extra arg") -> list[str]:
    """Normalize extra CLI arguments to a list, rejecting empty ones.

    No dedup: some flags are meaningfully repeatable, e.g. ``-v -v``.
    """
    if extra_args is None:
        return []
    values = normalize_strings(extra_args)
    require_texts(values, label)
    return values


def split_tags(values: Iterable[str], *, allow_empty: bool = True) -> list[str]:
    """Split comma-separated tag values and strip surrounding whitespace."""
    tags: list[str] = []
    for value in values:
        for part in value.split(","):
            tag = part.strip()
            if tag:
                tags.append(tag)
            elif not allow_empty:
                raise ValueError("tag must not be empty")
    return tags


def unique_strings(values: Iterable[str | Path]) -> list[str]:
    """Return deduplicated values as strings, preserving order."""
    return list(dict.fromkeys(str(value) for value in values))


def require_collection(values: Collection[object], label: str) -> None:
    """Reject an empty collection."""
    if not values:
        raise TaskFailed(f"{label} must not be empty")


def require_text(value: str, label: str) -> None:
    """Reject empty or whitespace-only text identifiers."""
    if not value.strip():
        raise TaskFailed(f"{label} must not be empty")


def require_texts(values: Iterable[str], label: str) -> None:
    """Validate every item in a collection of text identifiers."""
    for value in values:
        require_text(value, label)


def require_identity(value: str | int | None, label: str) -> None:
    """Validate an optional user or group name or numeric ID."""
    if value is None:
        return
    if isinstance(value, bool):
        raise TaskFailed(f"{label} must be a name or numeric ID")
    if isinstance(value, int):
        if value < 0:
            raise TaskFailed(f"{label} must be non-negative")
        return
    if not isinstance(value, str):
        raise TaskFailed(f"{label} must be a name or numeric ID")
    require_text(value, label)


def require_metadata(owner: str | int | None, group: str | int | None) -> None:
    """Validate optional owner and group identities."""
    require_identity(owner, "owner")
    require_identity(group, "group")


def require_bool(value: object, label: str) -> None:
    """Reject a non-boolean value."""
    if not isinstance(value, bool):
        raise TaskFailed(f"{label} must be a boolean")


def require_timeout(value: float, label: str = "timeout") -> None:
    """Reject non-finite, non-numeric, or non-positive timeouts."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskFailed(f"{label} must be a positive number")
    if not math.isfinite(value) or value <= 0:
        raise TaskFailed(f"{label} must be a positive number")


def has_control_characters(value: str, *, allow: str = "") -> bool:
    r"""Return whether ``value`` contains a non-printable character or line break.

    This covers Unicode category ``C`` (controls, format characters, surrogates,
    private use, and unassigned code points) plus every line boundary that
    :func:`str.splitlines` recognises. ``allow`` lists characters exempt from
    the check, such as ``"\t"`` for header values.
    """
    return any(
        character not in allow and (unicodedata.category(character).startswith("C") or character in LINE_BREAKS)
        for character in value
    )


def require_single_line(value: str, label: str) -> None:
    """Reject text containing any Unicode line break."""
    if any(character in LINE_BREAKS for character in value):
        raise TaskFailed(f"{label} must not contain line breaks")


def require_non_negative_int(value: int, label: str) -> None:
    """Reject a negative or non-integer value."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TaskFailed(f"{label} must be a non-negative integer")


def require_mode(mode: int | None) -> None:
    """Validate an optional file mode."""
    if mode is None:
        return
    if isinstance(mode, bool) or not isinstance(mode, int):
        raise TaskFailed("mode must be an integer")
    if not 0 <= mode <= 0o7777:
        raise TaskFailed("mode must be between 0 and 0o7777")


def require_choice(value: str, choices: Collection[str], label: str) -> None:
    """Reject a value outside the allowed choices."""
    if value not in choices:
        raise TaskFailed(f"invalid {label}: {value!r}")


def require_state(
    state: str,
    label: str,
    allowed: Collection[str] = _PRESENT_OR_ABSENT,
) -> None:
    """Reject a state value outside the allowed choices."""
    if state not in allowed:
        raise TaskFailed(f"invalid {label} state: {state!r}")


def require_scope(scope: str, label: str, *, is_root: bool) -> None:
    """Validate a system/user scope and reject user scope when running as root."""
    if scope == "system":
        return
    if scope == "user":
        if is_root:
            raise TaskFailed(f"user-scoped {label} cannot run as root")
        return
    raise TaskFailed(f"invalid {label} scope: {scope!r}")


def require_success(result: subprocess.CompletedProcess[str], message: str) -> None:
    """Raise ``TaskFailed`` with captured output when a command failed."""
    if result.returncode != 0:
        raise TaskFailed(
            message,
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def require_path(value: str | Path, label: str) -> None:
    """Reject empty path arguments."""
    if not str(value).strip():
        raise TaskFailed(f"{label} must not be empty")


def require_regex(value: str, label: str) -> re.Pattern[str]:
    """Validate a regular expression and return the compiled pattern."""
    if not isinstance(value, str):
        raise TaskFailed(f"{label} must be a string")
    if value == "":
        raise TaskFailed(f"{label} must not be empty")
    try:
        return re.compile(value)
    except re.error as exc:
        raise TaskFailed(f"invalid {label}: {exc}") from exc


def require_encodable(value: str, label: str) -> None:
    """Reject strings that cannot be encoded as UTF-8."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TaskFailed(f"{label} is not UTF-8 encodable") from exc
