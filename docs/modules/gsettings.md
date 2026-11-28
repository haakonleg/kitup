# `gsettings`

Manage GNOME settings through `gsettings`.

```python
from kitup.modules import gsettings
```

## `key(schema, key, value, *, state="present")`

Set a key, or reset it to its schema default.

### Parameters

- `schema` — schema name, such as `org.gnome.desktop.interface`.
- `key` — key name within the schema.
- `value` — desired value. Required unless `state="absent"`. For maybe-typed
  keys, `None` sets `nothing`, so omit `value` when resetting a key.
- `state` — `"present"` (default) sets the key; `"absent"` resets it to the
  schema default.

### Result fields

- `schema` — schema name.
- `key` — key name.
- `value` — requested value (set results).

```python
gsettings.key("org.gnome.desktop.interface", "color-scheme", "prefer-dark")
gsettings.key("org.gnome.desktop.interface", "text-scaling-factor", 1.25)
gsettings.key("org.gnome.desktop.interface", "color-scheme", state="absent")
```

## Value types

Values are converted from Python types to the type the key expects.

| Python | Value |
| --- | --- |
| `bool` | boolean |
| `int` | integer |
| `float` | double |
| `str` | string, object path, or signature |
| `None` | maybe-typed value `nothing` |
| `list` | array; elements must share a type |
| `tuple` | tuple |
| `dict` | dictionary |
| `bytes` | byte array |

```python
gsettings.key("org.gnome.desktop.interface", "enable-animations", False)
gsettings.key("org.example", "favorites", ["firefox.desktop", "org.gnome.Terminal.desktop"])
gsettings.key("org.example", "keyboard-layout", ("us", "no"))
gsettings.key("org.example", "labels", {"open": "Open", "close": "Close"})
gsettings.key("org.example", "icon-data", b"icon-data")
gsettings.key("org.example", "optional-name", None)
```

## `variant(text)`

Use verbatim GVariant text for a value that has no native Python mapping, such
as a variant key or a dictionary whose values are variants. Variants are
written in angle brackets, and the text must be valid for the key's type.

```python
gsettings.key("org.example", "pinned-value", gsettings.variant("<42>"))
gsettings.key(
    "org.example",
    "actions",
    {"enabled": gsettings.variant("<true>"), "count": gsettings.variant("<3>")},
)
```

## Notes

- `gsettings` runs as the current user and cannot be used when kitup runs as
  root.
