"""Import-based run profiles and their composition helpers."""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from ._importer import load_module
from .config import ConfigSource, mapping_key_error
from .errors import ProfileError
from .registry import Bundle, bundle_of
from .validation import split_tags


@dataclass(frozen=True)
class RunDefinition:
    """An immutable description of the bundles and config for one run."""

    source: Path
    bundles: tuple[Bundle, ...]
    config: tuple[ConfigSource, ...]
    description: str = ""
    tags: frozenset[str] = frozenset()
    skip_tags: frozenset[str] = frozenset()

    def extend(
        self,
        *,
        bundles: Sequence[ModuleType | Bundle] = (),
        config: Sequence[ConfigSource] = (),
        description: str | None = None,
        tags: Iterable[str] = (),
        skip_tags: Iterable[str] = (),
    ) -> RunDefinition:
        """Return a new definition with additions appended and register it."""
        caller = sys._getframe(1).f_globals
        return _extend_definition(
            self,
            bundles=bundles,
            config=config,
            description=description,
            tags=tags,
            skip_tags=skip_tags,
            caller=caller,
        )

    def replace(
        self,
        *,
        bundles: Sequence[ModuleType | Bundle] | None = None,
        config: Sequence[ConfigSource] | None = None,
        description: str | None = None,
        tags: Iterable[str] | None = None,
        skip_tags: Iterable[str] | None = None,
    ) -> RunDefinition:
        """Return a new definition with supplied fields replaced and register it."""
        caller = sys._getframe(1).f_globals
        source = _caller_file(caller)

        if bundles is None:
            bundle_values = self.bundles
        else:
            bundle_values = _normalize_bundles(bundles)
        if not bundle_values:
            raise ProfileError("profile option 'bundles' must not be empty")

        if config is None:
            config_values = self.config
        else:
            config_values = _normalize_config(config, source.parent)

        return _build_definition(
            caller=caller,
            source=source,
            bundles=bundle_values,
            config=config_values,
            description=self.description if description is None else _text(description),
            tags=self.tags if tags is None else _normalize_tags(tags, "tags"),
            skip_tags=(
                self.skip_tags
                if skip_tags is None
                else _normalize_tags(skip_tags, "skip_tags")
            ),
        )


class ProfileAPI:
    """Callable profile factory with import-based composition helpers."""

    def __call__(
        self,
        *,
        bundles: Sequence[ModuleType | Bundle],
        config: Sequence[ConfigSource] = (),
        description: str = "",
        tags: Iterable[str] = (),
        skip_tags: Iterable[str] = (),
    ) -> RunDefinition:
        caller = sys._getframe(1).f_globals
        source = _caller_file(caller)

        bundle_values = _normalize_bundles(bundles)
        if not bundle_values:
            raise ProfileError("profile option 'bundles' must not be empty")
        config_values = _normalize_config(config, source.parent)

        return _build_definition(
            caller=caller,
            source=source,
            bundles=bundle_values,
            config=config_values,
            description=_text(description),
            tags=_normalize_tags(tags, "tags"),
            skip_tags=_normalize_tags(skip_tags, "skip_tags"),
        )

    def extend(
        self,
        source: ModuleType | RunDefinition,
        *,
        bundles: Sequence[ModuleType | Bundle] = (),
        config: Sequence[ConfigSource] = (),
        description: str | None = None,
        tags: Iterable[str] = (),
        skip_tags: Iterable[str] = (),
    ) -> RunDefinition:
        """Extend an imported profile or an existing run definition."""
        caller = sys._getframe(1).f_globals
        base = _as_definition(source)
        return _extend_definition(
            base,
            bundles=bundles,
            config=config,
            description=description,
            tags=tags,
            skip_tags=skip_tags,
            caller=caller,
        )

    def of(self, module: ModuleType) -> RunDefinition:
        """Return a module's run definition without registering anything."""
        if not isinstance(module, ModuleType):
            raise ProfileError(
                "profile.of() expects a module, "
                f"not {type(module).__name__}"
            )
        return _as_definition(module)

    def use(self, source: ModuleType | RunDefinition) -> RunDefinition:
        """Register an existing definition in the calling module."""
        caller = sys._getframe(1).f_globals
        definition = _as_definition(source)
        _attach(caller, definition)
        return definition


profile = ProfileAPI()


def load_profile(path: Path) -> RunDefinition:
    """Import a profile file and return its registered run definition.

    The caller is responsible for placing the profile directory on
    ``sys.path`` while the profile and its run are loaded.
    """
    profile_path = Path(path).expanduser().resolve()
    if not profile_path.is_file():
        raise ProfileError(
            f"profile not found: {profile_path} "
            "(create it or pass its path to the command)"
        )

    try:
        module = load_module(profile_path, prefix="profile")
    except Exception as exc:
        raise ProfileError(f"failed to load profile '{profile_path}': {exc}") from exc

    definition = getattr(module, "__kitup_run__", None)
    if not isinstance(definition, RunDefinition):
        raise ProfileError(
            f"no run definition found in '{profile_path}'; call profile(...)"
        )
    return definition


def _extend_definition(
    base: RunDefinition,
    *,
    bundles: Sequence[ModuleType | Bundle],
    config: Sequence[ConfigSource],
    description: str | None,
    tags: Iterable[str],
    skip_tags: Iterable[str],
    caller: dict[str, Any],
) -> RunDefinition:
    source = _caller_file(caller)
    additions = _normalize_bundles(bundles)
    bundle_values = _normalize_bundles((*base.bundles, *additions))
    if not bundle_values:
        raise ProfileError("profile option 'bundles' must not be empty")

    config_values: list[ConfigSource] = []
    seen_config_paths: set[Path] = set()
    for entry in (*base.config, *_normalize_config(config, source.parent)):
        if isinstance(entry, Path):
            if entry in seen_config_paths:
                continue
            seen_config_paths.add(entry)
        config_values.append(entry)

    return _build_definition(
        caller=caller,
        source=source,
        bundles=bundle_values,
        config=config_values,
        description=base.description if description is None else _text(description),
        tags=base.tags | _normalize_tags(tags, "tags"),
        skip_tags=base.skip_tags | _normalize_tags(skip_tags, "skip_tags"),
    )


