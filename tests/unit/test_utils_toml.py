import tomllib

import pytest

from kitup._utils import toml
from kitup.errors import TaskFailed

from .helpers import call_data, local_sudo_context, make_context


def lines(text: str) -> list[str]:
    return toml.split_lines(text)


def test_toml_string_renders_a_basic_string():
    assert toml.toml_string("plain") == '"plain"'
    assert toml.toml_string('say "hi"\n') == '"say \\"hi\\"\\n"'
    assert toml.toml_string("æ") == '"æ"'
    assert toml.toml_string("del\x7f") == '"del\\u007f"'  # DEL must be escaped in TOML


def test_toml_value_renders_scalars_and_arrays():
    assert toml.toml_value(value=True) == "true"
    assert toml.toml_value(value=False) == "false"
    assert toml.toml_value(42) == "42"
    assert toml.toml_value(1.5) == "1.5"
    assert toml.toml_value("https://x") == '"https://x"'
    assert toml.toml_value(["a", "b"]) == '["a", "b"]'
    assert toml.toml_value(("a",)) == '["a"]'
    assert toml.toml_value([]) == "[]"


def test_toml_value_rejects_unsupported_values():
    for bad in ("", "   ", ["a", 1], {"a": 1}, {"a", "b"}, 1 + 2j, None):
        with pytest.raises(TaskFailed):
            toml.toml_value(bad)


def test_require_toml_key_normalizes_dotted_bare_keys():
    assert toml.require_toml_key("index-url", "option") == "index-url"
    assert toml.require_toml_key(" tool.uv . index-url ", "option") == "tool.uv.index-url"
    for bad in ("", " ", "a b", "a[b", '"quoted"', "a\nb", "a\0b", ".a", "a."):
        with pytest.raises(TaskFailed, match="option"):
            toml.require_toml_key(bad, "option")
    with pytest.raises(TaskFailed, match="must be a string"):
        toml.require_toml_key(1, "option")  # type: ignore[arg-type]


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


def test_set_key_creates_missing_table_and_file_and_top_level_keys():
    original = lines('[project]\nname = "demo"\n')
    new_lines, action = toml.set_key(original, "tool.uv", "native-tls", "true")
    assert action == "added"
    assert new_lines == lines('[project]\nname = "demo"\n[tool.uv]\nnative-tls = true\n')

    empty: list[str] = []
    new_lines, action = toml.set_key(empty, "tool.uv", "native-tls", "true")
    assert action == "added"
    assert new_lines == lines("[tool.uv]\nnative-tls = true\n")

    # table=None edits the top-level preamble, before any header
    original = lines("top = 1\n\n[other]\nx = 2\n")
    new_lines, action = toml.set_key(original, None, "key", '"v"')
    assert action == "added"
    assert new_lines == lines('top = 1\nkey = "v"\n\n[other]\nx = 2\n')


def test_set_key_matches_dotted_and_quoted_existing_keys():
    original = lines('[tool.uv]\n"index-url" = "old"\npip.no-cache = true\n')
    new_lines, action = toml.set_key(original, "tool.uv", "index-url", '"new"')
    assert action == "replaced"
    assert new_lines == lines('[tool.uv]\nindex-url = "new"\npip.no-cache = true\n')

    new_lines, action = toml.set_key(original, "tool.uv", "pip.no-cache", "false")
    assert action == "replaced"
    assert new_lines == lines('[tool.uv]\n"index-url" = "old"\npip.no-cache = false\n')


def test_set_key_rejects_array_of_tables_targets():
    original = lines('[[tool.uv.index]]\nurl = "https://x"\n')
    with pytest.raises(TaskFailed, match="array-of-tables"):
        toml.set_key(original, "tool.uv.index", "url", '"https://y"')
    with pytest.raises(TaskFailed, match="array-of-tables"):
        toml.remove_key(original, "tool.uv.index", "url")


def test_remove_key_removes_only_the_selected_entry():
    original = lines("[a]\nkeep = 1\nx = 2\n[b]\nx = 3\n")
    assert toml.remove_key(original, "a", "x") == lines("[a]\nkeep = 1\n[b]\nx = 3\n")
    assert toml.remove_key(original, "a", "missing") == original
    assert toml.remove_key(original, "missing", "x") == original


def test_set_key_handles_multi_line_values_and_comments():
    original = lines('[project]\ndependencies = [\n    "a",  # first\n    "b",\n]\n# trailing\n')
    new_lines, action = toml.set_key(original, "project", "dependencies", '["c"]')
    assert action == "replaced"
    assert new_lines == lines('[project]\ndependencies = ["c"]\n# trailing\n')


