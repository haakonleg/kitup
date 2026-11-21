# `command`

Run guarded one-off commands.

```python
from kitup.modules import command
```

## `run(*cmd, *, creates=None, unless=None, cwd=None, env=None, changed=True, sudo=False, timeout=600, check=None, ignore_errors=False)`

Run `cmd` unless a guard is already satisfied.

### Parameters

- `cmd` — command and arguments, executed directly.
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
- `ignore_errors` — return a failed result instead of failing the task when the
  command exits nonzero or cannot be started. Cannot be combined with
  `check=True`; timeouts still fail.

### Result fields

- `cmd` — command and arguments.
- `failed` — whether an executed command failed or could not start. Guarded
  skips and dry runs report `False`.
- `returncode` — exit code, or `127` when the command could not start (executed
  results only).
- `stdout` — captured standard output (executed results only).
- `stderr` — captured standard error (executed results only).
- `start` — UTC start timestamp in ISO 8601 format (executed results only).
- `end` — UTC end timestamp in ISO 8601 format (executed results only).
- `duration` — elapsed time in seconds (executed results only).

```python
command.run(
    "install",
    "-m",
    "0644",
    "/dev/null",
    "/var/lib/example/ready",
    creates="/var/lib/example/ready",
)
```

To act on an expected failure, check `failed` on the returned result:

```python
result = command.run("optional-tool", "--cleanup", ignore_errors=True)
if result.failed:
    ctx.note(f"optional cleanup failed with exit code {result.returncode}")
```

## `shell(cmd, *, shell=None, creates=None, unless=None, cwd=None, env=None, changed=True, sudo=False, timeout=600, check=None, ignore_errors=False)`

Run a command string through a shell. Other arguments match `run()`.

### Parameters

- `cmd` — command string passed to the shell.
- `shell` — shell executable. Defaults to `$SHELL`, then the user's login shell.
- `unless` is a shell command string, evaluated with the same shell.

### Result fields

The same fields as `run()`, with the shell invocation as `cmd`.

```python
command.shell(
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
- With `ignore_errors=True`, a nonzero exit or a command that cannot be started
  returns a result with `failed=True` instead of failing the task. The recorded
  `changed` value still follows the `changed` argument; use `changed=False` for
  probes that should not count as changes.
