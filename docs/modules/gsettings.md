# `gsettings`

Manage GNOME settings through `gsettings`.

```python
from kitup.modules import gsettings
from kitup.registry import task
```

## `key(ctx, schema, key, value=None, *, state="present")`

Set a key, or reset it to its schema default.

- `state="present"` requires `value` and sets the key only when it differs.
- `state="absent"` resets the key to its schema default when it differs.

```python
@task("Configure GNOME", tags={"desktop"})
def configure_gnome(ctx):
    gsettings.key(ctx, "org.gnome.desktop.interface", "color-scheme", "prefer-dark")


@task("Reset GNOME theme", tags={"desktop"})
def reset_gnome_theme(ctx):
    gsettings.key(ctx, "org.gnome.desktop.interface", "color-scheme", state="absent")
```

Supported value types: `str`, `bool`, `int`, `float`, `list`, and `tuple`.

## Notes

- `gsettings` always runs as the target user, never through sudo, and fails when
  kitup runs as root.
