# `gsettings`

Manage GNOME settings through `gsettings`.

```python
from kitup.modules import gsettings
```

## `key(schema, key, value=None, *, state="present")`

Set a key, or reset it to its schema default.

### Parameters

- `schema` — schema name, such as `org.gnome.desktop.interface`.
- `key` — key name within the schema.
- `value` — desired value. Required when `state="present"` and rejected when
  `state="absent"`.
- `state="present"` requires `value` and sets the key only when it differs.
- `state="absent"` resets the key to its schema default when it differs.

### Result fields

- `schema` — schema name.
- `key` — key name.
- `value` — requested value (set results).

```python
gsettings.key("org.gnome.desktop.interface", "color-scheme", "prefer-dark")
gsettings.key("org.gnome.desktop.interface", "color-scheme", state="absent")
```

Supported value types: `str`, `bool`, `int`, `float`, `list`, and `tuple`.

## Notes

- `gsettings` always runs as the target user, never through sudo, and fails when
  kitup runs as root.
