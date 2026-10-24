from __future__ import annotations

import pytest

from kitup.errors import SkipTask, TaskFailed
from kitup.registry import Bundle

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context


def task_for(needs_sudo: bool = False):
    bundle = Bundle("test")
    add_task(bundle, lambda ctx: None, needs_sudo=needs_sudo)
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


def test_input_stores_and_returns_value(tmp_path, monkeypatch):
    prompts = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "answer",
    )
    context = make_context(tmp_path)

    assert context.input("username", "Username: ") == "answer"
    assert context.get("username") == "answer"
    assert prompts == ["Username: "]


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


def test_input_allows_empty_value(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    context = make_context(tmp_path)

    assert context.input("key") == ""
    assert context.get("key") == ""
    assert context.get("key", "fallback") == ""


def test_get_returns_default_and_raises_for_missing_key(tmp_path):
    context = make_context(tmp_path)

    assert context.get("missing", "fallback") == "fallback"

    with pytest.raises(TaskFailed, match="no stored value for key: missing"):
        context.get("missing")


def test_input_and_get_reject_empty_keys(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "value")
    context = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="input key must not be empty"):
        context.input("  ")

    with pytest.raises(TaskFailed, match="input key must not be empty"):
        context.get("")


def test_input_without_key_only_returns_value(tmp_path, monkeypatch):
    prompts = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "answer",
    )
    context = make_context(tmp_path)

    assert context.input(prompt="Continue? ") == "answer"
    assert context.input(None, "Again? ") == "answer"
    assert prompts == ["Continue? ", "Again? "]
    assert context.get("answer", "missing") == "missing"


def test_input_eof_is_a_task_failure(tmp_path, monkeypatch):
    def no_input(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)
    context = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="no input received for key: key"):
        context.input("key")
    with pytest.raises(TaskFailed, match="no input received$"):
        context.input(prompt="Continue? ")


def test_skip_uses_reason_or_default(tmp_path):
    context = make_context(tmp_path)
    with pytest.raises(SkipTask, match="not this machine"):
        context.skip("not this machine")
    with pytest.raises(SkipTask, match="skipped by task"):
        context.skip()


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


def test_sudo_is_refreshed_once_per_task(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, command=command, sudo=sudo)
    context.begin_task(task_for(needs_sudo=True))

    context.run(["first"])
    context.run(["second"])

    assert sudo.refresh_count == 1


def test_dry_run_mutating_command_requests_sudo_but_does_not_execute(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, dry_run=True, command=command, sudo=sudo)
    context.begin_task(task_for(needs_sudo=True))

    context.run(["install"])

    assert sudo.refresh_count == 1
    assert command.calls == []
    assert len(context.changes) == 1


def test_dry_run_probe_requests_sudo_when_needed(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, dry_run=True, command=command, sudo=sudo)
    context.begin_task(task_for(needs_sudo=True))

    context.run(["rpm", "-q", "git"], mutates=False)

    assert sudo.refresh_count == 1
    assert command.calls[0][1]["sudo"] is True
    assert context.changes == []


def test_task_sudo_default_and_explicit_override(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, command=command)
    context.begin_task(task_for(needs_sudo=True))

    context.run(["first"])
    context.run(["second"], sudo=False)

    assert command.calls[0][1]["sudo"] is True
    assert command.calls[1][1]["sudo"] is False


def test_guard_can_run_against_nonzero_result(tmp_path):
    command = FakeCommandRunner()
    command.responses = [lambda argv: 1, lambda argv: 0]
    context = make_context(tmp_path, command=command)

    result = context.run(["install"], unless=["test", "-e", "/thing"])

    assert len(command.calls) == 2
    assert result.returncode == 0
    assert len(context.changes) == 1
