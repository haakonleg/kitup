"""Guarded one-off command module."""

from __future__ import annotations

import os
import pwd
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..context import TaskContext
from ..validation import normalize_strings, require_collection, require_path, require_text


def run(
    ctx: TaskContext,
    argv: str | Sequence[str],
    *,
    creates: str | Path | None = None,
    unless: str | Sequence[str] | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    changed: bool = True,
    sudo: bool = False,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command unless a guard says it is already satisfied."""
    argv = normalize_strings(argv)
    require_collection(argv, "argv")
    if unless is not None:
        unless = normalize_strings(unless)
        require_collection(unless, "unless")
    if creates is not None:
        require_path(creates, "creates path")
    if cwd is not None:
        require_path(cwd, "cwd")
    return ctx.run(
        argv,
        creates=creates,
        unless=unless,
        cwd=cwd,
        env=env,
        sudo=sudo,
        timeout=timeout,
        record=changed,
    )


def shell(
    ctx: TaskContext,
    command: str,
    *,
    shell: str | Path | None = None,
    creates: str | Path | None = None,
    unless: str | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    changed: bool = True,
    sudo: bool = False,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command string through a shell.

    Uses the shell from ``SHELL`` or the current user's passwd entry when
    ``shell`` is not specified. ``unless`` is also run through that shell.
    """
    require_text(command, "shell command")
    if shell is not None:
        require_path(shell, "shell")
    executable = str(shell) if shell is not None else _default_shell()
    unless_command = None
    if unless is not None:
        require_text(unless, "shell unless")
        unless_command = [executable, "-c", unless]
    return run(
        ctx,
        [executable, "-c", command],
        creates=creates,
        unless=unless_command,
        cwd=cwd,
        env=env,
        changed=changed,
        sudo=sudo,
        timeout=timeout,
    )


def _default_shell() -> str:
    configured = os.environ.get("SHELL")
    if configured:
        return configured
    try:
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    except KeyError:
        return "/bin/sh"
