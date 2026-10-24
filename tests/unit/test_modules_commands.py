from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import commands

from .helpers import FakeCommandRunner, make_context


def test_guarded_command_is_skipped(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    marker = tmp_path / "marker"
    marker.write_text("exists")

    commands.run(ctx, ["touch", str(marker)], creates=marker)

    assert command.calls == []
    assert ctx.changes == []


def test_unguarded_command_runs_and_records_change(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    commands.run(ctx, ["true"])

    assert command.calls[0][0] == ["true"]
    assert len(ctx.changes) == 1


def test_changed_false_suppresses_change_record(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    commands.run(ctx, ["true"], changed=False)

    assert command.calls[0][0] == ["true"]
    assert ctx.changes == []


def test_dry_run_does_not_execute_mutating_command(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=command)

    commands.run(ctx, ["touch", "file"])

    assert command.calls == []
    assert len(ctx.changes) == 1


def test_shell_uses_explicit_shell(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    commands.shell(ctx, "echo hello", shell="/bin/bash")

    assert command.calls[0][0] == ["/bin/bash", "-c", "echo hello"]


def test_shell_uses_default_shell_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    commands.shell(ctx, "echo hello")

    assert command.calls[0][0] == ["/bin/zsh", "-c", "echo hello"]


def test_shell_dry_run_does_not_execute(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=command)

    commands.shell(ctx, "touch file", shell="/bin/sh")

    assert command.calls == []
    assert len(ctx.changes) == 1


def test_run_rejects_string_argv(tmp_path):
    with pytest.raises(TaskFailed, match="sequence of arguments"):
        commands.run(make_context(tmp_path), "echo hello")


def test_run_rejects_empty_creates_path(tmp_path):
    with pytest.raises(TaskFailed, match="creates path must not be empty"):
        commands.run(make_context(tmp_path), ["true"], creates="")


def test_run_rejects_empty_cwd(tmp_path):
    with pytest.raises(TaskFailed, match="cwd must not be empty"):
        commands.run(make_context(tmp_path), ["true"], cwd="")


def test_shell_rejects_empty_command(tmp_path):
    with pytest.raises(TaskFailed, match="shell command must not be empty"):
        commands.shell(make_context(tmp_path), "  ")


def test_shell_rejects_empty_executable(tmp_path):
    with pytest.raises(TaskFailed, match="shell must not be empty"):
        commands.shell(make_context(tmp_path), "true", shell="")
