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
├── files/                      # optional project-level source overlays
│   ├── common.conf             # global override
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

Bundles can also live outside the project. Pass `--bundle-path DIR` to scan an
extra directory for bundle folders, exactly like `<root>/bundles`. The flag is
repeatable, relative values resolve against the project root, and bundle ids
must be unique across all roots. See [Presets](presets.md) for reusable option
sets stored under `presets/`.

## Bundles

A bundle is a Python package below `bundles/`. Its folder name is the bundle id,
must be a valid Python identifier, and is used in task ids and dependency
references. The `__init__.py` is optional; when present it should stay
declarative and construct the bundle:

```python
# bundles/base/__init__.py
from kitup import bundle

bundle("Base system", "Core packages and services")
```

The first argument is the display name shown by `kitup list`; it defaults to the
folder name. The description is optional. The bundle id always comes from the
folder name, so the factory has no `id` argument.

`bundle()` returns the bundle when you need the object, but the return value can
be discarded: calls made from a package `__init__.py` register the bundle
automatically.

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
from kitup import task
from kitup.modules import dnf


@task(tags={"packages"})
def install_base_packages(ctx):
    """Install the base package set."""
    dnf.packages(ctx, ctx.vars.PACKAGES)
```

The task above is shown as **Install base packages** with the description
*Install the base package set.* Pass a name to override it, and `description=`
to override the docstring:

```python
@task("Install packages", tags={"packages"}, description="Base package set")
def install_base_packages(ctx): ...
```

`tags` is a sequence of strings. Entries may be comma-separated, and surrounding
whitespace is ignored.

Use `@bundle.task` for tasks declared inline in `__init__.py`; it accepts the
same forms. Task ids are `<bundle id>.<function name>` and must be unique within
a bundle.

To annotate the context parameter, import the type from the package root:

```python
from kitup import TaskContext


@task
def install_base_packages(ctx: TaskContext): ...
```

### Resources

Bundles keep resources in a `files/` directory, which is also available through
`ctx.bundle`:

```python
content = ctx.bundle.read_resource("files/example.conf")
source = ctx.bundle.resource_path("files/dotfiles")
```

The project root may also contain a `files/` tree that overlays bundle sources.
Relative paths passed to modules such as `files.copy()` and `template.file()`
are resolved in this order:

1. `<root>/files/<bundle id>/<source>` — bundle-specific override
2. `<root>/files/<source>` — global override
3. Bundle fallback:
   - a bare name resolves under the bundle's `files/` directory
   - any other relative path resolves against the bundle directory

The source path is used literally in the overlay paths. For a task in bundle
`base`, `"sshd.conf"` checks `<root>/files/base/sshd.conf`, then
`<root>/files/sshd.conf`, then `<bundle>/files/sshd.conf`. A source such as
`"nested/app.conf"` checks `<root>/files/base/nested/app.conf`, then
`<root>/files/nested/app.conf`, then `<bundle>/nested/app.conf`.

An explicit `files/...` source is also literal, so `"files/nested/app.conf"`
checks `<root>/files/base/files/nested/app.conf`, then
`<root>/files/files/nested/app.conf`, then `<bundle>/files/nested/app.conf`.

Overlays may add new files as well as replace existing bundle files. Global
overrides apply to every bundle; bundle-specific overrides apply only to their
bundle id, including external bundles. Absolute paths and `~` are used as-is.
`ctx.bundle.read_resource()` and `ctx.bundle.resource_path()` remain bundle-only
and do not consult project overlays. Not every module path uses this lookup; for
example, `dnf.repository()` and `dnf.rpm()` treat local inputs as ordinary paths
relative to the current working directory.

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

Pass `--var-file PATH` to load an extra Python var source. The flag is
repeatable; relative values resolve against the project root, and absolute paths
and `~` are accepted. Discovered `vars/*.py` files load first, then extra files
in the order given, so later files replace same-named values. Extra files may
use any filename, but `--include-vars` and `--exclude-vars` still select by file
stem. Duplicate var ids are errors.

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
Bundle values are still read through `ctx.vars`.

The merged result is available as `ctx.vars`. Read an optional value with
`get()` or require one with `require()`:

```python
packages = ctx.vars.get("PACKAGES", [])
color_scheme = ctx.vars.require("GNOME_COLOR_SCHEME")
```

`require()` raises a vars error naming the missing key and listing the available
keys.

Plain nested dictionary values support attribute access as well as item access:

```python
# vars/base.py
SETTINGS = {"theme": {"name": "dark"}}
SERVERS = [{"host": "example", "port": 443}]
```

```python
theme = ctx.vars.SETTINGS.theme.name
port = ctx.vars.SERVERS[0].port
```

Lists and tuples are not given attribute access themselves; only the plain
dictionaries inside them are converted. Attribute access requires a valid
Python identifier and cannot shadow dictionary methods, so keys such as
`"my-key"` or `"items"` still need item access.

Var files may import each other through the `vars` package:

```python
from vars.laptop import GNOME_COLOR_SCHEME
```

Extra `--var-file` sources can import sibling helpers with `from . import ...`.
Keep var files side-effect-free and limited to public constants.

## Context

Every task receives `ctx`:

- `ctx.vars` — merged variable values, with nested plain dictionaries also
  readable as attributes.
- `ctx.facts` — read-only host facts; see [Facts](facts.md).
- `ctx.root` — the project root.
- `ctx.project` — the discovered project: bundles, var paths, preset paths, and
  external bundle roots.
- `ctx.bundle` — the bundle that owns the current task and its resources.
- `ctx.run()` and `ctx.probe()` — command helpers.
- `ctx.changed()`, `ctx.note()`, `ctx.memo()`, `ctx.skip()`, and `ctx.fail()` —
  run controls.

## Task results

Every task result is stored automatically for its bundle and keyed by task id.
A later task in the same bundle can inspect an earlier task's outcome and return
value:

```python
# bundles/base/packages.py
@task
def install_packages(ctx):
    """Install packages."""
    dnf.packages(ctx, ctx.vars.PACKAGES)


# bundles/base/services.py
from .packages import install_packages


@task
def enable_services(ctx):
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
from kitup import side_effect, task
from kitup.modules import command, dnf, files


@side_effect
def refresh_metadata(ctx):
    """Refresh package metadata."""
    command.run(ctx, ["dnf5", "makecache"], sudo=True)


@side_effect(one_shot=False)
def reload_nginx(ctx):
    """Reload nginx."""
    command.run(ctx, ["systemctl", "reload", "nginx"], sudo=True)


@task(tags={"packages"})
def install_packages(ctx):
    """Install packages."""
    dnf.packages(ctx, ctx.vars.PACKAGES)
    ctx.side_effect(refresh_metadata)


@task(tags={"web"})
def configure_nginx(ctx):
    """Configure nginx."""
    files.file(ctx, "/etc/nginx/nginx.conf", content="...")
    ctx.side_effect(reload_nginx)
```

`@side_effect` accepts the same bare, named, and options-only forms as `task`.
It returns a `SideEffect` that is not registered with a bundle and does not
appear in `Bundle.tasks`. Side-effect results are stored in the registering
bundle and can be read with `ctx.result()` from that bundle only.

A side effect runs only when the registering task reports a change; tasks that
are `ok`, `skipped`, or `failed` do not trigger theirs. Side effects inherit tag
selection from the registering task, use the registering bundle's context, and
can inspect earlier task results. They take only `ctx`; use closures for
parameters. Within a bundle, side-effect function names must be unique and must
not collide with task function names. Re-registering the same `SideEffect` is
allowed.

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
