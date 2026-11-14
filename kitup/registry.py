"""Task and bundle registration objects used by bundle authors."""

import importlib
import inspect
import keyword
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from itertools import count
from pathlib import Path
from types import ModuleType
from typing import Any, Final, overload, override

from ._utils.importer import load_task_module
from ._utils.paths import expand_path
from ._utils.validation import split_tags
from .errors import BundleError

_SPECS_ATTR: Final = "__kitup_task_specs__"
_MODULE_ATTR: Final = "__kitup_module__"
_MODULES_SEQUENCE: Final = "bundle modules must be a sequence of task modules or task module paths"
_ORDER: Final = count()

type TaskFunc = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Immutable declaration of a task.

    Specs are attached to task functions at decoration time and copied into a
    bundle-owned :class:`Task` when a bundle adopts the declaring module.
    """

    func: TaskFunc
    name: str
    tags: frozenset[str]
    description: str
    declared_in: str | None
    order: int


@dataclass(frozen=True, slots=True)
class Task:
    """A task owned by a bundle."""

    spec: TaskSpec
    bundle: Bundle
    bundle_path: Path | None = None

    @property
    def func(self) -> TaskFunc:
        """The underlying task function."""
        return self.spec.func

    @property
    def name(self) -> str:
        """Human-readable task name."""
        return self.spec.name

    @property
    def tags(self) -> frozenset[str]:
        """Tags used for selection."""
        return self.spec.tags

    @property
    def description(self) -> str:
        """Human-readable description."""
        return self.spec.description

    @property
    def id(self) -> str:
        """Stable identifier, e.g. ``base.install_base_packages``."""
        return f"{self.bundle.id}.{self.func.__name__}"


@dataclass(frozen=True, slots=True)
class SideEffect:
    """A side effect that a task can register."""

    name: str
    func: TaskFunc
    one_shot: bool = True
    description: str = ""


@dataclass(frozen=True, slots=True)
class Module:
    """A task module registered with :func:`module`.

    ``name`` is the action prefix, for example ``"mytool"``. ``module_name`` is
    the dotted Python module that declared it, for example ``"modules.mytool"``.
    ``description`` is the optional human-readable summary shown by
    ``kitup list-modules``.
    """

    name: str
    module_name: str
    description: str = ""


# Registered modules keyed by the dotted Python module that declared them.
# ``module()`` records an entry at import time; ``ctx.changed`` consults the
# registry to infer ``"<module>.<method>"`` and the CLI lists its contents.
_MODULES: dict[str, Module] = {}


class Bundle:
    """A bundle and the tasks it owns.

    A bundle is identified by its ``id``, which discovery sets from the bundle
    folder name. Use :func:`bundle` from a package's ``__init__.py`` to create
    and register one; the optional ``name`` is the display name and defaults to
    the id during discovery.

    A directly constructed ``Bundle`` does not auto-discover task modules; pass
    ``modules`` to adopt them. :func:`bundle` auto-discovers sibling ``*.py``
    task modules when ``modules`` is omitted inside a bundle package; pass an
    explicit sequence to control order, or ``modules=[]`` to disable discovery.
    Entries may be module objects or filesystem paths; string and ``Path``
    values are always treated as paths, never import names. ``var_paths`` is
    populated by project discovery with the bundle's optional ``vars/*.py``
    files.
    """

    def __init__(
        self,
        name: str | None = None,
        description: str = "",
        *,
        modules: Sequence[ModuleType | str | Path] | None = None,
        depends_on: Sequence[str | Bundle] = (),
        path: str | Path | None = None,
    ) -> None:
        if name is not None:
            if not isinstance(name, str):
                raise BundleError("bundle name must be a string")
            if not name.strip():
                raise BundleError("bundle name must not be empty")
            name = name.strip()
        if not isinstance(description, str):
            raise BundleError("bundle description must be a string")

        self._frozen = False
        self._tasks: list[Task] = []
        self._task_ids: set[str] = set()
        self.id = name or ""
        self.name = name or ""
        self.description = description
        self.path = expand_path(Path(path), error=BundleError, resolve=True) if path is not None else None
        self.var_paths: tuple[Path, ...] = ()
        self.depends_on = self._normalize_dependencies(depends_on)

        self._adopt_modules(self._normalize_modules(modules))

    @override
    def __repr__(self) -> str:
        return f"Bundle({self.name!r}, tasks={len(self.tasks)})"

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"bundle {self.id!r} is immutable after discovery")
        super().__setattr__(name, value)

    @cached_property
    def tasks(self) -> tuple[Task, ...]:
        """Tasks owned by this bundle, in declaration order."""
        return tuple(self._tasks)

    def _finalize(self, *, id: str, name: str, var_paths: tuple[Path, ...]) -> None:
        """Apply discovery-owned metadata once and freeze the bundle."""
        if self._frozen:
            raise BundleError(f"bundle {id!r} is already finalized")
        self.id = id
        self.name = name
        self.var_paths = var_paths
        self._frozen = True

    def _normalize_modules(self, modules: Sequence[ModuleType | str | Path] | None) -> tuple[ModuleType, ...]:
        if modules is None:
            return ()
        if isinstance(modules, (ModuleType, str, Path)):
            raise BundleError(f"{_MODULES_SEQUENCE}, not a single value")
        try:
            values = tuple(modules)
        except TypeError as exc:
            raise BundleError(_MODULES_SEQUENCE) from exc

        normalized: list[ModuleType] = []
        seen_modules: set[ModuleType] = set()
        seen_paths: set[Path] = set()
        for value in values:
            if isinstance(value, ModuleType):
                if value in seen_modules:
                    raise BundleError(f"duplicate task module: {value.__name__}")
                seen_modules.add(value)
                normalized.append(value)
                continue
            if isinstance(value, (str, Path)):
                path = self._resolve_module_path(value)
                if path in seen_paths:
                    raise BundleError(f"duplicate task module path: {path}")
                seen_paths.add(path)
                try:
                    module = load_task_module(path)
                except Exception as exc:
                    raise BundleError(f"failed to import task module {path}: {exc}") from exc
                normalized.append(module)
                continue
            raise BundleError(
                f"bundle modules must contain task modules or task module paths, not {type(value).__name__}"
            )
        return tuple(normalized)

    def _resolve_module_path(self, value: str | Path) -> Path:
        path = expand_path(value, error=BundleError)
        if not path.is_absolute():
            if self.path is None:
                raise BundleError(f"relative task module path requires a bundle path: {value}")
            path = self.path / path
        path = path.resolve()
        if not path.exists():
            raise BundleError(f"task module not found: {path}")
        if not path.is_file():
            raise BundleError(f"task module is not a file: {path}")
        if path.suffix != ".py":
            raise BundleError(f"task module must end in .py: {path}")
        if path.name == "__init__.py":
            raise BundleError(f"task module must not be __init__.py: {path}")
        return path

    def _adopt_modules(self, modules: tuple[ModuleType, ...]) -> None:
        for module in modules:
            specs = _module_task_specs(module)
            if not specs:
                raise BundleError(
                    f"task module {module.__name__!r} does not register any tasks; "
                    "rename it to a _private helper or list task modules explicitly"
                )
            for spec in specs:
                self._add_task(spec)

    def _add_task(self, spec: TaskSpec) -> None:
        if self._frozen:
            raise BundleError(f"bundle {self.id!r} is immutable after discovery")
        task = Task(spec=spec, bundle=self, bundle_path=self.path)
        task_id = task.id
        if task_id in self._task_ids:
            raise BundleError(f"duplicate task id {task_id!r}")
        self._tasks.append(task)
        self._task_ids.add(task_id)
        self.__dict__.pop("tasks", None)

    def _normalize_dependencies(self, depends_on: Sequence[str | Bundle]) -> tuple[str | Bundle, ...]:
        if isinstance(depends_on, (str, Bundle)):
            raise BundleError("bundle depends_on must be a sequence of bundle ids or Bundle objects")
        try:
            values = tuple(depends_on)
        except TypeError as exc:
            raise BundleError("bundle depends_on must be a sequence of bundle ids or Bundle objects") from exc

        normalized: list[str | Bundle] = []
        for value in values:
            if isinstance(value, Bundle):
                normalized.append(value)
            elif isinstance(value, str):
                if not value.strip():
                    raise BundleError("bundle dependency id must not be empty")
                normalized.append(value.strip())
            else:
                raise BundleError(
                    f"bundle depends_on must contain bundle ids or Bundle objects, not {type(value).__name__}"
                )
        return tuple(normalized)

    @overload
    def task(self, name: TaskFunc, *, tags: Iterable[str] = (), description: str | None = None) -> TaskFunc: ...

    @overload
    def task(
        self,
        name: str | None = None,
        *,
        tags: Iterable[str] = (),
        description: str | None = None,
    ) -> Callable[[TaskFunc], TaskFunc]: ...

    def task(
        self,
        name: str | TaskFunc | None = None,
        *,
        tags: Iterable[str] = (),
        description: str | None = None,
    ) -> TaskFunc | Callable[[TaskFunc], TaskFunc]:
        """Register a task and return the original function.

        Use as a bare decorator, with a display name, or with only options.
        The default name is humanized from the function name and the default
        description is its docstring.
        """
        tag_values = _normalize_tags(tags)
        if callable(name) and not isinstance(name, str):
            return self._register_task(name, None, tag_values, description)
        if name is not None:
            _validate_name(name, "task name")
        return lambda func: self._register_task(func, name, tag_values, description)

    def _register_task(
        self,
        func: TaskFunc,
        name: str | None,
        tags: tuple[str, ...],
        description: str | None,
    ) -> TaskFunc:
        self._add_task(_make_task_spec(func, name, tags, description, declared_in=None))
        return func

    def resource_path(self, *parts: str) -> Path:
        """Resolve a file below the bundle folder."""
        if self.path is None:
            raise BundleError(f"bundle {self.name!r} has no resource path")
        return self.path.joinpath(*parts)

    def read_resource(self, *parts: str) -> str:
        """Read a UTF-8 resource file below the bundle folder."""
        return self.resource_path(*parts).read_text(encoding="utf-8")


def bundle(
    name: str | None = None,
    description: str = "",
    *,
    modules: Sequence[ModuleType | str | Path] | None = None,
    depends_on: Sequence[str | Bundle] = (),
    path: str | Path | None = None,
) -> Bundle:
    """Create a bundle and register it on the calling package.

    Call this from a bundle package's ``__init__.py``. The return value may be
    assigned but does not need to be: when the caller is a package
    ``__init__.py``, the new bundle is attached to it as ``__kitup_bundle__``
    for discovery. Outside a package it simply constructs a bundle.

    When ``modules`` is omitted inside a package, sibling ``*.py`` task modules
    are discovered automatically; elsewhere the bundle starts with no tasks.
    """
    caller = sys._getframe(1).f_globals
    caller_file = caller.get("__file__")
    is_package = bool(caller_file and Path(caller_file).name == "__init__.py")

    if path is None:
        path = _caller_dir(caller_file)
    if modules is None and is_package:
        package = caller.get("__package__") or caller.get("__name__")
        if not package:
            raise BundleError("cannot auto-discover task modules outside a package")
        modules = discover_task_modules(package, Path(str(caller_file)).parent)

    created = Bundle(name, description, modules=modules, depends_on=depends_on, path=path)
    if is_package:
        caller["__kitup_bundle__"] = created
    return created


def module(name: str, description: str = "") -> Module:
    """Register the calling Python module as a kitup task module.

    Call this once at the top of a module file, or in a package
    ``__init__.py`` to cover every file in that package. Public functions that
    record calls with ``ctx.changed(...)`` then get actions of the form
    ``"<name>.<function>"`` without passing ``action`` explicitly.

    ``name`` must be a valid, non-keyword Python identifier because it becomes
    the first segment of every action id. ``description`` is an optional
    human-readable summary shown by ``kitup list-modules``.
    """
    caller = sys._getframe(1).f_globals
    module_name = caller.get("__name__")
    if not module_name:
        raise BundleError("module() must be called from a Python module")
    if not isinstance(description, str):
        raise BundleError("module description must be a string")
    registered = Module(
        name=_validate_module_name(name),
        module_name=module_name,
        description=description,
    )
    _MODULES[module_name] = registered
    caller[_MODULE_ATTR] = registered
    return registered


def module_of(value: ModuleType) -> Module | None:
    """Return the module registered on a Python module, if any."""
    registered = getattr(value, _MODULE_ATTR, None)
    return registered if isinstance(registered, Module) else None


def registered_modules(prefix: str | None = None) -> tuple[Module, ...]:
    """Return registered modules sorted by action prefix.

    Pass ``prefix`` to keep only modules declared by that dotted module or its
    submodules.
    """
    entries = [
        entry
        for entry in _MODULES.values()
        if prefix is None or entry.module_name == prefix or entry.module_name.startswith(f"{prefix}.")
    ]
    return tuple(sorted(entries, key=lambda entry: entry.name))


def action_prefix(module_name: str) -> str | None:
    """Return the registered action prefix for a dotted module name, if any.

    Submodules inherit the prefix of a registered package, so
    ``modules.webserver.nginx`` resolves through ``modules.webserver``.
    """
    name = module_name
    while name:
        registered = _MODULES.get(name)
        if registered is not None:
            return registered.name
        name = name.rpartition(".")[0]
    return None


def forget_module_prefix(prefix: str) -> None:
    """Drop registered modules under a dotted module name."""
    for module_name in tuple(_MODULES):
        if module_name == prefix or module_name.startswith(f"{prefix}."):
            del _MODULES[module_name]


def discover_task_modules(package: str, folder: Path) -> tuple[ModuleType, ...]:
    """Import a bundle package's public sibling task modules in filename order."""
    modules: list[ModuleType] = []
    for path in sorted(folder.glob("*.py"), key=lambda item: item.name):
        stem = path.stem
        if stem == "__init__" or stem.startswith("_"):
            continue
        module_name = f"{package}.{stem}"
        try:
            modules.append(importlib.import_module(module_name))
        except Exception as exc:
            raise BundleError(f"failed to import task module {module_name!r}: {exc}") from exc
    return tuple(modules)


