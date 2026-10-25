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
including `hostname`, `os_release`, `distro_id`, `version_id`, `arch`, `kernel`,
and `mounts`. Tasks needing live state should query it directly. See
[docs/facts.md](docs/facts.md) for details.

## Bundle layout

```text
bundles/
└── base/
    ├── __init__.py
    ├── packages.py
    └── files/
        └── dnf.conf
```

`__init__.py` declares the bundle. Import order defines task order across files:

```python
from kitup.registry import Bundle

bundle = Bundle("base", "Base system packages", depends_on=())

from . import packages, dnf_config  # noqa: F401
```

Every task module must be imported from `__init__.py`, directly or through
another imported module. Files in the bundle folder are not auto-discovered.

Task files register tasks with `@bundle.task`:

```python
from . import bundle
from kitup.modules import files

@bundle.task("Configure DNF", tags={"config"})
def configure_dnf(ctx):
    files.file(
        ctx,
        ctx.config.DNF_CONF,
        content=bundle.read_resource("files/dnf.conf"),
        mode=0o644,
        sudo=True,
    )
```

Dependencies run before dependents, and each bundle runs at most once:

```python
Bundle("desktop", "Desktop", depends_on=("base", "repos"))
```

Task code can use the context helpers:

```python
ctx.run(["command", "arg"])          # guarded, dry-run aware command
ctx.run(["command", "arg"], sudo=True)
ctx.probe(["query", "--flag"])       # read-only query
ctx.probe(["query", "--flag"], sudo=True)
ctx.changed({"detail": "data"}, "+change")
ctx.note("extra context")
ctx.memo("key", compute_once)        # per-run cache
ctx.input("key", "Prompt: ")         # prompt and store a value
ctx.input(prompt="Continue? ")       # prompt and return only
ctx.set("key", value)                # store a value for later tasks
ctx.get("key")                       # retrieve a stored value
ctx.skip("optional reason")          # skip before mutating
ctx.fail("reason")                   # fail the task
```

## Built-in modules

All modules are idempotent and report changes through `ctx.changed()`. Modules
are standalone and do not import each other.

- `dnf` — packages, groups, swaps, release RPMs, repositories, and COPR.
- `files` — files, copies, line/block edits, directories, and symlinks.
- `commands` — one-off commands with `creates`/`unless` guards.
- `systemd` — enable/disable and start/stop units.
- `gsettings` — GNOME settings.

Full API documentation is in [docs/modules/index.md](docs/modules/index.md).

## Privileges

Operations request sudo explicitly with `sudo=True`, for example
`ctx.run(..., sudo=True)`, `commands.run(..., sudo=True)`, or
`files.file(..., sudo=True)`. Inherently privileged operations, such as `dnf`
and system-scoped `systemd`, request sudo themselves.

Cached credentials are reused, and kitup prompts only when they expire. Running
as root is supported, but user-scoped modules and home-directory dotfiles must
run as the target user.

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

Integration tests exercise real Fedora tooling in a disposable, locked-down
container:

```bash
./tools/integration.sh
```

The suite needs Podman. The container runs without network access, drops all
capabilities except the documented minimum, applies CPU/memory/PID limits, and
mounts the repository read-only at `/kitup-integration-test`. A normal
`pytest` run only covers `tests/unit`; set `KITUP_INTEGRATION=1` to run
`tests/integration` directly inside the container.

## License

MIT
