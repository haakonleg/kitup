"""Task and bundle registration objects used by bundle authors."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

from .errors import BundleError
from .validation import normalize_string_sequence

_TASKS_ATTR = "__kitup_tasks__"
_CLAIMED_ATTR = "__kitup_bundle_owner__"


@dataclass(frozen=True)
class Task:
    """A named unit of work, or a declaration awaiting its bundle."""

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

    Task modules listed in ``modules`` must be imported before the bundle is
    constructed. Tasks declared with ``@task`` are recorded on their module and
    adopted by the bundle in list order.
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        modules: Sequence[ModuleType] = (),
        depends_on: Sequence[ModuleType | Bundle] = (),
        path: Path | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise BundleError("bundle name must not be empty")

        caller = sys._getframe(1).f_globals
        caller_file = caller.get("__file__")
        is_package = bool(caller_file and Path(caller_file).name == "__init__.py")

        self.name = name.strip()
        self.description = description
        self.path = _resolve_path(path) if path is not None else _caller_dir(caller_file)
        self.tasks: list[Task] = []
        task_modules = self._normalize_modules(modules)
        self.depends_on = self._normalize_dependencies(depends_on)

        if is_package:
            caller["__kitup_bundle__"] = self

        self._adopt_modules(task_modules)

    def __repr__(self) -> str:
        return f"Bundle({self.name!r}, tasks={len(self.tasks)})"

    def _normalize_modules(
        self, modules: Sequence[ModuleType]
    ) -> tuple[ModuleType, ...]:
        if modules is None:
            raise BundleError(
                "bundle modules must be a sequence of task modules, not None"
            )
        if isinstance(modules, ModuleType):
            raise BundleError(
                "bundle modules must be a sequence of task modules, not a single module"
            )
        try:
            values = tuple(modules)
        except TypeError as exc:
            raise BundleError(
                "bundle modules must be a sequence of task modules"
            ) from exc

        normalized: list[ModuleType] = []
        seen: set[ModuleType] = set()
        for value in values:
            if not isinstance(value, ModuleType):
                raise BundleError(
                    "bundle modules must contain task modules, "
                    f"not {type(value).__name__}"
                )
            if value in seen:
                raise BundleError(f"duplicate task module: {value.__name__}")
            seen.add(value)
            normalized.append(value)
        return tuple(normalized)

    def _adopt_modules(self, modules: tuple[ModuleType, ...]) -> None:
        for module in modules:
            specs = vars(module).pop(_TASKS_ATTR, None)
            if not specs:
                owner = vars(module).get(_CLAIMED_ATTR)
                if isinstance(owner, str):
                    raise BundleError(
                        f"task module {module.__name__!r} is already registered "
                        f"by bundle {owner!r}"
                    )
                raise BundleError(
                    f"task module {module.__name__!r} does not register any tasks"
                )
            self.tasks.extend(
                replace(spec, bundle=self, bundle_path=self.path) for spec in specs
            )
            vars(module)[_CLAIMED_ATTR] = self.name

    def _normalize_dependencies(
        self, depends_on: Sequence[ModuleType | Bundle]
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
    """Return a module's bundle, binding its resource path if needed."""
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
    """Declare a task for the module's bundle.

    The task is recorded on the calling module and registered when the module
    is listed in a bundle's ``modules`` argument.
    """
    name = _validate_task_name(name)
    tag_values = normalize_string_sequence(tags, "task tags")
    caller = sys._getframe(1).f_globals

    def register(func: Callable[[Any], None]) -> Callable[[Any], None]:
        specs = caller.setdefault(_TASKS_ATTR, [])
        specs.append(
            Task(
                bundle=None,
                name=name,
                func=func,
                tags=frozenset(tag_values),
                description=description,
            )
        )
        return func

    return register


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
