# Custom modules

A custom module is reusable, idempotent operation code that lives in your
project and is used by bundle tasks. Use one when a project needs an operation
the [built-in modules](modules/index.md) do not provide. Custom modules follow
the same conventions as the built-ins: they retrieve the task context
themselves, inspect the current state before changing anything, honor
`--dry-run`, and return a `CallResult`.

## Create a module

Custom modules live in the top-level `modules/` directory or a directory
supplied by `--modules-path DIR`. A module is a single file or a folder.

A single-file module calls `module()` at the top of the file. This one adds SSH
key generation, which the built-in modules do not cover:

```python
# modules/ssh_key.py
from pathlib import Path

from kitup import CallResult, module
from kitup.context import current_context

module("ssh_key", "Generate an SSH key pair when it is missing.")


def ensure(path: str, *, comment: str = "") -> CallResult:
    """Generate an ed25519 SSH key pair at ``path`` when it does not exist."""
    ctx = current_context()
    key = Path(path).expanduser()
    if key.exists():
        return ctx.changed(changed=False, summary=f"ensure ssh key {key}", path=str(key))
    if ctx.dry_run:
        return ctx.changed(summary=f"would generate ssh key {key}", path=str(key))
    key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ctx.run("ssh-keygen", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key))
    return ctx.changed(summary=f"generated ssh key {key}", path=str(key))
```

The function retrieves the task context, checks whether the key already exists,
honors `--dry-run`, runs `ssh-keygen`, and records what it did. Retrieve the
context with `current_context()`, or import the `ctx` proxy
(`from kitup import ctx`) and use it directly.

A package module calls `module()` in its `__init__.py`; every file in the folder
belongs to that module:

```text
modules/firewall/
├── __init__.py
├── port.py
└── _helpers.py
```

```python
# modules/firewall/__init__.py
from kitup import module

module("firewall", "Firewalld ports and services.")
```

```python
# modules/firewall/port.py
from kitup import CallResult
from kitup.context import current_context
from kitup.errors import TaskFailed

_PROTOCOLS = ("tcp", "udp")
_STATES = ("present", "absent")


def port(number: int, *, protocol: str = "tcp", state: str = "present") -> CallResult:
    """Ensure a firewalld port is open or closed."""
    ctx = current_context()
    if protocol not in _PROTOCOLS:
        raise TaskFailed(f"unsupported protocol: {protocol}")
    if state not in _STATES:
        raise TaskFailed(f"unsupported state: {state}")
    if not 1 <= number <= 65535:
        raise TaskFailed(f"invalid port: {number}")

    spec = f"{number}/{protocol}"
    is_open = ctx.probe("firewall-cmd", f"--query-port={spec}", sudo=True).returncode == 0
    if is_open == (state == "present"):
        summary = f"ensure port {spec} is {'open' if is_open else 'closed'}"
        return ctx.changed(changed=False, summary=summary, port=spec)
    if ctx.dry_run:
        verb = "open" if state == "present" else "close"
        return ctx.changed(summary=f"would {verb} port {spec}", port=spec)

    flag = "--add-port" if state == "present" else "--remove-port"
    ctx.run("firewall-cmd", f"{flag}={spec}", sudo=True)
    summary = f"{'opened' if state == 'present' else 'closed'} port {spec}"
    return ctx.changed(summary=summary, port=spec)
```

The name passed to `module()` identifies the module in recorded actions. The
optional second argument is a human-readable description shown by
`kitup list-modules`. Files and folders whose names start with `_` are private
helpers and are not loaded as modules. Run `kitup list-modules` to confirm which
modules are registered.

## Use a module

Bundle tasks import custom modules like any other Python module:

```python
# bundles/base/security.py
from kitup import task, vars
from modules import ssh_key
from modules.firewall.port import port


@task(tags={"security"})
def configure_ssh_and_firewall():
    """Create the deploy key and open the configured web ports."""
    ssh_key.ensure("~/.ssh/deploy", comment="kitup deploy key")
    for number in vars.WEB_PORTS:
        port(number)
```

## Write a module

- Retrieve the context inside the function with `current_context()` (or the
  `ctx` proxy).
- Be idempotent: inspect the current state first and change only what is needed.
- Honor `ctx.dry_run` before every mutating `ctx.run()`.
- Request privilege with `sudo=True` on `ctx.run()` or a module helper. Never
  embed `sudo` in the command arguments.
- Record the operation with `ctx.changed(...)` and return the resulting
  `CallResult`. Use `changed=False` when nothing changed.
- Use `ctx.probe(...)` for read-only queries and `ctx.run(...)` for
  mutations. Pass `check=False` to `ctx.run()` when the exit code is part of
  the logic; commands that cannot start are reported as exit code `127`.
- Use `ctx.warn(...)` for something that looks incorrect or unintended but
  should not fail the task. Warnings appear below the task line in yellow.
- Use `ctx.note(...)` for lower-severity informational details.

```python
from kitup import CallResult
from kitup.context import current_context


def write_marker(text: str) -> CallResult:
    """Write a marker file below the project root."""
    ctx = current_context()
    path = ctx.root / "marker.txt"
    content = text + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return ctx.changed(changed=False, summary=f"ensure file {path}", path=str(path))
    if ctx.dry_run:
        return ctx.changed(summary=f"would write {path}", path=str(path))
    path.write_text(content, encoding="utf-8")
    return ctx.changed(summary=f"write {path}", path=str(path))
```

When a function calls `ctx.changed(...)`, the recorded action is named after the
module and function, such as `ssh_key.ensure`. Use the same summary forms as the
built-in modules: `write /etc/motd` when changed, `ensure file /etc/motd` when
unchanged, and `would ...` for a dry run.

## Reference

The [built-in modules](modules/index.md) use the same API and are good examples
to copy. A missing `module(...)` call is reported by `kitup check` and
`kitup list-modules`.
