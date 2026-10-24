# Modules

Built-in modules provide idempotent operations for bundle tasks:

```python
from kitup.modules import dnf, files
```

Each module accepts `ctx` first and reports changes through `ctx.changed()`.
Modules are standalone and must not import each other.

Examples register tasks with `@bundle.task(...)`. The `bundle` object is defined
in the bundle's `__init__.py` and imported in task files with `from . import
bundle`.

- [dnf](dnf.md) — RPM packages, groups, repositories, and COPR
- [files](files.md) — files, directories, and symlinks
- [commands](commands.md) — guarded one-off commands
- [systemd](systemd.md) — systemd units
- [gsettings](gsettings.md) — GNOME settings
