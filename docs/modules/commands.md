# `commands`

Run guarded one-off commands.

```python
from kitup.modules import commands
```

## `run(ctx, argv, *, creates=None, unless=None, cwd=None, env=None, changed=True, sudo=None, timeout=600)`

Run `argv` unless a guard is already satisfied.

- `argv` — command and arguments as a sequence; no shell is used.
- `creates` — path that, if it exists, skips the command. Relative paths resolve
  against `cwd`.
- `unless` — command that, if it exits successfully, skips the command.
- `cwd` — working directory for the command and guards.
- `env` — additional environment variables.
- `changed` — when `False`, execute without recording a change.
- `sudo` — override the task's sudo default.
- `timeout` — command timeout in seconds.

```python
@bundle.task("Create marker", tags={"config"})
def create_marker(ctx):
    commands.run(
        ctx,
        ["install", "-m", "0644", "/dev/null", "/var/lib/example/ready"],
        creates="/var/lib/example/ready",
    )
```

## `shell(ctx, command, *, shell=None, creates=None, unless=None, cwd=None, env=None, changed=True, sudo=None, timeout=600)`

Run a command string through a shell. Other arguments match `run()`.

- `shell` — shell executable. Defaults to `$SHELL`, then the current user's
  passwd shell, then `/bin/sh`.
- The shell is invoked as `<shell> -c <command>`.

`env["SHELL"]` sets the variable inside the command but does not select the shell.

```python
@bundle.task("Install via shell pipeline", tags={"config"})
def install_with_pipeline(ctx):
    commands.shell(
        ctx,
        "cat data.txt | grep useful > /var/lib/example/result.txt",
        shell="/bin/bash",
        creates="/var/lib/example/result.txt",
    )
```

## Notes

- Guards are evaluated in dry-run mode; mutating commands are not executed.
- Pass an argument sequence, not a string. Commands never receive interactive
  stdin.
- A nonzero exit raises `TaskFailed` with captured stdout and stderr.
- Commands run in their own process group; timeouts terminate the whole group.
- Prefer a dedicated module when one exists.
