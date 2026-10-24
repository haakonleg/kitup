"""Task and bundle registration objects used by bundle authors."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Task:
    """A single named unit of work inside a bundle."""

    bundle: str
    name: str
    func: Callable[[Any], None]
    needs_sudo: bool = False
    tags: frozenset[str] = frozenset()
    description: str = ""

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
        self.depends_on = tuple(depends_on)
        self.path = path
        self.tasks: list[Task] = []

    def __repr__(self) -> str:
        return f"Bundle({self.name!r}, tasks={len(self.tasks)})"

    def task(
        self,
        name: str | None = None,
        *,
        needs_sudo: bool = False,
        tags: Iterable[str] = (),
        description: str = "",
    ) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
        """Register a task and return the original function.

        ``name`` is the display name. When omitted the Python function name is
        used, which is handy for small bundles.
        """

        def register(func: Callable[[Any], None]) -> Callable[[Any], None]:
            self.tasks.append(
                Task(
                    bundle=self.name,
                    name=name or func.__name__,
                    func=func,
                    needs_sudo=needs_sudo,
                    tags=frozenset(tags),
                    description=description,
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
