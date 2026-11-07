# kitup

The simple Python-driven system configuration tool.

kitup is a configuration management tool for Linux systems: describe how a
machine should be set up — what software to install, how services should run,
which files to manage, and which system settings to apply — and kitup applies
it idempotently through reusable task bundles.

kitup is inspired by Ansible, but with a smaller scope and a more expressive,
Python-centric design: profiles, bundles, tasks, conditions, and composition are
ordinary Python code. It currently supports local machines only; remote host
management is not yet included.

## Install

Install the CLI with `uv tool install .`, or run `./install.sh` to build a
standalone zipapp and install it to `~/.local/bin/kitup`.

A standalone zipapp can also be built with `./build.sh`; the result is
`dist/kitup` and only needs the system Python standard library. Building
requires [uv](https://docs.astral.sh/uv/): the build creates a temporary uv
environment from `uv.lock`, installs kitup's runtime dependencies into it, and
bundles them into the archive.

## Quick start

Create a configuration directory:

```text
my-machine/
├── kitup.py
├── config.py
└── bundles/
    └── workstation/
        ├── __init__.py
        ├── tasks.py
        └── files/
            └── sshd.conf
```

`kitup.py` is the profile. It imports the bundle and declares the run:

```python
import config

from bundles import workstation

from kitup import profile

profile(
    bundles=[workstation],
    config=[config],
    description="Fedora workstation",
)
```

`config.py` contains machine-specific values:

```python
PACKAGES = ["git", "ripgrep", "tmux", "neovim", "podman", "firewalld"]
FLATPAK_APPLICATIONS = ["com.spotify.Client", "md.obsidian.Obsidian"]
```

A bundle is a Python package. Its `__init__.py` imports its task modules and
creates the bundle:

```python
# bundles/workstation/__init__.py
from kitup.registry import Bundle

from . import tasks

Bundle("Workstation", modules=[tasks])
```

Tasks use the `task` decorator. The name is required and must be a non-empty
string:

```python
# bundles/workstation/tasks.py
from kitup.modules import dnf, files, flatpak, systemd
from kitup.registry import task


@task("Install development packages", tags={"packages"})
def install_dev_packages(ctx):
    dnf.packages(ctx, ctx.config.PACKAGES)


@task("Add Flathub", tags={"flatpak"})
def add_flathub(ctx):
    flatpak.remote(
        ctx,
        "flathub",
        "https://dl.flathub.org/repo/flathub.flatpakrepo",
    )


@task("Install Flatpak applications", tags={"flatpak"})
def install_flatpak_apps(ctx):
    flatpak.packages(ctx, ctx.config.FLATPAK_APPLICATIONS, remote="flathub")


@task("Enable and start firewalld", tags={"services"})
def enable_firewall(ctx):
    systemd.unit(ctx, "firewalld.service", enabled=True, state="started")


@task("Harden SSH", tags={"security"})
def harden_ssh(ctx):
    files.file(
        ctx,
        "/etc/ssh/sshd_config.d/99-kitup.conf",
        content=ctx.bundle.read_resource("files/sshd.conf"),
        mode=0o644,
        sudo=True,
    )
```

Run it:

```bash
kitup list
kitup apply
```

`list` shows the bundles and tasks without changing anything. `apply` runs
them. Every module is idempotent, so applying an unchanged configuration
changes nothing. Add `--dry-run` to preview changes and `--diff` to see file
diffs.

## Commands

Both commands use `./kitup.py` by default; pass another profile path to use a
different configuration, for example `kitup apply examples/kitup.py`.

Common options:

- `-v`, `--verbose` — show task IDs, descriptions, and profile details.
- `-n`, `--dry-run` — report what would change without changing anything.
- `-d`, `--diff` — show unified diffs for file changes.
- `-s`, `--sudo` — ask for sudo credentials before starting.
- `-t TAGS` — only run tasks matching these tags.
- `-x TAGS` — skip tasks matching these tags.
- `-b BUNDLES`, `--only-bundles BUNDLES` — only include bundles with these names and their dependencies.
- `-B BUNDLES`, `--skip-bundles BUNDLES` — exclude bundles with these names.
- `-c`, `--no-color` — disable colors.

Tags and bundle filters accept comma-separated values or repeated flags, for
example `-t packages,config` or `-t packages -t config`. Bundle names match the
names shown by `kitup list`; excluding a bundle that a selected bundle depends
on is an error.

## Profiles

A profile is a Python file that imports bundles and declares the run: bundles,
config sources, description, and default tags. Because it is ordinary Python,
you can branch, loop, and build the bundle list conditionally. Profiles can
reuse or extend other profiles with `profile.use()` and `profile.extend()`.

See [docs/profile.md](docs/profile.md) for the complete profile reference.

## Bundles

Bundles group related tasks and are ordinary Python packages. List task modules
in the bundle's `modules` argument; the list order defines task order within the
bundle. Bundles can depend on other bundles with `depends_on`; dependencies run
first, and each bundle runs at most once. Resources such as config files live
next to the bundle and are read through `ctx.bundle`.

See [docs/profile.md](docs/profile.md#bundles) for details.

## Configuration

Configuration values are declared in the profile and exposed as `ctx.config`.
Sources can be `Path` objects, imported modules, or dictionaries. They are
applied from left to right; later values win. Only public names become
configuration values, and relative config paths resolve relative to the profile
that declares them.

System information is available read-only as `ctx.facts`, including hostname,
distribution, architecture, kernel, and mounted filesystems. See
[docs/facts.md](docs/facts.md) for details.

## Task helpers

Tasks receive `ctx`, which provides the merged configuration (`ctx.config`),
system facts (`ctx.facts`), the current bundle and its resources (`ctx.bundle`),
command helpers (`ctx.run()` and `ctx.probe()`), and run controls such as
`ctx.changed()`, `ctx.note()`, `ctx.memo()`, `ctx.skip()`, and `ctx.fail()`.
Tasks can prompt for input, store values for later tasks, and read earlier task
results.

### Task results and side effects

Every task result is stored automatically and keyed by the task function, so a
later task can inspect an earlier task's outcome and return value. Side effects
are work a task registers to run only when it changes something; they can run
immediately or be deferred to the end of the bundle.

See [task results](docs/profile.md#task-results) and
[side effects](docs/profile.md#side-effects) for the full APIs.

## Built-in modules

All built-in modules are idempotent:

- `dnf` — packages, groups, release RPMs, repositories, COPR, and dnf.conf options.
- `flatpak` — remotes, applications, and runtimes.
- `files` — files, directories, symlinks, lines, blocks, INI files, and moves.
- `command` — guarded one-off commands.
- `github_release` — public GitHub release metadata and assets.
- `download` — remote file download over HTTP(S).
- `decompress` — tar/zip extraction and single-file decompression.
- `template` — Jinja2 templates rendered into managed files.
- `systemd` — units and daemon reloads.
- `gsettings` — GNOME settings.
- `git` — repository clones and checkouts.
- `sysctl` — kernel parameters, set at runtime and optionally persisted in a sysctl file.
- `host` — hostname, time zone, NTP, RTC, and locale.
- `users` — local users, groups, and group membership.
- `podman` — images, containers, registries, and systemd/Quadlet units.
- `pip` — Python packages via pip (optionally in a virtualenv) and pip.conf options.
- `uv` — uv project dependencies, per-user CLI tools, and uv.toml options.

See [docs/modules/index.md](docs/modules/index.md) for the full API.

## Privileges

Pass `sudo=True` to `ctx.run()` or a module helper when an operation needs root.
Package, system service, and system Flatpak operations request sudo
automatically. kitup handles sudo prompts and authenticates itself when
privileged operations are required.

## Examples

An example configuration is included in [`examples/`](examples/). Start with
[`examples/kitup.py`](examples/kitup.py).

## License

MIT