def _module_task_specs(module: ModuleType) -> tuple[TaskSpec, ...]:
    """Return the task declarations made in ``module``, in declaration order."""
    found: dict[int, TaskSpec] = {}
    for value in vars(module).values():
        for spec in getattr(value, _SPECS_ATTR, ()):
            if isinstance(spec, TaskSpec) and spec.declared_in == module.__name__:
                found[spec.order] = spec
    return tuple(found[order] for order in sorted(found))


def bundle_of(value: ModuleType | Bundle) -> Bundle:
    """Return a module's bundle."""
    if isinstance(value, Bundle):
        return value
    if isinstance(value, ModuleType):
        bundle = getattr(value, "__kitup_bundle__", None)
        if bundle is None:
            raise BundleError(
                f"module {value.__name__!r} does not define a bundle; construct Bundle(...) in its __init__.py"
            )
        if not isinstance(bundle, Bundle):
            raise BundleError(f"module {value.__name__!r} has a non-Bundle __kitup_bundle__")
        return bundle
    raise BundleError(f"expected a bundle module or Bundle object, not {type(value).__name__}")


@overload
def task(name: TaskFunc, *, tags: Iterable[str] = (), description: str | None = None) -> TaskFunc: ...


@overload
def task(
    name: str | None = None,
    *,
    tags: Iterable[str] = (),
    description: str | None = None,
) -> Callable[[TaskFunc], TaskFunc]: ...


