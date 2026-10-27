from __future__ import annotations

import pytest

from kitup.errors import BundleError, ResolutionError
from kitup.registry import Bundle
from kitup.resolver import resolve


def names(plan) -> list[str]:
    return [bundle.name for bundle in plan.bundles]


def test_roots_run_in_declared_order():
    base = Bundle("base")
    desktop = Bundle("desktop")

    assert names(resolve([base, desktop])) == ["base", "desktop"]


def test_dependencies_run_first_and_are_deduplicated():
    base = Bundle("base")
    repos = Bundle("repos", depends_on=[base])
    desktop = Bundle("desktop", depends_on=[base, repos])

    assert names(resolve([desktop])) == ["base", "repos", "desktop"]
    assert names(resolve([desktop, base])) == ["base", "repos", "desktop"]


def test_same_bundle_reached_twice_runs_once():
    base = Bundle("base")
    one = Bundle("one", depends_on=[base])
    two = Bundle("two", depends_on=[base])

    assert names(resolve([one, two])) == ["base", "one", "two"]


def test_duplicate_names_are_rejected():
    first = Bundle("same")
    second = Bundle("same")
    root = Bundle("root", depends_on=[first, second])

    with pytest.raises(BundleError, match="duplicate bundle name: same"):
        resolve([first, second])

    with pytest.raises(BundleError, match="duplicate bundle name: same"):
        resolve([root])


def test_cycle_reports_the_path():
    base = Bundle("base")
    repos = Bundle("repos", depends_on=[base])
    base.depends_on = (repos,)

    with pytest.raises(ResolutionError, match="dependency cycle: base -> repos -> base"):
        resolve([base])
