import pytest

from kitup.errors import TaskFailed
from kitup.modules import dconf
from kitup.registry import Bundle

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response


@pytest.mark.parametrize(
    ("value", "text", "canonical"),
    [
        ("dark", "'dark'", "'dark'"),
        ("it's dark", "'it\\'s dark'", '"it\'s dark"'),
        ("a\nb", "'a\\nb'", "'a\\nb'"),
        ("a\x01b", "'a\x01b'", "'a\\u0001b'"),
        (True, "true", "true"),
        (3, "3", "3"),
        (0.1, "0.1", "0.10000000000000001"),
        (["a", "b"], "['a', 'b']", "['a', 'b']"),
        (("xkb", "us"), "('xkb', 'us')", "('xkb', 'us')"),
        (("only",), "('only',)", "('only',)"),
        (b"abc", "[0x61, 0x62, 0x63]", "[97, 98, 99]"),
        ({"a": "b"}, "{'a': 'b'}", "{'a': 'b'}"),
        (dconf.variant("<42>"), "<42>", "<42>"),
        ({"a": dconf.variant("<'b'>")}, "{'a': <'b'>}", "{'a': <'b'>}"),
    ],
)
def test_native_values_are_formatted_and_idempotent(tmp_path, value, text, canonical):
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0, f"[/]\norg/example/a={canonical}\n"),
        response(0, f"{canonical}\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    result = dconf.write("/org/example/a", value)

    assert result.changed is False
    assert ctx.changes == []
    assert argvs(command) == [
        ["dbus-run-session", "--", "dconf", "load", "/"],
        ["dconf", "dump", "/"],
        ["dconf", "read", "/org/example/a"],
    ]
    assert command.calls[0][1]["stdin"] == f"[kitup]\n[/]\norg/example/a={text}\n"
    assert call_data(result) == {"paths": ["/org/example/a"], "values": {"/org/example/a": value}}


def test_changed_set_is_idempotent_on_rerun(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0, "[/]\norg/example/count=5\n"),
        response(0, "0\n"),
        response(0),
        response(0),
        response(0, "[/]\norg/example/count=5\n"),
        response(0, "5\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    first = dconf.write("/org/example/count", 5)
    second = dconf.write("/org/example/count", 5)

    assert first.changed is True
    assert first.summary == "set /org/example/count=5"
    assert second.changed is False
    assert second.summary == "ensure /org/example/count=5"
    assert len(ctx.changes) == 1
    assert [argv for argv in argvs(command) if argv[:2] == ["dconf", "write"]] == [
        ["dconf", "write", "/org/example/count", "5"]
    ]


def test_scratch_session_uses_an_isolated_home_and_a_busless_read(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0, "[/]\norg/example/theme='dark'\n"), response(0, "'dark'\n")]
    make_context(tmp_path, command=command)

    dconf.write("/org/example/theme", "dark")

    loaded, dumped, current = command.calls
    assert loaded[1]["env"]["XDG_CONFIG_HOME"] != loaded[1]["env"]["XDG_RUNTIME_DIR"]
    assert loaded[1]["env"]["DCONF_PROFILE"]
    assert loaded[1]["stdin"] == "[kitup]\n[/]\norg/example/theme='dark'\n"
    assert loaded[1]["sudo"] is False
    assert dumped[1]["env"] == loaded[1]["env"]
    assert dumped[1]["sudo"] is False
    assert current[1]["env"] is None


def test_scratch_read_back_falls_back_to_a_session(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(1, stderr="no session bus"),
        response(0, "[/]\norg/example/a=1\n"),
        response(0, "1\n"),
    ]
    make_context(tmp_path, command=command)

    result = dconf.write("/org/example/a", 1)

    assert result.changed is False
    assert argvs(command)[1:3] == [
        ["dconf", "dump", "/"],
        ["dbus-run-session", "--", "dconf", "dump", "/"],
    ]


def test_missing_dbus_run_session_fails_clearly(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(127, stderr="could not run dbus-run-session -- dconf load /: [Errno 2] No such file or directory")
    ]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="dbus-run-session"):
        dconf.write("/org/example/a", 1)

    assert ctx.changes == []


