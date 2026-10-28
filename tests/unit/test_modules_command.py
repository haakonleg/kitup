from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import command

from .helpers import FakeCommandRunner, FakeSudo, make_context


def test_guarded_command_is_skipped(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)
    marker = tmp_path / "marker"
    marker.write_text("exists")

    command.run(ctx, ["touch", str(marker)], creates=marker)

    assert runner.calls == []
    assert ctx.changes == []


def test_unguarded_command_runs_and_records_change(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    command.run(ctx, ["true"])

    assert runner.calls[0][0] == ["true"]
    assert len(ctx.changes) == 1


def test_changed_false_suppresses_change_record(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    command.run(ctx, ["true"], changed=False)

    assert runner.calls[0][0] == ["true"]
    assert ctx.changes == []


def test_sudo_is_forwarded_and_requested_only_when_set(tmp_path):
    runner = FakeCommandRunner()
    sudo = FakeSudo()
    ctx = make_context(tmp_path, command=runner, sudo=sudo)

    command.run(ctx, ["first"])
    command.run(ctx, ["second"], sudo=True)

    assert runner.calls[0][1]["sudo"] is False
    assert runner.calls[1][1]["sudo"] is True
    assert sudo.refresh_count == 1


def test_dry_run_does_not_execute_mutating_command(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=runner)

    command.run(ctx, ["touch", "file"])

    assert runner.calls == []
    assert len(ctx.changes) == 1


def test_shell_uses_explicit_shell(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    command.shell(ctx, "echo hello", shell="/bin/bash")

    assert runner.calls[0][0] == ["/bin/bash", "-c", "echo hello"]


def test_shell_uses_default_shell_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    command.shell(ctx, "echo hello")

    assert runner.calls[0][0] == ["/bin/zsh", "-c", "echo hello"]


def test_shell_unless_runs_through_shell(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    command.shell(ctx, "install", shell="/bin/bash", unless="test -e /thing")

    assert runner.calls == [
        (
            ["/bin/bash", "-c", "test -e /thing"],
            {
                "cwd": None,
                "env": None,
                "sudo": False,
                "timeout": 600,
                "check": False,
                "refresh_sudo": False,
            },
        )
    ]
    assert ctx.changes == []


def test_invalid_arguments_are_rejected(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="creates path must not be empty"):
        command.run(ctx, ["true"], creates="")
    with pytest.raises(TaskFailed, match="cwd must not be empty"):
        command.run(ctx, ["true"], cwd="")
    with pytest.raises(TaskFailed, match="unless must not be empty"):
        command.run(ctx, ["true"], unless=" ")
    with pytest.raises(TaskFailed, match="shell command must not be empty"):
        command.shell(ctx, "  ")
    with pytest.raises(TaskFailed, match="shell unless must not be empty"):
        command.shell(ctx, "true", unless="  ")
    with pytest.raises(TaskFailed, match="shell must not be empty"):
        command.shell(ctx, "true", shell="")
