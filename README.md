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

Install for the current user:

```bash
./install.sh
```

Or install with pip:

```bash
python3 -m pip install .
```

A standalone zipapp can also be built:

```bash
./build.sh
./dist/kitup --version
```

The build creates a temporary virtualenv, installs kitup's runtime
dependencies into it, and bundles them into the resulting archive. The system
Python only needs the standard library.

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
them.

## Commands

```bash
kitup list
kitup list -v
kitup apply
kitup apply profiles/laptop.py
kitup apply -n -d
kitup apply -t packages
kitup apply -x optional
```

`list` and `apply` use `./kitup.py` by default. Pass another profile path to use
a different configuration:

```bash
kitup apply examples/kitup.py
```

Common options:

- `-v`, `--verbose` — show task IDs, descriptions, and profile details.
- `-n`, `--dry-run` — report what would change without changing anything.
- `-d`, `--diff` — show unified diffs for file changes.
- `-s`, `--sudo` — ask for sudo credentials before starting.
- `-t TAGS` — only run tasks matching these tags.
- `-x TAGS` — skip tasks matching these tags.
- `-c`, `--no-color` — disable colors.

Tags can be comma-separated or repeated:

```bash
kitup apply -t packages,config
kitup apply -t packages -t config
```

## Profiles

A profile can use normal Python to build a run:

```python
import os

import config

from bundles import base, gaming, laptop

from kitup import profile

bundles = [base]

if os.environ.get("KITUP_LAPTOP") == "1":
    bundles.append(laptop)

if os.environ.get("KITUP_GAMING") == "1":
    bundles.append(gaming)

profile(
    bundles=bundles,
    config=[config],
    description="Fedora workstation",
)
```

Profiles can reuse and extend other profiles:

```python
# base_profile.py
import config

from bundles import base

from kitup import profile

profile(
    bundles=[base],
    config=[config],
    description="Fedora base",
)
```

```python
# kitup.py
import base_profile
import laptop as laptop_config
from bundles import laptop

from kitup import profile

profile.extend(
    base_profile,
    bundles=[laptop],
    config=[laptop_config],
    description="ThinkPad",
)
```

The result contains the base bundles first, then the laptop-specific bundle,
with both config files applied in order. Use `profile.use(base_profile)` to
reuse a profile unchanged.

See [docs/profile.md](docs/profile.md) for the complete profile reference.

## Bundles

Bundles group related tasks. Import task modules in the bundle's
`__init__.py` and list them in `modules`; the list order defines task order
within the bundle:

```python
from kitup.registry import Bundle

from . import packages, services

Bundle("Workstation", modules=[packages, services])
```

Every module listed in `modules` must declare at least one task with `@task`.

Bundles can depend on other bundles:

```python
# bundles/workstation/__init__.py
from bundles import base, repos

from kitup.registry import Bundle

from . import tasks

Bundle("Workstation", depends_on=[base, repos], modules=[tasks])
```

Dependencies run first, and each bundle runs at most once.

Resources such as config files live next to the bundle:

```text
bundles/workstation/
├── __init__.py
├── tasks.py
└── files/
    └── sshd.conf
```

Read them from a task:

```python
content = ctx.bundle.read_resource("files/sshd.conf")
source = ctx.bundle.resource_path("files")
```

## Configuration

Configuration values are declared in the profile and exposed as `ctx.config`.
Sources can be `Path` objects, imported modules, or dictionaries. They are
applied from left to right; later values win:

```python
import config
import configs.laptop

from bundles import workstation

from kitup import profile

profile(
    bundles=[workstation],
    config=[
        config,
        {"SSH_PORT": 2222},
        configs.laptop,
    ],
)
```

`config.py` might contain:

```python
PACKAGES = ["git", "ripgrep", "tmux", "neovim", "podman", "firewalld"]
FLATPAK_APPLICATIONS = ["com.spotify.Client", "md.obsidian.Obsidian"]
SSH_PORT = 22
GIT_NAME = "Ada Lovelace"
GIT_EMAIL = "ada@example.com"
```

Only public names from config files become configuration values. Explicit
mapping keys must be public strings. Relative config paths are resolved relative
to the profile that declares them.

System information is available read-only as `ctx.facts`, including hostname,
distribution, architecture, kernel, and mounted filesystems. See
[docs/facts.md](docs/facts.md) for details.

## Task helpers

Tasks receive `ctx`, which provides:

```python
ctx.config                          # merged configuration values
ctx.facts                           # system information
ctx.bundle                          # current bundle and its resources

ctx.run(["systemctl", "reload", "sshd"], sudo=True)  # run with sudo
ctx.probe(["systemctl", "is-active", "firewalld"])    # read-only query
ctx.changed({"detail": "x"}, "+x")  # report a change
ctx.note("extra context")           # add a note
ctx.memo("key", compute_once)       # cache a value for this run
ctx.input("key", "Prompt: ")        # prompt for input
ctx.set("key", value)               # store a value for later tasks
ctx.get("key")                      # read a stored value
ctx.skip("reason")                  # skip the task
ctx.fail("reason")                  # fail the task
```

## Built-in modules

All built-in modules are idempotent:

- `dnf` — packages, groups, release RPMs, repositories, and COPR.
- `flatpak` — remotes, applications, and runtimes.
- `files` — files, directories, symlinks, lines, blocks, and moves.
- `command` — guarded one-off commands.
- `github_release` — public GitHub release metadata and assets.
- `download` — remote file download over HTTP(S).
- `decompress` — tar/zip extraction and single-file decompression.
- `template` — Jinja2 templates rendered into managed files.
- `systemd` — units and daemon reloads.
- `gsettings` — GNOME settings.
- `git` — repository clones and checkouts.
- `sysctl` — kernel parameters, set at runtime and optionally persisted in a sysctl file.

See [docs/modules/index.md](docs/modules/index.md) for the full API.

## Privileges

Use `sudo=True` when an operation needs root:

```python
ctx.run(["systemctl", "reload", "sshd"], sudo=True)
files.file(
    ctx,
    "/etc/ssh/sshd_config.d/99-kitup.conf",
    content="PermitRootLogin no\n",
    mode=0o644,
    sudo=True,
)
```

Package, system service, and system Flatpak operations request sudo
automatically.

kitup handles sudo prompts and authenticates itself when privileged operations
are required.

## Examples

An example configuration is included in [`examples/`](examples/). Start with
[`examples/kitup.py`](examples/kitup.py).

## License

MIT
