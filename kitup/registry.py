"""Task and bundle registration objects used by bundle authors."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

from .errors import BundleError
from .validation import normalize_string_sequence


@dataclass(frozen=True)
class Task:
    """A single named unit of work inside a bundle."""

    bundle: Bundle | None
    name: str
    func: Callable[[Any], None]
    tags: frozenset[str] = frozenset()
    description: str = ""
    bundle_path: Path | None = None

    @property
    def id(self) -> str:
        """Stable identifier, e.g. ``base.install_base_packages``."""
        owner = self.bundle.name if self.bundle is not None else "unknown"
        return f"{owner}.{self.func.__name__}"


class Bundle:
    """A bundle and the tasks it owns.

    A bundle is identified by its ``name``. Constructing one in a package's
    ``__init__.py`` attaches it privately to that module as
    ``__kitup_bundle__``. Elsewhere, pass the object directly to a profile or
    to another bundle's ``depends_on``.
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        depends_on: Iterable[ModuleType | Bundle] = (),
        path: Path | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise BundleError("bundle name must not be empty")

        caller = sys._getframe(1).f_globals
        caller_file = caller.get("__file__")

        self.name = name.strip()
        self.description = description
        self.path = _resolve_path(path) if path is not None else _caller_dir(caller_file)
        self.tasks: list[Task] = []
        self.depends_on = self._normalize_dependencies(depends_on)

        if caller_file and Path(caller_file).name == "__init__.py":
            caller["__kitup_bundle__"] = self

    def __repr__(self) -> str:
        return f"Bundle({self.name!r}, tasks={len(self.tasks)})"

    def _normalize_dependencies(
        self, depends_on: Iterable[ModuleType | Bundle]
    ) -> tuple[Bundle, ...]:
        if isinstance(depends_on, str):
            raise BundleError(
                "bundle depends_on must be a sequence of bundle modules or Bundle "
                "objects, not a single string"
            )
        try:
            values = tuple(depends_on)
        except TypeError as exc:
            raise BundleError(
                "bundle depends_on must be a sequence of bundle modules or Bundle objects"
            ) from exc
        return tuple(bundle_of(value) for value in values)

    def bind(self, module: ModuleType) -> None:
        """Attach the bundle's resource root from its package module."""
        if self.path is not None:
            return
        path = getattr(module, "__file__", None)
        if not path:
            raise BundleError(
                f"module {module.__name__!r} has no file; pass path= to Bundle(...)"
            )
        self.set_path(Path(path).resolve().parent)

    def set_path(self, path: Path) -> None:
        """Attach the bundle resource root and update already-registered tasks."""
        self.path = Path(path).expanduser().resolve()
        self.tasks = [replace(task, bundle_path=self.path) for task in self.tasks]

    def task(
        self,
        name: str,
        *,
        tags: Iterable[str] = (),
        description: str = "",
    ) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
        """Register a task and return the original function.

        ``name`` is the required display name.
        """
        name = _validate_task_name(name)
        tag_values = normalize_string_sequence(tags, "task tags")

        def register(func: Callable[[Any], None]) -> Callable[[Any], None]:
            self.tasks.append(
                Task(
                    bundle=self,
                    name=name,
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
            raise BundleError(f"bundle {self.name!r} has no resource path")
        return self.path.joinpath(*parts)

    def read_resource(self, *parts: str) -> str:
        """Read a UTF-8 resource file below the bundle folder."""
        return self.resource_path(*parts).read_text(encoding="utf-8")


def bundle_of(value: ModuleType | Bundle) -> Bundle:
    """Return a module's bundle, binding its resource path if needed.

    Bundle objects are returned unchanged.
    """
    if isinstance(value, Bundle):
        return value
    if isinstance(value, ModuleType):
        bundle = getattr(value, "__kitup_bundle__", None)
        if bundle is None:
            raise BundleError(
                f"module {value.__name__!r} does not define a bundle; "
                "construct Bundle(...) in its __init__.py"
            )
        if not isinstance(bundle, Bundle):
            raise BundleError(
                f"module {value.__name__!r} has a non-Bundle __kitup_bundle__"
            )
        bundle.bind(value)
        return bundle
    raise BundleError(
        "expected a bundle module or Bundle object, "
        f"not {type(value).__name__}"
    )


def task(
    name: str,
    *,
    tags: Iterable[str] = (),
    description: str = "",
) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
    """Register a task on the bundle of the calling package.

    The bundle must already be constructed in the caller's module or in one of
    its parent packages.
    """
    name = _validate_task_name(name)
    caller = sys._getframe(1).f_globals
    bundle = _bundle_for_module(caller)
    return bundle.task(name, tags=tags, description=description)


def _bundle_for_module(caller: dict[str, Any]) -> Bundle:
    bundle = caller.get("__kitup_bundle__")
    if isinstance(bundle, Bundle):
        return bundle
    if bundle is not None:
        raise BundleError("__kitup_bundle__ is not a Bundle")

    module_name = caller.get("__name__")
    while isinstance(module_name, str) and "." in module_name:
        module_name = module_name.rpartition(".")[0]
        module = sys.modules.get(module_name)
        candidate = getattr(module, "__kitup_bundle__", None) if module else None
        if isinstance(candidate, Bundle):
            return candidate
        if candidate is not None:
            raise BundleError(f"{module_name!r}.__kitup_bundle__ is not a Bundle")

    label = caller.get("__name__", "<unknown module>")
    raise BundleError(
        f"no bundle found for module {label!r}; "
        "construct Bundle(...) before importing task modules"
    )


def _caller_dir(caller_file: str | None) -> Path | None:
    if not caller_file:
        return None
    return Path(caller_file).expanduser().resolve().parent


def _validate_task_name(name: object) -> str:
    if not isinstance(name, str):
        raise BundleError("task name must be a string")
    if not name.strip():
        raise BundleError("task name must not be empty")
    return name


def _resolve_path(path: Path) -> Path:
    return Path(path).expanduser().resolve()
