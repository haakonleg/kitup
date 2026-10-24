from __future__ import annotations

import pytest

from kitup.errors import ResolutionError
from kitup.registry import Bundle
from kitup.resolver import resolve


def bundle(name: str, *depends_on: str) -> Bundle:
    return Bundle(name, name, depends_on=depends_on)


def names(plan) -> list[str]:
    return [bundle.name for bundle in plan.bundles]


def test_all_bundles_preserve_discovery_order():
    registry = [bundle("base"), bundle("desktop", "base"), bundle("repos", "base")]

    assert names(resolve(None, registry)) == ["base", "desktop", "repos"]


def test_dependencies_run_first_and_are_deduplicated():
    base = bundle("base")
    repos = bundle("repos", "base")
    desktop = bundle("desktop", "base", "repos")
    registry = [base, repos, desktop]

    assert names(resolve(["desktop"], registry)) == ["base", "repos", "desktop"]
    assert names(resolve(["desktop", "base"], registry)) == ["base", "repos", "desktop"]


def test_requested_order_is_kept_for_roots():
    registry = [bundle("a"), bundle("b")]

    assert names(resolve(["b", "a"], registry)) == ["b", "a"]


def test_unknown_bundle_is_an_error():
    with pytest.raises(ResolutionError, match="unknown bundle: missing"):
        resolve(["missing"], [bundle("base")])


def test_unknown_dependency_is_an_error():
    with pytest.raises(ResolutionError, match="unknown dependency 'missing'"):
        resolve(None, [bundle("desktop", "missing")])


def test_cycle_reports_the_path():
    registry = [
        bundle("base", "repos"),
        bundle("repos", "base"),
    ]

    with pytest.raises(ResolutionError, match="dependency cycle: base -> repos -> base"):
        resolve(["base"], registry)


def test_validation_covers_unselected_bundles():
    registry = [bundle("base"), bundle("desktop", "missing")]

    with pytest.raises(ResolutionError, match="unknown dependency 'missing'"):
        resolve(["base"], registry)