def test_array_items_parses_arrays_and_reports_missing_keys():
    original = lines("[project]\ndependencies = [\n    \"a\",  # c\n    'b',\n]\n")
    assert toml.array_items(original, "project", "dependencies") == ["a", "b"]
    assert toml.array_items(original, "project", "missing") is None
    assert toml.array_items(original, "other", "dependencies") is None

    scalar = lines('[project]\ndependencies = "a"\n')
    with pytest.raises(TaskFailed, match="not an array of strings"):
        toml.array_items(scalar, "project", "dependencies")


def test_set_array_preserves_the_original_layout():
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


def test_set_array_creates_the_table_and_key_when_missing():
    original = lines('[project]\nname = "demo"\n')
    new_lines, action = toml.set_array(original, "dependency-groups", "lint", ["ruff", "mypy"])
    assert action == "added"
    assert new_lines == lines('[project]\nname = "demo"\n[dependency-groups]\nlint = [\n    "ruff",\n    "mypy",\n]\n')


def test_scan_ignores_brackets_inside_strings_and_comments():
    original = lines("[a]\n# [not-a-header]\nkey = \"text with ] bracket\"\nother = '[x]'\n")
    new_lines, action = toml.set_key(original, "a", "new", "1")
    assert action == "added"
    assert new_lines == lines("[a]\n# [not-a-header]\nkey = \"text with ] bracket\"\nother = '[x]'\nnew = 1\n")


def test_scan_handles_a_triple_quoted_value_ending_in_quotes():
    # """say "hi"""" is valid TOML (value: say "hi"); the closing quote run must be consumed whole
    original = lines('[a]\nx = """say "hi""""\n[b]\ny = 1\n')
    new_lines, action = toml.set_key(original, "b", "z", "2")
    assert action == "added"
    assert new_lines == lines('[a]\nx = """say "hi""""\n[b]\ny = 1\nz = 2\n')
    assert toml.array_items(lines('[a]\ndeps = ["x]"]  # not a header\n'), "a", "deps") == ["x]"]


def test_split_lines_does_not_split_on_unicode_line_separators():
    text = "# comment with \u2028 and \u0085 raw\nkey = 1\n"
    assert toml.split_lines(text) == ["# comment with \u2028 and \u0085 raw\n", "key = 1\n"]
    new_lines, action = toml.set_key(toml.split_lines(text), None, "other", "2")
    assert action == "added"
    joined = "".join(new_lines)
    assert joined == "# comment with \u2028 and \u0085 raw\nkey = 1\nother = 2\n"
    tomllib.loads(joined)  # still valid TOML, the comment line was not phantom-split


def test_set_key_renders_crlf_files_with_crlf(tmp_path):
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


def test_set_key_resolves_dotted_options_into_existing_sub_tables():
    original = lines("[tool.uv]\nnative-tls = true\n\n[tool.uv.pip]\nno-cache = false\n")
    new_lines, action = toml.set_key(original, "tool.uv", "pip.no-cache", "true")
    assert action == "replaced"
    assert new_lines == lines("[tool.uv]\nnative-tls = true\n\n[tool.uv.pip]\nno-cache = true\n")

    # without the sub-table header the dotted key stays in [tool.uv]
    original = lines("[tool.uv]\nnative-tls = true\n")
    new_lines, action = toml.set_key(original, "tool.uv", "pip.no-cache", "true")
    assert action == "added"
    assert new_lines == lines("[tool.uv]\nnative-tls = true\npip.no-cache = true\n")


def test_set_key_edits_a_table_declared_with_top_level_dotted_keys():
    # adding a [tool.uv] header here would collide with the implicit tool.uv table
    original = lines('tool.uv.native-tls = true\n[project]\nname = "demo"\n')
    new_lines, action = toml.set_key(original, "tool.uv", "index-url", '"https://x"')
    assert action == "added"
    assert new_lines == lines('tool.uv.native-tls = true\ntool.uv.index-url = "https://x"\n[project]\nname = "demo"\n')
    assert tomllib.loads("".join(new_lines))["tool"]["uv"]["index-url"] == "https://x"


def test_apply_toml_key_does_not_rewrite_a_file_without_trailing_newline_when_unchanged(tmp_path):
    path = tmp_path / "uv.toml"
    path.write_text('index-url = "https://x"', encoding="utf-8")  # no trailing newline
    ctx = make_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table=None, key="index-url", value="https://x", sudo=False)

    assert result.changed is False
    assert result["toml_action"] == "unchanged"
    assert path.read_text(encoding="utf-8") == 'index-url = "https://x"'  # untouched


def test_apply_toml_key_sets_removes_and_reports(tmp_path):
    path = tmp_path / "uv.toml"
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


