import stat

import pytest

from kitup.errors import TaskFailed
from kitup.modules import sysctl

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response


def make_sysctl_context(tmp_path, *, dry_run: bool = False, command=None):
    # is_root=True keeps privileged file operations local, on tmp_path files.
    return make_context(
        tmp_path,
        dry_run=dry_run,
        command=command or FakeCommandRunner(),
        sudo=FakeSudo(is_root=True),
    )


def test_matching_entries_and_whitespace_are_noops(tmp_path):
    # spaced entries keep their position; multiple keys are idempotent
    conf = tmp_path / "99-kitup.conf"
    command = FakeCommandRunner()
    ctx = make_sysctl_context(tmp_path, command=command)

    conf.write_text("vm.swappiness = 10\nother = 1\nkernel.panic = 3\n")
    command.responses = [response(0, "10\n"), response(0, "3\n")]
    sysctl.key("vm.swappiness", 10, file=conf)
    sysctl.key("kernel.panic", 3, file=conf)
    assert argvs(command) == [
        ["sysctl", "-n", "vm.swappiness"],
        ["sysctl", "-n", "kernel.panic"],
    ]
    assert command.calls[0][1]["sudo"] is False
    assert ctx.changes == []
    assert conf.read_text() == "vm.swappiness = 10\nother = 1\nkernel.panic = 3\n"

    # no-space entries are accepted as-is
    conf.write_text("vm.swappiness=10\n")
    command.responses = [response(0, "10\n")]
    ctx = make_sysctl_context(tmp_path, command=command)
    sysctl.key("vm.swappiness", 10, file=conf)
    assert ctx.changes == []
    assert conf.read_text() == "vm.swappiness=10\n"

    # dry-run with a matching no-space entry reports nothing
    command.responses = [response(0, "10\n")]
    ctx = make_sysctl_context(tmp_path, dry_run=True, command=command)
    sysctl.key("vm.swappiness", 10, file=conf)
    assert ctx.changes == []
    assert conf.read_text() == "vm.swappiness=10\n"

    # matching values normalize whitespace in the file
    conf.write_text("kernel.printk=4\t4\t1\t7\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "4 4 1 7\n")]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key("kernel.printk", "4 4 1 7", file=conf)
    assert ctx.changes == []
    assert conf.read_text() == "kernel.printk=4\t4\t1\t7\n"

    # runtime-only comparison uses the same whitespace normalization
    command.responses = [response(0, "4\t4\t1\t7\n")]
    ctx = make_sysctl_context(tmp_path, command=command)
    sysctl.key("kernel.printk", "4 4 1 7")
    assert len(command.calls) == 2  # probes only, no -w
    assert ctx.changes == []


def test_duplicate_and_mismatched_entries_are_rewritten(tmp_path):
    # duplicate matching entries collapse into one canonical line
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("vm.swappiness=10\nvm.swappiness = 10\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "10\n")]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key("vm.swappiness", 10, file=conf)

    assert conf.read_text() == "vm.swappiness = 10\n"
    assert len(ctx.changes) == 1

    command.responses = [response(0, "10\n")]
    ctx = make_sysctl_context(tmp_path, command=command)
    sysctl.key("vm.swappiness", 10, file=conf)

    assert ctx.changes == []

    # a no-space entry with a different value is rewritten
    conf.write_text("vm.swappiness=60\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "10\n")]  # runtime already matches
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key("vm.swappiness", 10, file=conf)

    assert conf.read_text() == "vm.swappiness = 10\n"
    assert len(ctx.changes) == 1

    # existing entries are replaced, comments are kept, and the runtime probe
    # alone determines whether -w is needed
    conf.write_text("# vm.swappiness = 60\nvm.swappiness = 60\nvm.swappiness=30\nother = 1\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "10\n")]  # runtime already matches
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key("vm.swappiness", 10, file=conf)

    assert conf.read_text() == "# vm.swappiness = 60\nother = 1\nvm.swappiness = 10\n"
    assert len(command.calls) == 1  # probe only, no -w
    assert len(ctx.changes) == 1  # file edit only


def test_runtime_only_when_file_is_none(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key("vm.swappiness", 10)

    assert argvs(command) == [
        ["sysctl", "-n", "vm.swappiness"],
        ["sysctl", "-w", "vm.swappiness=10"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert call_data(ctx.changes[0]) == {
        "key": "vm.swappiness",
        "value": "10",
        "runtime": True,
    }
    assert ctx.changes[0].action == "sysctl.key"
    assert ctx.changes[0].summary == "set vm.swappiness=10"
    assert list(tmp_path.glob("*.conf")) == []


def test_file_is_updated_created_and_runtime_set(tmp_path):
    # an existing file keeps its comments while the value and runtime change
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("# comment\nvm.swappiness = 60\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n"), response(0)]
    ctx = make_sysctl_context(tmp_path, command=command)

    sysctl.key("vm.swappiness", 10, file=conf)

    assert conf.read_text() == "# comment\nvm.swappiness = 10\n"
    assert argvs(command) == [
        ["sysctl", "-n", "vm.swappiness"],
        ["sysctl", "-w", "vm.swappiness=10"],
    ]
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {
        "key": "vm.swappiness",
        "value": "10",
        "runtime": True,
        "path": str(conf),
    }
    assert ctx.changes[0].summary == f"set vm.swappiness in {conf}; set vm.swappiness=10"

    # a missing file is created with the default mode
    conf = tmp_path / "created.conf"
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n"), response(0)]
    make_sysctl_context(tmp_path, command=command)

    sysctl.key("vm.swappiness", 10, file=conf)

    assert conf.read_text() == "vm.swappiness = 10\n"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o644


def test_absent_scenarios_and_missing_file_removal(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    ctx = make_sysctl_context(tmp_path)

    conf.write_text("vm.swappiness = 10\nother = 1\n")
    sysctl.key("vm.swappiness", state="absent", file=conf)
    assert conf.read_text() == "other = 1\n"
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {"path": str(conf), "key": "vm.swappiness"}
    assert ctx.changes[0].summary == f"remove vm.swappiness from {conf}"

    conf.write_text("other = 1\n")
    ctx = make_sysctl_context(tmp_path)
    sysctl.key("vm.swappiness", state="absent", file=conf)
    assert conf.read_text() == "other = 1\n"
    assert ctx.changes == []
    assert ctx.calls[0].summary == f"ensure vm.swappiness is absent from {conf}"

    # absent on a missing file creates nothing
    missing = tmp_path / "missing.conf"
    ctx = make_sysctl_context(tmp_path)
    sysctl.key("vm.swappiness", state="absent", file=missing)
    assert not missing.exists()
    assert ctx.changes == []
    assert ctx.calls[0].summary == f"ensure vm.swappiness is absent from {missing}"

    # absent without a file is rejected
    make_sysctl_context(tmp_path)

    with pytest.raises(TaskFailed, match="requires a sysctl file"):
        sysctl.key("vm.swappiness", state="absent")


def test_utf8_bom_handling(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    command = FakeCommandRunner()
    ctx = make_sysctl_context(tmp_path, command=command)

    # a no-op keeps the BOM untouched
    conf.write_bytes(b"\xef\xbb\xbfvm.swappiness = 10\n")
    command.responses = [response(0, "10\n")]
    sysctl.key("vm.swappiness", 10, file=conf)
    assert conf.read_bytes() == b"\xef\xbb\xbfvm.swappiness = 10\n"
    assert ctx.changes == []

    # the first real write drops the BOM
    conf.write_bytes(b"\xef\xbb\xbf# comment\nvm.swappiness = 60\n")
    command.responses = [response(0, "10\n")]  # runtime already matches
    ctx = make_sysctl_context(tmp_path, command=command)
    sysctl.key("vm.swappiness", 10, file=conf)
    assert conf.read_bytes() == b"# comment\nvm.swappiness = 10\n"
    assert len(ctx.changes) == 1


def test_dry_run_reports_without_mutating(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("vm.swappiness = 60\n")
    command = FakeCommandRunner()
    command.responses = [response(0, "60\n")]
    ctx = make_sysctl_context(tmp_path, dry_run=True, command=command)

    sysctl.key("vm.swappiness", 10, file=conf)

    assert conf.read_text() == "vm.swappiness = 60\n"
    assert argvs(command) == [["sysctl", "-n", "vm.swappiness"]]
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {
        "key": "vm.swappiness",
        "value": "10",
        "runtime": True,
        "path": str(conf),
    }


def test_bool_int_and_slash_normalization(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "0\n"), response(0), response(0, "0\n"), response(0)]
    make_sysctl_context(tmp_path, command=command)

    sysctl.key("net.ipv4.ip_forward", value=True)
    sysctl.key("kernel.panic", 3)
    assert command.calls[1][0] == ["sysctl", "-w", "net.ipv4.ip_forward=1"]
    assert command.calls[3][0] == ["sysctl", "-w", "kernel.panic=3"]

    conf = tmp_path / "99-kitup.conf"
    command.responses = [response(0, "0\n"), response(0)]
    make_sysctl_context(tmp_path, command=command)
    sysctl.key("net.ipv4.ip_forward", value=True, file=conf)
    assert conf.read_text() == "net.ipv4.ip_forward = 1\n"

    # slashes in a key name are normalized to dots
    command = FakeCommandRunner()
    command.responses = [response(0, "0\n"), response(0)]
    make_sysctl_context(tmp_path, command=command)

    sysctl.key("net/ipv4/ip_forward", 1)

    assert command.calls[0][0] == ["sysctl", "-n", "net.ipv4.ip_forward"]
    assert command.calls[1][0] == ["sysctl", "-w", "net.ipv4.ip_forward=1"]


def test_unknown_key_fails_before_any_change(tmp_path):
    conf = tmp_path / "99-kitup.conf"
    conf.write_text("other = 1\n")
    command = FakeCommandRunner()
    command.responses = [response(1, "", "sysctl: cannot stat /proc/sys/nope: No such file")]
    ctx = make_sysctl_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="cannot stat"):
        sysctl.key("nope.missing", 1, file=conf)

    assert conf.read_text() == "other = 1\n"
    assert ctx.changes == []


def test_invalid_arguments_are_rejected(tmp_path):
    make_sysctl_context(tmp_path)

    with pytest.raises(TaskFailed, match="value is required"):
        sysctl.key("vm.swappiness")
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        sysctl.key("vm.swappiness", 10, state="absent", file=tmp_path / "99-kitup.conf")
    with pytest.raises(TaskFailed, match="invalid sysctl state"):
        sysctl.key("vm.swappiness", 10, state="unknown")
    with pytest.raises(TaskFailed, match="sysctl key must not be empty"):
        sysctl.key("  ", 10)
    with pytest.raises(TaskFailed, match="invalid sysctl key"):
        sysctl.key("vm.swap piness", 10)
    with pytest.raises(TaskFailed, match="invalid sysctl key"):
        sysctl.key("vm..swappiness", 10)
    with pytest.raises(TaskFailed, match="newlines or null bytes"):
        sysctl.key("vm.swappiness", "10\n20")
    with pytest.raises(TaskFailed, match="unsupported sysctl value type"):
        sysctl.key("vm.swappiness", ["10"])
    with pytest.raises(TaskFailed, match="must be an absolute path"):
        sysctl.key("vm.swappiness", 10, file="relative.conf")
