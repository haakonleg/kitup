# `systemd`

Manage systemd units.

```python
from kitup.modules import systemd
```

## `unit(ctx, unit, *, enabled=None, state=None, scope="system")`

Manage a unit's enabled and active state.

- `unit` — unit name, for example `sshd.service`.
- `enabled` — `True`, `False`, or `None` to leave unchanged.
- `state` — `"started"`, `"stopped"`, or `None` to leave unchanged.
- `scope` — `"system"` or `"user"`.

```python
@bundle.task("Enable example service")
def enable_example_service(ctx):
    systemd.unit(ctx, "example.service", enabled=True, state="started")
```

## `daemon_reload(ctx, *, scope="system")`

Reload systemd manager configuration.

## Notes

- State is queried first; only differences produce commands.
- System scope requests sudo itself. User scope uses `systemctl --user` and
  fails clearly when kitup runs as root.
- Dry-run reports changes without acting.
