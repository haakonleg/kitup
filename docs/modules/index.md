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
- [command](command.md) — guarded one-off commands
- [github_release](github_release.md) — public GitHub release metadata and assets
- [download](download.md) — remote file download over HTTP(S)
- [decompress](decompress.md) — archive extraction and single-file decompression
- [template](template.md) — Jinja2 templates rendered into managed files
- [systemd](systemd.md) — systemd units
- [gsettings](gsettings.md) — GNOME settings
