import pytest

from kitup.errors import TaskFailed
from kitup.modules import command

from .helpers import FakeCommandRunner, FakeSudo, make_context


def test_guarded_command_is_skipped(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)
    marker = tmp_path / "marker"
    marker.write_text("exists")

    result = command.run(["touch", str(marker)], creates=marker)

    assert runner.calls == []
    assert ctx.changes == []
    assert ctx.calls == [result]
    assert result.changed is False
    assert result.summary == f"skip touch {marker}"


def test_broken_symlink_marker_skips_the_command(tmp_path):
    runner = FakeCommandRunner()
    make_context(tmp_path, command=runner)
    marker = tmp_path / "marker"
    marker.symlink_to(tmp_path / "missing")

    result = command.run(["true"], creates=marker)

    assert runner.calls == []
    assert result.changed is False
    assert result.summary == "skip true"


def test_unguarded_command_runs_and_records_change(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    result = command.run(["true"])

    assert runner.calls[0][0] == ["true"]
    assert ctx.changes == [result]
    assert result.changed is True
    assert result.action == "command.run"
    assert result.cmd == ["true"]
    assert result.returncode == 0


def test_cmd_keyword_is_accepted_by_run_and_shell(tmp_path):
    runner = FakeCommandRunner()
    make_context(tmp_path, command=runner)

    result = command.run(cmd=["true"])
    shell_result = command.shell(cmd="echo hello", shell="/bin/bash")

    assert result.cmd == ["true"]
    assert result.action == "command.run"
    assert shell_result.action == "command.shell"
    assert runner.calls[-1][0] == ["/bin/bash", "-c", "echo hello"]


def test_changed_false_records_a_no_change_call(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    result = command.run(["true"], changed=False)

    assert runner.calls[0][0] == ["true"]
    assert ctx.changes == []
    assert ctx.calls == [result]
    assert result.changed is False


def test_sudo_is_forwarded_and_requested_only_when_set(tmp_path):
    runner = FakeCommandRunner()
    sudo = FakeSudo()
    make_context(tmp_path, command=runner, sudo=sudo)

    command.run(["first"])
    command.run(["second"], sudo=True)

    assert runner.calls[0][1]["sudo"] is False
    assert runner.calls[1][1]["sudo"] is True


def test_dry_run_does_not_execute_mutating_command(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=runner)

    result = command.run(["touch", "file"])

    assert runner.calls == []
    assert ctx.changes == [result]
    assert result.changed is True
    assert result.summary == "would run: touch file"


def test_dry_run_does_not_execute_changed_false_commands(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=runner)

    result = command.run(["true"], changed=False)

    assert runner.calls == []
    assert ctx.changes == []
    assert ctx.calls == [result]
    assert result.changed is False


def test_shell_uses_explicit_shell(tmp_path):
    runner = FakeCommandRunner()
    make_context(tmp_path, command=runner)

    command.shell("echo hello", shell="/bin/bash")

    assert runner.calls[0][0] == ["/bin/bash", "-c", "echo hello"]


def test_shell_uses_default_shell_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    runner = FakeCommandRunner()
    make_context(tmp_path, command=runner)

    command.shell("echo hello")

    assert runner.calls[0][0] == ["/bin/zsh", "-c", "echo hello"]


def test_shell_unless_runs_through_shell(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    command.shell("install", shell="/bin/bash", unless="test -e /thing")

    assert runner.calls == [
        (
            ["/bin/bash", "-c", "test -e /thing"],
            {
                "cwd": None,
                "env": None,
                "sudo": False,
                "timeout": 600,
                "check": False,
                "stdin": None,
            },
        )
    ]
    assert ctx.changes == []
    assert len(ctx.calls) == 1


def test_invalid_arguments_are_rejected(tmp_path):
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="creates path must not be empty"):
        command.run(["true"], creates="")
    with pytest.raises(TaskFailed, match="cwd must not be empty"):
        command.run(["true"], cwd="")
    with pytest.raises(TaskFailed, match="unless must not be empty"):
        command.run(["true"], unless=" ")
    with pytest.raises(TaskFailed, match="shell command must not be empty"):
        command.shell("  ")
    with pytest.raises(TaskFailed, match="shell unless must not be empty"):
        command.shell("true", unless="  ")
    with pytest.raises(TaskFailed, match="shell must not be empty"):
        command.shell("true", shell="")

    for keyword, value in (
        ("changed", "yes"),
        ("sudo", 1),
        ("check", "no"),
        ("timeout", 0),
        ("timeout", float("inf")),
    ):
        with pytest.raises(TaskFailed, match="must be"):
            command.run(["true"], **{keyword: value})
