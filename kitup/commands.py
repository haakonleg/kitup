"""Low-level command execution."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence

from .errors import TaskFailed
from .output import Output
from .privileges import PrivilegeManager


class CommandRunner:
    """Run argv-style commands with captured output and optional sudo."""

    def __init__(
        self,
        privileges: PrivilegeManager,
        *,
        output: Output | None = None,
    ) -> None:
        self.privileges = privileges
        self.output = output

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        sudo: bool = False,
        timeout: float = 600,
        check: bool = True,
        refresh_sudo: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(part) for part in argv]
        if not command:
            raise TaskFailed("empty command")

        if sudo:
            if refresh_sudo:
                self.privileges.refresh()
            command = self.privileges.wrap(command)

        command_env = None
        if env is not None:
            command_env = {**os.environ, **env}

        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=command_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise TaskFailed(
                f"could not run {' '.join(command)}: {exc}",
                command=command,
            ) from exc

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(process)
            tail_out, tail_err = _drain(process)
            stdout = tail_out or _text(exc.stdout)
            stderr = tail_err or _text(exc.stderr)
            self._report(command, started, process.returncode or -1, stdout, stderr)
            raise TaskFailed(
                f"command timed out after {timeout:g}s: {' '.join(command)}",
                command=command,
                stdout=stdout,
                stderr=stderr,
            ) from exc

        self._report(command, started, process.returncode, stdout, stderr)

        if check and process.returncode != 0:
            raise TaskFailed(
                f"command failed with exit code {process.returncode}",
                command=command,
                stdout=stdout,
                stderr=stderr,
            )
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def _report(
        self,
        command: list[str],
        started: float,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        if self.output is not None and self.output.verbose:
            self.output.command(
                command,
                time.monotonic() - started,
                returncode,
                stdout,
                stderr,
            )


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    """Kill the command and any children in its process group."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _drain(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Collect remaining output after a timeout, without hanging forever."""
    try:
        stdout, stderr = process.communicate()
    except (OSError, ValueError):
        return "", ""
    return stdout or "", stderr or ""


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
