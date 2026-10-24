"""Integration tests for real systemd unit management."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import run, run_kitup, systemctl, write_bundle

pytestmark = pytest.mark.integration


@pytest.fixture
def unit(tmp_path: Path) -> Iterator[str]:
    name = f"kitup-integration-{uuid.uuid4().hex[:8]}.service"
    unit_path = Path("/etc/systemd/system") / name
    staged = tmp_path / name
    staged.write_text(
        "[Unit]\n"
        "Description=kitup integration test\n"
        "\n"
        "[Service]\n"
        "ExecStart=/bin/sleep 300\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    run(["sudo", "-n", "install", "-m", "0644", str(staged), str(unit_path)])

    yield name

    run(["sudo", "-n", "systemctl", "disable", "--now", name], check=False)
    run(["sudo", "-n", "rm", "-f", str(unit_path)], check=False)
    run(["sudo", "-n", "systemctl", "daemon-reload"], check=False)


def test_enable_start_and_disable_stop(tmp_path: Path, unit: str) -> None:
    start_root = write_bundle(
        tmp_path / "start",
        "systemd_start",
        f"""
        from . import bundle
        from kitup.modules import systemd

        @bundle.task("reload, enable, and start unit", needs_sudo=True)
        def enable_and_start(ctx):
            systemd.daemon_reload(ctx)
            systemd.unit(ctx, {unit!r}, enabled=True, state="started")
        """,
    )
    assert run_kitup("apply", start_root) == 0
    assert systemctl("is-enabled", unit).stdout.strip() == "enabled"
    assert systemctl("is-active", unit).stdout.strip() == "active"

    stop_root = write_bundle(
        tmp_path / "stop",
        "systemd_stop",
        f"""
        from . import bundle
        from kitup.modules import systemd

        @bundle.task("disable and stop unit", needs_sudo=True)
        def disable_and_stop(ctx):
            systemd.unit(ctx, {unit!r}, enabled=False, state="stopped")
        """,
    )
    assert run_kitup("apply", stop_root) == 0
    assert systemctl("is-enabled", unit).stdout.strip() == "disabled"
    assert systemctl("is-active", unit).stdout.strip() != "active"
