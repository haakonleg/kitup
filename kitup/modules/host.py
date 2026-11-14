"""System-level host settings through systemd tools."""

from collections.abc import Mapping
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .._utils.validation import (
    has_control_characters,
    require_bool,
    require_choice,
    require_collection,
    require_success,
    require_text,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..registry import module
from ..results import CallResult

module("host", "Hostname, time zone, NTP, RTC, and locale.")


_LOCALE_KEYS: Final = frozenset(
    {
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
)

type _HostnameKind = Literal["static", "pretty"]


def hostname(ctx: TaskContext, name: str | None = None, *, pretty: str | None = None) -> CallResult:
    """Ensure the static and/or pretty system hostname.

    ``name`` manages the static hostname. ``pretty`` manages the pretty
    hostname; an empty string clears it. At least one must be given.
    """
    if name is None and pretty is None:
        raise TaskFailed("hostname requires name or pretty")

    static = None if name is None else _require_hostname(name)
    pretty_name = None if pretty is None else _require_pretty(pretty)

    calls: list[CallResult] = []
    hostnames: dict[str, str] = {}
    if static is not None:
        calls.append(_converge_hostname(ctx, "static", static))
        hostnames["static"] = static
    if pretty_name is not None:
        calls.append(_converge_hostname(ctx, "pretty", pretty_name))
        hostnames["pretty"] = pretty_name
    return ctx.changed(
        changed=any(call.changed for call in calls),
        summary="; ".join(call.summary for call in calls),
        hostnames=hostnames,
    )


def timezone(ctx: TaskContext, zone: str) -> CallResult:
    """Ensure the system time zone."""
    _require_token(zone, "time zone")
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise TaskFailed(f"invalid time zone: {zone!r}") from exc

    current = _timedatectl_property(ctx, "Timezone")
    if current == zone:
        return ctx.changed(changed=False, summary=f"ensure time zone {zone}", timezone=zone)

    call = CallResult(changed=True, summary=f"set time zone {zone}", timezone=zone)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(["timedatectl", "set-timezone", zone], sudo=True)
    if _timedatectl_property(ctx, "Timezone") != zone:
        raise TaskFailed(f"time zone did not change to {zone!r}")
    return ctx.changed(call)


def ntp(
    ctx: TaskContext,
    enabled: bool,  # noqa: FBT001 - positional boolean matches the module API
) -> CallResult:
    """Enable or disable network time synchronization."""
    require_bool(enabled, "enabled")

    values = _timedatectl_show(ctx, "CanNTP", "NTP")
    current = _parse_yes_no(values["NTP"], "NTP")
    action = "enable" if enabled else "disable"
    if current == enabled:
        return ctx.changed(changed=False, summary=f"ensure network time synchronization is {action}d", ntp=enabled)
    if not _parse_yes_no(values["CanNTP"], "CanNTP"):
        raise TaskFailed("network time synchronization is not available (CanNTP=no)")

    call = CallResult(changed=True, summary=f"{action} network time synchronization", ntp=enabled)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(
        ["timedatectl", "set-ntp", "true" if enabled else "false"],
        sudo=True,
    )
    return ctx.changed(call)


def rtc_local(
    ctx: TaskContext,
    enabled: bool = False,  # noqa: FBT001, FBT002 - positional boolean matches the module API
) -> CallResult:
    """Configure whether the hardware clock is interpreted as local time."""
    require_bool(enabled, "enabled")

    current = _parse_yes_no(_timedatectl_property(ctx, "LocalRTC"), "LocalRTC")
    mode = "local time" if enabled else "UTC"
    if current == enabled:
        return ctx.changed(changed=False, summary=f"ensure RTC is {mode}", rtc_local=enabled)

    call = CallResult(changed=True, summary=f"set RTC to {mode}", rtc_local=enabled)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(
        ["timedatectl", "set-local-rtc", "1" if enabled else "0", "--adjust-system-clock"],
        sudo=True,
    )
    if _parse_yes_no(_timedatectl_property(ctx, "LocalRTC"), "LocalRTC") != enabled:
        raise TaskFailed(f"RTC mode did not change to {mode}")
    return ctx.changed(call)


def locale(ctx: TaskContext, values: Mapping[str, str]) -> CallResult:
    """Ensure system locale variables, leaving unmanaged variables unchanged."""
    if not isinstance(values, Mapping):
        raise TaskFailed("locale values must be a mapping")
    require_collection(values, "locale values")

    desired: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TaskFailed("locale keys and values must be strings")
        require_choice(key, _LOCALE_KEYS, "locale variable")
        _require_token(value, f"locale variable {key}")
        if key == "LANGUAGE" and ":" in value:
            raise TaskFailed("LANGUAGE must be a single locale name")
        desired[key] = value

    assignments = [f"{key}={value}" for key, value in desired.items()]
    current = _localectl_status(ctx)
    if all(_locale_satisfied(current, key, value) for key, value in desired.items()):
        return ctx.changed(changed=False, summary=f"ensure locale {' '.join(assignments)}", locale=desired)

    call = CallResult(changed=True, summary=f"set locale {' '.join(assignments)}", locale=desired)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(["localectl", "set-locale", *assignments], sudo=True)

    updated = _localectl_status(ctx)
    for key, value in desired.items():
        if not _locale_satisfied(updated, key, value):
            raise TaskFailed(f"locale variable {key} did not change to {value!r}")
    return ctx.changed(call)


def _converge_hostname(ctx: TaskContext, kind: _HostnameKind, desired: str) -> CallResult:
    current = _hostnamectl_get(ctx, kind)
    clearing = kind == "pretty" and desired == ""
    if current == desired:
        summary = "ensure pretty hostname is cleared" if clearing else f"ensure {kind} hostname {desired}"
        return CallResult(changed=False, summary=summary, hostname=desired, kind=kind)

    summary = "clear pretty hostname" if clearing else f"set {kind} hostname {desired}"
    call = CallResult(changed=True, summary=summary, hostname=desired, kind=kind)
    if ctx.dry_run:
        return call
    ctx.run(["hostnamectl", "hostname", f"--{kind}", desired], sudo=True)
    if _hostnamectl_get(ctx, kind) != desired:
        raise TaskFailed(f"{kind} hostname did not change to {desired!r}")
    return call


def _hostnamectl_get(ctx: TaskContext, kind: _HostnameKind) -> str:
    result = ctx.probe(["hostnamectl", "hostname", f"--{kind}"], sudo=False)
    require_success(result, f"hostnamectl hostname --{kind} failed")
    return result.stdout.strip()


def _timedatectl_show(ctx: TaskContext, *properties: str) -> dict[str, str]:
    argv = ["timedatectl", "show", *(f"--property={property}" for property in properties)]
    result = ctx.probe(argv, sudo=False)
    require_success(result, "timedatectl show failed")

    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()

    for property in properties:
        if not values.get(property):
            raise TaskFailed(f"timedatectl did not report {property}")
    return values


def _timedatectl_property(ctx: TaskContext, property: str) -> str:
    return _timedatectl_show(ctx, property)[property]


def _localectl_status(ctx: TaskContext) -> dict[str, str]:
    result = ctx.probe(["localectl", "status"], sudo=False, env={"LC_ALL": "C"})
    require_success(result, "localectl status failed")

    status: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("Command Line:"):
            continue
        if line.startswith("System Locale:"):
            line = line.removeprefix("System Locale:").strip()
        status.update(_parse_locale_entries(line))
    return status


def _parse_locale_entries(line: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for token in line.split():
        key, separator, value = token.partition("=")
        if separator and key in _LOCALE_KEYS:
            entries[key] = value
    return entries


def _locale_satisfied(current: Mapping[str, str], key: str, desired: str) -> bool:
    value = current.get(key, "")
    if value == desired:
        return True
    # localectl simplifies LANGUAGE and LC_* values that equal LANG.
    collapsible = key == "LANGUAGE" or key.startswith("LC_")
    return value == "" and collapsible and current.get("LANG", "") == desired


def _parse_yes_no(value: str, label: str) -> bool:
    if value == "yes":
        return True
    if value == "no":
        return False
    raise TaskFailed(f"unexpected {label} value: {value!r}")


def _require_hostname(value: str) -> str:
    _require_token(value, "static hostname")
    if "?" in value:
        raise TaskFailed("static hostname must not contain '?'")
    return value


def _require_pretty(value: str) -> str:
    if has_control_characters(value):
        raise TaskFailed("pretty hostname must not contain control characters")
    return value.strip()


def _require_token(value: str, label: str) -> None:
    require_text(value, label)
    if any(char.isspace() for char in value):
        raise TaskFailed(f"{label} must not contain whitespace")
    if has_control_characters(value):
        raise TaskFailed(f"{label} must not contain control characters")
