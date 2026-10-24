# `gsettings`

Manage GNOME settings through `gsettings`.

```python
from kitup.modules import gsettings
```

## `key(ctx, schema, key, value=None, *, state="present")`

Set a key, or reset it to its schema default.

- `state="present"` requires `value` and sets the key only when it differs.
- `state="absent"` runs `gsettings reset` when the value differs from the schema
  default.

```python
@bundle.task("Configure GNOME", tags={"desktop"})
def configure_gnome(ctx):
    gsettings.key(ctx, "org.gnome.desktop.interface", "color-scheme", "prefer-dark")


@bundle.task("Reset GNOME theme", tags={"desktop"})
def reset_gnome_theme(ctx):
    gsettings.key(ctx, "org.gnome.desktop.interface", "color-scheme", state="absent")
```

Supported value types: `str` (quoted, control characters escaped), `bool`, `int`,
`float`, `list` (GVariant array), and `tuple` (GVariant tuple).

## Notes

- The current value is read with `gsettings get` first; leading type annotations
  such as `@as` are ignored when comparing.
- gsettings always runs as the target user, never through sudo, and fails clearly
  when kitup runs as root.
- Dry-run reports the change without setting or resetting the key.
