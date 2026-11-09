# `systemd`

Manage systemd units.

```python
from kitup.modules import systemd
from kitup import task
```

## `unit(ctx, unit, *, enabled=None, state=None, scope="system")`

Manage a unit's enabled and active state. At least one of `enabled` or `state`
must be provided.

### Parameters

- `unit` — unit name, for example `sshd.service`.
- `enabled` — `True`, `False`, or `None` to leave unchanged.
- `state` — `"started"`, `"stopped"`, or `None` to leave unchanged.
- `scope` — `"system"` or `"user"`.

### Result fields

- `unit` — unit name.
- `scope` — `"system"` or `"user"`.
- `actions` — list of `"enable"`, `"disable"`, `"start"`, or `"stop"` actions
  performed; empty when nothing changed.

```python
@task("Enable example service")
def enable_example_service(ctx):
    systemd.unit(ctx, "example.service", enabled=True, state="started")
```

## `daemon_reload(ctx, *, scope="system")`

Reload systemd manager configuration.

This always runs `systemctl daemon-reload` and reports a change.

### Parameters

- `scope` — `"system"` or `"user"`.

### Result fields

- `scope` — `"system"` or `"user"`.

## Notes

- System scope requests sudo. User scope runs without sudo and fails when kitup
  runs as root.
