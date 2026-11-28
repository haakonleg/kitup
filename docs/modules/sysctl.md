# `sysctl`

Manage Linux kernel parameters (sysctls): set them at runtime and optionally
persist them in a sysctl configuration file.

```python
from kitup.modules import sysctl
```

## `key(name, value=None, *, state="present", file=None)`

Set a sysctl key, or remove its entry from a configuration file.

### Parameters

- `name` — dot-separated key, for example `vm.swappiness`. Slashes are accepted
  as separators, so `net/ipv4/ip_forward` works too.
- `value` — desired value: `str`, `int`, `float`, or `bool` (`True`/`False`
  become `1`/`0`). Required when `state="present"` and rejected when
  `state="absent"`.
- `state` — `"present"` or `"absent"`.
- `file` — absolute path of a sysctl configuration file, for example
  `/etc/sysctl.d/99-kitup.conf`. When given, a `name = value` entry is also
  managed in that file. When `None` (the default), only the runtime value is
  changed.

### Result fields

- `key` — normalized key name.
- `value` — formatted desired value (present results).
- `runtime` — whether the runtime value changed (present results).
- `path` — managed sysctl file (present results when `file` is set; absent
  results).

```python
sysctl.key("vm.swappiness", 10, file="/etc/sysctl.d/99-kitup.conf")
sysctl.key("net.ipv4.ip_forward", True)
sysctl.key("kernel.panic", state="absent", file="/etc/sysctl.d/99-kitup.conf")
```

## Behavior

- When `file` is set, the entry is created or updated. The parent directory
  must exist. `--dry-run` reports the change without writing, and `--diff`
  prints a unified diff for the file.
- `state="absent"` requires `file` and removes matching entries. The runtime
  value is not changed; it persists until the next reboot.

## Notes

- All mutating operations request sudo automatically.
