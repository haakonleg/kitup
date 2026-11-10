# kitup

**The simple Python-driven system configuration tool.**

kitup describes how a Linux machine should be set up — packages, services,
files, users, containers, and system settings — and applies that description
idempotently. Projects and tasks are ordinary Python code, so you can use
functions, loops, conditions, and imports instead of a separate configuration
language.

kitup is inspired by Ansible but has a smaller scope and a more expressive,
Python-centric design. It currently manages local machines only.

## Install

kitup requires Python 3.14 or newer.

```bash
uv tool install .
```

Or run `./install.sh` to build the standalone Python zipapp and install it to
`~/.local/bin`:

```bash
./install.sh
```

## Quick start

Create a project:

```bash
kitup init my-machine
```

A project looks like this:

```text
my-machine/
├── bundles/
│   └── workstation/
│       ├── __init__.py
│       ├── tasks.py
│       ├── files/
│       │   └── sshd.conf
│       └── vars/
│           └── defaults.py
├── files/
│   ├── common.conf
│   └── workstation/
│       └── sshd.conf
├── vars/
│   ├── base.py
│   └── laptop.py
└── presets/
    └── laptop
```

Bundles group related tasks. An optional `__init__.py` gives the bundle a
display name and description:

```python
# bundles/workstation/__init__.py
from kitup import bundle

bundle("Workstation", "Desktop packages and security settings")
```

Tasks are Python functions decorated with `@task`. Built-in modules keep each
step idempotent:

```python
# bundles/workstation/tasks.py
from kitup import task
from kitup.modules import dnf, files, systemd


@task(tags={"packages"})
def install_packages(ctx):
    """Install the base package set."""
    dnf.packages(ctx, ctx.vars.PACKAGES)


@task(tags={"security"})
def harden_ssh(ctx):
    """Write the hardened SSH configuration."""
    files.file(
        ctx,
        "/etc/ssh/sshd_config.d/99-kitup.conf",
        content=ctx.bundle.read_resource("files/sshd.conf"),
        mode=0o644,
        sudo=True,
    )


@task(tags={"services"})
def enable_firewall(ctx):
    """Enable and start firewalld."""
    systemd.unit(ctx, "firewalld.service", enabled=True, state="started")
```

The optional project `files/` tree overlays bundle resources. Relative source
paths check `files/<bundle_id>/<path>`, then `files/<path>`, then the bundle's
own resolution, so project files can override or add bundle files.

Variable files hold machine-specific values. Later files replace same-named
values:

```python
# vars/base.py
PACKAGES = ["git", "ripgrep", "tmux", "podman", "firewalld"]

# vars/laptop.py
PACKAGES = ["git", "ripgrep", "tmux", "neovim", "podman", "firewalld"]
```

Bundles may also contain a `vars/` folder. Bundle vars are scoped to that
bundle's tasks and act as defaults: project-level vars (including `--var-file`)
are layered on top, so project values win on same-named keys.

Presets are reusable sets of command-line options:

```text
# presets/laptop
--include-bundles workstation
-t packages,security
```

Inspect and apply the project:

```bash
kitup check my-machine          # discover and print the plan
kitup list my-machine -v        # show bundles, tasks, and details
kitup apply my-machine -n       # preview changes
kitup apply my-machine          # apply the configuration
```

Run selected tasks with tags, or select bundles and vars:

```bash
kitup apply my-machine -t packages
kitup apply my-machine -b workstation
kitup apply my-machine --preset laptop
```

## Features

### Bundles and tasks

Bundles are Python packages under `bundles/`. Sibling task modules are
discovered automatically, or you can list them explicitly for exact ordering
and reuse. Bundles can depend on each other with `depends_on`.

### Variables and facts

`ctx.vars` exposes merged values from `vars/*.py`, additional `--var-file`
sources, and the current bundle's optional `vars/` folder. Bundle vars are
scoped to their bundle and act as defaults; project values win on collisions.
`ctx.facts` provides read-only system information such as the hostname,
distribution, kernel, and mounted filesystems.

### Presets and selection

Presets keep common option combinations in `presets/`. Tag filters and bundle
selection let you apply just the part of a machine you are working on.

### Shared bundles and extra sources

Bundles can live outside the project with `--bundle-path`, and extra variable
files can be loaded with `--var-file`. Project `files/` overlays can override or
add resources globally or for one bundle id. Absolute paths and `~` are accepted
anywhere kitup takes a path.

### Task results and side effects

Later tasks can inspect earlier task results, and side effects run only when a
task reports a change — useful for reloading a service after its configuration
changes.

### Privileges

Mutating system operations request sudo automatically where appropriate.
`--sudo` validates credentials before a run starts.

## Commands

| Command | Purpose |
| --- | --- |
| `kitup init [PROJECT_DIR]` | Scaffold a new project. |
| `kitup check [PROJECT_DIR]` | Discover, resolve, and validate without running. |
| `kitup list [PROJECT_DIR]` | Show the bundles and tasks in the plan. |
| `kitup apply [PROJECT_DIR]` | Apply the plan. |

Common options include `-n/--dry-run`, `-d/--diff`, `-t/--tags`,
`-x/--skip-tags`, `-b/--include-bundles`, `-p/--preset`, and `-v/--verbose`.
See the [CLI reference](docs/cli.md) for the full list.

## Documentation

- [Projects and bundles](docs/project.md) — project layout, tasks, vars,
  results, and side effects.
- [CLI reference](docs/cli.md) — commands, options, precedence, and exit codes.
- [Presets](docs/presets.md) — reusable option sets.
- [Facts](docs/facts.md) — system information available to tasks.
- [Built-in modules](docs/modules/index.md) — the full task API.

## Examples

An example project is included in [`examples/`](examples/):

```bash
kitup list examples
kitup apply examples --preset laptop -n
```

## License

MIT
