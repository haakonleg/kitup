"""Shared helpers for importing filesystem-based task and variable modules."""

import importlib.util
import itertools
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

from .. import PACKAGE_NAME

_counter = itertools.count()
_task_modules: dict[Path, ModuleType] = {}
_packages: dict[tuple[str, Path], str] = {}
_pycache_disabled = False


def default_pycache_prefix() -> Path:
    """Return the default bytecode cache directory for project code.

    Follows the XDG base directory specification: ``$XDG_CACHE_HOME`` is used
    when it holds an absolute path, otherwise ``~/.cache``. Falls back to the
    system temporary directory when the home directory cannot be determined.
    """
    base = os.environ.get("XDG_CACHE_HOME", "")
    if base and Path(base).is_absolute():
        return Path(base) / PACKAGE_NAME / "pycache"
    try:
        return Path.home() / ".cache" / PACKAGE_NAME / "pycache"
    except RuntimeError:
        return Path(tempfile.gettempdir()) / PACKAGE_NAME / "pycache"


def configure_pycache(*, disabled: bool = False) -> None:
    """Enable or disable bytecode caching for project code.

    The cache location comes from :func:`default_pycache_prefix`; ``disabled``
    prevents writing any cache at all.
    """
    global _pycache_disabled
    _pycache_disabled = disabled


@contextmanager
def project_bytecode_cache() -> Iterator[None]:
    """Redirect or disable bytecode caching while project code is imported.

    Project modules are imported both by name and by path, so every route wraps
    its imports in this context. Previous ``sys`` settings are restored
    afterwards, which keeps nesting safe.
    """
    previous_prefix = sys.pycache_prefix
    previous_disabled = sys.dont_write_bytecode
    if _pycache_disabled:
        sys.dont_write_bytecode = True
    else:
        sys.pycache_prefix = str(default_pycache_prefix())
    try:
        yield
    finally:
        sys.pycache_prefix = previous_prefix
        sys.dont_write_bytecode = previous_disabled


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
        with project_bytecode_cache():
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
