import pytest

from kitup import resolver
from kitup.errors import BundleError, ResolutionError
from kitup.registry import Bundle
from kitup.resolver import filter_plan, resolve


def ids(plan) -> list[str]:
    return [bundle.id for bundle in plan.bundles]


def test_roots_run_in_declared_order():
    base = Bundle("base")
    desktop = Bundle("desktop")

    assert ids(resolve([base, desktop])) == ["base", "desktop"]


def test_dependencies_run_first_and_are_deduplicated():
    base = Bundle("base")
    repos = Bundle("repos", depends_on=[base])
    desktop = Bundle("desktop", depends_on=[base, repos])

    assert ids(resolve([desktop])) == ["base", "repos", "desktop"]
    assert ids(resolve([desktop, base])) == ["base", "repos", "desktop"]


def test_string_dependencies_are_resolved_from_the_registry():
    base = Bundle("base")
    repos = Bundle("repos", depends_on=["base"])
    desktop = Bundle("desktop", depends_on=["base", "repos"])
    registry = {"base": base, "repos": repos, "desktop": desktop}

    assert ids(resolve([desktop], registry=registry)) == ["base", "repos", "desktop"]


def test_unknown_string_dependency_lists_available_bundles():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=["missing"])

    with pytest.raises(ResolutionError, match=r"unknown bundle dependency 'missing'.*available: base, desktop"):
        resolve([desktop], registry={"base": base, "desktop": desktop})


def test_same_bundle_reached_twice_runs_once():
    base = Bundle("base")
    one = Bundle("one", depends_on=[base])
    two = Bundle("two", depends_on=[base])

    assert ids(resolve([one, two])) == ["base", "one", "two"]


def test_duplicate_ids_are_rejected():
    first = Bundle("same")
    second = Bundle("same")
    root = Bundle("root", depends_on=[first, second])

    with pytest.raises(BundleError, match="duplicate bundle id: same"):
        resolve([first, second])

    with pytest.raises(BundleError, match="duplicate bundle id: same"):
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

    assert ids(filtered) == ["base", "repos", "desktop"]


def test_filter_plan_include_can_select_a_dependency():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    filtered = filter_plan(resolve([desktop]), include={"base"})

    assert ids(filtered) == ["base"]


def test_filter_plan_exclude_removes_bundle():
    base = Bundle("base")
    desktop = Bundle("desktop")

    filtered = filter_plan(resolve([base, desktop]), exclude={"desktop"})

    assert ids(filtered) == ["base"]


def test_filter_plan_exclude_required_dependency_is_rejected():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    with pytest.raises(ResolutionError, match="cannot exclude bundle 'base': required by 'desktop'"):
        filter_plan(resolve([desktop]), exclude={"base"})


def test_filter_plan_exclude_dependency_when_dependent_is_also_excluded():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    filtered = filter_plan(resolve([desktop]), exclude={"base", "desktop"})

    assert ids(filtered) == []


def test_filter_plan_unknown_include_is_rejected():
    base = Bundle("base")

    with pytest.raises(ResolutionError, match="unknown bundle: missing \\(available: base\\)"):
        filter_plan(resolve([base]), include={"missing"})


def test_filter_plan_unknown_exclude_is_rejected():
    base = Bundle("base")

    with pytest.raises(ResolutionError, match="unknown bundle: missing \\(available: base\\)"):
        filter_plan(resolve([base]), exclude={"missing"})


def test_filter_plan_combines_include_and_exclude():
    base = Bundle("base")
    desktop = Bundle("desktop")
    gaming = Bundle("gaming")

    filtered = filter_plan(resolve([base, desktop, gaming]), include={"base", "desktop"}, exclude={"gaming"})

    assert ids(filtered) == ["base", "desktop"]


def test_filter_plan_overlapping_filters_are_rejected():
    base = Bundle("base")

    with pytest.raises(ResolutionError, match="bundle selected and excluded: base"):
        filter_plan(resolve([base]), include={"base"}, exclude={"base"})


def test_filter_plan_empty_include_selects_nothing():
    base = Bundle("base")

    assert ids(filter_plan(resolve([base]), include=frozenset())) == []


def test_filter_plan_memoizes_transitive_dependencies(monkeypatch):
    base = Bundle("base")
    one = Bundle("one", depends_on=[base])
    two = Bundle("two", depends_on=[base])
    extra = Bundle("extra")
    other = Bundle("other")
    plan = resolve([one, two, extra, other])

    calls = []
    original = resolver._transitive_dependencies

    def counting(bundle, by_id):
        calls.append(bundle)
        return original(bundle, by_id)

    monkeypatch.setattr(resolver, "_transitive_dependencies", counting)

    filtered = filter_plan(plan, exclude={"extra", "other"})

    assert ids(filtered) == ["base", "one", "two"]
    assert sorted(bundle.id for bundle in calls) == ["base", "one", "two"]


def test_filter_plan_without_filters_returns_the_same_plan():
    base = Bundle("base")
    plan = resolve([base])

    assert filter_plan(plan) is plan
