"""Shared argument validation for module APIs."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path

from .errors import TaskFailed


def normalize_strings(values: str | Iterable[str]) -> list[str]:
    """Return a list, treating a single string as a one-element sequence."""
    if isinstance(values, str):
        return [values]
    return list(values)


def require_collection(values: Collection[object], label: str) -> None:
    """Reject an empty collection."""
    if not values:
        raise TaskFailed(f"{label} must not be empty")


def require_text(value: str, label: str) -> None:
    """Reject empty or whitespace-only text identifiers."""
    if not value.strip():
        raise TaskFailed(f"{label} must not be empty")


def require_path(value: str | Path, label: str) -> None:
    """Reject empty path arguments before pathlib turns them into ``.``."""
    if isinstance(value, str):
        if not value.strip():
            raise TaskFailed(f"{label} must not be empty")
        return
    if not str(value).strip():
        raise TaskFailed(f"{label} must not be empty")


def require_regex(value: str, label: str) -> None:
    """Reject an empty regular expression."""
    if value == "":
        raise TaskFailed(f"{label} must not be empty")
