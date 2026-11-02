import pytest

from kitup.errors import BundleError, ResolutionError
from kitup.registry import Bundle
from kitup.resolver import filter_plan, resolve


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


def test_filter_plan_include_keeps_dependencies():
    base = Bundle("base")
    repos = Bundle("repos", depends_on=[base])
    desktop = Bundle("desktop", depends_on=[base, repos])

    filtered = filter_plan(resolve([desktop]), include={"desktop"})

    assert names(filtered) == ["base", "repos", "desktop"]


def test_filter_plan_include_can_select_a_dependency():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    filtered = filter_plan(resolve([desktop]), include={"base"})

    assert names(filtered) == ["base"]


def test_filter_plan_exclude_removes_bundle():
    base = Bundle("base")
    desktop = Bundle("desktop")

    filtered = filter_plan(resolve([base, desktop]), exclude={"desktop"})

    assert names(filtered) == ["base"]


def test_filter_plan_exclude_required_dependency_is_rejected():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    with pytest.raises(ResolutionError, match="cannot exclude bundle 'base': required by 'desktop'"):
        filter_plan(resolve([desktop]), exclude={"base"})


def test_filter_plan_exclude_dependency_when_dependent_is_also_excluded():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    filtered = filter_plan(resolve([desktop]), exclude={"base", "desktop"})

    assert names(filtered) == []


def test_filter_plan_unknown_include_is_rejected():
    base = Bundle("base")

    with pytest.raises(ResolutionError, match="unknown bundle: missing"):
        filter_plan(resolve([base]), include={"missing"})


def test_filter_plan_unknown_exclude_is_rejected():
    base = Bundle("base")

    with pytest.raises(ResolutionError, match="unknown bundle: missing"):
        filter_plan(resolve([base]), exclude={"missing"})


def test_filter_plan_combines_include_and_exclude():
    base = Bundle("base")
    desktop = Bundle("desktop")
    gaming = Bundle("gaming")

    filtered = filter_plan(resolve([base, desktop, gaming]), include={"base", "desktop"}, exclude={"gaming"})

    assert names(filtered) == ["base", "desktop"]


def test_filter_plan_overlapping_filters_are_rejected():
    base = Bundle("base")

    with pytest.raises(ResolutionError, match="bundle selected and excluded: base"):
        filter_plan(resolve([base]), include={"base"}, exclude={"base"})


def test_filter_plan_empty_include_selects_nothing():
    base = Bundle("base")

    assert names(filter_plan(resolve([base]), include=frozenset())) == []


def test_filter_plan_without_filters_returns_the_same_plan():
    base = Bundle("base")
    plan = resolve([base])

    assert filter_plan(plan) is plan
