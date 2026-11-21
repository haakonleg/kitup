from pathlib import Path

import pytest

from kitup.errors import VarsError
from kitup.registry import Bundle
from kitup.variables import Vars, filter_var_paths, load_bundle_vars, load_vars

from ..helpers import write


def test_path_sources_merge_in_order(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    write(
        first,
        """
        KEEP = "first"
        REPLACE = "first"
        COLLECTION = [1, 2]
    """,
    )
    write(
        second,
        """
        REPLACE = "second"
        COLLECTION = [3]
        ADDED = True
    """,
    )

    values = load_vars([first, second])

    assert values.KEEP == "first"
    assert values.REPLACE == "second"
    assert values.COLLECTION == [3]
    assert values.ADDED is True


def test_only_plain_assignments_become_var_values(tmp_path):
    write(
        tmp_path / "base.py",
        """
        from typing import Any, Final
        from kitup import Bundle

        VALUE: Final = 1
        _PRIVATE = "hidden"
        BUNDLE = Bundle("nested")

        def helper():
            return None
    """,
    )

    assert vars(load_vars([tmp_path / "base.py"])) == {"VALUE": 1}


def test_vars_get_and_require(tmp_path):
    write(tmp_path / "base.py", "VALUE = 1\n")

    values = load_vars([tmp_path / "base.py"])

    assert values.get("VALUE") == 1
    assert values.get("MISSING") is None
    assert values.get("MISSING", "fallback") == "fallback"
    assert values.require("VALUE") == 1

    with pytest.raises(VarsError, match="missing var value: MISSING \\(available: VALUE\\)"):
        values.require("MISSING")

    with pytest.raises(VarsError, match="var key must be a non-empty string"):
        values.require("")


def test_nested_dicts_support_attribute_access_in_values_lists_and_tuples(tmp_path):
    write(
        tmp_path / "base.py",
        """
        SETTINGS = {"theme": {"name": "dark"}}
        SERVERS = [{"host": "one"}, {"host": "two"}]
        PAIRS = ({"host": "three"},)
        CONFIG = {"servers": [{"host": "four"}]}
        LIMITS = {"my-key": 1, "items": ["a"], "get": "value"}
    """,
    )

    values = load_vars([tmp_path / "base.py"])

    assert values.SETTINGS.theme.name == "dark"
    assert values.SETTINGS["theme"]["name"] == "dark"
    assert values.SETTINGS == {"theme": {"name": "dark"}}
    assert isinstance(values.SETTINGS, dict)
    assert values.SERVERS[0].host == "one"
    assert values.SERVERS[0]["host"] == "one"
    assert values.PAIRS[0].host == "three"
    assert values.CONFIG.servers[0].host == "four"
    assert isinstance(values.SERVERS, list)
    assert isinstance(values.PAIRS, tuple)
    assert values.LIMITS["my-key"] == 1
    assert values.LIMITS["items"] == ["a"]
    assert values.LIMITS["get"] == "value"
    assert callable(values.LIMITS.items)
    with pytest.raises(AttributeError, match="missing"):
        _ = values.LIMITS.missing

    constructed = Vars(SETTINGS={"theme": {"name": "dark"}})
    assert constructed.SETTINGS.theme.name == "dark"


def test_vars_reject_reserved_names(tmp_path):
    for name in ("get", "require"):
        write(tmp_path / "base.py", f"{name} = 1\n")
        with pytest.raises(VarsError, match=rf"defines reserved name.*{name}"):
            load_vars([tmp_path / "base.py"])

    with pytest.raises(VarsError, match="var defines reserved name"):
        Vars(get=1)


def test_invalid_missing_and_unknown_user_sources_are_errors(tmp_path):
    for source in ("base.py", Path("base.py")):
        with pytest.raises(VarsError, match="not a single source"):
            load_vars(source)  # type: ignore[arg-type]

    with pytest.raises(VarsError, match="Path objects"):
        load_vars(["base.py"])  # type: ignore[list-item]

    with pytest.raises(VarsError, match="var not found"):
        load_vars([tmp_path / "missing.py"])

    with pytest.raises(VarsError, match=r"cannot resolve path '~bogususer/base\.py'"):
        load_vars([Path("~bogususer/base.py")])


def test_import_exception_is_reported(tmp_path):
    write(tmp_path / "base.py", "raise RuntimeError('boom')\n")

    with pytest.raises(VarsError, match="boom"):
        load_vars([tmp_path / "base.py"])


def test_empty_var_sequence_returns_empty_vars():
    assert isinstance(load_vars([]), Vars)
    assert vars(load_vars([])) == {}


def test_filter_var_paths_selects_and_validates_ids(tmp_path):
    base = tmp_path / "base.py"
    laptop = tmp_path / "laptop.py"
    work = tmp_path / "work.py"
    paths = [base, laptop, work]

    assert filter_var_paths(paths) == tuple(paths)
    assert filter_var_paths(paths, include={"laptop"}) == (laptop,)
    assert filter_var_paths(paths, exclude={"laptop"}) == (base, work)
    assert filter_var_paths(paths, include=frozenset()) == ()
    assert filter_var_paths(paths, include={"work", "laptop"}) == (laptop, work)

    with pytest.raises(VarsError, match="unknown var: missing \\(available: base\\)"):
        filter_var_paths([base], include={"missing"})

    with pytest.raises(VarsError, match="unknown var: missing \\(available: base\\)"):
        filter_var_paths([base], exclude={"missing"})

    with pytest.raises(VarsError, match="var selected and excluded: base"):
        filter_var_paths([base], include={"base"}, exclude={"base"})

    first = tmp_path / "one" / "base.py"
    second = tmp_path / "two" / "base.py"
    duplicates = [first, second]

    with pytest.raises(VarsError, match=r"duplicate var id 'base'.*one/base\.py.*two/base\.py"):
        filter_var_paths(duplicates)

    with pytest.raises(VarsError, match=r"duplicate var id 'base'.*one/base\.py.*two/base\.py"):
        filter_var_paths(duplicates, include={"base"})


def test_var_file_can_import_a_sibling_helper(tmp_path):
    write(tmp_path / "_helper.py", "VALUE = 'shared'\n")
    write(tmp_path / "secrets.py", "from ._helper import VALUE\nSECRET = VALUE\n")

    values = load_vars([tmp_path / "secrets.py"])

    assert values.SECRET == "shared"


def test_load_bundle_vars_layers_skips_wraps_and_supports_sibling_imports(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    write(first, "VALUE = 'first'\nBUNDLE_ONLY = 1\nSETTINGS = {'theme': {'name': 'dark'}}\n")
    write(second, "VALUE = 'second'\n")
    bundle = Bundle("base")
    bundle.var_paths = (first, second)

    scoped = load_bundle_vars(Vars(VALUE="project"), [bundle])

    assert scoped[bundle].VALUE == "project"
    assert scoped[bundle].BUNDLE_ONLY == 1
    assert scoped[bundle].SETTINGS.theme.name == "dark"

    empty = Bundle("empty")
    assert load_bundle_vars(Vars(VALUE=1), [empty]) == {}

    helper_root = tmp_path / "helpers"
    write(helper_root / "_helper.py", "VALUE = 'shared'\n")
    write(helper_root / "local.py", "from ._helper import VALUE\nSECRET = VALUE\n")
    helper_bundle = Bundle("helpers")
    helper_bundle.var_paths = (helper_root / "local.py",)

    helper_scoped = load_bundle_vars(Vars(), [helper_bundle])

    assert helper_scoped[helper_bundle].SECRET == "shared"
