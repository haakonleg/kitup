"""Unit tests for the host module."""

import pytest

from kitup.errors import TaskFailed
from kitup.modules import host

from .helpers import FakeCommandRunner, argvs, call_data, make_context, response


def status(*lines: str):
    return response(0, "\n".join(lines) + "\n")


def make_host_context(tmp_path, *, dry_run: bool = False, command=None):
    return make_context(
        tmp_path,
        dry_run=dry_run,
        command=command or FakeCommandRunner(),
    )


def test_hostname_matching_noops(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "workstation\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname("workstation")
    assert argvs(command) == [["hostnamectl", "hostname", "--static"]]
    assert command.calls[0][1]["sudo"] is False
    assert ctx.changes == []

    command.responses = [response(0, "Workstation\n")]
    ctx = make_host_context(tmp_path, command=command)
    host.hostname(pretty="Workstation")
    assert command.calls[-1][0] == ["hostnamectl", "hostname", "--pretty"]
    assert ctx.changes == []

    # Input is trimmed before comparison.
    command.responses = [response(0, "Workstation\n")]
    ctx = make_host_context(tmp_path, command=command)
    host.hostname(pretty="  Workstation  ")
    assert command.calls[-1][0] == ["hostnamectl", "hostname", "--pretty"]
    assert ctx.changes == []


def test_static_hostname_differs_is_set_and_verified(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "workstation\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname("workstation")

    assert argvs(command) == [
        ["hostnamectl", "hostname", "--static"],
        ["hostnamectl", "hostname", "--static", "workstation"],
        ["hostnamectl", "hostname", "--static"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {"hostnames": {"static": "workstation"}}
    assert ctx.changes[0].summary == "set static hostname workstation"


def test_pretty_hostname_differs_is_set_and_verified(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "Workstation\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(pretty="Workstation")

    assert argvs(command) == [
        ["hostnamectl", "hostname", "--pretty"],
        ["hostnamectl", "hostname", "--pretty", "Workstation"],
        ["hostnamectl", "hostname", "--pretty"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {"hostnames": {"pretty": "Workstation"}}
    assert ctx.changes[0].summary == "set pretty hostname Workstation"


def test_empty_pretty_hostname_is_cleared(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "fedora\n"),
        response(0),
        response(0, "\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.hostname(pretty="")

    assert argvs(command) == [
        ["hostnamectl", "hostname", "--pretty"],
        ["hostnamectl", "hostname", "--pretty", ""],
        ["hostnamectl", "hostname", "--pretty"],
    ]
    assert call_data(ctx.changes[0]) == {"hostnames": {"pretty": ""}}
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

    host.hostname("workstation", pretty="Workstation")

    assert argvs(command) == [
        ["hostnamectl", "hostname", "--static"],
        ["hostnamectl", "hostname", "--static", "workstation"],
        ["hostnamectl", "hostname", "--static"],
        ["hostnamectl", "hostname", "--pretty"],
        ["hostnamectl", "hostname", "--pretty", "Workstation"],
        ["hostnamectl", "hostname", "--pretty"],
    ]
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {
        "hostnames": {"static": "workstation", "pretty": "Workstation"},
    }
    assert ctx.changes[0].summary == "set static hostname workstation; set pretty hostname Workstation"


def test_dry_run_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "fedora\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)
    host.hostname("workstation")
    assert argvs(command) == [["hostnamectl", "hostname", "--static"]]
    assert len(ctx.changes) == 1
    assert ctx.changes[0].summary == "set static hostname workstation"

    command.responses = [response(0, "Timezone=UTC\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)
    host.timezone("Europe/Oslo")
    assert command.calls[-1][0] == ["timedatectl", "show", "--property=Timezone"]
    assert len(ctx.changes) == 1

    command.responses = [response(0, "CanNTP=yes\nNTP=no\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)
    host.ntp(enabled=True)
    assert command.calls[-1][0] == ["timedatectl", "show", "--property=CanNTP", "--property=NTP"]
    assert len(ctx.changes) == 1

    command.responses = [response(0, "LocalRTC=no\n")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)
    host.rtc_local(enabled=True)
    assert command.calls[-1][0] == ["timedatectl", "show", "--property=LocalRTC"]
    assert len(ctx.changes) == 1

    command.responses = [status("   System Locale: LANG=en_US.UTF-8")]
    ctx = make_host_context(tmp_path, dry_run=True, command=command)
    host.locale({"LANG": "en_GB.UTF-8"})
    assert command.calls[-1][0] == ["localectl", "status"]
    assert len(ctx.changes) == 1


def test_invalid_arguments_are_rejected(tmp_path):
    make_host_context(tmp_path)

    with pytest.raises(TaskFailed, match="requires name or pretty"):
        host.hostname()
    with pytest.raises(TaskFailed, match="static hostname must not be empty"):
        host.hostname("  ")
    with pytest.raises(TaskFailed, match="static hostname must not contain whitespace"):
        host.hostname("work station")
    with pytest.raises(TaskFailed, match="must not contain '\\?'"):
        host.hostname("workstation?")
    with pytest.raises(TaskFailed, match="pretty hostname must not contain control characters"):
        host.hostname(pretty="Work\nstation")
    with pytest.raises(TaskFailed, match="time zone must not contain whitespace"):
        host.timezone("Europe/ Oslo")
    with pytest.raises(TaskFailed, match="enabled must be a boolean"):
        host.ntp(1)
    with pytest.raises(TaskFailed, match="enabled must be a boolean"):
        host.rtc_local(1)
    with pytest.raises(TaskFailed, match="invalid locale variable: 'LC_ALL'"):
        host.locale({"LC_ALL": "en_US.UTF-8"})
    with pytest.raises(TaskFailed, match="LANGUAGE must be a single locale name"):
        host.locale({"LANGUAGE": "en_US.UTF-8:de_DE.UTF-8"})
    with pytest.raises(TaskFailed, match="locale values must not be empty"):
        host.locale({})
    with pytest.raises(TaskFailed, match="locale values must be a mapping"):
        host.locale([("LANG", "en_US.UTF-8")])
    with pytest.raises(TaskFailed, match="locale variable LANG must not contain whitespace"):
        host.locale({"LANG": "en US"})
    with pytest.raises(TaskFailed, match="locale variable LANG must not contain control characters"):
        host.locale({"LANG": "en_US\x00"})


def test_hostname_failures(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "", "hostnamectl: unavailable")]
    make_host_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="hostnamectl hostname --static failed"):
        host.hostname("workstation")

    command.responses = [response(0, "fedora\n"), response(0), response(0, "fedora\n")]
    make_host_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="static hostname did not change to 'workstation'"):
        host.hostname("workstation")


def test_timezone_matching_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "Timezone=Europe/Oslo\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.timezone("Europe/Oslo")

    assert argvs(command) == [
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

    host.timezone("Europe/Oslo")

    assert argvs(command) == [
        ["timedatectl", "show", "--property=Timezone"],
        ["timedatectl", "set-timezone", "Europe/Oslo"],
        ["timedatectl", "show", "--property=Timezone"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {"timezone": "Europe/Oslo"}
    assert ctx.changes[0].action == "host.timezone"
    assert ctx.changes[0].summary == "set time zone Europe/Oslo"


def test_timezone_invalid_zone_fails_before_probing(tmp_path):
    command = FakeCommandRunner()
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="invalid time zone"):
        host.timezone("Not/AZone")

    assert command.calls == []
    assert ctx.changes == []


def test_timezone_failures(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "", "timedatectl: unavailable")]
    make_host_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="timedatectl show failed"):
        host.timezone("Europe/Oslo")

    command.responses = [response(0, "Timezone=\n")]
    make_host_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="timedatectl did not report Timezone"):
        host.timezone("Europe/Oslo")

    command.responses = [
        response(0, "Timezone=UTC\n"),
        response(0),
        response(0, "Timezone=UTC\n"),
    ]
    make_host_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="time zone did not change to 'Europe/Oslo'"):
        host.timezone("Europe/Oslo")


def test_ntp_matching_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=yes\nNTP=yes\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.ntp(enabled=True)
    assert argvs(command) == [
        ["timedatectl", "show", "--property=CanNTP", "--property=NTP"],
    ]
    assert ctx.changes == []

    command.responses = [response(0, "CanNTP=yes\nNTP=no\n")]
    ctx = make_host_context(tmp_path, command=command)
    host.ntp(enabled=False)
    assert command.calls[-1][0] == ["timedatectl", "show", "--property=CanNTP", "--property=NTP"]
    assert ctx.changes == []


def test_ntp_needed_change_without_capability_fails(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=no\nNTP=no\n")]
    ctx = make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="not available"):
        host.ntp(enabled=True)

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_ntp_synchronization_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "CanNTP=yes\nNTP=no\n"), response(0)]
    ctx = make_host_context(tmp_path, command=command)

    host.ntp(enabled=True)
    assert argvs(command) == [
        ["timedatectl", "show", "--property=CanNTP", "--property=NTP"],
        ["timedatectl", "set-ntp", "true"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {"ntp": True}
    assert ctx.changes[0].summary == "enable network time synchronization"

    command.responses = [response(0, "CanNTP=yes\nNTP=yes\n"), response(0)]
    ctx = make_host_context(tmp_path, command=command)
    host.ntp(enabled=False)
    assert command.calls[-1][0] == ["timedatectl", "set-ntp", "false"]
    assert call_data(ctx.changes[0]) == {"ntp": False}
    assert ctx.changes[0].summary == "disable network time synchronization"


def test_rtc_local_matching_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "LocalRTC=no\n")]
    ctx = make_host_context(tmp_path, command=command)

    host.rtc_local(enabled=False)
    assert argvs(command) == [
        ["timedatectl", "show", "--property=LocalRTC"],
    ]
    assert ctx.changes == []

    command.responses = [response(0, "LocalRTC=yes\n")]
    ctx = make_host_context(tmp_path, command=command)
    host.rtc_local(enabled=True)
    assert command.calls[-1][0] == ["timedatectl", "show", "--property=LocalRTC"]
    assert ctx.changes == []


def test_rtc_local_switch_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "LocalRTC=no\n"),
        response(0),
        response(0, "LocalRTC=yes\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.rtc_local(enabled=True)
    assert argvs(command) == [
        ["timedatectl", "show", "--property=LocalRTC"],
        ["timedatectl", "set-local-rtc", "1", "--adjust-system-clock"],
        ["timedatectl", "show", "--property=LocalRTC"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {"rtc_local": True}
    assert ctx.changes[0].summary == "set RTC to local time"

    command.responses = [
        response(0, "LocalRTC=yes\n"),
        response(0),
        response(0, "LocalRTC=no\n"),
    ]
    ctx = make_host_context(tmp_path, command=command)
    host.rtc_local(enabled=False)
    assert command.calls[-2][0] == [
        "timedatectl",
        "set-local-rtc",
        "0",
        "--adjust-system-clock",
    ]
    assert call_data(ctx.changes[0]) == {"rtc_local": False}
    assert ctx.changes[0].summary == "set RTC to UTC"


def test_rtc_local_verification_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "LocalRTC=no\n"),
        response(0),
        response(0, "LocalRTC=no\n"),
    ]
    make_host_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="RTC mode did not change to local time"):
        host.rtc_local(enabled=True)


def test_locale_matching_values_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        status(
            "   System Locale: LANG=en_US.UTF-8",
            "                  LC_TIME=en_GB.UTF-8",
        )
    ]
    ctx = make_host_context(tmp_path, command=command)

    host.locale({"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"})

    assert argvs(command) == [["localectl", "status"]]
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

    host.locale({"LANG": "en_US.UTF-8"})

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

    host.locale({"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"})

    assert argvs(command) == [
        ["localectl", "status"],
        ["localectl", "set-locale", "LANG=en_US.UTF-8", "LC_TIME=en_GB.UTF-8"],
        ["localectl", "status"],
    ]
    assert command.calls[1][1]["sudo"] is True
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0]) == {
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

    host.locale({"LANG": "en_US.UTF-8", "LANGUAGE": "en_GB.UTF-8"})

    assert command.calls[1][0] == [
        "localectl",
        "set-locale",
        "LANG=en_US.UTF-8",
        "LANGUAGE=en_GB.UTF-8",
    ]
    assert len(ctx.changes) == 1


def test_locale_redundant_values(tmp_path):
    command = FakeCommandRunner()
    command.responses = [status("   System Locale: LANG=en_US.UTF-8")]
    ctx = make_host_context(tmp_path, command=command)

    host.locale(
        {
            "LANG": "en_US.UTF-8",
            "LANGUAGE": "en_US.UTF-8",
            "LC_CTYPE": "en_US.UTF-8",
        },
    )
    assert len(command.calls) == 1
    assert ctx.changes == []

    command.responses = [
        status("   System Locale: LANG=en_US.UTF-8"),
        response(0),
        status("   System Locale: LANG=de_DE.UTF-8"),
    ]
    ctx = make_host_context(tmp_path, command=command)
    host.locale({"LANG": "de_DE.UTF-8", "LC_CTYPE": "de_DE.UTF-8"})
    assert command.calls[-2][0] == [
        "localectl",
        "set-locale",
        "LANG=de_DE.UTF-8",
        "LC_CTYPE=de_DE.UTF-8",
    ]
    assert len(ctx.changes) == 1


def test_locale_failures(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "", "localectl: unavailable")]
    make_host_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="localectl status failed"):
        host.locale({"LANG": "en_US.UTF-8"})

    command.responses = [
        status("   System Locale: LANG=en_US.UTF-8"),
        response(0),
        status("   System Locale: LANG=en_US.UTF-8"),
    ]
    make_host_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="locale variable LANG did not change"):
        host.locale({"LANG": "en_GB.UTF-8"})