def test_dconf_refuses_root(tmp_path):
    make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="must run as the target user"):
        dconf.write("/org/example/a", 1)
    with pytest.raises(TaskFailed, match="must run as the target user"):
        dconf.load("/org/example/", "[/]\na=1\n")


def test_dry_run_does_not_write(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0, "[/]\norg/example/a=1\n"), response(0, "2\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = dconf.write("/org/example/a", 1)

    assert result.changed is True
    assert len(command.calls) == 3
    assert all(argv[:2] != ["dconf", "write"] for argv in argvs(command))
    assert len(ctx.changes) == 1


def test_absent_resets_the_key_or_noops_at_the_default(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'dark'\n"), response(0, "'default'\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    result = dconf.write("/org/example/theme", state="absent")

    assert result.changed is True
    assert argvs(command) == [
        ["dconf", "read", "/org/example/theme"],
        ["dconf", "read", "-d", "/org/example/theme"],
        ["dconf", "reset", "/org/example/theme"],
    ]
    assert call_data(ctx.changes[0]) == {"paths": ["/org/example/theme"]}
    assert ctx.changes[0].action == "dconf.write"
    assert ctx.changes[0].summary == "reset /org/example/theme"

    command = FakeCommandRunner()
    command.responses = [response(0, "'default'\n"), response(0, "'default'\n")]
    ctx = make_context(tmp_path, command=command)

    result = dconf.write("/org/example/theme", state="absent")

    assert result.changed is False
    assert result.summary == "ensure /org/example/theme is reset"
    assert len(command.calls) == 2
    assert ctx.changes == []


@pytest.mark.parametrize("value", [dconf.variant("nothing"), []])
def test_invalid_values_fail_before_recording_a_change(tmp_path, value):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="error: [kitup]: org/example/a: invalid value: nothing:")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="invalid value"):
        dconf.write("/org/example/a", value)

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_invalid_arguments_are_rejected(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="value is required"):
        dconf.write("/org/example/a")
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        dconf.write("/org/example/a", 1, state="absent")
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        dconf.write("/org/example/a", None, state="absent")
    with pytest.raises(TaskFailed, match="invalid dconf state"):
        dconf.write("/org/example/a", 1, state="unknown")
    with pytest.raises(TaskFailed, match="mapping does not take a separate value"):
        dconf.write({"/org/example/a": 1}, 2)
    with pytest.raises(TaskFailed, match="'absent' requires a single key"):
        dconf.write({"/org/example/a": 1}, state="absent")
    with pytest.raises(TaskFailed, match="keys must not be empty"):
        dconf.write({})
    with pytest.raises(TaskFailed, match="key must be a path or a mapping"):
        dconf.write(5)
    with pytest.raises(TaskFailed, match="key paths must be strings"):
        dconf.write({1: "x"})
    assert command.calls == []


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/", "must not end with a slash"),
        ("org/example/a", "must begin with a slash"),
        ("/org/example/a/", "must not end with a slash"),
        ("/org//example", "two consecutive slashes"),
        ("/org/example/a=b", "must not contain"),
        ("/org/example/[a]", "must not contain"),
        ("/org/example/\ta", "control characters"),
        ("/#comment", "start with '#' or whitespace"),
        ("/org/example/a ", "end with whitespace"),
        ("/ ", "start with '#' or whitespace"),
    ],
)
def test_invalid_key_paths_are_rejected(tmp_path, path, message):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match=message):
        dconf.write(path, 1)

    assert command.calls == []


@pytest.mark.parametrize(
    ("directory", "message"),
    [
        ("org/example/", "must begin with a slash"),
        ("/org/example", "must end with a slash"),
        ("/org//example/", "two consecutive slashes"),
    ],
)
def test_invalid_directories_are_rejected(tmp_path, directory, message):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match=message):
        dconf.load(directory, "[/]\na=1\n")
    with pytest.raises(TaskFailed, match=message):
        dconf.dump(directory)

    assert command.calls == []


def test_unrepresentable_values_are_rejected_before_running_dconf(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="must not contain NUL"):
        dconf.write("/org/example/a", "a\x00b")
    with pytest.raises(TaskFailed, match="not UTF-8 encodable"):
        dconf.write("/org/example/a", "\ud800")
    with pytest.raises(TaskFailed, match="unsupported dconf value type: object"):
        dconf.write("/org/example/a", object())

    assert command.calls == []


