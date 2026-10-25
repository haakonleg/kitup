from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from kitup.errors import SkipTask, TaskFailed
from kitup.registry import Bundle

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context


def task_for(name: str = "test"):
    bundle = Bundle("test")
    add_task(bundle, lambda ctx: None, name=name)
    return bundle.tasks[0]


def test_changed_and_notes_are_task_scoped(tmp_path):
    context = make_context(tmp_path)

    context.changed({"added": ["git"]}, "+git")
    context.note("note")
    assert [change.summary for change in context.changes] == ["+git"]
    assert context.notes == ["note"]

    context.begin_task(task_for())
    assert context.changes == []
    assert context.notes == []


def test_memo_is_cached_across_tasks(tmp_path):
    context = make_context(tmp_path)
    calls = []

    def load():
        calls.append(1)
        return "value"

    assert context.memo("key", load) == "value"
    context.begin_task(task_for())
    assert context.memo("key", load) == "value"
    assert len(calls) == 1


def test_set_stores_values_for_later_tasks(tmp_path):
    context = make_context(tmp_path)
    context.set("key", {"value": 1})
    context.begin_task(task_for())

    assert context.get("key") == {"value": 1}

    with pytest.raises(TaskFailed, match="input key must not be empty"):
        context.set("  ", "value")


def test_input_shares_store_with_memo_and_survives_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "provided")
    context = make_context(tmp_path)
    calls = []

    def load():
        calls.append(1)
        return "loaded"

    context.input("key")
    context.begin_task(task_for())

    assert context.memo("key", load) == "provided"
    assert context.get("key") == "provided"
    assert calls == []


def test_input_enters_interactive_output_and_clears_prompt(tmp_path, monkeypatch):
    calls = []
    prompts = []
    cleared = []

    @contextmanager
    def interactive():
        calls.append("begin")
        try:
            yield
        finally:
            calls.append("end")

    def fake_input(prompt):
        prompts.append(prompt)
        return "provided"

    def clear_input(prompt, value):
        cleared.append((prompt, value))

    monkeypatch.setattr("builtins.input", fake_input)
    output = SimpleNamespace(interactive=interactive, clear_input=clear_input)
    context = make_context(tmp_path, output=output)

    assert context.input(prompt="Value: ") == "provided"
    assert prompts == ["Value: "]
    assert cleared == [("Value: ", "provided")]
    assert calls == ["begin", "end"]


def test_get_returns_default_and_raises_for_missing_key(tmp_path):
    context = make_context(tmp_path)

    assert context.get("missing", "fallback") == "fallback"

    with pytest.raises(TaskFailed, match="no stored value for key: missing"):
        context.get("missing")


def test_skip_uses_reason_or_none(tmp_path):
    context = make_context(tmp_path)
    with pytest.raises(SkipTask, match="not this machine"):
        context.skip("not this machine")
    with pytest.raises(SkipTask) as exc:
        context.skip()
    assert exc.value.reason == ""


def test_creates_guard_skips_command_and_resolves_cwd(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, command=command)
    marker = tmp_path / "marker"
    marker.write_text("exists")
    work = tmp_path / "work"
    work.mkdir()
    (work / "marker").write_text("exists")

    context.run(["touch", str(marker)], creates=marker)
    context.run(["touch", "marker"], creates="marker", cwd=work)

    assert command.calls == []
    assert context.changes == []


def test_unless_guard_is_evaluated_even_in_dry_run(tmp_path):
    command = FakeCommandRunner(returncode=0)
    context = make_context(tmp_path, dry_run=True, command=command)

    context.run(["install", "thing"], unless=["test", "-e", "/thing"])

    assert command.calls == [(["test", "-e", "/thing"], {
        "cwd": None,
        "env": None,
        "sudo": False,
        "timeout": 600,
        "check": False,
        "refresh_sudo": False,
    })]
    assert context.changes == []


def test_dry_run_records_but_does_not_execute_mutating_command(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, dry_run=True, command=command)

    context.run(["dnf5", "install", "-y", "git"])

    assert command.calls == []
    assert len(context.changes) == 1
    assert context.changes[0].summary == "would run: dnf5 install -y git"
    assert context.changes[0].data["reason"] == "would run unconditionally"


def test_command_runs_and_records_change(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, command=command)

    result = context.run(["dnf5", "install", "-y", "git"])

    assert result.returncode == 0
    assert command.calls[0][0] == ["dnf5", "install", "-y", "git"]
    assert len(context.changes) == 1
    assert context.changes[0].data == {"argv": ["dnf5", "install", "-y", "git"]}


def test_single_string_is_one_argument(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, command=command)

    context.run("dnf5 install -y git")
    context.probe("rpm -q git")
    context.run("install", unless="test -e /thing")

    assert command.calls[0][0] == ["dnf5 install -y git"]
    assert command.calls[1][0] == ["rpm -q git"]
    assert command.calls[2][0] == ["test -e /thing"]


def test_non_mutating_run_does_not_record_change(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, command=command)

    context.run(["rpm", "-q", "git"], mutates=False)

    assert command.calls[0][0] == ["rpm", "-q", "git"]
    assert context.changes == []


def test_probe_returns_nonzero_result_instead_of_failing(tmp_path):
    command = FakeCommandRunner(returncode=1)
    context = make_context(tmp_path, command=command)

    result = context.probe(["rpm", "-q", "git"])

    assert result.returncode == 1
    assert command.calls[0][1]["check"] is False


def test_sudo_is_refreshed_once_per_run(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, command=command, sudo=sudo)
    context.begin_task(task_for("Install packages"))

    context.run(["first"], sudo=True)
    context.run(["second"], sudo=True)

    assert sudo.refresh_count == 1
    assert sudo.reasons == ['task "Install packages" requires sudo privileges']
    assert command.calls[0][1]["sudo"] is True
    assert command.calls[1][1]["sudo"] is True


def test_sudo_refresh_enters_interactive_output(tmp_path):
    calls = []

    @contextmanager
    def interactive():
        calls.append("begin")
        try:
            yield
        finally:
            calls.append("end")

    context = make_context(tmp_path, output=SimpleNamespace(interactive=interactive))

    context.ensure_sudo()

    assert calls == ["begin", "end"]


def test_sudo_is_revalidated_when_credentials_expire(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, command=command, sudo=sudo)
    context.begin_task(task_for("Install packages"))

    context.run(["first"], sudo=True)
    sudo.valid = False
    context.run(["second"], sudo=True)

    expected = 'task "Install packages" requires sudo privileges'
    assert sudo.refresh_count == 2
    assert sudo.reasons == [expected, expected]


def test_dry_run_mutating_command_requests_sudo_but_does_not_execute(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, dry_run=True, command=command, sudo=sudo)
    context.begin_task(task_for())

    context.run(["install"], sudo=True)

    assert sudo.refresh_count == 1
    assert command.calls == []
    assert len(context.changes) == 1


def test_guard_can_run_against_nonzero_result(tmp_path):
    command = FakeCommandRunner()
    command.responses = [lambda argv: 1, lambda argv: 0]
    context = make_context(tmp_path, command=command)

    result = context.run(["install"], unless=["test", "-e", "/thing"])

    assert len(command.calls) == 2
    assert result.returncode == 0
    assert len(context.changes) == 1
