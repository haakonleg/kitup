"""Merge ordered configuration sources into a namespace."""

from __future__ import annotations

import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ._utils.importer import load_module
from .errors import ConfigError
from .registry import Bundle

type ConfigSource = Path | Mapping[str, Any] | types.ModuleType


def load_config(sources: Sequence[ConfigSource]) -> SimpleNamespace:
    """Merge config sources left-to-right into one namespace.

    Mapping and module sources contribute public fields. Path sources are
    imported as Python modules. Later sources replace same-named fields; fields
    from earlier sources remain when not replaced.
    """
    if isinstance(sources, (str, bytes, Path, Mapping, types.ModuleType)):
        raise ConfigError("config sources must be a sequence, not a single source")

    values: dict[str, Any] = {}
    for source in sources:
        if isinstance(source, Mapping):
            values.update(_mapping_fields(source))
        elif isinstance(source, types.ModuleType):
            values.update(_public_fields(source))
        elif isinstance(source, Path):
            path = source.expanduser()
            if not path.is_file():
                raise ConfigError(f"config not found: {path}")
            values.update(_import_public_fields(path))
        else:
            raise ConfigError(
                "config sources must be Path objects, modules, or mappings, "
                f"not {type(source).__name__}"
            )

    return SimpleNamespace(**values)


def _import_public_fields(path: Path) -> dict[str, Any]:
    """Return public top-level names from a config file."""
    try:
        module = load_module(path, prefix="config")
    except Exception as exc:
        raise ConfigError(f"error while importing {path}: {exc}") from exc
    return _public_fields(module)


def _public_fields(module: types.ModuleType) -> dict[str, Any]:
    return {
        name: value
        for name, value in vars(module).items()
        if _public(name, value)
    }


def _mapping_fields(source: Mapping[Any, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, value in source.items():
        if error := mapping_key_error(name):
            raise ConfigError(error)
        if _public(name, value):
            fields[name] = value
    return fields


def mapping_key_error(key: object) -> str | None:
    """Return a validation error for a config mapping key, if any."""
    if not isinstance(key, str):
        return f"config mapping keys must be strings, not {type(key).__name__}"
    if key.startswith("_"):
        return f"config mapping key must not be private: {key}"
    return None


def _public(name: str, value: Any) -> bool:
    if name.startswith("_"):
        return False
    ignored = (
        types.ModuleType,
        types.FunctionType,
        types.BuiltinFunctionType,
        type,
        Bundle,
    )
    return not isinstance(value, ignored)
