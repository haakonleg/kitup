"""Bundle dependency resolution."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from .errors import BundleError, ResolutionError
from .registry import Bundle


@dataclass(frozen=True, slots=True)
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
                cycle = " -> ".join([entry.name for entry in (*active[index:], bundle)])
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


def filter_plan(
    plan: BundlePlan,
    *,
    include: Collection[str] | None = None,
    exclude: Collection[str] | None = None,
) -> BundlePlan:
    """Return a plan restricted to the selected bundles and their dependencies.

    ``include`` keeps the named bundles and their transitive dependencies.
    ``exclude`` removes the named bundles and raises when a kept bundle still
    depends on one.
    """
    if include is None and not exclude:
        return plan

    by_name = {bundle.name: bundle for bundle in plan.bundles}
    included = frozenset(include or ())
    excluded = frozenset(exclude or ())

    unknown = sorted((included | excluded) - set(by_name))
    if unknown:
        raise ResolutionError(f"unknown bundle: {', '.join(unknown)}")

    overlap = included & excluded
    if overlap:
        raise ResolutionError(f"bundle selected and excluded: {', '.join(sorted(overlap))}")

    if include is None:
        keep = set(plan.bundles)
    else:
        keep = set()
        for name in included:
            bundle = by_name[name]
            keep.add(bundle)
            keep.update(_transitive_dependencies(bundle))

    excluded_bundles = {by_name[name] for name in excluded}
    keep -= excluded_bundles
    for bundle in excluded_bundles:
        dependents = sorted(kept.name for kept in keep if bundle in _transitive_dependencies(kept))
        if dependents:
            required_by = ", ".join(repr(name) for name in dependents)
            raise ResolutionError(f"cannot exclude bundle {bundle.name!r}: required by {required_by}")

    return BundlePlan(tuple(bundle for bundle in plan.bundles if bundle in keep))


def _transitive_dependencies(bundle: Bundle) -> set[Bundle]:
    dependencies: set[Bundle] = set()
    stack = list(bundle.depends_on)
    while stack:
        dependency = stack.pop()
        if dependency in dependencies:
            continue
        dependencies.add(dependency)
        stack.extend(dependency.depends_on)
    return dependencies
