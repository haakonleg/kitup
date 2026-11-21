import tomllib

import pytest

from kitup._utils import toml
from kitup.errors import TaskFailed

from .helpers import call_data, local_sudo_context, make_context


def lines(text: str) -> list[str]:
    return toml.split_lines(text)


def test_set_key_adds_replaces_and_keeps_entries():
    original = lines('# top\n[project]\nname = "demo"\n\n[tool.uv]\n')
    new_lines, action = toml.set_key(original, "project", "dependencies", '["a"]')
    assert action == "added"
    assert new_lines == lines('# top\n[project]\nname = "demo"\ndependencies = ["a"]\n\n[tool.uv]\n')

    new_lines, action = toml.set_key(original, "tool.uv", "index-url", '"https://x"')
    assert action == "added"
    assert new_lines == lines('# top\n[project]\nname = "demo"\n\n[tool.uv]\nindex-url = "https://x"\n')

    # an equal value with different spacing/quotes is unchanged
    spaced = lines('[tool.uv]\nindex-url  =  "https://x"  # mirror\n')
    assert toml.set_key(spaced, "tool.uv", "index-url", '"https://x"') == (spaced, "unchanged")

    replaced, action = toml.set_key(spaced, "tool.uv", "index-url", '"https://y"')
    assert action == "replaced"
    assert replaced == lines('[tool.uv]\nindex-url = "https://y"\n')

    # a missing table is appended
    original = lines('[project]\nname = "demo"\n')
    new_lines, action = toml.set_key(original, "tool.uv", "native-tls", "true")
    assert action == "added"
    assert new_lines == lines('[project]\nname = "demo"\n[tool.uv]\nnative-tls = true\n')

    # an empty document gets the table
    empty: list[str] = []
    new_lines, action = toml.set_key(empty, "tool.uv", "native-tls", "true")
    assert action == "added"
    assert new_lines == lines("[tool.uv]\nnative-tls = true\n")

    # table=None edits the top-level preamble, before any header
    original = lines("top = 1\n\n[other]\nx = 2\n")
    new_lines, action = toml.set_key(original, None, "key", '"v"')
    assert action == "added"
    assert new_lines == lines('top = 1\nkey = "v"\n\n[other]\nx = 2\n')


def test_set_key_matches_quoted_and_dotted_keys():
    # quoted existing keys still match the bare key argument
    original = lines('[tool.uv]\n"index-url" = "old"\npip.no-cache = true\n')
    new_lines, action = toml.set_key(original, "tool.uv", "index-url", '"new"')
    assert action == "replaced"
    assert new_lines == lines('[tool.uv]\nindex-url = "new"\npip.no-cache = true\n')

    new_lines, action = toml.set_key(original, "tool.uv", "pip.no-cache", "false")
    assert action == "replaced"
    assert new_lines == lines('[tool.uv]\n"index-url" = "old"\npip.no-cache = false\n')

    # a dotted option resolves into an existing sub-table
    original = lines("[tool.uv]\nnative-tls = true\n\n[tool.uv.pip]\nno-cache = false\n")
    new_lines, action = toml.set_key(original, "tool.uv", "pip.no-cache", "true")
    assert action == "replaced"
    assert new_lines == lines("[tool.uv]\nnative-tls = true\n\n[tool.uv.pip]\nno-cache = true\n")

    # without the sub-table header the dotted key stays in [tool.uv]
    original = lines("[tool.uv]\nnative-tls = true\n")
    new_lines, action = toml.set_key(original, "tool.uv", "pip.no-cache", "true")
    assert action == "added"
    assert new_lines == lines("[tool.uv]\nnative-tls = true\npip.no-cache = true\n")

    # a table declared with top-level dotted keys is edited in place; adding a
    # [tool.uv] header here would collide with the implicit tool.uv table
    original = lines('tool.uv.native-tls = true\n[project]\nname = "demo"\n')
    new_lines, action = toml.set_key(original, "tool.uv", "index-url", '"https://x"')
    assert action == "added"
    assert new_lines == lines('tool.uv.native-tls = true\ntool.uv.index-url = "https://x"\n[project]\nname = "demo"\n')
    assert tomllib.loads("".join(new_lines))["tool"]["uv"]["index-url"] == "https://x"


def test_set_key_rejects_array_of_tables_targets():
    original = lines('[[tool.uv.index]]\nurl = "https://x"\n')
    with pytest.raises(TaskFailed, match="array-of-tables"):
        toml.set_key(original, "tool.uv.index", "url", '"https://y"')
    with pytest.raises(TaskFailed, match="array-of-tables"):
        toml.remove_key(original, "tool.uv.index", "url")