def task(
    name: str | TaskFunc | None = None,
    *,
    tags: Iterable[str] = (),
    description: str | None = None,
) -> TaskFunc | Callable[[TaskFunc], TaskFunc]:
    """Declare a task for the module's bundle.

    The task is recorded on the calling module and registered when the module
    is discovered or listed in a bundle's ``modules`` argument.
    """
    tag_values = _normalize_tags(tags)
    caller = sys._getframe(1).f_globals
    declared_in = caller.get("__name__")

    def register(func: TaskFunc, task_name: str | None) -> TaskFunc:
        spec = _make_task_spec(func, task_name, tag_values, description, declared_in=declared_in)
        existing = getattr(func, _SPECS_ATTR, ())
        setattr(func, _SPECS_ATTR, (*existing, spec))
        return func

    if callable(name) and not isinstance(name, str):
        return register(name, None)
    if name is not None:
        _validate_name(name, "task name")
    return lambda func: register(func, name)


@overload
def side_effect(name: TaskFunc, *, one_shot: bool = True, description: str | None = None) -> SideEffect: ...


@overload
def side_effect(
    name: str | None = None,
    *,
    one_shot: bool = True,
    description: str | None = None,
) -> Callable[[TaskFunc], SideEffect]: ...


def side_effect(
    name: str | TaskFunc | None = None,
    *,
    one_shot: bool = True,
    description: str | None = None,
) -> SideEffect | Callable[[TaskFunc], SideEffect]:
    """Declare a side effect that tasks can register."""
    if not isinstance(one_shot, bool):
        raise BundleError("side effect one_shot must be a boolean")

    def decorate(func: TaskFunc, effect_name: str | None) -> SideEffect:
        return SideEffect(
            name=_resolve_side_effect_name(effect_name, func),
            func=func,
            one_shot=one_shot,
            description=_resolve_description(description, func),
        )

    if callable(name) and not isinstance(name, str):
        return decorate(name, None)
    if name is not None:
        _validate_name(name, "side effect name")
    return lambda func: decorate(func, name)


