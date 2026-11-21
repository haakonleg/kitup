import pytest

from kitup.errors import TaskFailed
from kitup.modules import command

from .helpers import FakeCommandRunner, FakeSudo, make_context


def test_creates_guards_skip_for_present_and_broken_symlink_markers(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)
    marker = tmp_path / "marker"
    marker.write_text("exists")

    result = command.run("touch", str(marker), creates=marker)

    assert runner.calls == []
    assert ctx.changes == []
    assert ctx.calls == [result]
    assert result.changed is False
    assert result.failed is False
    assert result.summary == f"skip touch {marker}"

    # a broken symlink marker counts as present too
    runner = FakeCommandRunner()
    make_context(tmp_path, command=runner)
    marker = tmp_path / "broken"
    marker.symlink_to(tmp_path / "missing")

    result = command.run("true", creates=marker)

    assert runner.calls == []
    assert result.changed is False
    assert result.failed is False
    assert result.summary == "skip true"


def test_unless_guard_skips_the_command(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    result = command.run("install", unless=["test", "-e", "marker"])

    assert runner.calls[0][0] == ["test", "-e", "marker"]
    assert ctx.changes == []
    assert ctx.calls == [result]
    assert result.changed is False
    assert result.failed is False
    assert result.summary == "skip install"


def test_successful_changed_and_unchanged_commands(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    result = command.run("true")

    assert runner.calls[0][0] == ["true"]
    assert ctx.changes == [result]
    assert result.changed is True
    assert result.action == "command.run"
    assert result.cmd == ["true"]
    assert result.returncode == 0
    assert result.failed is False

    # cmd= is accepted by shell, while run takes positional arguments
    result = command.run("true")
    shell_result = command.shell(cmd="echo hello", shell="/bin/bash")

    assert result.cmd == ["true"]
    assert result.action == "command.run"
    assert shell_result.action == "command.shell"
    assert runner.calls[-1][0] == ["/bin/bash", "-c", "echo hello"]

    # changed=False still runs but records a no-change call
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, command=runner)

    result = command.run("true", changed=False)

    assert runner.calls[0][0] == ["true"]
    assert ctx.changes == []
    assert ctx.calls == [result]
    assert result.changed is False
    assert result.failed is False


def test_ignore_errors_returns_failure_results(tmp_path):
    runner = FakeCommandRunner(returncode=2, stdout="out", stderr="bad")
    ctx = make_context(tmp_path, command=runner)

    result = command.run("tool", ignore_errors=True)

    assert runner.calls[0][1]["check"] is False
    assert result.failed is True
    assert result.returncode == 2
    assert result.stdout == "out"
    assert result.stderr == "bad"
    assert result.changed is True
    assert ctx.calls == [result]
    assert ctx.changes == [result]

    # runner-level failures are not swallowed by ignore_errors
    class TimedOutRunner(FakeCommandRunner):
        def run(self, argv, **kwargs):
            raise TaskFailed("command timed out after 1s")

    make_context(tmp_path, command=TimedOutRunner())

    with pytest.raises(TaskFailed, match="timed out"):
        command.run("tool", ignore_errors=True)

    # ignore_errors and check are mutually exclusive
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="cannot be combined"):
        command.run("true", ignore_errors=True, check=True)


def test_sudo_is_forwarded_and_requested_only_when_set(tmp_path):
    runner = FakeCommandRunner()
    sudo = FakeSudo()
    make_context(tmp_path, command=runner, sudo=sudo)

    command.run("first")
    command.run("second", sudo=True)

    assert runner.calls[0][1]["sudo"] is False
    assert runner.calls[1][1]["sudo"] is True


def test_dry_run_does_not_execute_mutating_command(tmp_path):
    runner = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=runner)

    result = command.run("touch", "file")

    assert runner.calls == []
    assert ctx.changes == [result]
    assert result.changed is True
    assert result.failed is False
    assert result.summary == "would run: touch file"


def test_shell_ignore_errors_returns_failure_result(tmp_path):
    runner = FakeCommandRunner(returncode=1, stderr="bad")
    make_context(tmp_path, command=runner)

    result = command.shell("false", shell="/bin/bash", ignore_errors=True)

    assert result.failed is True
    assert runner.calls[0][0] == ["/bin/bash", "-c", "false"]
    assert runner.calls[0][1]["check"] is False


def test_shell_uses_explicit_and_default_shell(tmp_path, monkeypatch):
    runner = FakeCommandRunner()
    make_context(tmp_path, command=runner)

    command.shell("echo hello", shell="/bin/bash")

    assert runner.calls[0][0] == ["/bin/bash", "-c", "echo hello"]

    # without an explicit shell the environment's SHELL is used
    monkeypatch.setenv("SHELL", "/bin/zsh")

    command.shell("echo hello")

    assert runner.calls[1][0] == ["/bin/zsh", "-c", "echo hello"]


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

    with pytest.raises(TaskFailed, match="cmd must not be empty"):
        command.run()
    with pytest.raises(TaskFailed, match="creates path must not be empty"):
        command.run("true", creates="")
    with pytest.raises(TaskFailed, match="cwd must not be empty"):
        command.run("true", cwd="")
    with pytest.raises(TaskFailed, match="unless must not be empty"):
        command.run("true", unless=" ")
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
        ("ignore_errors", "yes"),
        ("timeout", 0),
        ("timeout", float("inf")),
    ):
        with pytest.raises(TaskFailed, match="must be"):
            command.run("true", **{keyword: value})
