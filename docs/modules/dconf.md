# `dconf`

Manage dconf database entries by path, for use cases not covered by
`gsettings`.

```python
from kitup.modules import dconf
```

dconf stores values at absolute paths such as
`/org/gnome/desktop/interface/color-scheme`. It does not use schemas and does
not check a key's type, so a value must be valid GVariant text.

## `write(path, value, *, state="present")`

Set a key, or reset it to the value provided by the system databases.

### Parameters

- `path` — absolute key path. It starts with `/`, does not end with `/`, and
  contains no `//`. Each path element must not contain `=`, `[`, or `]`.
  Paths must also be representable in a dconf keyfile: they cannot contain
  control characters or start with `#`, and cannot end with whitespace.
- `value` — desired value. Required unless `state="absent"`.
- `state` — `"present"` (default) sets the key; `"absent"` removes the value
  from the user database.

### Result fields

- `paths` — key paths in the call.
- `values` — requested values (present results).

```python
dconf.write("/org/gnome/desktop/interface/color-scheme", "prefer-dark")
dconf.write("/org/gnome/desktop/interface/text-scaling-factor", 1.25)
dconf.write("/org/gnome/desktop/interface/color-scheme", state="absent")
```

## `write(mapping, *, state="present")`

Set several keys in one call. The mapping maps absolute key paths to values,
and the result reports one `paths` entry and one `values` entry per key.

```python
dconf.write(
    {
        "/org/gnome/desktop/interface/color-scheme": "prefer-dark",
        "/org/gnome/desktop/interface/text-scaling-factor": 1.25,
    }
)
```

`state="absent"` applies to a single path only.

## `read(paths)`

Read keys without changing anything. A single path returns the effective value
as canonical GVariant text, or `None` when the key is unset. A sequence of paths
returns a mapping of paths to values.

```python
scheme = dconf.read("/org/gnome/desktop/interface/color-scheme")
values = dconf.read(
    [
        "/org/gnome/desktop/interface/color-scheme",
        "/org/gnome/desktop/interface/text-scaling-factor",
    ]
)
```

Values include defaults from the system databases, matching `dconf read`.

## `dump(directory="/")`

Return the keyfile text for a subtree, in the format accepted by `load()`.

```python
keyfile = dconf.dump("/org/gnome/desktop/interface/")
```

## `load(directory, content=None, *, source=None)`

Set keys under `directory` from keyfile text or a source file. Pass `content`
with keyfile text, or `source` with the path to a keyfile. Keys absent from the
keyfile are left untouched, so `load()` only adds or updates values.

### Parameters

- `directory` — directory the keys are loaded under. It must end with `/`.
- `content` — keyfile text. Mutually exclusive with `source`.
- `source` — keyfile path. Relative paths use the project and bundle lookup
  described in [Projects](../project.md#resources). Mutually exclusive with
  `content`.

### Result fields

- `dir` — directory the keys are loaded under.
- `paths` — absolute key paths defined by the keyfile.
- `src` — resolved source path, when `source` is used.

```python
dconf.load(
    "/org/gnome/desktop/interface/",
    "[/]\ncolor-scheme='prefer-dark'\ntext-scaling-factor=1.25\n",
)

dconf.load("/org/gnome/desktop/interface/", source="interface.dconf")
```

## Value types

Values are converted from Python types to GVariant text.

| Python | Value |
| --- | --- |
| `bool` | boolean |
| `int` | integer |
| `float` | double |
| `str` | string |
| `list` | array; elements must share a type |
| `tuple` | tuple |
| `dict` | dictionary |
| `bytes` | byte array or bytestring |

```python
dconf.write("/org/example/keyboard-layout", ("us", "no"))
dconf.write("/org/example/labels", {"open": "Open", "close": "Close"})
dconf.write("/org/example/icon-data", b"icon-data")
```

dconf has no schema to supply the type of an empty container, `None`, or an
empty byte array. Wrap those values in `variant()` with an explicit type
annotation:

```python
dconf.write("/org/example/favorites", dconf.variant("@as []"))
dconf.write("/org/example/optional-name", dconf.variant("@ms nothing"))
```

## `variant(text)`

Use verbatim GVariant text for a value that has no native Python mapping or
cannot be inferred from the text alone. The text must be valid for the stored
value.

```python
dconf.write("/org/example/pinned-value", dconf.variant("<42>"))
dconf.write(
    "/org/example/actions",
    {"enabled": dconf.variant("<true>"), "count": dconf.variant("<3>")},
)
```

## Notes

- `dconf` runs as the current user and cannot write when kitup runs as root.
- Writes require a session bus. kitup validates values in a throwaway session
  started with `dbus-run-session`, so the `dbus-run-session` command must be
  installed.
- `dconf` has no schema defaults. `state="absent"` removes the value from the
  user database, so the system databases take effect again.
