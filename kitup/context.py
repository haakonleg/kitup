"""Per-run task context exposed to bundle task code."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, TypeVar

from .commands import CommandRunner
from .errors import SkipTask, TaskFailed
from .output import Output
from .privileges import PrivilegeManager
from .registry import Task
from .results import Change

T = TypeVar("T")


class TaskContext:
    """State and services available to every task.

    One context is reused for the whole run so ``memo`` is a per-run cache.
    ``begin_task`` resets the task-specific parts before each task executes.
    """

    def __init__(
        self,
        *,
        dry_run: bool,
        diff: bool,
        verbose: bool,
        config_dir: Path,
        config: SimpleNamespace,
        facts: Mapping[str, Any],
        sudo: PrivilegeManager,
        command: CommandRunner,
        output: Output | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.diff = diff
        self.verbose = verbose
        self.config_dir = config_dir
        self.config = config
        self.facts = facts
        self.sudo = sudo
        self.command = command
        self.output = output

        self._changes: list[Change] = []
        self._notes: list[str] = []
        self._memo: dict[str, Any] = {}
        self._default_sudo = False
        self._sudo_ready = False

    @property
    def changes(self) -> list[Change]:
        return list(self._changes)

    @property
    def needs_sudo(self) -> bool:
        return self._default_sudo

    @property
    def notes(self) -> list[str]:
        return list(self._notes)

    def begin_task(self, task: Task) -> None:
        """Reset task-scoped state while retaining the per-run memo cache."""
        self._changes.clear()
        self._notes.clear()
        self._default_sudo = task.needs_sudo
        self._sudo_ready = False

    def ensure_sudo(self) -> None:
        """Validate sudo once per task, before the first command that needs it."""
        if self._sudo_ready:
            return
        self.sudo.refresh()
        self._sudo_ready = True

    def run(
        self,
        argv: Sequence[str],
        *,
        mutates: bool = True,
        creates: str | Path | None = None,
        unless: Sequence[str] | None = None,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        sudo: bool | None = None,
        timeout: float = 600,
        record: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command, honoring guards, dry-run, and task sudo defaults."""
        command = [str(part) for part in argv]
        if not command:
            raise TaskFailed("empty command")
        use_sudo = self._default_sudo if sudo is None else sudo

        if creates is not None:
            marker = Path(creates)
            if cwd is not None and not marker.is_absolute():
                marker = Path(cwd) / marker
            if marker.exists():
                return _skipped(command)

        if use_sudo:
            self.ensure_sudo()

        run_options: dict[str, Any] = {
            "cwd": cwd,
            "env": env,
            "sudo": use_sudo,
            "timeout": timeout,
            "refresh_sudo": False,
        }

        if unless is not None:
            probe = self.command.run(unless, check=False, **run_options)
            if probe.returncode == 0:
                return _skipped(command)

        if mutates and self.dry_run:
            if record:
                self._changes.append(
                    Change(
                        {"argv": command, "reason": "would run unconditionally"},
                        f"would run: {' '.join(command)}",
                    )
                )
            return _skipped(command)

        if mutates:
            result = self.command.run(command, **run_options)
            if record:
                self._changes.append(Change({"argv": command}, " ".join(command)))
            return result

        return self.command.run(command, **run_options)

    def probe(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Run a query command without recording a change."""
        kwargs.pop("mutates", None)
        return self.run(argv, mutates=False, **kwargs)

    def skip(self, reason: str) -> NoReturn:
        """Skip the current task before making any changes."""
        raise SkipTask(reason)

    def changed(self, data: dict[str, Any], summary: str) -> None:
        """Record one module-level change for the current task."""
        self._changes.append(Change(data, summary))

    def note(self, detail: str) -> None:
        """Attach an informational note to the current task line."""
        self._notes.append(detail)

    def memo(self, key: str, loader: Callable[[], T]) -> T:
        """Compute a value once per run and return the cached result."""
        if key not in self._memo:
            self._memo[key] = loader()
        return self._memo[key]


def _skipped(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, "", "")
