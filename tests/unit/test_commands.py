from __future__ import annotations

import signal
import subprocess
from io import StringIO

import pytest

from kitup import commands as command_module
from kitup.commands import CommandRunner
from kitup.errors import TaskFailed
from kitup.output import Output
from kitup.privileges import PrivilegeManager

from .helpers import FakeSudo


class FakeProcess:
    def __init__(
        self,
        *,
        pid: int = 12345,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def communicate(self, timeout=None):
        return self.stdout, self.stderr


class TimeoutProcess:
    pid = 12345
    returncode = None

    def communicate(self, timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired(
                ["tool"],
                timeout,
                output="partial out",
                stderr="partial err",
            )
        self.returncode = -9
        return "tail out", "tail err"


def patch_popen(monkeypatch, process):
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


def test_command_captures_output(monkeypatch):
    patch_popen(monkeypatch, FakeProcess(stdout="hello"))
    runner = CommandRunner(PrivilegeManager())

    result = runner.run(["tool"])

    assert result.returncode == 0
    assert result.stdout == "hello"


def test_nonzero_exit_raises_task_failed(monkeypatch):
    patch_popen(monkeypatch, FakeProcess(returncode=3, stderr="oops"))
    runner = CommandRunner(PrivilegeManager())

    with pytest.raises(TaskFailed) as exc:
        runner.run(["tool"])

    assert exc.value.command == ["tool"]
    assert "exit code 3" in exc.value.message
    assert exc.value.stderr == "oops"


def test_nonzero_exit_can_be_probed(monkeypatch):
    patch_popen(monkeypatch, FakeProcess(returncode=4))
    runner = CommandRunner(PrivilegeManager())

    result = runner.run(["tool"], check=False)

    assert result.returncode == 4


def test_commands_get_devnull_stdin(monkeypatch):
    calls = patch_popen(monkeypatch, FakeProcess())
    runner = CommandRunner(PrivilegeManager())

    runner.run(["true"])

    assert calls[0][1]["stdin"] is subprocess.DEVNULL


def test_sudo_is_refreshed_and_wrapped(monkeypatch):
    calls = patch_popen(monkeypatch, FakeProcess())
    sudo = FakeSudo()
    runner = CommandRunner(sudo)  # type: ignore[arg-type]

    runner.run(["dnf5", "install"], sudo=True)

    assert sudo.refresh_count == 1
    assert calls[0][0] == ["sudo", "-n", "--", "dnf5", "install"]


def test_verbose_mode_logs_commands_and_output(monkeypatch):
    patch_popen(monkeypatch, FakeProcess(stdout="out", stderr="err"))
    stream = StringIO()
    output = Output(stream, no_color=True, verbose=True)
    runner = CommandRunner(PrivilegeManager(), output=output)

    runner.run(["tool"])

    text = stream.getvalue()
    assert "$ tool" in text
    assert "exit 0" in text
    assert "stdout:\n      out" in text
    assert "stderr:\n      err" in text


def test_timeout_kills_the_process_group(monkeypatch):
    process = TimeoutProcess()
    patch_popen(monkeypatch, process)
    killed = []
    monkeypatch.setattr(
        command_module,
        "_kill_process_group",
        lambda target: killed.append(target),
    )
    runner = CommandRunner(PrivilegeManager())

    with pytest.raises(TaskFailed, match="timed out") as exc:
        runner.run(["tool"], timeout=0.1)

    assert killed == [process]
    assert exc.value.stdout == "tail out"
    assert exc.value.stderr == "tail err"


def test_kill_process_group_sends_sigkill(monkeypatch):
    calls = []
    monkeypatch.setattr("os.killpg", lambda pid, sig: calls.append((pid, sig)))
    process = FakeProcess(pid=4321)

    command_module._kill_process_group(process)  # type: ignore[arg-type]

    assert calls == [(4321, signal.SIGKILL)]
