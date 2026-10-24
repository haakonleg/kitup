from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import gsettings

from .helpers import FakeCommandRunner, FakeSudo, completed, make_context


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


def test_matching_value_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'dark'\n")]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "theme", "dark")

    assert [call[0] for call in command.calls] == [
        ["gsettings", "get", "org.example", "theme"]
    ]
    assert ctx.changes == []


def test_string_value_is_set_with_gvariant_quoting(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'light'\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "theme", "it's dark")

    assert command.calls[1][0] == [
        "gsettings",
        "set",
        "org.example",
        "theme",
        "'it\\'s dark'",
    ]
    assert command.calls[1][1]["sudo"] is False
    assert ctx.changes[0].data["value"] == "it's dark"


def test_bool_int_and_list_values_are_formatted(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "false\n"),
        response(0),
        response(0, "0\n"),
        response(0),
        response(0, "[]\n"),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "enabled", True)
    gsettings.key(ctx, "org.example", "count", 3)
    gsettings.key(ctx, "org.example", "items", ["a", 2])

    assert command.calls[1][0][-1] == "true"
    assert command.calls[3][0][-1] == "3"
    assert command.calls[5][0][-1] == "['a', 2]"


def test_tuple_values_use_gvariant_tuple_syntax(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "[]\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "sources", [("xkb", "us")])

    assert command.calls[1][0][-1] == "[('xkb', 'us')]"


def test_type_annotation_is_ignored_when_comparing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "@as []\n")]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "items", [])

    assert [call[0] for call in command.calls] == [
        ["gsettings", "get", "org.example", "items"]
    ]
    assert ctx.changes == []


def test_control_characters_are_escaped(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'old'\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "text", "a\nb\tc")

    assert command.calls[1][0][-1] == "'a\\nb\\tc'"


def test_empty_string_value_is_allowed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'old'\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "text", "")

    assert command.calls[1][0][-1] == "''"
    assert ctx.changes[0].data["value"] == ""


def test_schema_error_is_actionable(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="schema not found\n")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="org.example theme: schema not found"):
        gsettings.key(ctx, "org.example", "theme", "dark")


def test_gsettings_refuses_root(tmp_path):
    ctx = make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="must run as the target user"):
        gsettings.key(ctx, "org.example", "theme", "dark")


def test_dry_run_does_not_set(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'light'\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    gsettings.key(ctx, "org.example", "theme", "dark")

    assert len(command.calls) == 1
    assert len(ctx.changes) == 1


def test_absent_resets_the_key(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "'dark'\n"),
        response(0, "'default'\n"),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "theme", state="absent")

    assert [call[0] for call in command.calls] == [
        ["gsettings", "get", "org.example", "theme"],
        ["gsettings", "get", "org.example", "theme"],
        ["gsettings", "reset", "org.example", "theme"],
    ]
    assert command.calls[1][1]["env"] == {"GSETTINGS_BACKEND": "memory"}
    assert command.calls[2][1]["sudo"] is False
    assert ctx.changes[0].data == {
        "schema": "org.example",
        "key": "theme",
        "state": "absent",
    }
    assert ctx.changes[0].summary == "reset org.example theme"


def test_absent_is_a_noop_at_the_schema_default(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "'default'\n"),
        response(0, "'default'\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "theme", state="absent")

    assert len(command.calls) == 2
    assert ctx.changes == []


def test_absent_resets_when_default_lookup_fails(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "'dark'\n"),
        response(1),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    gsettings.key(ctx, "org.example", "theme", state="absent")

    assert command.calls[-1][0] == ["gsettings", "reset", "org.example", "theme"]
    assert len(ctx.changes) == 1


def test_present_requires_a_value(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="value is required"):
        gsettings.key(ctx, "org.example", "theme")


def test_invalid_state_is_an_error(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid gsettings state"):
        gsettings.key(ctx, "org.example", "theme", state="unknown")


def test_empty_schema_and_key_are_errors(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="gsettings schema must not be empty"):
        gsettings.key(ctx, "  ", "theme", "dark")

    with pytest.raises(TaskFailed, match="gsettings key must not be empty"):
        gsettings.key(ctx, "org.example", "  ", "dark")
