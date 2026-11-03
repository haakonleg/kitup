# `host`

Manage system-level host settings through systemd: static and pretty hostname,
time zone, network time synchronization, RTC mode, and system locale.

```python
from kitup.modules import host
from kitup.registry import task
```

## `hostname(ctx, name=None, *, pretty=None)`

Ensure the static and/or pretty system hostname.

- `name` — static hostname, for example `workstation`. `None` leaves it
  unmanaged.
- `pretty` — pretty hostname, for example `Workstation`. `None` leaves it
  unmanaged; an empty string clears it.

At least one of `name` or `pretty` is required. Static hostnames must be valid
lower-case DNS names and must not contain `?`. Surrounding whitespace is trimmed
from `pretty`.

```python
@task("Configure the hostname")
def configure_hostname(ctx):
    host.hostname(ctx, "workstation", pretty="Workstation")


@task("Clear the pretty hostname")
def clear_pretty_hostname(ctx):
    host.hostname(ctx, pretty="")
```

Only the requested hostname forms are changed. `host.hostname(ctx, name)` does
not modify the pretty or transient hostname.

## `timezone(ctx, zone)`

Ensure the system time zone.

- `zone` — IANA time zone name, for example `Europe/Oslo`.

```python
@task("Set the time zone")
def set_timezone(ctx):
    host.timezone(ctx, "Europe/Oslo")
```

Setting the time zone may also adjust the RTC when it is configured as local
time.

## `ntp(ctx, enabled)`

Enable or disable network time synchronization.

- `enabled` — `True` to enable, `False` to disable.

```python
@task("Enable network time synchronization")
def enable_ntp(ctx):
    host.ntp(ctx, True)
```

Fails when network time synchronization is unavailable.

## `rtc_local(ctx, enabled=False)`

Configure whether the hardware clock is interpreted as local time.

- `enabled` — `False` keeps the RTC in UTC (the default); `True` switches it to
  local time.

```python
@task("Keep the RTC in UTC")
def keep_rtc_utc(ctx):
    host.rtc_local(ctx, False)
```

Changing the RTC mode may adjust the system clock.

## `locale(ctx, values)`

Ensure system locale variables, leaving variables that are not listed
unchanged.

- `values` — mapping of locale variable names to values, for example
  `{"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"}`.

Supported keys are `LANG`, `LANGUAGE`, and the `LC_*` variables accepted by
`localectl`: `LC_CTYPE`, `LC_NUMERIC`, `LC_TIME`, `LC_COLLATE`, `LC_MONETARY`,
`LC_MESSAGES`, `LC_PAPER`, `LC_NAME`, `LC_ADDRESS`, `LC_TELEPHONE`,
`LC_MEASUREMENT`, and `LC_IDENTIFICATION`.

```python
@task("Set the system locale")
def set_locale(ctx):
    host.locale(ctx, {"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"})
```

`LC_ALL` is rejected. `LANGUAGE` must be a single locale name; colon-separated
lists are rejected.

## Behavior

- Every function reads the current state first and changes only what differs.
  A second run with the same arguments reports no changes.
- `--dry-run` reports the changes without modifying the system.
- Mutations request sudo automatically.
- Changes are verified after the mutation, except for NTP.

## Notes

- Requires systemd.
- `locale` only sets and updates variables; it cannot remove them.
- Kernel command line locale settings take precedence over `/etc/locale.conf`.
- Locale changes may require a new session or reboot to take effect.
