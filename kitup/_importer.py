"""Shared helper for importing kitup configuration and bundle modules."""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path
from types import ModuleType

_counter = itertools.count()


def load_module(
    path: Path,
    *,
    prefix: str,
    package_dir: Path | None = None,
    keep: bool = False,
) -> ModuleType:
    """Import a Python file under a unique private module name.

    ``package_dir`` marks the file as a package ``__init__.py`` so relative
    imports work. ``keep`` retains the module in ``sys.modules`` after a
    successful import; failed imports are always cleaned up.
    """
    module_name = f"_kitup_{prefix}_{next(_counter)}"
    if package_dir is None:
        spec = importlib.util.spec_from_file_location(module_name, path)
    else:
        spec = importlib.util.spec_from_file_location(
            module_name,
            path,
            submodule_search_locations=[str(package_dir)],
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

    if not keep:
        _purge(module_name)
    return module


def _purge(module_name: str) -> None:
    prefix = f"{module_name}."
    for name in tuple(sys.modules):
        if name == module_name or name.startswith(prefix):
            sys.modules.pop(name, None)
