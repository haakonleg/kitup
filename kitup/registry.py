"""Task and bundle registration objects used by bundle authors."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .validation import normalize_string_sequence


@dataclass(frozen=True)
class Task:
    """A single named unit of work inside a bundle."""

    bundle: str
    name: str
    func: Callable[[Any], None]
    tags: frozenset[str] = frozenset()
    description: str = ""
    bundle_path: Path | None = None

    @property
    def id(self) -> str:
        """Stable identifier, e.g. ``base.install_base_packages``."""
        return f"{self.bundle}.{self.func.__name__}"


class Bundle:
    """A discovered bundle and the tasks it owns."""

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        depends_on: Iterable[str] = (),
        path: Path | None = None,
    ) -> None:
        if not name:
            raise ValueError("bundle name must not be empty")
        self.name = name
        self.description = description
        self.depends_on = normalize_string_sequence(depends_on, "bundle depends_on")
        self.path = path
        self.tasks: list[Task] = []

    def __repr__(self) -> str:
        return f"Bundle({self.name!r}, tasks={len(self.tasks)})"

    def set_path(self, path: Path) -> None:
        """Attach the bundle folder and update already-registered tasks."""
        self.path = path
        self.tasks = [replace(task, bundle_path=path) for task in self.tasks]

    def task(
        self,
        name: str | None = None,
        *,
        tags: Iterable[str] = (),
        description: str = "",
    ) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
        """Register a task and return the original function.

        ``name`` is the display name. When omitted the Python function name is
        used, which is handy for small bundles.
        """
        tag_values = normalize_string_sequence(tags, "task tags")

        def register(func: Callable[[Any], None]) -> Callable[[Any], None]:
            self.tasks.append(
                Task(
                    bundle=self.name,
                    name=name or func.__name__,
                    func=func,
                    tags=frozenset(tag_values),
                    description=description,
                    bundle_path=self.path,
                )
            )
            return func

        return register

    def resource_path(self, *parts: str) -> Path:
        """Resolve a file below the bundle folder."""
        if self.path is None:
            raise RuntimeError(f"bundle {self.name!r} has not been loaded from disk")
        return self.path.joinpath(*parts)

    def read_resource(self, *parts: str) -> str:
        """Read a UTF-8 resource file below the bundle folder."""
        return self.resource_path(*parts).read_text(encoding="utf-8")