def test_set_key_handles_multi_line_values_comments_and_scanner_edges():
    # replacing a multi-line value drops its continuation lines but keeps trailing text
    original = lines('[project]\ndependencies = [\n    "a",  # first\n    "b",\n]\n# trailing\n')
    new_lines, action = toml.set_key(original, "project", "dependencies", '["c"]')
    assert action == "replaced"
    assert new_lines == lines('[project]\ndependencies = ["c"]\n# trailing\n')

    # brackets inside strings and comments are not headers or value terminators
    original = lines("[a]\n# [not-a-header]\nkey = \"text with ] bracket\"\nother = '[x]'\n")
    new_lines, action = toml.set_key(original, "a", "new", "1")
    assert action == "added"
    assert new_lines == lines("[a]\n# [not-a-header]\nkey = \"text with ] bracket\"\nother = '[x]'\nnew = 1\n")

    # """say "hi"""" is valid TOML (value: say "hi"); the closing quote run must be consumed whole
    original = lines('[a]\nx = """say "hi""""\n[b]\ny = 1\n')
    new_lines, action = toml.set_key(original, "b", "z", "2")
    assert action == "added"
    assert new_lines == lines('[a]\nx = """say "hi""""\n[b]\ny = 1\nz = 2\n')
    assert toml.array_items(lines('[a]\ndeps = ["x]"]  # not a header\n'), "a", "deps") == ["x]"]


def test_split_lines_and_crlf_rendering():
    text = "# comment with \u2028 and \u0085 raw\nkey = 1\n"
    assert toml.split_lines(text) == ["# comment with \u2028 and \u0085 raw\n", "key = 1\n"]
    new_lines, action = toml.set_key(toml.split_lines(text), None, "other", "2")
    assert action == "added"
    joined = "".join(new_lines)
    assert joined == "# comment with \u2028 and \u0085 raw\nkey = 1\nother = 2\n"
    tomllib.loads(joined)  # still valid TOML, the comment line was not phantom-split

    original = toml.split_lines('[tool.uv]\r\nindex-url = "old"\r\n')
    new_lines, action = toml.set_key(original, "tool.uv", "native-tls", "true")
    assert action == "added"
    assert new_lines == ["[tool.uv]\r\n", 'index-url = "old"\r\n', "native-tls = true\r\n"]

    replaced, action = toml.set_key(original, "tool.uv", "index-url", '"new"')
    assert action == "replaced"
    assert replaced == ["[tool.uv]\r\n", 'index-url = "new"\r\n']


def test_value_equality_distinguishes_types():
    assert toml.set_key(lines("flag = true\n"), None, "flag", "1") != (lines("flag = true\n"), "unchanged")
    assert toml.set_key(lines("n = 1\n"), None, "n", "1.0") != (lines("n = 1\n"), "unchanged")
    assert toml.set_key(lines("flag = true\n"), None, "flag", "true") == (lines("flag = true\n"), "unchanged")


def test_array_items_and_set_array_layout():
    original = lines("[project]\ndependencies = [\n    \"a\",  # c\n    'b',\n]\n")
    assert toml.array_items(original, "project", "dependencies") == ["a", "b"]
    assert toml.array_items(original, "project", "missing") is None
    assert toml.array_items(original, "other", "dependencies") is None

    scalar = lines('[project]\ndependencies = "a"\n')
    with pytest.raises(TaskFailed, match="not an array of strings"):
        toml.array_items(scalar, "project", "dependencies")

    single = lines("[project]\ndependencies = []\n")
    new_lines, action = toml.set_array(single, "project", "dependencies", ["a"])
    assert action == "replaced"
    assert new_lines == lines('[project]\ndependencies = ["a"]\n')

    multi = lines('[project]\ndependencies = [\n    "a",\n]\n')
    new_lines, action = toml.set_array(multi, "project", "dependencies", ["b", "c"])
    assert action == "replaced"
    assert new_lines == lines('[project]\ndependencies = [\n    "b",\n    "c",\n]\n')

    assert toml.set_array(multi, "project", "dependencies", ["a"]) == (multi, "unchanged")

    new_lines, action = toml.set_array(single, "project", "dependencies", [])
    assert action == "unchanged"
    new_lines, action = toml.set_array(multi, "project", "dependencies", [])
    assert action == "replaced"
    assert new_lines == lines("[project]\ndependencies = []\n")

    # a missing table and key are appended with the multi-line layout
    original = lines('[project]\nname = "demo"\n')
    new_lines, action = toml.set_array(original, "dependency-groups", "lint", ["ruff", "mypy"])
    assert action == "added"
    assert new_lines == lines('[project]\nname = "demo"\n[dependency-groups]\nlint = [\n    "ruff",\n    "mypy",\n]\n')


