# kitup

The simple python-driven system configuration tool.

`kitup` loads Python task bundles from a configuration directory and applies them
sequentially. The runtime is standard-library only and requires Python 3.14+.

## Usage

```bash
kitup list
kitup list -v
kitup apply
kitup apply -n -d
kitup apply desktop
kitup apply -t packages
```

The current directory is the configuration root by default. It must contain a
`bundles/` directory; `config.py` is optional.

- `-D PATH` / `--dir PATH` — select another configuration root.
- `-C PATH` / `--config PATH` — merge an override file over `config.py`.
- `-v` / `--verbose` — task IDs and descriptions in `list`, plus extra detail
  during `apply`.
- `-t TAGS` / `-x TAGS` — include or skip tasks by tag.
- `-n` / `--dry-run` — report changes without modifying the system.
- `-d` / `--diff` — print unified diffs for file changes.
- `-s` / `--sudo` — validate sudo credentials before running tasks.
- `-c` / `--no-color` — disable colors.

See `examples/` for a minimal configuration:

```bash
kitup list -D examples -v
```

## Standalone zipapp

```bash
./build.sh
./dist/kitup --version
./dist/kitup list -D examples -v
```

`build.sh` runs `python3`; set `PYTHON` to override. The archive contains only
the `kitup` package. Bundles stay external: run the zipapp from a directory
containing `bundles/`, or pass `-D`.

To build and install to `~/.local/bin`:

```bash
./install.sh
```

## Configuration

Public values in `config.py` are exposed as `ctx.config`:

```python
PACKAGES = ["git", "vim-enhanced", "tmux"]
DNF_CONF = "/etc/dnf/dnf.conf"
```

An override passed with `-C` replaces same-named fields and adds new ones.

System facts are collected once per run and exposed read-only as `ctx.facts`,
including `hostname`, `os_release`, `distro_id`, `version_id`, `arch`, and
`kernel`. Tasks needing live state should query it directly.

## Bundle layout

```text
bundles/
└── base/
    ├── __init__.py
    ├── packages.py
    └── resources/
        └── dnf.conf
```

`__init__.py` declares the bundle. Import order defines task order across files:

```python
from kitup.registry import Bundle

bundle = Bundle("base", "Base system packages", depends_on=())

from . import packages, dnf_config  # noqa: F401
```

Task files register tasks with `@bundle.task`:

```python
from . import bundle
from kitup.modules import files

@bundle.task("Configure DNF", needs_sudo=True, tags={"config"})
def configure_dnf(ctx):
    files.file(
        ctx,
        ctx.config.DNF_CONF,
        content=bundle.read_resource("resources/dnf.conf"),
        mode=0o644,
    )
```

Dependencies run before dependents, and each bundle runs at most once:

```python
Bundle("desktop", "Desktop", depends_on=("base", "repos"))
```

Task code can use the context helpers:

```python
ctx.run(["command", "arg"])          # guarded, dry-run aware command
ctx.probe(["query", "--flag"])       # read-only query
ctx.changed({"detail": "data"}, "+change")
ctx.note("extra context")
ctx.memo("key", compute_once)        # per-run cache
ctx.skip("reason")                   # skip before mutating
```

## Built-in modules

All modules are idempotent and report changes through `ctx.changed()`. Modules
are standalone and do not import each other.

- `dnf` — packages, groups, repositories, and COPR.
- `files` — files, copies, line/block edits, directories, and symlinks.
- `commands` — one-off commands with `creates`/`unless` guards.
- `systemd` — enable/disable and start/stop units.
- `gsettings` — GNOME settings.

Full API documentation is in [docs/modules/index.md](docs/modules/index.md).

## Privileges

Tasks that need root declare `needs_sudo=True`. Root commands run as
`sudo -n -- <argv>`; file operations in root tasks use a privileged helper.
Running kitup as root is supported, but user-scoped modules (`gsettings`,
`systemctl --user`) and home-directory dotfiles must run as the target user.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy kitup
```

Tests use scripted command runners and never invoke real package, service, or
settings-management commands.

## License

MIT
