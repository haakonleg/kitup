"""Load ``config.py`` and an optional override file into a namespace."""

from __future__ import annotations

import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ._importer import load_module
from .errors import ConfigError


def load_config(config_dir: Path, override: Path | None = None) -> SimpleNamespace:
    """Load the base config and merge an optional override over it.

    Merge semantics are intentionally shallow: override fields replace base
    fields wholesale, base-only fields remain, and new fields are added.
    """
    values: dict[str, Any] = {}
    base = config_dir / "config.py"
    if base.is_file():
        values.update(_import_public_fields(base))

    if override is not None:
        if not override.is_file():
            raise ConfigError(f"config override not found: {override}")
        values.update(_import_public_fields(override))

    return SimpleNamespace(**values)


def _import_public_fields(path: Path) -> dict[str, Any]:
    """Return public top-level names, excluding modules, functions, and classes.

    Imported values are not inspected; the filter only removes obvious
    code/import objects from the resulting configuration namespace.
    """
    try:
        module = load_module(path, prefix="config")
    except Exception as exc:
        raise ConfigError(f"error while importing {path}: {exc}") from exc

    ignored = (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type)
    return {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("_") and not isinstance(value, ignored)
    }