def test_variant_text_is_validated():
    with pytest.raises(TaskFailed, match="variant text must not be empty"):
        dconf.variant("  ")
    with pytest.raises(TaskFailed, match="must not contain NUL"):
        dconf.variant("<42\x00>")


def test_read_returns_values_for_one_or_many_keys(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'dark'\n"), response(0), response(0, "5\n")]
    ctx = make_context(tmp_path, command=command)

    assert dconf.read("/org/example/theme") == "'dark'"
    assert dconf.read(["/org/example/unset", "/org/example/count"]) == {
        "/org/example/unset": None,
        "/org/example/count": "5",
    }
    assert ctx.changes == []
    assert argvs(command) == [
        ["dconf", "read", "/org/example/theme"],
        ["dconf", "read", "/org/example/unset"],
        ["dconf", "read", "/org/example/count"],
    ]


def test_read_requires_at_least_one_key(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="keys must not be empty"):
        dconf.read([])
    with pytest.raises(TaskFailed, match="key paths must be strings"):
        dconf.read([1])

    assert command.calls == []


def test_read_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(2, stderr="error: invalid key")]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="dconf read /org/example/a"):
        dconf.read("/org/example/a")


def test_dump_returns_keyfile_text(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "[/]\na=1\n")]
    make_context(tmp_path, command=command)

    assert dconf.dump("/org/example/") == "[/]\na=1\n"
    assert argvs(command) == [["dconf", "dump", "/org/example/"]]


def test_dump_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="error: no database")]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="dconf dump /org/example/"):
        dconf.dump("/org/example/")


