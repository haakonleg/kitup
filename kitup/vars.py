"""Merge ordered variable files into a namespace."""

import types
from collections.abc import Collection, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

from ._utils.importer import load_var_module
from ._utils.mapping import wrap_attr
from ._utils.paths import expand_path
from .errors import VarsError
from .registry import Bundle

_RESERVED_NAMES: Final = frozenset({"get", "require"})


class Vars(SimpleNamespace):
    """Merged variable values with attribute and lookup access."""

    def __init__(self, **values: Any) -> None:
        _reject_reserved_names(values, source=None)
        super().__init__(**{name: wrap_attr(value) for name, value in values.items()})

    def get(self, key: str, default: Any = None) -> Any:
        """Return a variable value, or ``default`` when it is missing."""
        return getattr(self, key, default)

    def require(self, key: str) -> Any:
        """Return a variable value, raising when it is missing."""
        if not isinstance(key, str) or not key.strip():
            raise VarsError("var key must be a non-empty string")
        if not hasattr(self, key):
            available = ", ".join(sorted(vars(self))) or "none"
            raise VarsError(f"missing var value: {key} (available: {available})")
        return getattr(self, key)


def load_vars(paths: Sequence[Path]) -> Vars:
    """Merge var files left-to-right into one :class:`Vars`.

    Later files replace same-named fields; fields from earlier files remain
    when not replaced.
    """
    if isinstance(paths, (str, bytes, Path)):
        raise VarsError("var sources must be a sequence of Path objects, not a single source")
    try:
        entries = tuple(paths)
    except TypeError as exc:
        raise VarsError("var sources must be a sequence of Path objects") from exc

    values: dict[str, Any] = {}
    for source in entries:
        if not isinstance(source, Path):
            raise VarsError(f"var sources must be Path objects, not {type(source).__name__}")
        path = expand_path(source, error=VarsError)
        if not path.is_file():
            raise VarsError(f"var not found: {path}")
        values.update(_import_public_fields(path))

    return Vars(**values)


def load_bundle_vars(values: Vars, bundles: Sequence[Bundle]) -> dict[Bundle, Vars]:
    """Load each bundle's vars with project values layered on top.

    Bundle values act as defaults: a project value replaces a same-named bundle
    value. Bundles without var files are omitted.
    """
    project_values = vars(values)
    scoped: dict[Bundle, Vars] = {}
    for bundle in bundles:
        if not bundle.var_paths:
            continue
        merged = {**vars(load_vars(bundle.var_paths)), **project_values}
        scoped[bundle] = Vars(**merged)
    return scoped


def filter_var_paths(
    paths: Sequence[Path],
    *,
    include: Collection[str] | None = None,
    exclude: Collection[str] | None = None,
) -> tuple[Path, ...]:
    """Return var files restricted to the selected var ids."""
    _reject_duplicate_ids(paths)
    if include is None and not exclude:
        return tuple(paths)

    by_id = {path.stem: path for path in paths}
    included = frozenset(include or ())
    excluded = frozenset(exclude or ())

    unknown = sorted((included | excluded) - set(by_id))
    if unknown:
        available = ", ".join(sorted(by_id)) or "none"
        raise VarsError(f"unknown var: {', '.join(unknown)} (available: {available})")

    overlap = included & excluded
    if overlap:
        raise VarsError(f"var selected and excluded: {', '.join(sorted(overlap))}")

    keep = set(by_id) if include is None else set(included)
    keep -= excluded
    return tuple(path for path in paths if path.stem in keep)


def _reject_duplicate_ids(paths: Sequence[Path]) -> None:
    seen: dict[str, Path] = {}
    for path in paths:
        existing = seen.get(path.stem)
        if existing is not None:
            raise VarsError(f"duplicate var id {path.stem!r}: {existing} and {path}")
        seen[path.stem] = path


def _import_public_fields(path: Path) -> dict[str, Any]:
    """Return public top-level names from a var file."""
    try:
        module = load_var_module(path)
    except Exception as exc:
        raise VarsError(f"error while importing {path}: {exc}") from exc
    fields = _public_fields(module)
    _reject_reserved_names(fields, source=str(path))
    return fields


def _reject_reserved_names(names: Collection[str], *, source: str | None) -> None:
    reserved = sorted(_RESERVED_NAMES.intersection(names))
    if not reserved:
        return
    location = f"var {source}" if source else "var"
    rendered = ", ".join(repr(name) for name in reserved)
    raise VarsError(f"{location} defines reserved name(s): {rendered}")


def _public_fields(module: types.ModuleType) -> dict[str, Any]:
    return {name: value for name, value in vars(module).items() if _public(name, value)}


def _public(name: str, value: Any) -> bool:
    if name.startswith("_"):
        return False
    if type(value).__module__ == "typing":
        return False
    ignored = (
        types.ModuleType,
        types.FunctionType,
        types.BuiltinFunctionType,
        type,
        Bundle,
    )
    return not isinstance(value, ignored)
