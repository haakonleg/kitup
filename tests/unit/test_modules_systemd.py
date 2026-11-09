import pytest

from kitup.errors import TaskFailed
from kitup.modules import systemd

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response


def test_enabled_active_unit_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "enabled\n"), response(0, "active\n")]
    ctx = make_context(tmp_path, command=command)

    systemd.unit(ctx, "sshd.service", enabled=True, state="started")

    assert argvs(command) == [
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

    assert argvs(command) == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "enable", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
        ["systemctl", "start", "sshd.service"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert command.calls[3][1]["sudo"] is True
    assert ctx.changes[0].action == "systemd.unit"


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
        systemd.unit(ctx, "app.service", state="started", scope="session")
    with pytest.raises(TaskFailed, match="invalid systemd state"):
        systemd.unit(ctx, "app.service", state="sleeping")
    with pytest.raises(TaskFailed, match="invalid systemd state"):
        systemd.unit(ctx, "app.service", state="running")
    with pytest.raises(TaskFailed, match="systemd unit must not be empty"):
        systemd.unit(ctx, "  ")
    with pytest.raises(TaskFailed, match="requires enabled or state"):
        systemd.unit(ctx, "app.service")
    with pytest.raises(TaskFailed, match="systemd enabled must be a boolean"):
        systemd.unit(ctx, "app.service", enabled="yes")  # type: ignore[arg-type]


def test_dry_run_does_not_enable_or_start(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(3)]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = systemd.unit(ctx, "sshd.service", enabled=True, state="started")

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


def test_unit_is_idempotent_when_already_enabled_and_active(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(3), response(0)]
    ctx = make_context(tmp_path, command=command)

    first = systemd.unit(ctx, "sshd.service", enabled=True, state="started")
    assert first.changed is True

    before = len(command.calls)
    command.responses = [response(0, "enabled\n"), response(0, "active\n")]
    ctx = make_context(tmp_path, command=command)

    second = systemd.unit(ctx, "sshd.service", enabled=True, state="started")

    assert argvs(command)[before:] == [
        ["systemctl", "is-enabled", "sshd.service"],
        ["systemctl", "is-active", "sshd.service"],
    ]
    assert second.changed is False
    assert second.summary == "ensure sshd.service is enabled and started"
    assert ctx.changes == []


def test_daemon_reload_dry_run_does_not_run_systemctl(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = systemd.daemon_reload(ctx)

    assert argvs(command) == []
    assert result.changed is True
    assert result.summary == "would reload systemd (system)"
    assert call_data(result) == {"scope": "system"}


def test_daemon_reload_records_a_change(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    systemd.daemon_reload(ctx)

    assert command.calls[0][0] == ["systemctl", "daemon-reload"]
    assert len(ctx.changes) == 1
    assert ctx.changes[0].action == "systemd.daemon_reload"
    assert call_data(ctx.changes[0]) == {"scope": "system"}
