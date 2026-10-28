# Profiles

A profile is a Python file that imports bundle packages and declares what a run
contains. The default file is `./kitup.py`; pass another path to `list` or
`apply`:

```bash
kitup list profiles/laptop.py
kitup apply profiles/laptop.py -n
```

Profiles are trusted executable Python and run with the same privileges as
kitup itself.

## Basic profile

```python
from pathlib import Path

from bundles import base, dotfiles, desktop

from kitup import profile

profile(
    bundles=[base, dotfiles, desktop],
    config=[Path("config.py"), Path("workstation.py")],
    description="Workstation setup",
    tags=["desktop"],
    skip_tags=["optional"],
)
```

The directory containing the invoked profile is the configuration root and
`ctx.config_dir`. Because profiles are ordinary Python, you can branch, loop,
and build the bundle list conditionally:

```python
if platform.machine() == "x86_64":
    profile(bundles=[base, steam])
else:
    profile(bundles=[base])
```

## API

```python
profile(
    *,
    bundles: Sequence[ModuleType | Bundle],
    config: Sequence[str | Path | Mapping[str, Any] | ModuleType] = (),
    description: str = "",
    tags: Iterable[str] = (),
    skip_tags: Iterable[str] = (),
)
```

`profile.of(module)` returns a profile's definition without selecting it:

```python
base_run = profile.of(base_profile)
bundles = list(base_run.bundles)
config = list(base_run.config)
```

`profile.extend(source, ...)` adds bundles and config to an existing profile:

```python
profile.extend(
    base_profile,
    bundles=[dotfiles],
    config=[Path("host.py")],
    description="Workstation",
)
```

`profile.use(source)` reuses an existing profile unchanged:

```python
profile.use(base_profile)
```

The profile you invoke determines `ctx.config_dir`, even when it reuses or
extends a profile from another directory.

## Composition

Profiles compose through normal imports:

```python
# base_profile.py
from pathlib import Path

from bundles import base

from kitup import profile

profile(
    bundles=[base],
    config=[Path("config.py")],
    description="Base system",
)
```

```python
# kitup.py
import base_profile
from pathlib import Path

from bundles import dotfiles

from kitup import profile

profile.extend(
    base_profile,
    bundles=[dotfiles],
    config=[Path("host.py")],
)
```

The result contains the base bundles first, then `dotfiles`, with both config
sources applied in order. New relative config paths resolve against the file
calling `extend`.

Importing a profile or bundle does not select it. Use the `bundles` argument,
`profile.extend()`, or `profile.use()` explicitly.

## Bundles

A bundle is an importable package whose `__init__.py` constructs a `Bundle` and
imports its task modules:

```python
# bundles/base/__init__.py
from kitup.registry import Bundle

Bundle("Base system")

from . import packages
```

Bundle names must be unique within a run.

Bundles can depend on other bundles:

```python
from bundles import base, repos

Bundle("Workstation", depends_on=[base, repos])
```

Dependencies run first, and each bundle runs at most once.

A bundle can also be defined inline in a profile. Its resource root defaults to
the profile's directory:

```python
from kitup.registry import Bundle

local = Bundle("Local overrides")

@local.task("Write marker")
def write_marker(ctx):
    content = ctx.bundle.read_resource("local-bundle/marker.txt")
```

Pass `path=` to use another resource root.

## Task modules

Task modules use the public `task` decorator:

```python
from kitup.registry import task


@task("Install packages", tags={"packages"}, description="Optional detail")
def install_packages(ctx):
    ...
```

The task name is required and must be a non-empty string.

Resources are available through `ctx.bundle`:

```python
content = ctx.bundle.read_resource("files/example.conf")
source = ctx.bundle.resource_path("files/dotfiles")
```

## Config sources

Config sources merge left-to-right. Later sources replace same-named fields;
earlier-only fields remain; new fields are added.

```python
import configs.laptop
from pathlib import Path

from bundles import base

profile(
    bundles=[base],
    config=[
        Path("config.py"),
        {"DNF_CONF": "/etc/dnf/dnf.conf"},
        configs.laptop,
    ],
)
```

- A `Path` is imported as a Python file. Relative paths resolve against the
  profile that declared them. File sources must be `Path` objects, not strings.
- A module contributes its public fields.
- A mapping contributes its public string keys.

The merged result is `ctx.config`. Missing paths and invalid mapping keys are
errors.

## Tags

`tags` and `skip_tags` are profile defaults. They may be separate entries or
comma-separated; surrounding whitespace is ignored. CLI filters narrow them:

- include tags — intersection when both profile and CLI tags are set;
- skip tags — union.

`--dry-run`, `--diff`, `--sudo`, verbosity, and color remain CLI flags and
cannot be declared in a profile.

## Paths

- The invoked profile's directory is the configuration root.
- Relative config paths resolve against the file that declared them.
