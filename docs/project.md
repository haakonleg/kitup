# Projects

A kitup project describes one machine. It contains bundles of tasks, optional
project-level file overlays, variable files, and optional presets. Pass the
project directory to `list`, `check`, or `apply`; it defaults to the current
directory:

```bash
kitup list my-machine
kitup apply my-machine -n
```

## Layout

```text
my-machine/                     # project root
├── bundles/
│   └── base/                   # one directory per bundle; the folder name is the bundle id
│       ├── __init__.py         # optional; declares the bundle
│       ├── packages.py         # task module
│       ├── files/              # bundle resources
│       └── vars/               # optional; bundle-scoped variable values
│           └── defaults.py
├── modules/                    # optional custom modules
│   ├── motd.py                 # single-file module; calls module("motd")
│   └── desktop/                # package module; calls module("desktop")
│       └── __init__.py
├── files/                      # optional project-level source overlays
│   └── base/                   # overrides for the base bundle
│       └── sshd.conf
├── vars/
│   ├── base.py                 # variable values; id = file stem
│   └── laptop.py               # merged after base.py
└── presets/
    └── laptop                  # optional preset file
```

At least one bundle is required by `apply` and `check`. `kitup init [DIR]`
scaffolds a working project with the layout above.

Bundles, overlays, and custom modules can also live outside the project using
`--bundle-path`, `--files-path`, and `--modules-path`. Bundle and module ids
must be unique across all roots. See [Presets](presets.md) for reusable option
sets.

## Bundles

