"""Discover and import bundle packages from a configuration root."""

from __future__ import annotations

import re
from pathlib import Path

from ._importer import load_module
from .errors import DiscoveryError
from .registry import Bundle


def bundles_dir(config_dir: Path) -> Path:
    return config_dir / "bundles"


def discover_bundles(config_dir: Path) -> list[Bundle]:
    """Import every immediate package folder under ``<config_dir>/bundles``.

    Folders without ``__init__.py`` are ignored. Bundles are returned in
    folder-name order; malformed bundles and duplicate names are errors.
    """
    root = bundles_dir(config_dir)
    if not root.is_dir():
        raise _no_bundles(root)

    folders = sorted(
        (entry for entry in root.iterdir() if entry.is_dir() and (entry / "__init__.py").is_file()),
        key=lambda entry: entry.name,
    )
    if not folders:
        raise _no_bundles(root)

    bundles: list[Bundle] = []
    seen: dict[str, Path] = {}
    for folder in folders:
        bundle = _load_bundle(folder)
        if bundle.name in seen:
            raise DiscoveryError(
                f"duplicate bundle name {bundle.name!r} "
                f"in {seen[bundle.name]} and {folder}"
            )
        seen[bundle.name] = folder
        bundle.path = folder
        bundles.append(bundle)

    return bundles


def _no_bundles(root: Path) -> DiscoveryError:
    return DiscoveryError(
        f"no bundles found in {root} (create one or pass -D PATH)"
    )


def _load_bundle(folder: Path) -> Bundle:
    try:
        module = load_module(
            folder / "__init__.py",
            prefix=f"bundle_{_safe_name(folder.name)}",
            package_dir=folder,
            keep=True,
        )
    except Exception as exc:
        raise DiscoveryError(f"failed to load bundle {folder.name!r}: {exc}") from exc

    bundle = getattr(module, "bundle", None)
    if bundle is None:
        raise DiscoveryError(f"bundle {folder.name!r} does not define a 'bundle' object")
    if not isinstance(bundle, Bundle):
        raise DiscoveryError(f"bundle {folder.name!r} has a non-Bundle 'bundle' object")

    return bundle


def _safe_name(name: str) -> str:
    return re.sub(r"\W", "_", name)
