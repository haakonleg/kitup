from __future__ import annotations

import stat

import pytest

from kitup.errors import TaskFailed
from kitup.modules import sysctl

from .helpers import FakeCommandRunner, FakeSudo, completed, make_context


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


def make_sysctl_context(tmp_path, *, dry_run: bool = False, command=None):
    # is_root=True keeps privileged file operations local, on tmp_path files.
    return make_context(
        tmp_path,
        dry_run=dry_run,
        command=command or FakeCommandRunner(),
        sudo=FakeSudo(is_root=True),
    )


def test_matching_runtime_and_file_is_a_noop(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("vm.swappiness = 10\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "10\n")]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "vm.swappiness", 10, file=conf)

    assert [call[0] for call in command.calls] == [["sysctl", "-n", "vm.swappiness"]]
    assert command.calls[0][1]["sudo"] is False
    assert ctx.changes == []
    assert conf.read_text() == "vm.swappiness = 10\n"


def test_runtime_only_when_file_is_none(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "vm.swappiness", 10)

    assert [call[0] for call in command.calls] == [
        ["sysctl", "-n", "vm.swappiness"],
        ["sysctl", "-w", "vm.swappiness=10"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].data == {"key": "vm.swappiness", "value": "10"}
    assert ctx.changes[0].summary == "set vm.swappiness=10"
    assert list(tmp_path.glob("*.conf")) == []


def test_runtime_matching_without_file_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "10\n")]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "vm.swappiness", 10)

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_file_is_updated_and_runtime_is_set(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("# comment\nvm.swappiness = 60\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "vm.swappiness", 10, file=conf)

    assert conf.read_text() == "# comment\nvm.swappiness = 10\n"
    assert [call[0] for call in command.calls] == [
        ["sysctl", "-n", "vm.swappiness"],
        ["sysctl", "-w", "vm.swappiness=10"],
    ]
    assert len(ctx.changes) == 2  # file edit plus runtime set


def test_file_is_created_when_missing(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "vm.swappiness", 10, file=conf)

    assert conf.read_text() == "vm.swappiness = 10\n"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o644


def test_existing_entries_are_replaced_and_comments_kept(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("# vm.swappiness = 60\nvm.swappiness = 60\nvm.swappiness=30\nother = 1\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "10\n")]  # runtime already matches
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "vm.swappiness", 10, file=conf)

    assert conf.read_text() == "# vm.swappiness = 60\nother = 1\nvm.swappiness = 10\n"
    assert len(command.calls) == 1  # probe only, no -w
    assert len(ctx.changes) == 1  # file edit only


def test_absent_removes_entries(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("vm.swappiness = 10\nother = 1\n")
    ctx = make_sysctl_context(tmp_path)

    sysctl.key(ctx, "vm.swappiness", state="absent", file=conf)

    assert conf.read_text() == "other = 1\n"
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data["state"] == "absent"
    assert ctx.changes[0].summary == f"remove vm.swappiness from {conf}"


def test_absent_is_a_noop_when_the_entry_is_missing(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("other = 1\n")
    ctx = make_sysctl_context(tmp_path)

    sysctl.key(ctx, "vm.swappiness", state="absent", file=conf)

    assert conf.read_text() == "other = 1\n"
    assert ctx.changes == []


def test_absent_without_file_is_rejected(tmp_path):
    ctx = make_sysctl_context(tmp_path)

    with pytest.raises(TaskFailed, match="requires a sysctl file"):
        sysctl.key(ctx, "vm.swappiness", state="absent")


def test_dry_run_reports_without_mutating(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("vm.swappiness = 60\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n")]
    ctx = make_sysctl_context(tmp_path, dry_run=True, command=command)

    sysctl.key(ctx, "vm.swappiness", 10, file=conf)

    assert conf.read_text() == "vm.swappiness = 60\n"
    assert [call[0] for call in command.calls] == [["sysctl", "-n", "vm.swappiness"]]
    assert len(ctx.changes) == 2  # file edit and runtime set are both reported


def test_bool_and_int_values_are_formatted(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "0\n"), response(0), response(0, "0\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "net.ipv4.ip_forward", True)
    sysctl.key(ctx, "kernel.panic", 3)

    assert command.calls[1][0] == ["sysctl", "-w", "net.ipv4.ip_forward=1"]
    assert command.calls[3][0] == ["sysctl", "-w", "kernel.panic=3"]


def test_file_line_uses_the_formatted_value(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    command = FakeCommandRunner()
    command.responses = [response(0, "0\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "net.ipv4.ip_forward", True, file=conf)

    assert conf.read_text() == "net.ipv4.ip_forward = 1\n"


def test_multivalue_runtime_compare_normalizes_whitespace(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "4\t4\t1\t7\n")]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "kernel.printk", "4 4 1 7")

    assert len(command.calls) == 1  # probe only, no -w
    assert ctx.changes == []


def test_unknown_key_fails_before_any_change(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("other = 1\n")
    command = FakeCommandRunner()
    command.responses = [response(1, "", "sysctl: cannot stat /proc/sys/nope: No such file")]
    ctx = make_sysctl_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="cannot stat"):
        sysctl.key(ctx, "nope.missing", 1, file=conf)

    assert conf.read_text() == "other = 1\n"
    assert ctx.changes == []


def test_slashes_in_name_are_normalized(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "0\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key(ctx, "net/ipv4/ip_forward", 1)

    assert command.calls[0][0] == ["sysctl", "-n", "net.ipv4.ip_forward"]
    assert command.calls[1][0] == ["sysctl", "-w", "net.ipv4.ip_forward=1"]


def test_invalid_arguments_are_rejected(tmp_path):
    ctx = make_sysctl_context(tmp_path)

    with pytest.raises(TaskFailed, match="value is required"):
        sysctl.key(ctx, "vm.swappiness")
    with pytest.raises(TaskFailed, match="invalid sysctl state"):
        sysctl.key(ctx, "vm.swappiness", 10, state="unknown")
    with pytest.raises(TaskFailed, match="sysctl key must not be empty"):
        sysctl.key(ctx, "  ", 10)
    with pytest.raises(TaskFailed, match="invalid sysctl key"):
        sysctl.key(ctx, "vm.swap piness", 10)
    with pytest.raises(TaskFailed, match="invalid sysctl key"):
        sysctl.key(ctx, "vm..swappiness", 10)
    with pytest.raises(TaskFailed, match="newlines or null bytes"):
        sysctl.key(ctx, "vm.swappiness", "10\n20")
    with pytest.raises(TaskFailed, match="unsupported sysctl value type"):
        sysctl.key(ctx, "vm.swappiness", ["10"])
    with pytest.raises(TaskFailed, match="must be an absolute path"):
        sysctl.key(ctx, "vm.swappiness", 10, file="relative.conf")
