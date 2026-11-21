import pytest

from kitup.errors import TaskFailed
from kitup.modules import systemd

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response


def test_unit_noop_enable_start_and_idempotency(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "enabled\n"), response(0, "active\n")]
    ctx = make_context(tmp_path, command=command)

    systemd.unit("sshd.service", enabled=True, state="started")

    assert argvs(command) == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
    ]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [
        response(1),
        response(0),
        response(3),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    first = systemd.unit("sshd.service", enabled=True, state="started")

    assert first.changed is True
    assert argvs(command) == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "enable", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
        ["systemctl", "start", "sshd.service"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert command.calls[3][1]["sudo"] is True
    assert ctx.changes[0].action == "systemd.unit"

    before = len(command.calls)
    command.responses = [response(0, "enabled\n"), response(0, "active\n")]
    second = systemd.unit("sshd.service", enabled=True, state="started")

    assert argvs(command)[before:] == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
    ]
    assert second.changed is False
    assert second.summary == "ensure sshd.service is enabled and started"


def test_disable_and_stop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "enabled\n"),
        response(0),
        response(0, "active\n"),
        response(0),
    ]
    make_context(tmp_path, command=command)

    systemd.unit("sshd.service", enabled=False, state="stopped")

    assert command.calls[1][0] == ["systemctl", "disable", "sshd.service"]
    assert command.calls[3][0] == ["systemctl", "stop", "sshd.service"]


def test_user_scope_uses_user_commands_and_refuses_root(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(3), response(0)]
    make_context(tmp_path, command=command)

    systemd.unit("app.service", enabled=True, state="started", scope="user")

    assert command.calls[1][0] == ["systemctl", "--user", "enable", "app.service"]
    assert command.calls[1][1]["sudo"] is False
    assert command.calls[3][0] == ["systemctl", "--user", "start", "app.service"]

    make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="cannot run as root"):
        systemd.unit("app.service", state="started", scope="user")


def test_invalid_arguments_are_rejected(tmp_path):
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid systemd scope"):
        systemd.unit("app.service", state="started", scope="session")
    with pytest.raises(TaskFailed, match="invalid systemd state"):
        systemd.unit("app.service", state="sleeping")
    with pytest.raises(TaskFailed, match="invalid systemd state"):
        systemd.unit("app.service", state="running")
    with pytest.raises(TaskFailed, match="systemd unit must not be empty"):
        systemd.unit("  ")
    with pytest.raises(TaskFailed, match="requires enabled or state"):
        systemd.unit("app.service")
    with pytest.raises(TaskFailed, match="systemd enabled must be a boolean"):
        systemd.unit("app.service", enabled="yes")  # type: ignore[arg-type]


def test_dry_run_does_not_enable_or_start(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(3)]
    make_context(tmp_path, dry_run=True, command=command)

    result = systemd.unit("sshd.service", enabled=True, state="started")

    assert argvs(command) == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
    ]
    assert result.changed is True
    assert result.summary == "enabled sshd.service; started sshd.service"
    assert call_data(result) == {
        "unit": "sshd.service",
        "scope": "system",
        "actions": ["enable", "start"],
    }


def test_daemon_reload_dry_run_and_real_change(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, dry_run=True, command=command)

    result = systemd.daemon_reload()

    assert argvs(command) == []
    assert result.changed is True
    assert result.summary == "would reload systemd (system)"
    assert call_data(result) == {"scope": "system"}

    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    systemd.daemon_reload()

    assert command.calls[0][0] == ["systemctl", "daemon-reload"]
    assert len(ctx.changes) == 1
    assert ctx.changes[0].action == "systemd.daemon_reload"
    assert call_data(ctx.changes[0]) == {"scope": "system"}
