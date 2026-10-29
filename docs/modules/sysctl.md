# `sysctl`

Manage Linux kernel parameters (sysctls): set them at runtime and optionally
persist them in a sysctl configuration file.

```python
from kitup.modules import sysctl
from kitup.registry import task
```

## `key(ctx, name, value=None, *, state="present", file=None)`

Set a sysctl key, or remove its entry from a configuration file.

- `name` — dot-separated key, for example `vm.swappiness`. Slashes are
  normalized to dots, so `net/ipv4/ip_forward` works too.
- `value` — desired value: `str`, `int`, `float`, or `bool` (`True`/`False`
  become `1`/`0`). Required when `state="present"`.
- `state` — `"present"` or `"absent"`.
- `file` — absolute path of a sysctl configuration file, for example
  `/etc/sysctl.d/99-kitup.conf`. When given, a `name = value` entry is also
  managed in that file. When `None` (the default), only the runtime value is
  changed.

```python
@task("Reduce swap usage", tags={"tuning"})
def reduce_swappiness(ctx):
    sysctl.key(ctx, "vm.swappiness", 10, file="/etc/sysctl.d/99-kitup.conf")


@task("Enable IPv4 forwarding for now", tags={"network"})
def enable_forwarding(ctx):
    sysctl.key(ctx, "net.ipv4.ip_forward", True)


@task("Stop managing kernel.panic", tags={"tuning"})
def forget_panic(ctx):
    sysctl.key(ctx, "kernel.panic", state="absent", file="/etc/sysctl.d/99-kitup.conf")
```

## Behavior

- `state="present"` reads the current value with `sysctl -n` first. An unknown
  key fails the task before any change is made. When the value differs,
  `sysctl -w name=value` applies it at runtime; multi-value keys (such as
  `kernel.printk`) are compared with normalized whitespace.
- When `file` is set, all existing `name = ...` lines are removed and
  `name = value` is appended. A missing file is created with mode `0o644`; an
  existing file keeps its mode. Commented-out lines are left alone. The parent
  directory must exist. `--dry-run` reports the change without writing, and
  `--diff` prints a unified diff for the file.
- `state="absent"` requires `file` and removes every matching entry from it.
  The runtime value is not changed; it persists until the next reboot.

## Notes

- `sysctl` must be installed.
- All mutating operations request sudo automatically.
- Only Linux is supported.
