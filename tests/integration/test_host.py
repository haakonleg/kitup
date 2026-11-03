"""Integration tests for real systemd host settings."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import run, run_kitup, write_bundle

pytestmark = pytest.mark.integration

LOCALE_CONF = Path("/etc/locale.conf")
LOCALE_KEYS = {
    "LANG",
    "LANGUAGE",
    "LC_CTYPE",
    "LC_NUMERIC",
    "LC_TIME",
    "LC_COLLATE",
    "LC_MONETARY",
    "LC_MESSAGES",
    "LC_PAPER",
    "LC_NAME",
    "LC_ADDRESS",
    "LC_TELEPHONE",
    "LC_MEASUREMENT",
    "LC_IDENTIFICATION",
}


def timedatectl_show(*properties: str) -> dict[str, str]:
    """Return selected properties from ``timedatectl show``."""
    argv = ["timedatectl", "show", *(f"--property={property}" for property in properties)]
    result = run(argv)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def localectl_status() -> dict[str, str]:
    """Return locale assignments from ``localectl status``."""
    result = run(["localectl", "status"], env={**os.environ, "LC_ALL": "C"})
    status: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("Command Line:"):
            continue
        if line.startswith("System Locale:"):
            line = line.removeprefix("System Locale:").strip()
        for token in line.split():
            key, separator, value = token.partition("=")
            if separator and key in LOCALE_KEYS:
                status[key] = value
    return status


def output(capsys) -> str:
    """Return everything the CLI printed, regardless of the stream."""
    captured = capsys.readouterr()
    return captured.out + captured.err


def read_root_file(path: Path) -> str | None:
    """Read a root-owned file, returning ``None`` when it does not exist."""
    if run(["sudo", "-n", "test", "-e", str(path)], check=False).returncode != 0:
        return None
    return run(["sudo", "-n", "cat", str(path)]).stdout


def restore_root_file(path: Path, content: str | None, tmp_path: Path) -> None:
    """Restore a root-owned file, or remove it when it did not exist."""
    if content is None:
        run(["sudo", "-n", "rm", "-f", str(path)], check=False)
        return
    staged = tmp_path / f"restore-{path.name}"
    staged.write_text(content, encoding="utf-8")
    run(["sudo", "-n", "install", "-m", "0644", str(staged), str(path)])


@pytest.fixture(autouse=True)
def restore_host_settings(tmp_path: Path) -> Iterator[None]:
    """Restore host settings after each test so random ordering stays safe."""
    locale_conf = read_root_file(LOCALE_CONF)
    timezone = timedatectl_show("Timezone")["Timezone"]
    rtc = timedatectl_show("LocalRTC")["LocalRTC"]
    pretty = run(["hostnamectl", "hostname", "--pretty"]).stdout.strip()

    yield

    run(["sudo", "-n", "timedatectl", "set-timezone", timezone], check=False)
    run(
        [
            "sudo",
            "-n",
            "timedatectl",
            "set-local-rtc",
            "1" if rtc == "yes" else "0",
            "--adjust-system-clock",
        ],
        check=False,
    )
    restore_root_file(LOCALE_CONF, locale_conf, tmp_path)
    # localed caches the file it loaded, so restart it after restoring it.
    # Reset the start limit first: the suite restarts it once per test.
    run(["sudo", "-n", "systemctl", "reset-failed", "systemd-localed.service"], check=False)
    run(["sudo", "-n", "systemctl", "restart", "systemd-localed.service"], check=False)
    run(["sudo", "-n", "hostnamectl", "hostname", "--pretty", pretty], check=False)


def test_timezone_set_and_idempotent(tmp_path: Path, capsys) -> None:
    run(["sudo", "-n", "timedatectl", "set-timezone", "UTC"])
    root = write_bundle(
        tmp_path / "timezone",
        "host_timezone",
        """
        from kitup.modules import host

        @task("set time zone")
        def set_timezone(ctx):
            host.timezone(ctx, "Europe/Oslo")
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert timedatectl_show("Timezone")["Timezone"] == "Europe/Oslo"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_invalid_timezone_fails_without_changing(tmp_path: Path, capsys) -> None:
    run(["sudo", "-n", "timedatectl", "set-timezone", "UTC"])
    root = write_bundle(
        tmp_path / "timezone-invalid",
        "host_timezone_invalid",
        """
        from kitup.modules import host

        @task("set invalid time zone")
        def set_timezone(ctx):
            host.timezone(ctx, "Not/AZone")
        """,
    )

    assert run_kitup("apply", root) == 1
    assert "invalid time zone" in output(capsys)
    assert timedatectl_show("Timezone")["Timezone"] == "UTC"


