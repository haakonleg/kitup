"""Shared helper for importing profile and configuration files."""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path
from types import ModuleType

from . import PACKAGE_NAME

_counter = itertools.count()


def load_module(path: Path, *, prefix: str) -> ModuleType:
    """Import a Python file under a unique private module name."""
    module_name = f"_{PACKAGE_NAME}_{prefix}_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        _purge(module_name)
        raise

    _purge(module_name)
    return module


def _purge(module_name: str) -> None:
    prefix = f"{module_name}."
    for name in tuple(sys.modules):
        if name == module_name or name.startswith(prefix):
            sys.modules.pop(name, None)
