"""Shared argument validation for module APIs."""

from __future__ import annotations

import subprocess
from collections.abc import Collection, Iterable
from pathlib import Path

from .errors import TaskFailed

_PRESENT_OR_ABSENT = ("present", "absent")


def normalize_strings(values: str | Iterable[str]) -> list[str]:
    """Return a list, treating a single string as a one-element sequence."""
    if isinstance(values, str):
        return [values]
    return list(values)


def normalize_string_sequence(values: Iterable[str], label: str) -> tuple[str, ...]:
    """Return values as a tuple, rejecting a single string."""
    if isinstance(values, str):
        raise TypeError(f"{label} must be a sequence of strings, not a single string")
    return tuple(values)


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


def require_regex(value: str, label: str) -> None:
    """Reject an empty regular expression."""
    if value == "":
        raise TaskFailed(f"{label} must not be empty")