def test_timezone_dry_run_does_not_change(tmp_path: Path, capsys) -> None:
    run(["sudo", "-n", "timedatectl", "set-timezone", "UTC"])
    root = write_bundle(
        tmp_path / "timezone-dry-run",
        "host_timezone_dry_run",
        """
        from kitup.modules import host

        @task("set time zone")
        def set_timezone(ctx):
            host.timezone(ctx, "Europe/Oslo")
        """,
    )

    assert run_kitup("apply", root, "--dry-run") == 0
    assert "1 changed" in capsys.readouterr().out
    assert timedatectl_show("Timezone")["Timezone"] == "UTC"


def test_locale_set_and_idempotent(tmp_path: Path, capsys) -> None:
    run(["sudo", "-n", "localectl", "set-locale", "LANG=en_US.UTF-8", "LC_TIME=en_US.UTF-8"])
    root = write_bundle(
        tmp_path / "locale",
        "host_locale",
        """
        from kitup.modules import host

        @task("set locale")
        def set_locale(ctx):
            host.locale(ctx, {"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"})
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "1 changed" in capsys.readouterr().out
    status = localectl_status()
    assert status["LANG"] == "en_US.UTF-8"
    assert status["LC_TIME"] == "en_GB.UTF-8"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_locale_language_persists(tmp_path: Path, capsys) -> None:
    run(
        [
            "sudo",
            "-n",
            "localectl",
            "set-locale",
            "LANG=en_US.UTF-8",
            "LANGUAGE=en_US.UTF-8",
        ]
    )
    root = write_bundle(
        tmp_path / "locale-language",
        "host_locale_language",
        """
        from kitup.modules import host

        @task("set language")
        def set_language(ctx):
            host.locale(ctx, {"LANG": "en_US.UTF-8", "LANGUAGE": "en_GB.UTF-8"})
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert localectl_status()["LANGUAGE"] == "en_GB.UTF-8"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_locale_redundant_values_are_idempotent(tmp_path: Path, capsys) -> None:
    run(
        [
            "sudo",
            "-n",
            "localectl",
            "set-locale",
            "LANG=en_US.UTF-8",
            "LANGUAGE=en_US.UTF-8",
            "LC_CTYPE=en_US.UTF-8",
        ]
    )
    root = write_bundle(
        tmp_path / "locale-redundant",
        "host_locale_redundant",
        """
        from kitup.modules import host

        @task("set redundant locale values")
        def set_locale(ctx):
            host.locale(
                ctx,
                {
                    "LANG": "en_US.UTF-8",
                    "LANGUAGE": "en_US.UTF-8",
                    "LC_CTYPE": "en_US.UTF-8",
                },
            )
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_locale_rejects_colon_separated_language(tmp_path: Path, capsys) -> None:
    root = write_bundle(
        tmp_path / "locale-language-list",
        "host_locale_language_list",
        """
        from kitup.modules import host

        @task("set language list")
        def set_language(ctx):
            host.locale(ctx, {"LANGUAGE": "en_US.UTF-8:de_DE.UTF-8"})
        """,
    )

    assert run_kitup("apply", root) == 1
    assert "single locale name" in output(capsys)


def test_locale_uninstalled_fails_without_changing(tmp_path: Path, capsys) -> None:
    before = read_root_file(LOCALE_CONF)
    root = write_bundle(
        tmp_path / "locale-uninstalled",
        "host_locale_uninstalled",
        """
        from kitup.modules import host

        @task("set uninstalled locale")
        def set_locale(ctx):
            host.locale(ctx, {"LANG": "xx_YY.UTF-8"})
        """,
    )

    assert run_kitup("apply", root) == 1
    assert read_root_file(LOCALE_CONF) == before


def test_locale_dry_run_does_not_change(tmp_path: Path, capsys) -> None:
    run(["sudo", "-n", "localectl", "set-locale", "LANG=en_US.UTF-8"])
    before = read_root_file(LOCALE_CONF)
    root = write_bundle(
        tmp_path / "locale-dry-run",
        "host_locale_dry_run",
        """
        from kitup.modules import host

        @task("set locale")
        def set_locale(ctx):
            host.locale(ctx, {"LANG": "en_GB.UTF-8"})
        """,
    )

    assert run_kitup("apply", root, "--dry-run") == 0
    assert "1 changed" in capsys.readouterr().out
    assert read_root_file(LOCALE_CONF) == before


def test_rtc_local_utc_is_idempotent(tmp_path: Path, capsys) -> None:
    run(
        ["sudo", "-n", "timedatectl", "set-local-rtc", "0", "--adjust-system-clock"],
        check=False,
    )
    root = write_bundle(
        tmp_path / "rtc-utc",
        "host_rtc_utc",
        """
        from kitup.modules import host

        @task("keep RTC in UTC")
        def keep_rtc_utc(ctx):
            host.rtc_local(ctx, enabled=False)
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_rtc_local_switches_modes(tmp_path: Path, capsys) -> None:
    run(
        ["sudo", "-n", "timedatectl", "set-local-rtc", "0", "--adjust-system-clock"],
        check=False,
    )
    local_root = write_bundle(
        tmp_path / "rtc-local",
        "host_rtc_local",
        """
        from kitup.modules import host

        @task("set RTC to local time")
        def set_rtc_local(ctx):
            host.rtc_local(ctx, enabled=True)
        """,
    )
    assert run_kitup("apply", local_root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert timedatectl_show("LocalRTC")["LocalRTC"] == "yes"

    utc_root = write_bundle(
        tmp_path / "rtc-back-to-utc",
        "host_rtc_back_to_utc",
        """
        from kitup.modules import host

        @task("set RTC to UTC")
        def set_rtc_utc(ctx):
            host.rtc_local(ctx, enabled=False)
        """,
    )
    assert run_kitup("apply", utc_root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert timedatectl_show("LocalRTC")["LocalRTC"] == "no"


def test_ntp_disabled_is_idempotent(tmp_path: Path, capsys) -> None:
    run(["sudo", "-n", "timedatectl", "set-ntp", "false"], check=False)
    if timedatectl_show("NTP")["NTP"] != "no":
        pytest.skip("could not disable network time synchronization")
    root = write_bundle(
        tmp_path / "ntp-disabled",
        "host_ntp_disabled",
        """
        from kitup.modules import host

        @task("disable NTP")
        def disable_ntp(ctx):
            host.ntp(ctx, enabled=False)
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_ntp_enable_fails_when_unavailable(tmp_path: Path, capsys) -> None:
    if timedatectl_show("CanNTP")["CanNTP"] != "no":
        pytest.skip("network time synchronization is available in this image")
    run(["sudo", "-n", "timedatectl", "set-ntp", "false"], check=False)
    if timedatectl_show("NTP")["NTP"] != "no":
        pytest.skip("could not disable network time synchronization")

    root = write_bundle(
        tmp_path / "ntp-unavailable",
        "host_ntp_unavailable",
        """
        from kitup.modules import host

        @task("enable NTP")
        def enable_ntp(ctx):
            host.ntp(ctx, enabled=True)
        """,
    )

    assert run_kitup("apply", root) == 1
    assert "not available" in output(capsys)


def test_pretty_hostname_set_and_clear(tmp_path: Path, capsys) -> None:
    run(["sudo", "-n", "hostnamectl", "hostname", "--pretty", ""], check=False)
    static = run(["hostnamectl", "hostname", "--static"]).stdout.strip()
    set_root = write_bundle(
        tmp_path / "pretty-hostname",
        "host_pretty_hostname",
        """
        from kitup.modules import host

        @task("set pretty hostname")
        def set_pretty(ctx):
            host.hostname(ctx, pretty="Kitup Integration")
        """,
    )

    assert run_kitup("apply", set_root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert run(["hostnamectl", "hostname", "--pretty"]).stdout.strip() == "Kitup Integration"
    assert run(["hostnamectl", "hostname", "--static"]).stdout.strip() == static

    assert run_kitup("apply", set_root) == 0
    assert "0 changed" in capsys.readouterr().out

    clear_root = write_bundle(
        tmp_path / "clear-pretty-hostname",
        "host_clear_pretty_hostname",
        """
        from kitup.modules import host

        @task("clear pretty hostname")
        def clear_pretty(ctx):
            host.hostname(ctx, pretty="")
        """,
    )
    assert run_kitup("apply", clear_root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert run(["hostnamectl", "hostname", "--pretty"]).stdout.strip() == ""
    assert run(["hostnamectl", "hostname", "--static"]).stdout.strip() == static
