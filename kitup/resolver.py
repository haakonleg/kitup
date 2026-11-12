"""Bundle dependency resolution."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from .errors import BundleError, ResolutionError
from .registry import Bundle


@dataclass(frozen=True, slots=True)
class BundlePlan:
    """An execution plan in dependency-first, deduplicated order."""

    bundles: tuple[Bundle, ...]


def resolve(roots: Sequence[Bundle], *, registry: Mapping[str, Bundle] | None = None) -> BundlePlan:
    """Resolve a root bundle sequence into a dependency-first run plan.

    String dependencies are looked up in ``registry``, which normally contains
    every bundle discovered in the project.
    """
    known: dict[str, Bundle] = dict(registry or {})
    ordered: list[Bundle] = []
    by_id: dict[str, Bundle] = {}
    done: set[int] = set()
    active: list[Bundle] = []

    def register(bundle: Bundle) -> None:
        existing = by_id.get(bundle.id)
        if existing is not None and existing is not bundle:
            raise BundleError(f"duplicate bundle id: {bundle.id}")
        by_id[bundle.id] = bundle
        known.setdefault(bundle.id, bundle)

    def visit(bundle: Bundle) -> None:
        register(bundle)
        if id(bundle) in done:
            return
        for index, active_bundle in enumerate(active):
            if active_bundle is bundle:
                cycle = " -> ".join([entry.id for entry in (*active[index:], bundle)])
                raise ResolutionError(f"dependency cycle: {cycle}")

        active.append(bundle)
        for dependency in _resolved_dependencies(bundle, known):
            visit(dependency)
        active.pop()

        done.add(id(bundle))
        ordered.append(bundle)

    for root in roots:
        visit(root)

    return BundlePlan(tuple(ordered))


def filter_plan(
    plan: BundlePlan,
    *,
    include: Collection[str] | None = None,
    exclude: Collection[str] | None = None,
    registry: Mapping[str, Bundle] | None = None,
) -> BundlePlan:
    """Return a plan restricted to the selected bundles and their dependencies.

    ``include`` keeps the named bundles and their transitive dependencies.
    ``exclude`` removes the named bundles and raises when a kept bundle still
    depends on one.
    """
    if include is None and not exclude:
        return plan

    by_id = {bundle.id: bundle for bundle in plan.bundles}
    if registry is not None:
        for bundle_id, bundle in registry.items():
            by_id.setdefault(bundle_id, bundle)

    transitive_cache: dict[Bundle, set[Bundle]] = {}

    def transitive(bundle: Bundle) -> set[Bundle]:
        cached = transitive_cache.get(bundle)
        if cached is None:
            cached = _transitive_dependencies(bundle, by_id)
            transitive_cache[bundle] = cached
        return cached

    included = frozenset(include or ())
    excluded = frozenset(exclude or ())

    unknown = sorted((included | excluded) - set(by_id))
    if unknown:
        available = ", ".join(sorted(by_id)) or "none"
        raise ResolutionError(f"unknown bundle: {', '.join(unknown)} (available: {available})")

    overlap = included & excluded
    if overlap:
        raise ResolutionError(f"bundle selected and excluded: {', '.join(sorted(overlap))}")

    if include is None:
        keep = set(plan.bundles)
    else:
        keep = set()
        for bundle_id in included:
            bundle = by_id[bundle_id]
            keep.add(bundle)
            keep.update(transitive(bundle))

    excluded_bundles = {by_id[bundle_id] for bundle_id in excluded}
    keep -= excluded_bundles
    for bundle in excluded_bundles:
        dependents = sorted(kept.id for kept in keep if bundle in transitive(kept))
        if dependents:
            required_by = ", ".join(repr(bundle_id) for bundle_id in dependents)
            raise ResolutionError(f"cannot exclude bundle {bundle.id!r}: required by {required_by}")

    return BundlePlan(tuple(bundle for bundle in plan.bundles if bundle in keep))


def _transitive_dependencies(bundle: Bundle, by_id: Mapping[str, Bundle]) -> set[Bundle]:
    dependencies: set[Bundle] = set()
    stack = list(_resolved_dependencies(bundle, by_id))
    while stack:
        dependency = stack.pop()
        if dependency in dependencies:
            continue
        dependencies.add(dependency)
        stack.extend(_resolved_dependencies(dependency, by_id))
    return dependencies


def _resolved_dependencies(bundle: Bundle, by_id: Mapping[str, Bundle]) -> tuple[Bundle, ...]:
    resolved: list[Bundle] = []
    for dependency in bundle.depends_on:
        if isinstance(dependency, Bundle):
            resolved.append(dependency)
            continue
        target = by_id.get(dependency)
        if target is None:
            available = ", ".join(sorted(by_id)) or "none"
            raise ResolutionError(f"unknown bundle dependency {dependency!r} in {bundle.id!r} (available: {available})")
        resolved.append(target)
    return tuple(resolved)