A bundle is a Python package below `bundles/`. Its folder name is the bundle id,
must be a valid Python identifier, and is used in task ids and dependency
references. An optional numeric prefix such as `01_setup` orders bundles and is
stripped from the id; see [Run order](#run-order). The `__init__.py` is optional;
when present it should stay declarative and construct the bundle:

```python
# bundles/base/__init__.py
from kitup import bundle

bundle("Base system", "Core packages and services")
```

The first argument is the display name shown by `kitup list`; it defaults to the
bundle id. The description is optional. The bundle id always comes from the
folder name, so the factory has no `id` argument.

`bundle()` returns the bundle when you need the object, but the return value can
be discarded: calls made from a package `__init__.py` register the bundle
automatically.

### Run order

Bundles run in this order, from highest to lowest precedence:

1. `depends_on` — dependencies always run before the bundles that need them.
2. `priority` — lower values run first; equal values keep folder order.
3. Numeric folder prefix — folders such as `01_setup` or `2_packages` order
   numerically (`2_x` before `10_x`, regardless of zero-padding). Numbered
   folders run before unnumbered ones, which stay in name order.

The prefix is not part of the id: folder `01_setup` has id `setup`, so use
`depends_on=["setup"]`, `-b setup`, and `files/setup/`. The part after the prefix
must still be a valid, non-keyword Python identifier, and ids must be unique, so
`01_setup` and `setup` cannot coexist.

```python
# bundles/02_packages/__init__.py: priority beats the folder prefix
from kitup import bundle

bundle("Packages", priority=-10)  # runs before default-priority (0) bundles
```

### Task modules

When `bundle(...)` omits `modules`, kitup discovers sibling `*.py` files except
`__init__.py` and `_`-prefixed helpers. Files load in filename order, and tasks
run in declaration order within each file. Zero-padded prefixes are an easy way
to control order:

```text
bundles/base/
├── __init__.py
├── 01_packages.py
├── 02_services.py
└── _helpers.py          # imported by task modules, never discovered
```

Pass an explicit sequence to control order or include only some modules:

```python
from kitup import bundle

from . import packages, services

bundle("Base system", modules=[packages, services])
```

`modules=[]` disables discovery. Every module passed to `modules` or discovered
automatically must declare at least one task. Helper modules should be prefixed
with `_` or imported from task modules instead.

Modules may also be listed by filesystem path. String and `Path` values are
always treated as paths. Relative paths resolve against the bundle directory;
absolute paths and paths beginning with `~` are accepted:

```python
from pathlib import Path
from kitup import bundle

bundle(
    "Base",
    modules=[
        "tasks.py",
        Path("/srv/kitup/tasks/security.py"),
        "~/shared/tasks/network.py",
    ],
)
```

A path must refer to a `.py` file other than `__init__.py`. Path-loaded modules
can import sibling helpers with `from . import helper`.

A task module may be listed by more than one bundle. Each bundle creates its own
tasks, so a shared module produces independent task ids such as `base.install`
and `desktop.install`.

### Dependencies

Depend on other bundles by id:

```python
# bundles/workstation/__init__.py
from kitup import bundle

bundle("Workstation", depends_on=["base"])
```

Dependencies run first and each bundle runs at most once. Unknown ids and
dependency cycles are errors.

### Tasks

Import `task` from `kitup` and use it as a decorator. The default display name is
humanized from the function name, and the default description is the docstring:

```python
# bundles/base/packages.py
from kitup import task, vars
from kitup.modules import dnf


@task(tags={"packages"})
def install_base_packages():
    """Install the base package set."""
    dnf.packages(vars.PACKAGES)
```

The task above is shown as **Install base packages** with the description
*Install the base package set.* Pass a name to override it, and `description=`
to override the docstring:

```python
@task("Install packages", tags={"packages"}, description="Base package set")
def install_base_packages(): ...
```

`tags` is a sequence of strings. Entries may be comma-separated, and surrounding
whitespace is ignored.

Use `@bundle.task` for tasks declared inline in `__init__.py`; it accepts the
same forms. Task ids are `<bundle id>.<function name>` and must be unique within
a bundle.

Read variables with the `vars` proxy. Reach the rest of the task context with
the `ctx` proxy exported from the package root:

```python
from kitup import ctx, task, vars


@task
def install_base_packages():
    ctx.note("installing base packages")
    dnf.packages(vars.PACKAGES)
```

### Resources

Bundles keep resources in `files/`. `ctx.files` merges that directory with the
project overlay and any `--files-path` overlays:

- `<bundle>/files/` — bundle resources
- `<root>/files/<bundle id>/` — project overlay
- `<files path>/<bundle id>/` — extra overlays

Overlays add or replace files for their bundle id, including external bundles.
Extra overlays win over the project overlay, and all overlays win over the
bundle's own files.

Iterate the merged view to copy a resource tree, or read entries with `Path`
methods such as `read_text()`:

```python
from pathlib import Path

from kitup.modules import files


def install_tree(source: Path, target: Path) -> None:
    for entry in source.iterdir():
        destination = target / entry.name
        if entry.is_symlink():
            files.symlink(entry.readlink(), destination)
        elif entry.is_dir():
            files.directory(destination)
            install_tree(entry, destination)
        else:
            files.copy(entry, destination)


install_tree(ctx.files / "dotfiles", Path.home())
```

Relative source paths passed to modules such as `files.copy()` and
`files.template()` resolve like this:

1. A bare name such as `"sshd.conf"`, or a `files/...` path such as
   `"files/sshd.conf"`, resolves inside `ctx.files`, so extra overlays win over
   `<root>/files/<bundle id>/sshd.conf`, which wins over
   `<bundle>/files/sshd.conf`.
2. Any other relative path, such as `"nested/app.conf"`, resolves against the
   bundle directory without overlays.
3. Absolute paths and `~` are used as-is.

## Custom modules

Reusable, idempotent operation code belongs in an optional top-level `modules/`
directory. A module is a single file, or a folder with an `__init__.py`; either
way it calls `module(...)` once. The optional second argument is a
human-readable description shown by `kitup list-modules`.

`--modules-path` adds a module directory like `<root>/modules`. External
modules import as `modules.<name>`, so tasks use them normally. Module ids must
be unique across all module directories.

```python
# modules/motd.py
from kitup import CallResult, module
from kitup.modules import files

module("motd", "Write the message of the day.")


def message(text: str) -> CallResult:
    """Write the message of the day."""
    return files.file("/etc/motd", content=text + "\n", sudo=True)
```

Tasks import custom modules like any other Python module:

```python
from kitup import task, vars
from modules import motd


@task
def configure_machine():
    """Write the message of the day."""
    motd.message(vars.MOTD)
```

See [Custom modules](custom-modules.md) for package modules and the full
authoring contract. A missing `module(...)` call is reported by `kitup check`
and `kitup list-modules`.

## Vars

Var files are ordinary Python modules with public constants. Direct `*.py`
children of `vars/` load in lexicographic order. Later files replace same-named
values:

```python
# vars/base.py
PACKAGES = ["git", "ripgrep", "tmux"]

# vars/laptop.py
PACKAGES = ["git", "ripgrep", "tmux", "neovim"]
GNOME_COLOR_SCHEME = "prefer-dark"
```

Only public names become variable values; functions, classes, modules, and
bundles are ignored. Var ids are file stems, so `vars/laptop.py` has the id
`laptop`. Use `--include-vars` and `--exclude-vars` to select sources. See
[CLI](cli.md).

`--var-file` loads extra Python var sources after discovered `vars/*.py` files,
so later files replace same-named values. Extra files may use any filename, but
`--include-vars` and `--exclude-vars` still select by file stem. Duplicate var
ids are errors.

### Bundle vars

A bundle may also contain a `vars/` directory in its bundle folder (the bundle's
`path`, normally `bundles/<id>/`). Files load in lexicographic order,
just like project vars. Bundle vars are scoped to that bundle: its tasks and
side effects see them, but sibling and dependency bundles do not.

Project-level vars are layered on top of bundle vars, so a same-named project
value wins and bundle vars act as defaults:

```python
# bundles/workstation/vars/defaults.py
PACKAGES = ["neovim", "firefox"]

# vars/base.py
PACKAGES = ["git", "ripgrep"]
```

A task in `workstation` sees `PACKAGES == ["git", "ripgrep"]`; a task in another
bundle does not see the workstation defaults. When the project does not define
a key, the bundle value is used. `--include-vars` and `--exclude-vars` apply
only to project-level sources, and `--var-file` sources are also project-level.
Bundle values are still read through `vars`.

The merged result is available as `vars`. Read an optional value with `get()`
or require one with `require()`:

```python
from kitup import vars

packages = vars.get("PACKAGES", [])
color_scheme = vars.require("GNOME_COLOR_SCHEME")
```

`require()` raises a vars error naming the missing key and listing the available
keys.

For example:

```python
# vars/base.py
SETTINGS = {"theme": {"name": "dark"}}
SERVERS = [{"host": "example", "port": 443}]
```

```python
from kitup import vars

theme = vars.SETTINGS.theme.name
port = vars.SERVERS[0].port
```

Var files may import each other through the `vars` package:

```python
from vars.laptop import GNOME_COLOR_SCHEME
```

Extra `--var-file` sources can import sibling helpers with `from . import ...`.
Keep var files side-effect-free and limited to public constants.

## Context

Tasks reach the task context through the `ctx` proxy exported from the package
root:

- `ctx.vars` — merged variable values. Also available as the `vars` shortcut:
  `from kitup import vars`.
- `ctx.facts` — read-only host facts; see [Facts](facts.md). Also available as
  the `facts` shortcut: `from kitup import facts`.
- `ctx.root` — the project root.
- `ctx.project` — the discovered project: bundles, var paths, preset paths, and
  external bundle, files, and module roots.
- `ctx.bundle` — the bundle that owns the current task and its resources.
- `ctx.run()` and `ctx.probe()` — command helpers.
- `ctx.changed()`, `ctx.note()`, `ctx.memo()`, `ctx.skip()`, and `ctx.fail()` —
  run controls. `ctx.memo(key, loader)` caches a value under a string key.
- `@memo` — decorator from `kitup` (`from kitup import memo`) that caches a
  function's result once per run, keyed by the function and its arguments.

## Task results

Every task result is stored automatically for its bundle and keyed by task id.
A later task in the same bundle can inspect an earlier task's outcome and return
value:

```python
# bundles/base/packages.py
@task
def install_packages():
    """Install packages."""
    dnf.packages(vars.PACKAGES)


# bundles/base/services.py
from .packages import install_packages


@task
def enable_services():
    """Enable services when packages changed."""
    if not ctx.result(install_packages).changed:
        ctx.skip("packages unchanged")
```

`ctx.result(task)` returns the result for a task function in the current bundle.
`TaskResult` exposes `status`, `changed`, `ok`, `skipped`, `failed`, `calls`,
`changes`, `notes`, `detail`, `duration`, `error`, and `value`. `ctx.results`
returns a snapshot of the current bundle's results keyed by task id.

Results are isolated per bundle. A task cannot inspect another bundle's results,
even through a dependency; `depends_on` controls execution order only. Reading a
task that did not run in the current bundle raises `TaskFailed`.

Task functions may return a value; it is stored in `TaskResult.value`. Results
are stored for tasks that ran, including skipped tasks. Declare task functions
once and keep imports between task modules one-directional to avoid cycles.

## Side effects

Side effects run only when the registering task changes something. Declare them
with `@side_effect` and register them from a task:

```python
from kitup import ctx, side_effect, task, vars
from kitup.modules import command, dnf, files


@side_effect
def refresh_metadata():
    """Refresh package metadata."""
    command.run(["dnf5", "makecache"], sudo=True)


@side_effect(one_shot=False)
def reload_nginx():
    """Reload nginx."""
    command.run(["systemctl", "reload", "nginx"], sudo=True)


@task(tags={"packages"})
def install_packages():
    """Install packages."""
    dnf.packages(vars.PACKAGES)
    ctx.side_effect(refresh_metadata)


@task(tags={"web"})
def configure_nginx():
    """Configure nginx."""
    files.file("/etc/nginx/nginx.conf", content="...")
    ctx.side_effect(reload_nginx)
```

`@side_effect` accepts the same bare, named, and options-only forms as `task`.
It returns a `SideEffect` that is not registered with a bundle and does not
appear in `Bundle.tasks`. Side-effect results are stored in the registering
bundle and can be read with `ctx.result()` from that bundle only.

A side effect runs only when the registering task reports a change; tasks that
are `ok`, `skipped`, or `failed` do not trigger theirs. Side effects inherit tag
selection from the registering task, use the registering bundle's context, and
can inspect earlier task results. They take no parameters. Within a bundle,
side-effect function names must be unique and must not collide with task
function names. Re-registering the same `SideEffect` is allowed.

- `one_shot=True` (the default) defers the side effect to the end of the bundle
  and runs it at most once per bundle per apply run. A side effect shared by two
  bundles runs once in each bundle. It is marked as executed after an `ok` or
  `changed` result, so a skipped side effect can run again when a later task
  registers it. If the bundle fails before the end, queued one-shot side effects
  do not run.
- `one_shot=False` runs the side effect immediately after the registering task,
  before the next task starts. Every registration is honored, including
  duplicates, and a failure aborts the run immediately.

A side effect cannot register another side effect. Side-effect-only modules
should not be discovered as task modules; prefix them with `_` or import them
from task modules instead.

## Trust

Project code is trusted executable Python and runs with the same privileges as
kitup itself. Keep bundle `__init__.py` files declarative and avoid side effects
at import time.
