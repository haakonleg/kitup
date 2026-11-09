"""Guarded one-off command module."""

import os
import pwd
from collections.abc import Mapping, Sequence
from pathlib import Path

from .._utils.paths import expand_path
from .._utils.timing import timed
from .._utils.validation import (
    normalize_strings,
    require_bool,
    require_collection,
    require_path,
    require_text,
    require_timeout,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult


def run(
    ctx: TaskContext,
    cmd: str | Sequence[str],
    *,
    creates: str | Path | None = None,
    unless: str | Sequence[str] | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    changed: bool = True,
    sudo: bool = False,
    timeout: float = 600,
    check: bool | None = None,
) -> CallResult:
    """Run a command unless a guard says it is already satisfied."""
    return _run(
        ctx,
        cmd,
        action="command.run",
        creates=creates,
        unless=unless,
        cwd=cwd,
        env=env,
        changed=changed,
        sudo=sudo,
        timeout=timeout,
        check=check,
    )


def shell(
    ctx: TaskContext,
    cmd: str,
    *,
    shell: str | Path | None = None,
    creates: str | Path | None = None,
    unless: str | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    changed: bool = True,
    sudo: bool = False,
    timeout: float = 600,
    check: bool | None = None,
) -> CallResult:
    """Run a command string through a shell.

    Uses the shell from ``SHELL`` or the current user's passwd entry when
    ``shell`` is not specified. ``unless`` is also run through that shell.
    """
    require_text(cmd, "shell command")
    if shell is not None:
        require_path(shell, "shell")
    executable = str(expand_path(shell, error=TaskFailed)) if shell is not None else _default_shell()
    unless_command = None
    if unless is not None:
        require_text(unless, "shell unless")
        unless_command = [executable, "-c", unless]
    return _run(
        ctx,
        [executable, "-c", cmd],
        action="command.shell",
        creates=creates,
        unless=unless_command,
        cwd=cwd,
        env=env,
        changed=changed,
        sudo=sudo,
        timeout=timeout,
        check=check,
    )


def _run(
    ctx: TaskContext,
    cmd: str | Sequence[str],
    *,
    action: str,
    creates: str | Path | None,
    unless: str | Sequence[str] | None,
    cwd: str | Path | None,
    env: Mapping[str, str] | None,
    changed: bool,
    sudo: bool,
    timeout: float,
    check: bool | None,
) -> CallResult:
    cmd = normalize_strings(cmd)
    require_collection(cmd, "cmd")
    require_bool(changed, "changed")
    require_bool(sudo, "sudo")
    if check is not None:
        require_bool(check, "check")
    require_timeout(timeout)
    if unless is not None:
        unless = normalize_strings(unless)
        require_collection(unless, "unless")
        require_text(unless[0], "unless")
    if creates is not None:
        require_path(creates, "creates path")
    if cwd is not None:
        require_path(cwd, "cwd")

    cwd_path = expand_path(cwd, error=TaskFailed) if cwd is not None else None
    cmd = [str(part) for part in cmd]
    if creates is not None:
        marker = expand_path(creates, error=TaskFailed)
        if cwd_path is not None and not marker.is_absolute():
            marker = cwd_path / marker
        if os.path.lexists(marker):
            return ctx.changed(
                action=action,
                changed=False,
                summary=f"skip {' '.join(cmd)}",
                cmd=cmd,
            )

    if unless is not None:
        probe = ctx.probe(unless, cwd=cwd_path, env=env, sudo=sudo, timeout=timeout)
        if probe.returncode == 0:
            return ctx.changed(
                action=action,
                changed=False,
                summary=f"skip {' '.join(cmd)}",
                cmd=cmd,
            )

    if ctx.dry_run:
        return ctx.changed(
            action=action,
            changed=changed,
            summary=f"would run: {' '.join(cmd)}",
            cmd=cmd,
        )

    if check is None:
        check = changed

    with timed() as timing:
        process = ctx.run(
            cmd,
            cwd=cwd_path,
            env=env,
            sudo=sudo,
            timeout=timeout,
            check=check,
        )
    return ctx.changed(
        action=action,
        changed=changed,
        summary=" ".join(cmd),
        cmd=cmd,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
        **timing.as_dict(),
    )


def _default_shell() -> str:
    configured = os.environ.get("SHELL")
    if configured:
        return configured
    try:
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    except KeyError:
        return "/bin/sh"
