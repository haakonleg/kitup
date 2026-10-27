"""Bundle dependency resolution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .errors import BundleError, ResolutionError
from .registry import Bundle


@dataclass(frozen=True)
class BundlePlan:
    """An execution plan in dependency-first, deduplicated order."""

    bundles: tuple[Bundle, ...]


def resolve(roots: Sequence[Bundle]) -> BundlePlan:
    """Resolve a root bundle sequence into a dependency-first run plan."""
    ordered: list[Bundle] = []
    by_name: dict[str, Bundle] = {}
    done: set[int] = set()
    active: list[Bundle] = []

    def register(bundle: Bundle) -> None:
        existing = by_name.get(bundle.name)
        if existing is not None and existing is not bundle:
            raise BundleError(f"duplicate bundle name: {bundle.name}")
        by_name[bundle.name] = bundle

    def visit(bundle: Bundle) -> None:
        register(bundle)
        if id(bundle) in done:
            return
        for index, active_bundle in enumerate(active):
            if active_bundle is bundle:
                cycle = " -> ".join(
                    [entry.name for entry in (*active[index:], bundle)]
                )
                raise ResolutionError(f"dependency cycle: {cycle}")

        active.append(bundle)
        for dependency in bundle.depends_on:
            visit(dependency)
        active.pop()

        done.add(id(bundle))
        ordered.append(bundle)

    for root in roots:
        visit(root)

    return BundlePlan(tuple(ordered))
