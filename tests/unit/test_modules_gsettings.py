import pytest

from kitup.errors import TaskFailed
from kitup.modules import gsettings

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response


def test_matching_and_all_value_types_use_gvariant_syntax(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'dark'\n")]
    ctx = make_context(tmp_path, command=command)

    gsettings.key("org.example", "theme", "dark")

    assert argvs(command) == [["gsettings", "get", "org.example", "theme"]]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [response(0, "'light'\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    gsettings.key("org.example", "theme", "it's dark")

    assert command.calls[1][0] == [
        "gsettings",
        "set",
        "org.example",
        "theme",
        "'it\\'s dark'",
    ]
    assert command.calls[1][1]["sudo"] is False
    assert call_data(ctx.changes[0])["value"] == "it's dark"

    command = FakeCommandRunner()
    command.responses = [
        response(0, "false\n"),
        response(0),
        response(0, "0\n"),
        response(0),
        response(0, "[]\n"),
        response(0),
        response(0, "[]\n"),
        response(0),
    ]
    make_context(tmp_path, command=command)

    gsettings.key("org.example", "enabled", value=True)
    gsettings.key("org.example", "count", 3)
    gsettings.key("org.example", "items", ["a", 2])
    gsettings.key("org.example", "sources", [("xkb", "us")])

    assert command.calls[1][0][-1] == "true"
    assert command.calls[3][0][-1] == "3"
    assert command.calls[5][0][-1] == "['a', 2]"
    assert command.calls[7][0][-1] == "[('xkb', 'us')]"


def test_gsettings_refuses_root(tmp_path):
    make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="must run as the target user"):
        gsettings.key("org.example", "theme", "dark")


def test_dry_run_does_not_set(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "'light'\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    gsettings.key("org.example", "theme", "dark")

    assert len(command.calls) == 1
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


def test_invalid_arguments_are_rejected(tmp_path):
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="value is required"):
        gsettings.key("org.example", "theme")
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        gsettings.key("org.example", "theme", "dark", state="absent")
    with pytest.raises(TaskFailed, match="invalid gsettings state"):
        gsettings.key("org.example", "theme", state="unknown")
    with pytest.raises(TaskFailed, match="gsettings schema must not be empty"):
        gsettings.key("  ", "theme", "dark")
    with pytest.raises(TaskFailed, match="gsettings key must not be empty"):
        gsettings.key("org.example", "  ", "dark")
