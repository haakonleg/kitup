"""Unit tests for the host module."""

import pytest

from kitup.errors import TaskFailed
from kitup.modules import host

from .helpers import FakeCommandRunner, completed, make_context


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


def status(*lines: str):
    return response(0, "\n".join(lines) + "\n")


def make_host_context(tmp_path, *, dry_run: bool = False, command=None):
    return make_context(
        tmp_path,
        dry_run=dry_run,
        command=command or FakeCommandRunner(),
    )


def test_static_hostname_matching_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "workstation\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(ctx, "workstation")

    assert [call[0] for call in command.calls] == [["hostnamectl", "hostname", "--static"]]
    assert command.calls[0][1]["sudo"] is False
    assert ctx.changes == []


def test_static_hostname_differs_is_set_and_verified(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "workstation\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(ctx, "workstation")

    assert [call[0] for call in command.calls] == [
        ["hostnamectl", "hostname", "--static"],
        ["hostnamectl", "hostname", "--static", "workstation"],
        ["hostnamectl", "hostname", "--static"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data == {"hostname": "workstation", "kind": "static"}
    assert ctx.changes[0].summary == "set static hostname workstation"


def test_pretty_hostname_matching_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "Workstation\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(ctx, pretty="Workstation")

    assert [call[0] for call in command.calls] == [["hostnamectl", "hostname", "--pretty"]]
    assert ctx.changes == []


def test_pretty_hostname_differs_is_set_and_verified(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "Workstation\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(ctx, pretty="Workstation")

    assert [call[0] for call in command.calls] == [
        ["hostnamectl", "hostname", "--pretty"],
        ["hostnamectl", "hostname", "--pretty", "Workstation"],
        ["hostnamectl", "hostname", "--pretty"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data == {"hostname": "Workstation", "kind": "pretty"}
    assert ctx.changes[0].summary == "set pretty hostname Workstation"


def test_pretty_hostname_is_trimmed_before_comparison(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "Workstation\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(ctx, pretty="  Workstation  ")

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_empty_pretty_hostname_is_cleared(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(ctx, pretty="")

    assert [call[0] for call in command.calls] == [
        ["hostnamectl", "hostname", "--pretty"],
        ["hostnamectl", "hostname", "--pretty", ""],
        ["hostnamectl", "hostname", "--pretty"],
    ]
    assert ctx.changes[0].data == {"hostname": "", "kind": "pretty"}
    assert ctx.changes[0].summary == "clear pretty hostname"


def test_static_and_pretty_hostnames_are_both_managed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "workstation\n"),
        response(0, "fedora\n"),
        response(0),
        response(0, "Workstation\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(ctx, "workstation", pretty="Workstation")

    assert [call[0] for call in command.calls] == [
        ["hostnamectl", "hostname", "--static"],
        ["hostnamectl", "hostname", "--static", "workstation"],
        ["hostnamectl", "hostname", "--static"],
        ["hostnamectl", "hostname", "--pretty"],
        ["hostnamectl", "hostname", "--pretty", "Workstation"],
        ["hostnamectl", "hostname", "--pretty"],
    ]
    assert len(ctx.changes) == 2


def test_hostname_dry_run_reports_without_mutating(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "fedora\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)

    host.hostname(ctx, "workstation")

    assert [call[0] for call in command.calls] == [["hostnamectl", "hostname", "--static"]]
    assert len(ctx.changes) == 1
    assert ctx.changes[0].summary == "set static hostname workstation"


def test_hostname_requires_name_or_pretty(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="requires name or pretty"):
        host.hostname(ctx)


def test_hostname_rejects_invalid_names(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="static hostname must not be empty"):
        host.hostname(ctx, "  ")
    with pytest.raises(TaskFailed, match="static hostname must not contain whitespace"):
        host.hostname(ctx, "work station")
    with pytest.raises(TaskFailed, match="must not contain '\\?'"):
        host.hostname(ctx, "workstation?")
    with pytest.raises(TaskFailed, match="pretty hostname must not contain control characters"):
        host.hostname(ctx, pretty="Work\nstation")


def test_hostname_probe_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "", "hostnamectl: unavailable")]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="hostnamectl hostname --static failed"):
        host.hostname(ctx, "workstation")


def test_hostname_verification_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "fedora\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="static hostname did not change to 'workstation'"):
        host.hostname(ctx, "workstation")


def test_timezone_matching_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "Timezone=Europe/Oslo\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.timezone(ctx, "Europe/Oslo")

    assert [call[0] for call in command.calls] == [
        ["timedatectl", "show", "--property=Timezone"],
    ]
    assert command.calls[0][1]["sudo"] is False
    assert ctx.changes == []


def test_timezone_differs_is_set_and_verified(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Timezone=UTC\n"),
        response(0),
        response(0, "Timezone=Europe/Oslo\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.timezone(ctx, "Europe/Oslo")

    assert [call[0] for call in command.calls] == [
        ["timedatectl", "show", "--property=Timezone"],
        ["timedatectl", "set-timezone", "Europe/Oslo"],
        ["timedatectl", "show", "--property=Timezone"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data == {"timezone": "Europe/Oslo"}
    assert ctx.changes[0].summary == "set time zone Europe/Oslo"


def test_timezone_invalid_zone_fails_before_probing(tmp_path):
    command = FakeCommandRunner()
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="invalid time zone"):
        host.timezone(ctx, "Not/AZone")

    assert command.calls == []
    assert ctx.changes == []


def test_timezone_rejects_whitespace(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="time zone must not contain whitespace"):
        host.timezone(ctx, "Europe/ Oslo")


def test_timezone_dry_run_reports_without_mutating(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "Timezone=UTC\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)

    host.timezone(ctx, "Europe/Oslo")

    assert len(command.calls) == 1
    assert len(ctx.changes) == 1


def test_timezone_probe_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "", "timedatectl: unavailable")]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="timedatectl show failed"):
        host.timezone(ctx, "Europe/Oslo")


def test_timezone_missing_property_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "Timezone=\n")]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="timedatectl did not report Timezone"):
        host.timezone(ctx, "Europe/Oslo")


def test_timezone_verification_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Timezone=UTC\n"),
        response(0),
        response(0, "Timezone=UTC\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="time zone did not change to 'Europe/Oslo'"):
        host.timezone(ctx, "Europe/Oslo")


def test_ntp_matching_enabled_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=yes\nNTP=yes\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.ntp(ctx, enabled=True)

    assert [call[0] for call in command.calls] == [
        ["timedatectl", "show", "--property=CanNTP", "--property=NTP"],
    ]
    assert ctx.changes == []


def test_ntp_matching_disabled_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=yes\nNTP=no\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.ntp(ctx, enabled=False)

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_ntp_needed_change_without_capability_fails(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=no\nNTP=no\n")]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="not available"):
        host.ntp(ctx, enabled=True)

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_ntp_enables_synchronization(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=yes\nNTP=no\n"), response(0)]
    ctx = make_host_context(tmp_path, command=command)

    host.ntp(ctx, enabled=True)

    assert [call[0] for call in command.calls] == [
        ["timedatectl", "show", "--property=CanNTP", "--property=NTP"],
        ["timedatectl", "set-ntp", "true"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data == {"ntp": True}
    assert ctx.changes[0].summary == "enable network time synchronization"


def test_ntp_disables_synchronization(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=yes\nNTP=yes\n"), response(0)]
    ctx = make_host_context(tmp_path, command=command)

    host.ntp(ctx, enabled=False)

    assert command.calls[1][0] == ["timedatectl", "set-ntp", "false"]
    assert ctx.changes[0].data == {"ntp": False}
    assert ctx.changes[0].summary == "disable network time synchronization"


def test_ntp_dry_run_reports_without_mutating(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=yes\nNTP=no\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)

    host.ntp(ctx, enabled=True)

    assert len(command.calls) == 1
    assert len(ctx.changes) == 1


def test_ntp_rejects_non_boolean_enabled(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="enabled must be a boolean"):
        host.ntp(ctx, 1)


def test_rtc_local_matching_utc_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "LocalRTC=no\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.rtc_local(ctx, enabled=False)

    assert [call[0] for call in command.calls] == [
        ["timedatectl", "show", "--property=LocalRTC"],
    ]
    assert ctx.changes == []


def test_rtc_local_matching_local_time_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "LocalRTC=yes\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.rtc_local(ctx, enabled=True)

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_rtc_local_switches_to_local_time(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "LocalRTC=no\n"),
        response(0),
        response(0, "LocalRTC=yes\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.rtc_local(ctx, enabled=True)

    assert [call[0] for call in command.calls] == [
        ["timedatectl", "show", "--property=LocalRTC"],
        ["timedatectl", "set-local-rtc", "1", "--adjust-system-clock"],
        ["timedatectl", "show", "--property=LocalRTC"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data == {"rtc_local": True}
    assert ctx.changes[0].summary == "set RTC to local time"


def test_rtc_local_switches_to_utc(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "LocalRTC=yes\n"),
        response(0),
        response(0, "LocalRTC=no\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.rtc_local(ctx, enabled=False)

    assert command.calls[1][0] == [
        "timedatectl",
        "set-local-rtc",
        "0",
        "--adjust-system-clock",
    ]
    assert ctx.changes[0].data == {"rtc_local": False}
    assert ctx.changes[0].summary == "set RTC to UTC"


def test_rtc_local_dry_run_reports_without_mutating(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "LocalRTC=no\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)

    host.rtc_local(ctx, enabled=True)

    assert len(command.calls) == 1
    assert len(ctx.changes) == 1


def test_rtc_local_rejects_non_boolean_enabled(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="enabled must be a boolean"):
        host.rtc_local(ctx, 1)


def test_rtc_local_verification_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "LocalRTC=no\n"),
        response(0),
        response(0, "LocalRTC=no\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="RTC mode did not change to local time"):
        host.rtc_local(ctx, enabled=True)


def test_locale_matching_values_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        status(
            "   System Locale: LANG=en_US.UTF-8",
            "                  LC_TIME=en_GB.UTF-8",
        )
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.locale(ctx, {"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"})

    assert [call[0] for call in command.calls] == [["localectl", "status"]]
    assert command.calls[0][1]["sudo"] is False
    assert command.calls[0][1]["env"] == {"LC_ALL": "C"}
    assert ctx.changes == []


def test_locale_ignores_kernel_command_line_settings(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        status(
            "   System Locale: LANG=en_US.UTF-8",
            "      Command Line: BOOT_IMAGE=/vmlinuz LANG=de_DE.UTF-8",
        )
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.locale(ctx, {"LANG": "en_US.UTF-8"})

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_locale_differs_sets_all_requested_values(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        status(
            "   System Locale: LANG=en_US.UTF-8",
            "                  LC_TIME=en_US.UTF-8",
        ),
        response(0),
        status(
            "   System Locale: LANG=en_US.UTF-8",
            "                  LC_TIME=en_GB.UTF-8",
        ),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.locale(ctx, {"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"})

    assert [call[0] for call in command.calls] == [
        ["localectl", "status"],
        ["localectl", "set-locale", "LANG=en_US.UTF-8", "LC_TIME=en_GB.UTF-8"],
        ["localectl", "status"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data == {
        "locale": {"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"},
    }
    assert ctx.changes[0].summary == "set locale LANG=en_US.UTF-8 LC_TIME=en_GB.UTF-8"


def test_locale_supports_language(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        status(
            "   System Locale: LANG=en_US.UTF-8",
            "                  LANGUAGE=de_DE.UTF-8",
        ),
        response(0),
        status(
            "   System Locale: LANG=en_US.UTF-8",
            "                  LANGUAGE=en_GB.UTF-8",
        ),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.locale(ctx, {"LANG": "en_US.UTF-8", "LANGUAGE": "en_GB.UTF-8"})

    assert command.calls[1][0] == [
        "localectl",
        "set-locale",
        "LANG=en_US.UTF-8",
        "LANGUAGE=en_GB.UTF-8",
    ]
    assert len(ctx.changes) == 1


def test_locale_redundant_values_equal_to_lang_are_satisfied(tmp_path):
    command = FakeCommandRunner()
    command.responses = [status("   System Locale: LANG=en_US.UTF-8")]
    ctx = make_host_context(tmp_path, command=command)

    host.locale(
        ctx,
        {
            "LANG": "en_US.UTF-8",
            "LANGUAGE": "en_US.UTF-8",
            "LC_CTYPE": "en_US.UTF-8",
        },
    )

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_locale_redundant_values_are_verified_after_setting(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        status("   System Locale: LANG=en_US.UTF-8"),
        response(0),
        status("   System Locale: LANG=de_DE.UTF-8"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.locale(ctx, {"LANG": "de_DE.UTF-8", "LC_CTYPE": "de_DE.UTF-8"})

    assert command.calls[1][0] == [
        "localectl",
        "set-locale",
        "LANG=de_DE.UTF-8",
        "LC_CTYPE=de_DE.UTF-8",
    ]
    assert len(ctx.changes) == 1


def test_locale_rejects_lc_all(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid locale variable: 'LC_ALL'"):
        host.locale(ctx, {"LC_ALL": "en_US.UTF-8"})


def test_locale_rejects_colon_separated_language(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="LANGUAGE must be a single locale name"):
        host.locale(ctx, {"LANGUAGE": "en_US.UTF-8:de_DE.UTF-8"})


def test_locale_rejects_empty_mapping(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="locale values must not be empty"):
        host.locale(ctx, {})


def test_locale_rejects_non_mapping(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="locale values must be a mapping"):
        host.locale(ctx, [("LANG", "en_US.UTF-8")])


def test_locale_rejects_invalid_values(tmp_path):
    ctx = make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="locale variable LANG must not contain whitespace"):
        host.locale(ctx, {"LANG": "en US"})
    with pytest.raises(TaskFailed, match="locale variable LANG must not contain control characters"):
        host.locale(ctx, {"LANG": "en_US\x00"})


def test_locale_dry_run_reports_without_mutating(tmp_path):
    command = FakeCommandRunner()
    command.responses = [status("   System Locale: LANG=en_US.UTF-8")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)

    host.locale(ctx, {"LANG": "en_GB.UTF-8"})

    assert len(command.calls) == 1
    assert len(ctx.changes) == 1


def test_locale_probe_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "", "localectl: unavailable")]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="localectl status failed"):
        host.locale(ctx, {"LANG": "en_US.UTF-8"})


def test_locale_verification_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        status("   System Locale: LANG=en_US.UTF-8"),
        response(0),
        status("   System Locale: LANG=en_US.UTF-8"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="locale variable LANG did not change"):
        host.locale(ctx, {"LANG": "en_GB.UTF-8"})
