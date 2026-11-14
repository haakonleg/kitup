# Custom modules

A custom module is reusable, idempotent operation code that lives in your
project and is used by bundle tasks. Custom modules follow the same conventions
as the [built-in modules](modules/index.md): they take `ctx` first, inspect the
current state before changing anything, honor `--dry-run`, and return a
`CallResult`.

## Create a module

Custom modules live in a top-level `modules/` directory. A module is either a
single file or a folder.

A single-file module calls `module()` at the top of the file:

```python
# modules/motd.py
from kitup import CallResult, module
from kitup.modules import files

module("motd", "Write the message of the day.")


def message(ctx, text: str) -> CallResult:
    """Write the message of the day."""
    return files.file(ctx, "/etc/motd", content=text + "\n", sudo=True)
```

A package module calls `module()` in its `__init__.py`; every file in the folder
belongs to that module:

```text
modules/desktop/
├── __init__.py
├── wallpaper.py
└── _helpers.py
```

```python
# modules/desktop/__init__.py
from kitup import module

module("desktop", "Desktop wallpaper and settings.")
```

```python
# modules/desktop/wallpaper.py
from kitup import CallResult
from kitup.modules import gsettings


def set_wallpaper(ctx, uri: str) -> CallResult:
    """Set the desktop wallpaper."""
    return gsettings.key(ctx, "org.gnome.desktop.background", "picture-uri", uri)
```

The name passed to `module()` identifies the module in recorded actions. The
optional second argument is a human-readable description shown by
`kitup list-modules`. Files and folders whose names start with `_` are private
helpers and are not loaded as modules. Run `kitup list-modules` to confirm which
modules are registered.

## Use a module

Bundle tasks import custom modules like any other Python module:

```python
# bundles/base/tasks.py
from kitup import task
from modules import motd
from modules.desktop import wallpaper


@task
def configure_machine(ctx):
    """Write the message of the day and set the wallpaper."""
    motd.message(ctx, ctx.vars.MOTD)
    wallpaper.set_wallpaper(ctx, ctx.vars.WALLPAPER)
```

## Write a module

- Take `ctx: TaskContext` as the first parameter.
- Be idempotent: inspect the current state first and change only what is needed.
- Honor `ctx.dry_run` before every mutating `ctx.run()`.
- Request privilege with `sudo=True` on `ctx.run()` or a module helper. Never
  embed `sudo` in the command list.
- Record the operation with `ctx.changed(...)` and return the resulting
  `CallResult`. Use `changed=False` when nothing changed.
- Use `ctx.probe([...])` for read-only queries and `ctx.run([...])` for
  mutations.

```python
def message(ctx, text: str) -> CallResult:
    """Write the message of the day."""
    path = ctx.root / "motd"
    content = text + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return ctx.changed(changed=False, summary=f"ensure file {path}", path=str(path))
    path.write_text(content, encoding="utf-8")
    return ctx.changed(summary=f"write {path}", path=str(path))
```

When a function calls `ctx.changed(...)`, the recorded action is named after the
module and function, such as `motd.message`. Use the same summary forms as the
built-in modules: `write /etc/motd` when changed, `ensure file /etc/motd` when
unchanged, and `would ...` for a dry run.

A function that only delegates to a built-in module can return that module's
`CallResult` directly.

## Reference

The [built-in modules](modules/index.md) use the same API and are good examples
to copy. A missing `module(...)` call is reported by `kitup check` and
`kitup list-modules`.