def test_apply_toml_key_lifecycle(tmp_path):
    # An unchanged key does not rewrite a file without a trailing newline.
    path = tmp_path / "uv.toml"
    path.write_text('index-url = "https://x"', encoding="utf-8")
    ctx = make_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table=None, key="index-url", value="https://x", sudo=False)

    assert result.changed is False
    assert result["toml_action"] == "unchanged"
    assert path.read_text(encoding="utf-8") == 'index-url = "https://x"'  # untouched

    # setting, replacing, and removing a key reports each action and summary
    path = tmp_path / "mutate.toml"
    path.write_text('index-url = "old"\n', encoding="utf-8")
    ctx = make_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table=None, key="index-url", value="https://x", sudo=False)

    assert path.read_text() == 'index-url = "https://x"\n'
    assert result.changed is True
    assert result.summary == f"set index-url in {path}"
    assert call_data(result) == {
        "path": str(path),
        "table": None,
        "key": "index-url",
        "value": "https://x",
        "toml_action": "replaced",
    }

    result = toml.apply_toml_key(ctx, path, table=None, key="index-url", value="https://x", sudo=False)
    assert result.changed is False
    assert result.summary == f'ensure index-url is "https://x" in {path}'
    assert call_data(result)["toml_action"] == "unchanged"

    result = toml.apply_toml_key(ctx, path, table=None, key="index-url", value=None, sudo=False)
    assert result.changed is True
    assert result.summary == f"remove index-url from {path}"
    assert path.read_text() == ""
    assert call_data(result)["toml_action"] == "removed"

    result = toml.apply_toml_key(ctx, path, table=None, key="index-url", value=None, sudo=False)
    assert result.changed is False
    assert result.summary == f"ensure index-url is absent from {path}"

    # a missing file and table are created through the privileged worker
    (tmp_path / "deep").mkdir()
    path = tmp_path / "deep" / "pyproject.toml"
    ctx = local_sudo_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table="tool.uv", key="native-tls", value=True, sudo=True)

    assert path.read_text() == "[tool.uv]\nnative-tls = true\n"
    assert call_data(result)["table"] == "tool.uv"
    assert call_data(result)["value"] is True

    # dry-run reports without writing or creating anything
    path = tmp_path / "dry.toml"
    ctx = make_context(tmp_path, dry_run=True)

    result = toml.apply_toml_key(ctx, path, table=None, key="key", value=["a", "b"], sudo=False)

    assert result.changed is True
    assert not path.exists()
    assert call_data(result)["value"] == ["a", "b"]

    # removing a whole table takes its span and is idempotent
    path = tmp_path / "table.toml"
    path.write_text('[server]\nhost = "x"\n\n[tool.uv]\nnative-tls = true\n')
    ctx = make_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table="tool.uv", key=None, value=None, sudo=False)

    # the blank line before [tool.uv] belongs to the [server] span and stays
    assert path.read_text() == '[server]\nhost = "x"\n\n'
    assert result.changed is True
    assert result.summary == f"remove table [tool.uv] from {path}"
    assert call_data(result) == {
        "path": str(path),
        "table": "tool.uv",
        "key": None,
        "value": None,
        "toml_action": "removed",
    }

    result = toml.apply_toml_key(ctx, path, table="tool.uv", key=None, value=None, sudo=False)
    assert result.changed is False
    assert result.summary == f"ensure table [tool.uv] is absent from {path}"

    # arrays are written with the set_array layout and compared by parsed value
    path = tmp_path / "arrays.toml"
    path.write_text('[project]\ndependencies = [\n    "a",\n]\n')
    ctx = make_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table="project", key="dependencies", value=["b", "c"], sudo=False)

    assert path.read_text() == '[project]\ndependencies = [\n    "b",\n    "c",\n]\n'
    assert result.changed is True
    assert call_data(result)["value"] == ["b", "c"]

    result = toml.apply_toml_key(ctx, path, table="project", key="dependencies", value=["b", "c"], sudo=False)
    assert result.changed is False
    assert call_data(result)["toml_action"] == "unchanged"


def test_remove_table_removes_header_span_and_dotted_declarations():
    original = lines("# top\n[a]\nx = 1\n# inside\n\n[b]\ny = 2\n")
    assert toml.remove_table(original, "a") == lines("# top\n[b]\ny = 2\n")
    assert toml.remove_table(original, "missing") == original
    # removing the last table leaves an empty document
    assert toml.remove_table(lines("[a]\nx = 1\n"), "a") == []
    # dotted table names address their header
    dotted = lines("[tool]\nname = 1\n[tool.uv]\nnative-tls = true\n[other]\nz = 3\n")
    assert toml.remove_table(dotted, "tool.uv") == lines("[tool]\nname = 1\n[other]\nz = 3\n")

    # tables declared with top-level dotted keys are removed too
    original = lines('tool.uv.native-tls = true\ntool.uv.index-url = "x"\n[project]\nname = "demo"\n')
    new_lines = toml.remove_table(original, "tool.uv")
    assert new_lines == lines('[project]\nname = "demo"\n')
    assert tomllib.loads("".join(new_lines)) == {"project": {"name": "demo"}}
