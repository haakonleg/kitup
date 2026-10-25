"""Per-run task context exposed to bundle task code."""

from __future__ import annotations

import builtins
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, TypeVar, overload

from .commands import CommandRunner
from .errors import SkipTask, TaskFailed
from .output import Output
from .privileges import PrivilegeManager
from .registry import Task
from .results import Change
from .validation import normalize_strings, require_text

T = TypeVar("T")

_MISSING = object()


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
        self._task_name = ""
        self._bundle_path: Path | None = None
        self._sudo_ready = False

    @property
    def changes(self) -> list[Change]:
        return list(self._changes)

    @property
    def notes(self) -> list[str]:
        return list(self._notes)

    @property
    def bundle_path(self) -> Path | None:
        """Directory of the bundle owning the current task."""
        return self._bundle_path

    def resolve_source(self, path: str | Path) -> Path:
        """Resolve a source path against the current bundle.

        Absolute paths are returned unchanged. A bare filename resolves under
        the bundle's ``files`` directory, while other relative paths resolve
        against the bundle directory.
        """
        source = Path(path)
        if source.is_absolute():
            return source
        if self._bundle_path is None:
            raise TaskFailed(f"cannot resolve relative source path: {source}")
        if len(source.parts) == 1:
            return self._bundle_path / "files" / source
        return self._bundle_path / source

    def begin_task(self, task: Task) -> None:
        """Reset task-scoped state while retaining per-run state."""
        self._changes.clear()
        self._notes.clear()
        self._task_name = task.name
        self._bundle_path = task.bundle_path

    def ensure_sudo(self, reason: str | None = None) -> None:
        """Revalidate sudo, prompting only when cached credentials expired."""
        if self._sudo_ready and self.sudo.credentials_valid():
            return
        if self.output is not None:
            with self.output.interactive():
                self.sudo.refresh(reason)
        else:
            self.sudo.refresh(reason)
        self._sudo_ready = True

    def _sudo_reason(self) -> str:
        if not self._task_name:
            return "sudo privileges are required"
        return f'task "{self._task_name}" requires sudo privileges'

    def run(
        self,
        argv: str | Sequence[str],
        *,
        mutates: bool = True,
        creates: str | Path | None = None,
        unless: str | Sequence[str] | None = None,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        sudo: bool = False,
        timeout: float = 600,
        record: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command, honoring guards and dry-run.

        Only commands run with ``sudo=True`` request sudo credentials. Cached
        credentials are checked again before every privileged operation.
        """
        command = [str(part) for part in normalize_strings(argv)]
        if not command:
            raise TaskFailed("empty command")

        if creates is not None:
            marker = Path(creates)
            if cwd is not None and not marker.is_absolute():
                marker = Path(cwd) / marker
            if marker.exists():
                return _skipped(command)

        if sudo:
            self.ensure_sudo(self._sudo_reason())

        run_options: dict[str, Any] = {
            "cwd": cwd,
            "env": env,
            "sudo": sudo,
            "timeout": timeout,
            "refresh_sudo": False,
        }

        if unless is not None:
            probe = self.command.run(
                normalize_strings(unless), check=False, **run_options
            )
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

        # Probes are queries: a nonzero exit is data for the caller, not a
        # task failure.
        return self.command.run(command, check=False, **run_options)

    def probe(
        self, argv: str | Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        """Run a query command without recording a change."""
        kwargs.pop("mutates", None)
        return self.run(argv, mutates=False, **kwargs)

    def skip(self, reason: str = "") -> NoReturn:
        """Skip the current task before making any changes."""
        raise SkipTask(reason)

    def fail(self, reason: str) -> NoReturn:
        """Fail the current task with the given reason."""
        raise TaskFailed(reason)

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

    def input(self, key: str | None = None, prompt: str = "") -> str:
        """Prompt for a line of input and return it.

        When ``key`` is supplied, the value is stored in the same per-run store
        as :meth:`memo`. Without a key, the value is only returned.
        """
        if key is not None:
            require_text(key, "input key")
        try:
            if self.output is not None:
                with self.output.interactive():
                    value = builtins.input(prompt)
                    self.output.clear_input(prompt, value)
            else:
                value = builtins.input(prompt)
        except EOFError as exc:
            label = f" for key: {key}" if key is not None else ""
            raise TaskFailed(f"no input received{label}") from exc
        if key is not None:
            self._memo[key] = value
        return value

    def set(self, key: str, value: Any) -> None:
        """Store a value that later tasks can retrieve with :meth:`get`."""
        require_text(key, "input key")
        self._memo[key] = value

    @overload
    def get(self, key: str) -> Any: ...

    @overload
    def get(self, key: str, default: T) -> T: ...

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Return a value stored by :meth:`set`, :meth:`input`, or :meth:`memo`.

        Raises ``TaskFailed`` when the key is missing and no default is given.
        """
        require_text(key, "input key")
        if key in self._memo:
            return self._memo[key]
        if default is _MISSING:
            raise TaskFailed(f"no stored value for key: {key}")
        return default


def _skipped(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, "", "")
