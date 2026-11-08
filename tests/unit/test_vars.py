from pathlib import Path

import pytest

from kitup.errors import VarsError
from kitup.registry import Bundle
from kitup.vars import Vars, filter_var_paths, load_bundle_vars, load_vars

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


def test_private_names_and_helpers_are_not_var_values(tmp_path):
    write(
        tmp_path / "base.py",
        """
        VALUE = 1
        _PRIVATE = "hidden"

        def helper():
            return None
    """,
    )

    assert vars(load_vars([tmp_path / "base.py"])) == {"VALUE": 1}


def test_typing_helpers_are_not_var_values(tmp_path):
    write(tmp_path / "base.py", "from typing import Any, Final\nVALUE: Final = 1\n")

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


def test_nested_dicts_support_attribute_access(tmp_path):
    write(
        tmp_path / "base.py",
        """
        SETTINGS = {"theme": {"name": "dark"}}
    """,
    )

    values = load_vars([tmp_path / "base.py"])

    assert values.SETTINGS.theme.name == "dark"
    assert values.SETTINGS["theme"]["name"] == "dark"
    assert values.SETTINGS == {"theme": {"name": "dark"}}
    assert isinstance(values.SETTINGS, dict)


def test_nested_dicts_in_lists_and_tuples_support_attribute_access(tmp_path):
    write(
        tmp_path / "base.py",
        """
        SERVERS = [{"host": "one"}, {"host": "two"}]
        PAIRS = ({"host": "three"},)
        CONFIG = {"servers": [{"host": "four"}]}
    """,
    )

    values = load_vars([tmp_path / "base.py"])

    assert values.SERVERS[0].host == "one"
    assert values.SERVERS[0]["host"] == "one"
    assert values.PAIRS[0].host == "three"
    assert values.CONFIG.servers[0].host == "four"
    assert isinstance(values.SERVERS, list)
    assert isinstance(values.PAIRS, tuple)


def test_nested_dict_attribute_access_limits(tmp_path):
    write(
        tmp_path / "base.py",
        """
        SETTINGS = {"my-key": 1, "items": ["a"], "get": "value"}
    """,
    )

    values = load_vars([tmp_path / "base.py"])

    assert values.SETTINGS["my-key"] == 1
    assert values.SETTINGS["items"] == ["a"]
    assert values.SETTINGS["get"] == "value"
    assert callable(values.SETTINGS.items)

    with pytest.raises(AttributeError, match="missing"):
        _ = values.SETTINGS.missing


def test_vars_constructor_wraps_nested_dicts():
    values = Vars(SETTINGS={"theme": {"name": "dark"}})

    assert values.SETTINGS.theme.name == "dark"


@pytest.mark.parametrize("name", ["get", "require"])
def test_vars_rejects_reserved_names(tmp_path, name):
    write(tmp_path / "base.py", f"{name} = 1\n")

    with pytest.raises(VarsError, match=rf"defines reserved name.*{name}"):
        load_vars([tmp_path / "base.py"])


def test_vars_constructor_rejects_reserved_names():
    with pytest.raises(VarsError, match="var defines reserved name"):
        Vars(get=1)


def test_invalid_source_shapes_are_rejected(tmp_path):
    for source in ("base.py", Path("base.py")):
        with pytest.raises(VarsError, match="not a single source"):
            load_vars(source)  # type: ignore[arg-type]

    with pytest.raises(VarsError, match="Path objects"):
        load_vars(["base.py"])  # type: ignore[list-item]


def test_missing_var_is_an_error(tmp_path):
    with pytest.raises(VarsError, match="var not found"):
        load_vars([tmp_path / "missing.py"])


def test_unknown_user_in_var_path_is_a_var_error():
    with pytest.raises(VarsError, match=r"cannot resolve path '~bogususer/base\.py'"):
        load_vars([Path("~bogususer/base.py")])


def test_import_exception_is_reported(tmp_path):
    write(tmp_path / "base.py", "raise RuntimeError('boom')\n")

    with pytest.raises(VarsError, match="boom"):
        load_vars([tmp_path / "base.py"])


