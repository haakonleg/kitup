# Modules

Built-in modules provide idempotent operations for bundle tasks:

```python
from kitup.modules import dnf, files
```

Each module accepts `ctx` first and reports changes through `ctx.changed()`.
Modules are standalone and must not import each other.

Examples register tasks with `@task(...)` from `kitup.registry`:

```python
from kitup.modules import dnf
from kitup.registry import task
```

The decorator resolves the owning bundle from the task module's package. Bundle
packages construct `Bundle(...)` in their `__init__.py` before importing task
modules. Resources are reached through `ctx.bundle`.

- [dnf](dnf.md) — RPM packages, groups, repositories, and COPR
- [flatpak](flatpak.md) — Flatpak remotes, applications, and runtimes
- [files](files.md) — files, directories, and symlinks
- [commands](commands.md) — guarded one-off commands
- [download](download.md) — remote file download over HTTP(S)
- [systemd](systemd.md) — systemd units
- [gsettings](gsettings.md) — GNOME settings
