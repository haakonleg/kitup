# Modules

Built-in modules provide idempotent operations for bundle tasks. Each function
accepts `ctx` first. Methods inspect the current system state and change only
what is needed, and mutating operations honor `--dry-run`.

```python
from kitup import task
from kitup.modules import dnf, files
```

## Return values

Methods that manage system state return a `CallResult`. It records what the call
did and the data fields documented on each module page:

- `action` — method identifier, such as `"files.file"`.
- `changed` — `True` when the call changed something, `False` when the system
  already matched the requested state.
- `summary` — short human-readable description of the result.
- Method-specific fields, such as `path` for `files.file()`.

```python
result = files.file(ctx, "/etc/example.conf", content="value\n")

if result.changed:
    ctx.note(result.summary)
```

Results can be read as attributes or as dictionary keys, for example
`result.changed` or `result["path"]`.

Some methods only read data and return it directly instead of a `CallResult`.
For example, `download.to_bytes()` returns `bytes`. These methods do not record
changes.

Each module page documents its parameters, result fields, and usage examples.

- [dnf](dnf.md) — RPM packages, groups, repositories, COPR, and dnf.conf options
- [flatpak](flatpak.md) — Flatpak remotes, applications, and runtimes
- [files](files.md) — files, directories, symlinks, lines, blocks, INI files, and moves
- [command](command.md) — guarded one-off commands
- [github_release](github_release.md) — public GitHub release metadata and assets
- [download](download.md) — remote file download over HTTP(S)
- [decompress](decompress.md) — archive extraction and single-file decompression
- [template](template.md) — Jinja2 templates rendered into managed files
- [systemd](systemd.md) — systemd units
- [gsettings](gsettings.md) — GNOME settings
- [git](git.md) — repository clones and checkouts
- [sysctl](sysctl.md) — kernel parameters at runtime and in sysctl files
- [host](host.md) — hostname, time zone, NTP, RTC, and locale
- [users](users.md) — local users, groups, and group membership
- [podman](podman.md) — images, containers, registries, and systemd/Quadlet units
- [pip](pip.md) — Python packages via pip (optionally in a virtualenv) and pip.conf options
- [uv](uv.md) — uv project dependencies, per-user CLI tools, and uv.toml options

## Custom modules

Projects can also define their own modules. See
[Custom modules](../custom-modules.md).
