# `command`

Run guarded one-off commands.

```python
from kitup.modules import command
from kitup.registry import task
```

## `run(ctx, cmd, *, creates=None, unless=None, cwd=None, env=None, changed=True, sudo=False, timeout=600, check=None)`

Run `cmd` unless a guard is already satisfied.

### Parameters

- `cmd` — command and arguments; no shell is used.
- `creates` — path that, if it exists, skips the command. Relative paths resolve
  against `cwd`.
- `unless` — command that, if it exits successfully, skips the command.
- `cwd` — working directory for the command and guards.
- `env` — additional environment variables.
- `changed` — when `False`, execute without recording a change.
- `sudo` — run through sudo.
- `timeout` — command timeout in seconds.
- `check` — fail on a nonzero exit. Defaults to `changed`, so `changed=False`
  probes return their exit code instead of failing.

### Result fields

- `cmd` — command and arguments.
- `returncode` — exit code (executed results only).
- `stdout` — captured standard output (executed results only).
- `stderr` — captured standard error (executed results only).
- `start` — UTC start timestamp in ISO 8601 format (executed results only).
- `end` — UTC end timestamp in ISO 8601 format (executed results only).
- `duration` — elapsed time in seconds (executed results only).

```python
@task("Create marker", tags={"config"})
def create_marker(ctx):
    command.run(
        ctx,
        ["install", "-m", "0644", "/dev/null", "/var/lib/example/ready"],
        creates="/var/lib/example/ready",
    )
```

## `shell(ctx, cmd, *, shell=None, creates=None, unless=None, cwd=None, env=None, changed=True, sudo=False, timeout=600, check=None)`

Run a command string through a shell. Other arguments match `run()`.

### Parameters

- `cmd` — command string passed to the shell.
- `shell` — shell executable. Defaults to `$SHELL`, then the user's login shell.
- `unless` is a shell command string, evaluated with the same shell.

### Result fields

The same fields as `run()`, with the shell invocation as `cmd`.

```python
@task("Install via shell pipeline", tags={"config"})
def install_with_pipeline(ctx):
    command.shell(
        ctx,
        "cat data.txt | grep useful > /var/lib/example/result.txt",
        shell="/bin/bash",
        creates="/var/lib/example/result.txt",
    )
```

## Notes

- Guards are evaluated in dry-run mode; commands are not executed.
- Arguments may be a sequence or a single string; a single string is one
  argument and is not split. Commands never receive interactive stdin.
- A nonzero exit fails the task when `check` is true. With `changed=False`,
  `check` defaults to false so probes can inspect the exit code.
