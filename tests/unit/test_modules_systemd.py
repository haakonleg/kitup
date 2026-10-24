from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import systemd

from .helpers import FakeCommandRunner, FakeSudo, completed, make_context


def response(returncode: int = 0, stdout: str = ""):
    return lambda argv: completed(argv, returncode, stdout)


def test_enabled_active_unit_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "enabled\n"), response(0, "active\n")]
    ctx = make_context(tmp_path, command=command)

    systemd.unit(ctx, "sshd.service", enabled=True, state="started")

    assert [call[0] for call in command.calls] == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
    ]
    assert ctx.changes == []


def test_enable_and_start(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(1),
        response(0),
        response(3),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    systemd.unit(
        ctx,
        "sshd.service",
        enabled=True,
        state="started",
    )

    assert [call[0] for call in command.calls] == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "enable", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
        ["systemctl", "start", "sshd.service"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert command.calls[3][1]["sudo"] is True


def test_disable_and_stop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "enabled\n"),
        response(0),
        response(0, "active\n"),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    systemd.unit(ctx, "sshd.service", enabled=False, state="stopped")

    assert command.calls[1][0] == ["systemctl", "disable", "sshd.service"]
    assert command.calls[3][0] == ["systemctl", "stop", "sshd.service"]


def test_user_scope_uses_user_commands_without_sudo(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(3), response(0)]
    ctx = make_context(tmp_path, command=command)

    systemd.unit(ctx, "app.service", enabled=True, state="started", scope="user")

    assert command.calls[1][0] == ["systemctl", "--user", "enable", "app.service"]
    assert command.calls[1][1]["sudo"] is False
    assert command.calls[3][0] == ["systemctl", "--user", "start", "app.service"]


def test_user_scope_refuses_root(tmp_path):
    ctx = make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="cannot run as root"):
        systemd.unit(ctx, "app.service", state="started", scope="user")


def test_invalid_arguments_are_rejected(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid systemd scope"):
        systemd.unit(ctx, "app.service", scope="session")
    with pytest.raises(TaskFailed, match="invalid systemd state"):
        systemd.unit(ctx, "app.service", state="sleeping")
    with pytest.raises(TaskFailed, match="invalid systemd state"):
        systemd.unit(ctx, "app.service", state="running")
    with pytest.raises(TaskFailed, match="systemd unit must not be empty"):
        systemd.unit(ctx, "  ")


def test_daemon_reload_records_a_change(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    systemd.daemon_reload(ctx)

    assert command.calls[0][0] == ["systemctl", "daemon-reload"]
    assert len(ctx.changes) == 1