def test_load_is_idempotent_and_reports_only_listed_keys(tmp_path):
    content = "[/]\na=1\nb='x'\n"
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0, "[/]\na=1\nb='x'\n"),
        response(0, "1\n"),
        response(0, "'x'\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    result = dconf.load("/org/example/", content)

    assert result.changed is False
    assert result.summary == "ensure 2 keys under /org/example/"
    assert call_data(result) == {"dir": "/org/example/", "paths": ["/org/example/a", "/org/example/b"]}
    assert ctx.changes == []
    assert argvs(command) == [
        ["dbus-run-session", "--", "dconf", "load", "/org/example/"],
        ["dconf", "dump", "/org/example/"],
        ["dconf", "read", "/org/example/a"],
        ["dconf", "read", "/org/example/b"],
    ]
    assert command.calls[0][1]["stdin"] == content


def test_changed_load_writes_the_original_content(tmp_path):
    content = "[/]\na=1\n"
    command = FakeCommandRunner()
    command.responses = [response(0), response(0, "[/]\na=1\n"), response(0, "2\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    result = dconf.load("/org/example/", content)

    assert result.changed is True
    assert result.summary == "load 1 key under /org/example/"
    assert argvs(command)[-1] == ["dconf", "load", "/org/example/"]
    assert command.calls[-1][1]["stdin"] == content
    assert command.calls[-1][1]["sudo"] is False
    assert len(ctx.changes) == 1


def test_load_dry_run_does_not_write(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0, "[/]\na=1\n"), response(0, "2\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = dconf.load("/org/example/", "[/]\na=1\n")

    assert result.changed is True
    assert len(command.calls) == 3
    assert len(ctx.changes) == 1


def test_load_empty_content_is_a_no_op(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    result = dconf.load("/org/example/", "")

    assert result.changed is False
    assert result.summary == "ensure 0 keys under /org/example/"
    assert call_data(result) == {"dir": "/org/example/", "paths": []}
    assert len(command.calls) == 2
    assert ctx.changes == []


def test_invalid_load_content_fails_before_recording_a_change(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="error: [/]: a: invalid value: nothing:")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="invalid value"):
        dconf.load("/org/example/", "[/]\na=nothing\n")

    assert len(command.calls) == 1
    assert ctx.changes == []


def _bundle_context(tmp_path, command):
    bundle_dir = tmp_path / "bundles" / "base"
    bundle_dir.mkdir(parents=True)
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda: None)
    ctx = make_context(tmp_path, command=command)
    ctx.begin_task(bundle.tasks[0])
    return ctx, bundle_dir


def test_load_reads_a_source_file_from_the_merged_files_view(tmp_path):
    content = "[/]\na=1\n"
    command = FakeCommandRunner()
    command.responses = [response(0), response(0, content), response(0, "1\n")]
    _, bundle_dir = _bundle_context(tmp_path, command)
    (bundle_dir / "files").mkdir()
    (bundle_dir / "files" / "dconf.ini").write_text("[/]\na=2\n", encoding="utf-8")
    overlay = tmp_path / "files" / "base" / "dconf.ini"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(content, encoding="utf-8")

    result = dconf.load("/org/example/", source="dconf.ini")

    assert result.changed is False
    assert call_data(result) == {"dir": "/org/example/", "src": str(overlay), "paths": ["/org/example/a"]}
    assert command.calls[0][1]["stdin"] == content


def test_load_resolves_other_relative_sources_against_the_bundle(tmp_path):
    content = "[/]\na=1\n"
    command = FakeCommandRunner()
    command.responses = [response(0), response(0, content), response(0, "1\n")]
    _, bundle_dir = _bundle_context(tmp_path, command)
    source = bundle_dir / "keyfiles" / "dconf.ini"
    source.parent.mkdir()
    source.write_text(content, encoding="utf-8")

    result = dconf.load("/org/example/", source="keyfiles/dconf.ini")

    assert call_data(result)["src"] == str(source)
    assert command.calls[0][1]["stdin"] == content


def test_load_rejects_content_and_source_together(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="either content or source"):
        dconf.load("/org/example/", "[/]\na=1\n", source="dconf.ini")

    assert command.calls == []


def test_load_requires_content_or_source(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="requires content or source"):
        dconf.load("/org/example/")

    assert command.calls == []


def test_load_source_read_errors_are_reported(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not read dconf source"):
        dconf.load("/org/example/", source=tmp_path / "missing.ini")

    invalid = tmp_path / "invalid.ini"
    invalid.write_bytes(b"\xff")
    with pytest.raises(TaskFailed, match="not UTF-8 text"):
        dconf.load("/org/example/", source=invalid)

    assert command.calls == []


def test_multiple_keys_are_set_in_one_call(tmp_path):
    values = {"/org/example/a": 1, "/org/example/b": "x"}
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0, "[/]\norg/example/a=1\norg/example/b='x'\n"),
        response(0, "2\n"),
        response(0, "2\n"),
        response(0),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    result = dconf.write(values)

    assert result.changed is True
    assert result.summary == "set /org/example/a=1; set /org/example/b='x'"
    assert call_data(result) == {"paths": ["/org/example/a", "/org/example/b"], "values": values}
    assert command.calls[0][1]["stdin"] == "[kitup]\n[/]\norg/example/a=1\norg/example/b='x'\n"
    assert [argv for argv in argvs(command) if argv[:2] == ["dconf", "write"]] == [
        ["dconf", "write", "/org/example/a", "1"],
        ["dconf", "write", "/org/example/b", "'x'"],
    ]
    assert len(ctx.changes) == 1


def test_multiple_keys_summarize_changed_and_unchanged_entries(tmp_path):
    values = {"/org/example/a": 1, "/org/example/b": 2}
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0, "[/]\norg/example/a=1\norg/example/b=2\n"),
        response(0, "1\n"),
        response(0, "3\n"),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    result = dconf.write(values)

    assert result.changed is True
    assert result.summary == "ensure /org/example/a=1; set /org/example/b=2"
    assert [argv for argv in argvs(command) if argv[:2] == ["dconf", "write"]] == [
        ["dconf", "write", "/org/example/b", "2"]
    ]
    assert len(ctx.changes) == 1


def test_load_canonicalizes_groups_relative_to_the_directory(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0, "[sub]\na=1\n"), response(0, "1\n")]
    ctx = make_context(tmp_path, command=command)

    result = dconf.load("/org/example/", "[sub]\na=1\n")

    assert result.changed is False
    assert call_data(result) == {"dir": "/org/example/", "paths": ["/org/example/sub/a"]}
    assert ctx.changes == []
