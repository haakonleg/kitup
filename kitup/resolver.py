"""Bundle dependency resolution."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .errors import ResolutionError
from .registry import Bundle


@dataclass(frozen=True)
class BundlePlan:
    """An execution plan in dependency-first, deduplicated order."""

    bundles: tuple[Bundle, ...]


def resolve(requested: Sequence[str] | None, registry: Iterable[Bundle]) -> BundlePlan:
    """Resolve requested bundle names (or all bundles) into a run plan."""
    by_name = {bundle.name: bundle for bundle in registry}

    if requested is None:
        roots = list(by_name.values())
    else:
        roots = []
        for name in requested:
            bundle = by_name.get(name)
            if bundle is None:
                raise ResolutionError(f"unknown bundle: {name}")
            if bundle not in roots:
                roots.append(bundle)

    _validate_registry(by_name)

    ordered: list[Bundle] = []
    done: set[str] = set()
    active: list[str] = []

    for root in roots:
        _visit(root, by_name, done, active, ordered)

    return BundlePlan(tuple(ordered))


def _validate_registry(by_name: dict[str, Bundle]) -> None:
    """Reject unknown dependencies and cycles anywhere in the registry.

    The configuration is validated as a whole at startup, even when only a
    subset of bundles was requested.
    """
    done: set[str] = set()
    active: list[str] = []

    for bundle in by_name.values():
        _visit(bundle, by_name, done, active, ordered=None)


def _visit(
    bundle: Bundle,
    by_name: dict[str, Bundle],
    done: set[str],
    active: list[str],
    ordered: list[Bundle] | None,
) -> None:
    """Validate one bundle and optionally append it after its dependencies."""
    if bundle.name in done:
        return
    if bundle.name in active:
        start = active.index(bundle.name)
        cycle = " -> ".join([*active[start:], bundle.name])
        raise ResolutionError(f"dependency cycle: {cycle}")

    active.append(bundle.name)
    for dependency_name in bundle.depends_on:
        dependency = by_name.get(dependency_name)
        if dependency is None:
            raise ResolutionError(
                f"unknown dependency {dependency_name!r} for bundle {bundle.name!r}"
            )
        _visit(dependency, by_name, done, active, ordered)
    active.pop()

    done.add(bundle.name)
    if ordered is not None:
        ordered.append(bundle)