def test_apply_toml_key_creates_missing_files_and_tables(tmp_path):
    (tmp_path / "deep").mkdir()
    path = tmp_path / "deep" / "pyproject.toml"
    ctx = local_sudo_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table="tool.uv", key="native-tls", value=True, sudo=True)

    assert path.read_text() == "[tool.uv]\nnative-tls = true\n"
    assert call_data(result)["table"] == "tool.uv"
    assert call_data(result)["value"] is True


def test_apply_toml_key_dry_run_reports_without_writing(tmp_path):
    path = tmp_path / "uv.toml"
    ctx = make_context(tmp_path, dry_run=True)

    result = toml.apply_toml_key(ctx, path, table=None, key="key", value=["a", "b"], sudo=False)

    assert result.changed is True
    assert not path.exists()
    assert call_data(result)["value"] == ["a", "b"]


def test_remove_table_removes_the_header_and_its_span():
    original = lines("# top\n[a]\nx = 1\n# inside\n\n[b]\ny = 2\n")
    assert toml.remove_table(original, "a") == lines("# top\n[b]\ny = 2\n")
    assert toml.remove_table(original, "missing") == original
    # removing the last table leaves an empty document
    assert toml.remove_table(lines("[a]\nx = 1\n"), "a") == []
    # dotted table names address their header
    dotted = lines("[tool]\nname = 1\n[tool.uv]\nnative-tls = true\n[other]\nz = 3\n")
    assert toml.remove_table(dotted, "tool.uv") == lines("[tool]\nname = 1\n[other]\nz = 3\n")


def test_remove_table_removes_dotted_key_declared_tables():
    original = lines('tool.uv.native-tls = true\ntool.uv.index-url = "x"\n[project]\nname = "demo"\n')
    new_lines = toml.remove_table(original, "tool.uv")
    assert new_lines == lines('[project]\nname = "demo"\n')
    assert tomllib.loads("".join(new_lines)) == {"project": {"name": "demo"}}


def test_remove_table_rejects_array_of_tables():
    original = lines('[[tool.uv.index]]\nurl = "https://x"\n')
    with pytest.raises(TaskFailed, match="array-of-tables"):
        toml.remove_table(original, "tool.uv.index")


def test_apply_toml_key_removes_a_whole_table(tmp_path):
    path = tmp_path / "uv.toml"
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


def test_apply_toml_key_writes_arrays_with_set_array_layout(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\ndependencies = [\n    "a",\n]\n')
    ctx = make_context(tmp_path)

    result = toml.apply_toml_key(ctx, path, table="project", key="dependencies", value=["b", "c"], sudo=False)

    assert path.read_text() == '[project]\ndependencies = [\n    "b",\n    "c",\n]\n'
    assert result.changed is True
    assert call_data(result)["value"] == ["b", "c"]

    # an equal multi-line array is unchanged
    result = toml.apply_toml_key(ctx, path, table="project", key="dependencies", value=["b", "c"], sudo=False)
    assert result.changed is False
    assert call_data(result)["toml_action"] == "unchanged"


def test_array_edits_scan_the_file_once(tmp_path, monkeypatch):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\ndependencies = [\n    "a",\n]\n')
    ctx = make_context(tmp_path)
    calls = []
    original = toml._scan

    def counting(lines):
        calls.append(lines)
        return original(lines)

    monkeypatch.setattr(toml, "_scan", counting)
    toml.apply_toml_key(ctx, path, table="project", key="dependencies", value=["b"], sudo=False)

    assert len(calls) == 1


def test_apply_toml_key_create_false_rejects_a_missing_file(tmp_path):
    ctx = make_context(tmp_path)
    missing = tmp_path / "missing.toml"

    with pytest.raises(TaskFailed, match="does not exist and create=False"):
        toml.apply_toml_key(ctx, missing, table=None, key="a", value=1, sudo=False, create=False)
    # the create check runs before the state handling, mirroring apply_ini
    with pytest.raises(TaskFailed, match="does not exist and create=False"):
        toml.apply_toml_key(ctx, missing, table=None, key="a", value=None, sudo=False, create=False)

    assert not missing.exists()


def test_apply_toml_key_absent_on_a_missing_file_is_a_noop(tmp_path):
    ctx = make_context(tmp_path)
    missing = tmp_path / "missing.toml"

    result = toml.apply_toml_key(ctx, missing, table="a", key="b", value=None, sudo=False)

    assert result.changed is False
    assert not missing.exists()


def test_apply_toml_key_dry_run_on_a_missing_file_creates_nothing(tmp_path):
    ctx = make_context(tmp_path, dry_run=True)
    missing = tmp_path / "deep" / "uv.toml"

    result = toml.apply_toml_key(ctx, missing, table=None, key="a", value=1, sudo=False)

    assert result.changed is True
    assert not missing.exists()
    assert not (tmp_path / "deep").exists()
