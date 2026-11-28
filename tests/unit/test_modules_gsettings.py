import pytest

from kitup.errors import TaskFailed
from kitup.modules import gsettings

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response


def responses(canonical: str, current: str, *, scratch_error: str | None = None, real_set: bool = False):
    """Script the scratch validate/get pair and the real read (and optional set)."""
    if scratch_error is not None:
        return [response(1, stderr=scratch_error)]
    items = [response(0), response(0, f"{canonical}\n"), response(0, f"{current}\n")]
    if real_set:
        items.append(response(0))
    return items


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
        (None, "nothing", "@ms nothing"),
        ([], "[]", "@as []"),
        (["a", "b"], "['a', 'b']", "['a', 'b']"),
        (("xkb", "us"), "('xkb', 'us')", "('xkb', 'us')"),
        (("only",), "('only',)", "('only',)"),
        (b"", "[]", "@ay []"),
        (b"abc", "[0x61, 0x62, 0x63]", "[byte 0x61, 0x62, 0x63]"),
        (b"abc\x00", "b'abc'", "b'abc'"),
        (b"a'b\x00", "b'a\\'b'", 'b"a\'b"'),
        (b"a\x00b", "[0x61, 0x00, 0x62]", "[byte 0x61, 0x00, 0x62]"),
        ({"a": "b"}, "{'a': 'b'}", "{'a': 'b'}"),
        (gsettings.variant("<42>"), "<42>", "<42>"),
        ({"a": gsettings.variant("<'b'>")}, "{'a': <'b'>}", "{'a': <'b'>}"),
    ],
)
def test_native_values_are_formatted_and_idempotent(tmp_path, value, text, canonical):
    command = FakeCommandRunner()
    command.responses = responses(canonical, canonical)
    ctx = make_context(tmp_path, command=command)

    result = gsettings.key("org.example", "example", value)

    assert result.changed is False
    assert ctx.changes == []
    assert argvs(command) == [
        ["gsettings", "set", "org.example", "example", text],
        ["gsettings", "get", "org.example", "example"],
        ["gsettings", "get", "org.example", "example"],
    ]
    assert call_data(result) == {"schema": "org.example", "key": "example", "value": value}


def test_changed_set_is_idempotent_on_rerun(tmp_path):
    command = FakeCommandRunner()
    command.responses = responses("byte 0x05", "byte 0x00", real_set=True) + responses("byte 0x05", "byte 0x05")
    ctx = make_context(tmp_path, command=command)

    first = gsettings.key("org.example", "count", 5)
    second = gsettings.key("org.example", "count", 5)

    assert first.changed is True
    assert first.summary == "set org.example count=5"
    assert second.changed is False
    assert second.summary == "ensure org.example count=5"
    assert len(ctx.changes) == 1
    real_sets = [argv for argv, kwargs in command.calls if argv[1] == "set" and kwargs["env"] is None]
    assert real_sets == [["gsettings", "set", "org.example", "count", "5"]]
    assert command.calls[-1][1]["sudo"] is False


def test_scratch_backend_uses_a_shared_isolated_keyfile_home(tmp_path):
    command = FakeCommandRunner()
    command.responses = responses("'dark'", "'dark'")
    make_context(tmp_path, command=command)

    gsettings.key("org.example", "theme", "dark")

    written, read, current = command.calls
    assert written[1]["env"]["GSETTINGS_BACKEND"] == "keyfile"
    assert written[1]["env"]["XDG_CONFIG_HOME"]
    assert read[1]["env"] == written[1]["env"]
    assert current[1]["env"] is None
    assert written[1]["sudo"] is False
    assert read[1]["sudo"] is False


def test_gsettings_refuses_root(tmp_path):
    make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="must run as the target user"):
        gsettings.key("org.example", "theme", "dark")


def test_dry_run_does_not_set(tmp_path):
    command = FakeCommandRunner()
    command.responses = responses("'dark'", "'light'")
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = gsettings.key("org.example", "theme", "dark")

    assert result.changed is True
    assert len(command.calls) == 3
    assert [argv for argv, kwargs in command.calls if argv[1] == "set" and kwargs["env"] is None] == []
    assert len(ctx.changes) == 1


def test_absent_resets_the_key_or_noops_at_the_schema_default(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "'dark'\n"),
        response(0, "'default'\n"),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    gsettings.key("org.example", "theme", state="absent")

    assert argvs(command) == [
        ["gsettings", "get", "org.example", "theme"],
        ["gsettings", "get", "org.example", "theme"],
        ["gsettings", "reset", "org.example", "theme"],
    ]
    assert command.calls[1][1]["env"] == {"GSETTINGS_BACKEND": "memory"}
    assert command.calls[2][1]["sudo"] is False
    assert call_data(ctx.changes[0]) == {
        "schema": "org.example",
        "key": "theme",
    }
    assert ctx.changes[0].action == "gsettings.key"
    assert ctx.changes[0].summary == "reset org.example theme"

    command = FakeCommandRunner()
    command.responses = [
        response(0, "'default'\n"),
        response(0, "'default'\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    gsettings.key("org.example", "theme", state="absent")

    assert len(command.calls) == 2
    assert ctx.changes == []


@pytest.mark.parametrize(
    ("value", "stderr", "match"),
    [
        (["a", 1], "can not parse as value of type 's':", "can not parse"),
        (300, "number out of range for type 'y':", "number out of range"),
        ("not-a-path", "not a valid object path:", "not a valid object path"),
        (gsettings.variant("42"), "can not parse as value of type 'v':", "can not parse"),
    ],
)
def test_invalid_values_fail_before_recording_a_change(tmp_path, value, stderr, match):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr=stderr)]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match=match):
        gsettings.key("org.example", "example", value)

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_invalid_arguments_are_rejected(tmp_path):
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="value is required"):
        gsettings.key("org.example", "theme")
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        gsettings.key("org.example", "theme", "dark", state="absent")
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        gsettings.key("org.example", "theme", None, state="absent")
    with pytest.raises(TaskFailed, match="invalid gsettings state"):
        gsettings.key("org.example", "theme", state="unknown")
    with pytest.raises(TaskFailed, match="gsettings schema must not be empty"):
        gsettings.key("  ", "theme", "dark")
    with pytest.raises(TaskFailed, match="gsettings key must not be empty"):
        gsettings.key("org.example", "  ", "dark")


def test_unrepresentable_values_are_rejected_before_running_gsettings(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="must not contain NUL"):
        gsettings.key("org.example", "theme", "a\x00b")
    with pytest.raises(TaskFailed, match="not UTF-8 encodable"):
        gsettings.key("org.example", "theme", "\ud800")
    with pytest.raises(TaskFailed, match="unsupported gsettings value type: object"):
        gsettings.key("org.example", "example", object())

    assert command.calls == []


def test_variant_text_is_validated():
    with pytest.raises(TaskFailed, match="variant text must not be empty"):
        gsettings.variant("  ")
    with pytest.raises(TaskFailed, match="must not contain NUL"):
        gsettings.variant("<42\x00>")
