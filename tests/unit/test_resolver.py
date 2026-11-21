import pytest

from kitup.errors import BundleError, ResolutionError
from kitup.registry import Bundle
from kitup.resolver import filter_plan, resolve


def ids(plan) -> list[str]:
    return [bundle.id for bundle in plan.bundles]


def test_roots_and_dependencies_run_first_in_order_and_are_deduplicated():
    base = Bundle("base")
    desktop = Bundle("desktop")

    assert ids(resolve([base, desktop])) == ["base", "desktop"]

    repos = Bundle("repos", depends_on=[base])
    desktop = Bundle("desktop", depends_on=[base, repos])

    assert ids(resolve([desktop])) == ["base", "repos", "desktop"]
    assert ids(resolve([desktop, base])) == ["base", "repos", "desktop"]

    one = Bundle("one", depends_on=[base])
    two = Bundle("two", depends_on=[base])
    assert ids(resolve([one, two])) == ["base", "one", "two"]

    string_repos = Bundle("repos", depends_on=["string-base"])
    string_desktop = Bundle("desktop", depends_on=["string-base", "repos"])
    registry = {"string-base": Bundle("string-base"), "repos": string_repos, "desktop": string_desktop}
    assert ids(resolve([string_desktop], registry=registry)) == ["string-base", "repos", "desktop"]


def test_unknown_string_dependency_lists_available_bundles():
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=["missing"])

    with pytest.raises(ResolutionError, match=r"unknown bundle dependency 'missing'.*available: base, desktop"):
        resolve([desktop], registry={"base": base, "desktop": desktop})


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


def test_filter_plan_include_exclude_and_error_scenarios():
    base = Bundle("base")
    repos = Bundle("repos", depends_on=[base])
    desktop = Bundle("desktop", depends_on=[base, repos])

    assert ids(filter_plan(resolve([desktop]), include={"desktop"})) == ["base", "repos", "desktop"]
    assert ids(filter_plan(resolve([desktop]), include={"base"})) == ["base"]

    plain = Bundle("base")
    other = Bundle("desktop")
    assert ids(filter_plan(resolve([plain, other]), exclude={"desktop"})) == ["base"]

    with pytest.raises(ResolutionError, match="cannot exclude bundle 'base': required by 'desktop'"):
        filter_plan(resolve([desktop]), exclude={"base"})

    simple_base = Bundle("simple-base")
    simple_desktop = Bundle("simple-desktop", depends_on=[simple_base])
    assert ids(filter_plan(resolve([simple_desktop]), exclude={"simple-base", "simple-desktop"})) == []

    with pytest.raises(ResolutionError, match="unknown bundle: missing \\(available: base\\)"):
        filter_plan(resolve([plain]), include={"missing"})
    with pytest.raises(ResolutionError, match="unknown bundle: missing \\(available: base\\)"):
        filter_plan(resolve([plain]), exclude={"missing"})

    gaming = Bundle("gaming")
    filtered = filter_plan(resolve([plain, other, gaming]), include={"base", "desktop"}, exclude={"gaming"})
    assert ids(filtered) == ["base", "desktop"]

    with pytest.raises(ResolutionError, match="bundle selected and excluded: base"):
        filter_plan(resolve([plain]), include={"base"}, exclude={"base"})

    assert ids(filter_plan(resolve([plain]), include=frozenset())) == []

    plan = resolve([plain])
    assert filter_plan(plan) is plan