def _as_definition(source: ModuleType | RunDefinition) -> RunDefinition:
    if isinstance(source, RunDefinition):
        return source
    if isinstance(source, ModuleType):
        definition = getattr(source, "__kitup_run__", None)
        if not isinstance(definition, RunDefinition):
            raise ProfileError(f"module {source.__name__!r} has no run definition")
        return definition
    raise ProfileError(
        "expected a profile module or RunDefinition, "
        f"not {type(source).__name__}"
    )


def _normalize_bundles(
    values: Sequence[ModuleType | Bundle],
) -> tuple[Bundle, ...]:
    if isinstance(values, str):
        raise ProfileError(
            "profile option 'bundles' must be a sequence of bundle modules or "
            "Bundle objects, not a single string"
        )
    try:
        entries = tuple(values)
    except TypeError as exc:
        raise ProfileError(
            "profile option 'bundles' must be a sequence of bundle modules or "
            "Bundle objects"
        ) from exc

    bundles: list[Bundle] = []
    by_name: dict[str, Bundle] = {}
    for entry in entries:
        if not isinstance(entry, (ModuleType, Bundle)):
            raise ProfileError(
                "profile option 'bundles' must contain bundle modules or "
                f"Bundle objects, not {type(entry).__name__}"
            )
        bundle = bundle_of(entry)
        existing = by_name.get(bundle.name)
        if existing is not None:
            if existing is not bundle:
                raise ProfileError(
                    f"duplicate bundle name in profile: {bundle.name}"
                )
            continue
        by_name[bundle.name] = bundle
        bundles.append(bundle)
    return tuple(bundles)


def _normalize_config(
    values: Sequence[ConfigSource], caller_dir: Path
) -> tuple[ConfigSource, ...]:
    if isinstance(values, Mapping):
        raise ProfileError(
            "profile option 'config' must be a sequence of config sources, "
            "not a single mapping"
        )
    if isinstance(values, (str, bytes)):
        raise ProfileError(
            "profile option 'config' must be a sequence of Path objects, "
            "modules, or mappings, not a single string"
        )
    try:
        entries = tuple(values)
    except TypeError as exc:
        raise ProfileError("profile option 'config' must be a sequence") from exc

    sources: list[ConfigSource] = []
    seen_paths: set[Path] = set()
    for entry in entries:
        if isinstance(entry, Path):
            path = _resolve_config_path(entry, caller_dir)
            if path in seen_paths:
                raise ProfileError(f"duplicate config path in profile: {path}")
            seen_paths.add(path)
            sources.append(path)
        elif isinstance(entry, str):
            raise ProfileError(
                "config file sources must use pathlib.Path, not str"
            )
        elif isinstance(entry, Mapping):
            _validate_mapping(entry)
            sources.append(entry)
        elif isinstance(entry, ModuleType):
            sources.append(entry)
        else:
            raise ProfileError(
                "config sources must be Path objects, modules, or mappings, "
                f"not {type(entry).__name__}"
            )
    return tuple(sources)


def _resolve_config_path(value: Path, caller_dir: Path) -> Path:
    path = value.expanduser()
    if not path.is_absolute():
        path = caller_dir / path
    return path.resolve()


def _validate_mapping(source: Mapping[Any, Any]) -> None:
    for key in source:
        if error := mapping_key_error(key):
            raise ProfileError(error)


def _normalize_tags(values: Iterable[str], label: str) -> frozenset[str]:
    if isinstance(values, str):
        raise ProfileError(
            f"profile option {label!r} must be a sequence of strings, "
            "not a single string"
        )
    try:
        entries = tuple(values)
    except TypeError as exc:
        raise ProfileError(
            f"profile option {label!r} must be a sequence of strings"
        ) from exc

    for value in entries:
        if not isinstance(value, str):
            raise ProfileError(f"profile option {label!r} contains an invalid tag")
    try:
        return frozenset(split_tags(entries, allow_empty=False))
    except ValueError as exc:
        raise ProfileError(
            f"profile option {label!r} contains an invalid tag"
        ) from exc


def _text(value: str) -> str:
    if not isinstance(value, str):
        raise ProfileError("profile description must be a string")
    return value


def _caller_file(caller: dict[str, Any]) -> Path:
    filename = caller.get("__file__")
    if not filename:
        raise ProfileError("profile code must run from a file")
    return Path(filename).expanduser().resolve()


def _build_definition(
    *,
    caller: dict[str, Any],
    source: Path,
    bundles: Sequence[Bundle],
    config: Sequence[ConfigSource],
    description: str,
    tags: frozenset[str],
    skip_tags: frozenset[str],
) -> RunDefinition:
    definition = RunDefinition(
        source=source,
        bundles=tuple(bundles),
        config=tuple(config),
        description=description,
        tags=tags,
        skip_tags=skip_tags,
    )
    _attach(caller, definition)
    return definition


def _attach(caller: dict[str, Any], definition: RunDefinition) -> None:
    caller["__kitup_run__"] = definition


__all__ = ["ConfigSource", "ProfileAPI", "RunDefinition", "load_profile", "profile"]