def _caller_dir(caller_file: str | None) -> Path | None:
    if not caller_file:
        return None
    return expand_path(caller_file, error=BundleError, resolve=True).parent


def _validate_name(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise BundleError(f"{label} must be a string")
    if not value.strip():
        raise BundleError(f"{label} must not be empty")
    return value.strip()


def _require_task_function(func: TaskFunc) -> None:
    if not inspect.isfunction(func):
        raise BundleError(f"task decorator requires a Python function, not {type(func).__name__}")


def _normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Return validated tags, splitting comma-separated entries and stripping whitespace."""
    if isinstance(tags, str):
        raise BundleError("task tags must be a sequence of strings, not a single string")
    try:
        values = tuple(tags)
    except TypeError as exc:
        raise BundleError("task tags must be a sequence of strings") from exc

    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise BundleError(f"task tags must contain strings, not {type(value).__name__}")
        try:
            normalized.extend(split_tags([value], allow_empty=False))
        except ValueError as exc:
            raise BundleError("task tags must not contain empty tags") from exc
    return tuple(normalized)


def _validate_module_name(name: object) -> str:
    value = _validate_name(name, "module name")
    if not value.isidentifier() or keyword.iskeyword(value):
        raise BundleError(f"module name must be a valid, non-keyword Python identifier: {value!r}")
    return value


def _humanize_name(func: TaskFunc) -> str:
    name = " ".join(part for part in func.__name__.split("_") if part)
    if not name:
        return func.__name__
    return name[:1].upper() + name[1:]


def _resolve_task_name(name: str | None, func: TaskFunc) -> str:
    if name is None:
        return _humanize_name(func)
    return _validate_name(name, "task name")


def _resolve_side_effect_name(name: str | None, func: TaskFunc) -> str:
    if name is None:
        return _humanize_name(func)
    return _validate_name(name, "side effect name")


def _resolve_description(description: str | None, func: TaskFunc) -> str:
    if description is None:
        return inspect.getdoc(func) or ""
    if not isinstance(description, str):
        raise BundleError("description must be a string")
    return description


def _make_task_spec(
    func: TaskFunc,
    name: str | None,
    tags: tuple[str, ...],
    description: str | None,
    *,
    declared_in: str | None,
) -> TaskSpec:
    _require_task_function(func)
    return TaskSpec(
        func=func,
        name=_resolve_task_name(name, func),
        tags=frozenset(tags),
        description=_resolve_description(description, func),
        declared_in=declared_in,
        order=next(_ORDER),
    )
