"""Integration tests for real sysctl management.

The harness container runs rootless --privileged with --network=none, so net.*
sysctls (namespaced to the container's private netns) are writable inside it
while host sysctls stay untouched. Global sysctls such as vm.* remain
unwritable from the container's user namespace.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import run, run_kitup, write_bundle

pytestmark = pytest.mark.integration

# A network-namespace-owned key: writable inside the container, host-local netns
# unaffected.
KEY = "net.ipv4.icmp_echo_ignore_all"
CONF = "/etc/sysctl.d/99-kitup-integration.conf"


def sysctl_value(key: str = KEY) -> str:
    return run(["sysctl", "-n", key]).stdout.strip()


@pytest.fixture(autouse=True)
def restore_sysctl() -> Iterator[None]:
    run(["sudo", "-n", "sysctl", "-w", f"{KEY}=0"], check=False)
    run(["sudo", "-n", "rm", "-f", CONF], check=False)
    yield
    run(["sudo", "-n", "sysctl", "-w", f"{KEY}=0"], check=False)
    run(["sudo", "-n", "rm", "-f", CONF], check=False)


def test_runtime_only_set(tmp_path: Path, capsys) -> None:
    root = write_bundle(
        tmp_path / "runtime",
        "sysctl_runtime",
        f"""
        from kitup.modules import sysctl

        @task("set key at runtime only")
        def set_runtime(ctx):
            sysctl.key(ctx, {KEY!r}, 1)
        """,
    )
    assert run_kitup("apply", root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert sysctl_value() == "1"
    assert not Path(CONF).exists()

    # A second run is idempotent: the runtime value already matches.
    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out
    assert sysctl_value() == "1"


def test_persist_to_file(tmp_path: Path, capsys) -> None:
    root = write_bundle(
        tmp_path / "persist",
        "sysctl_persist",
        f"""
        from kitup.modules import sysctl

        @task("set key and persist it")
        def set_and_persist(ctx):
            sysctl.key(ctx, {KEY!r}, 1, file={CONF!r})
        """,
    )
    assert run_kitup("apply", root) == 0
    out = capsys.readouterr().out
    assert "1 changed" in out  # one task, two changes:
    assert f"set {KEY} in {CONF}" in out  # the file edit
    assert f"set {KEY}=1" in out  # the runtime sysctl -w
    assert sysctl_value() == "1"
    assert Path(CONF).read_text(encoding="utf-8") == f"{KEY} = 1\n"

    # A second run is idempotent.
    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out
    assert Path(CONF).read_text(encoding="utf-8") == f"{KEY} = 1\n"


def test_absent_removes_file_entry(tmp_path: Path, capsys) -> None:
    present_root = write_bundle(
        tmp_path / "present",
        "sysctl_present",
        f"""
        from kitup.modules import sysctl

        @task("persist key")
        def persist(ctx):
            sysctl.key(ctx, {KEY!r}, 1, file={CONF!r})
        """,
    )
    assert run_kitup("apply", present_root) == 0
    capsys.readouterr()
    assert f"{KEY} = 1" in Path(CONF).read_text(encoding="utf-8")

    absent_root = write_bundle(
        tmp_path / "absent",
        "sysctl_absent",
        f"""
        from kitup.modules import sysctl

        @task("remove persisted key")
        def forget(ctx):
            sysctl.key(ctx, {KEY!r}, state="absent", file={CONF!r})
        """,
    )
    assert run_kitup("apply", absent_root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert KEY not in Path(CONF).read_text(encoding="utf-8")


def test_unknown_key_fails(tmp_path: Path) -> None:
    root = write_bundle(
        tmp_path / "unknown",
        "sysctl_unknown",
        f"""
        from kitup.modules import sysctl

        @task("set unknown key")
        def set_unknown(ctx):
            sysctl.key(ctx, "kitup.no_such_key", 1, file={CONF!r})
        """,
    )
    assert run_kitup("apply", root) == 1
    # The unknown-key probe fails before the file is touched.
    assert not Path(CONF).exists()
