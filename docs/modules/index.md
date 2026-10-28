# Modules

Built-in modules provide idempotent operations for bundle tasks. Each function
accepts `ctx` first, and all mutating operations honor `--dry-run`.

```python
from kitup.modules import dnf, files
from kitup.registry import task
```

- [dnf](dnf.md) — RPM packages, groups, repositories, and COPR
- [flatpak](flatpak.md) — Flatpak remotes, applications, and runtimes
- [files](files.md) — files, directories, symlinks, lines, blocks, and moves
- [commands](commands.md) — guarded one-off commands
- [download](download.md) — remote file download over HTTP(S)
- [decompress](decompress.md) — archive extraction and single-file decompression
- [systemd](systemd.md) — systemd units
- [gsettings](gsettings.md) — GNOME settings
