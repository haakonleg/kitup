"""Shared helpers for importing filesystem-based task and variable modules."""

import importlib.util
import itertools
import sys
from pathlib import Path
from types import ModuleType

from .. import PACKAGE_NAME

_counter = itertools.count()
_task_modules: dict[Path, ModuleType] = {}
_packages: dict[tuple[str, Path], str] = {}


def _task_prefix() -> str:
    return f"_{PACKAGE_NAME}_task_"


def _var_prefix() -> str:
    return f"_{PACKAGE_NAME}_var_"


def load_task_module(path: Path) -> ModuleType:
    """Import a task module by path, cached by resolved path for the run.

    The module is loaded under a private synthetic package so sibling modules
    can be imported with ``from . import helper``. The directory's own
    ``__init__.py`` is never executed.
    """
    resolved = path.resolve()
    cached = _task_modules.get(resolved)
    if cached is not None and sys.modules.get(cached.__name__) is cached:
        return cached

    prefix = _task_prefix()
    package = _synthetic_package(resolved.parent, prefix=prefix)
    module = _load_packaged_module(resolved, package, prefix=prefix)
    _task_modules[resolved] = module
    return module


def load_var_module(path: Path) -> ModuleType:
    """Import a variable file under a unique private module name.

    The module is removed from ``sys.modules`` after import, while its sibling
    helpers stay importable through the private parent package.
    """
    resolved = path.resolve()
    prefix = _var_prefix()
    package = _synthetic_package(resolved.parent, prefix=prefix)
    module = _load_packaged_module(resolved, package, prefix=prefix)
    _purge(module.__name__)
    return module


def load_package_module(path: Path, package_name: str) -> ModuleType:
    """Import a package ``__init__.py`` under an explicit synthetic package name."""
    return _exec_module(package_name, path, search_path=path.parent)


def _synthetic_package(directory: Path, *, prefix: str) -> ModuleType:
    """Return one synthetic package per directory, reusing it within a run."""
    key = (prefix, directory)
    name = _packages.get(key)
    if name is not None:
        existing = sys.modules.get(name)
        if existing is not None:
            return existing

    name = f"{prefix}pkg_{next(_counter)}"
    package = ModuleType(name)
    package.__path__ = [str(directory)]
    package.__package__ = name
    package.__file__ = None
    sys.modules[name] = package
    _packages[key] = name
    return package


def _load_packaged_module(path: Path, package: ModuleType, *, prefix: str) -> ModuleType:
    module_name = f"{package.__name__}.{prefix}module_{next(_counter)}"
    return _exec_module(module_name, path)


def _exec_module(module_name: str, path: Path, *, search_path: Path | None = None) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
        submodule_search_locations=[str(search_path)] if search_path is not None else None,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        _purge(module_name)
        raise
    return module


def _purge(module_name: str) -> None:
    prefix = f"{module_name}."
    for name in tuple(sys.modules):
        if name == module_name or name.startswith(prefix):
            sys.modules.pop(name, None)