def test_bundle_instances_are_not_var_values(tmp_path):
    write(
        tmp_path / "base.py",
        """
        from kitup import Bundle

        VALUE = 1
        BUNDLE = Bundle("nested")
    """,
    )

    values = load_vars([tmp_path / "base.py"])

    assert vars(values) == {"VALUE": 1}


def test_empty_var_sequence_returns_empty_vars():
    assert isinstance(load_vars([]), Vars)
    assert vars(load_vars([])) == {}


def test_filter_var_paths_by_id(tmp_path):
    base = tmp_path / "base.py"
    laptop = tmp_path / "laptop.py"
    work = tmp_path / "work.py"
    paths = [base, laptop, work]

    assert filter_var_paths(paths) == tuple(paths)
    assert filter_var_paths(paths, include={"laptop"}) == (laptop,)
    assert filter_var_paths(paths, exclude={"laptop"}) == (base, work)
    assert filter_var_paths(paths, include=frozenset()) == ()
    assert filter_var_paths(paths, include={"work", "laptop"}) == (laptop, work)


def test_filter_var_paths_rejects_unknown_and_overlapping_ids(tmp_path):
    base = tmp_path / "base.py"

    with pytest.raises(VarsError, match="unknown var: missing \\(available: base\\)"):
        filter_var_paths([base], include={"missing"})

    with pytest.raises(VarsError, match="unknown var: missing \\(available: base\\)"):
        filter_var_paths([base], exclude={"missing"})

    with pytest.raises(VarsError, match="var selected and excluded: base"):
        filter_var_paths([base], include={"base"}, exclude={"base"})


def test_filter_var_paths_rejects_duplicate_stems(tmp_path):
    first = tmp_path / "one" / "base.py"
    second = tmp_path / "two" / "base.py"
    paths = [first, second]

    with pytest.raises(VarsError, match=r"duplicate var id 'base'.*one/base\.py.*two/base\.py"):
        filter_var_paths(paths)

    with pytest.raises(VarsError, match=r"duplicate var id 'base'.*one/base\.py.*two/base\.py"):
        filter_var_paths(paths, include={"base"})


def test_var_file_can_import_a_sibling_helper(tmp_path):
    write(tmp_path / "_helper.py", "VALUE = 'shared'\n")
    write(tmp_path / "secrets.py", "from ._helper import VALUE\nSECRET = VALUE\n")

    values = load_vars([tmp_path / "secrets.py"])

    assert values.SECRET == "shared"


def test_load_bundle_vars_layers_project_values_on_top(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    write(first, "VALUE = 'first'\nBUNDLE_ONLY = 1\n")
    write(second, "VALUE = 'second'\n")
    bundle = Bundle("base")
    bundle.var_paths = (first, second)

    scoped = load_bundle_vars(Vars(VALUE="project"), [bundle])

    assert scoped[bundle].VALUE == "project"
    assert scoped[bundle].BUNDLE_ONLY == 1


def test_load_bundle_vars_skips_bundles_without_var_files():
    bundle = Bundle("base")

    assert load_bundle_vars(Vars(VALUE=1), [bundle]) == {}


def test_load_bundle_vars_wraps_nested_dicts(tmp_path):
    write(tmp_path / "local.py", 'SETTINGS = {"theme": {"name": "dark"}}\n')
    bundle = Bundle("base")
    bundle.var_paths = (tmp_path / "local.py",)

    scoped = load_bundle_vars(Vars(), [bundle])

    assert scoped[bundle].SETTINGS.theme.name == "dark"


def test_bundle_var_file_can_import_a_sibling_helper(tmp_path):
    write(tmp_path / "_helper.py", "VALUE = 'shared'\n")
    write(tmp_path / "local.py", "from ._helper import VALUE\nSECRET = VALUE\n")
    bundle = Bundle("base")
    bundle.var_paths = (tmp_path / "local.py",)

    scoped = load_bundle_vars(Vars(), [bundle])

    assert scoped[bundle].SECRET == "shared"
