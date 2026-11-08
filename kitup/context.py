"""Per-run task context exposed to bundle task code."""

import builtins
import subprocess
import traceback
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Never, cast, overload

from ._privileged.client import PrivilegedWorker
from ._utils.paths import expand_path
from ._utils.validation import require_text
from .commands import CommandRunner
from .errors import SkipTask, TaskFailed
from .output import Output
from .privileges import PrivilegeManager
from .project import FILES_DIR, Project
from .registry import Bundle, SideEffect, Task
from .results import CallResult, TaskResult
from .runtime import BundleState, RunState
from .vars import Vars

_MISSING: Final = object()


class TaskContext:
    """State and services available to every task.

    One context is reused for the whole run so ``memo`` is a per-run cache and
    ``results`` retains task outcomes. ``begin_task`` resets the task-specific
    parts before each task executes. ``root`` is the project root and
    ``project`` describes the discovered project.
    """

    def __init__(
        self,
        *,
        dry_run: bool,
        diff: bool,
        verbose: bool,
        root: Path,
        project: Project,
        vars: Vars,
        facts: Mapping[str, Any],
        sudo: PrivilegeManager,
        command: CommandRunner,
        bundle_vars: Mapping[Bundle, Vars] | None = None,
        output: Output | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.diff = diff
        self.verbose = verbose
        self.root = root
        self.project = project
        self._project_vars = vars
        self._bundle_vars = dict(bundle_vars or {})
        self.vars = vars
        self.facts = facts
        self.sudo = sudo
        self.command = command
        self.output = output

        self._calls: list[CallResult] = []
        self._notes: list[str] = []
        self._memo: dict[str, Any] = {}
        self._run_state = RunState()
        self._task_name = ""
        self._bundle: Bundle | None = None
        self._bundle_path: Path | None = None
        self._task: Task | None = None
        self._running_side_effect = False
        self._sudo_ready = False
        self.privileged_worker: PrivilegedWorker = PrivilegedWorker(self)

    @property
    def calls(self) -> list[CallResult]:
        """Return all calls recorded for the current task."""
        return list(self._calls)

    @property
    def changes(self) -> list[CallResult]:
        """Return the calls that changed something for the current task."""
        return [call for call in self._calls if call.changed]

    @property
    def notes(self) -> list[str]:
        """Return the notes recorded for the current task."""
        return list(self._notes)

    @property
    def results(self) -> dict[str, TaskResult]:
        """Return a snapshot of the current bundle's results keyed by task id."""
        state = self._run_state.current
        if state is None:
            return {}
        return dict(state.results)

    def result(self, task: Callable[..., Any]) -> TaskResult:
        """Return the result of a task that already ran in the current bundle."""
        state = self._run_state.current
        if state is None:
            raise TaskFailed("no current bundle")
        for result in state.results.values():
            if result.task.func is task:
                return result
        name = getattr(task, "__name__", repr(task))
        raise TaskFailed(f"no result for task: {name}")

    @property
    def bundle(self) -> Bundle:
        """The bundle that owns the current task."""
        if self._bundle is None:
            raise TaskFailed("the current task has no owning bundle")
        return self._bundle

    @property
    def bundle_path(self) -> Path | None:
        """Directory of the bundle owning the current task."""
        return self._bundle_path

    def resolve_source(self, path: str | Path) -> Path:
        """Resolve a source path against the project and current bundle.

        Absolute paths are returned unchanged. Relative paths are checked first
        under ``files/<bundle id>/`` and then under the project's ``files/``
        directory. A missing overlay falls back to the bundle: a bare name to
        the bundle's ``files`` directory, and other relative paths to the
        bundle directory.
        """
        source = expand_path(path, error=TaskFailed)
        if source.is_absolute():
            return source
        if self._bundle_path is None:
            raise TaskFailed(f"cannot resolve relative source path: {source}")
        project_files = self.root / FILES_DIR
        for overlay in (project_files / self.bundle.id, project_files):
            candidate = overlay / source
            if candidate.exists():
                return candidate
        if len(source.parts) == 1:
            return self._bundle_path / FILES_DIR / source
        return self._bundle_path / source

    def begin_task(self, task: Task, *, is_side_effect: bool = False) -> None:
        """Reset task-scoped state and enter the task's bundle."""
        self._calls.clear()
        self._notes.clear()
        self._task_name = task.name
        self._bundle = task.bundle
        self._bundle_path = task.bundle_path
        self._task = task
        self.vars = self._bundle_vars.get(task.bundle, self._project_vars)
        state = self._run_state.for_bundle(task.bundle)
        state.pending_effects.clear()
        self._run_state.current = state
        self._running_side_effect = is_side_effect

    def side_effect(self, effect: SideEffect) -> None:
        """Queue a side effect to run if this task changed."""
        if not isinstance(effect, SideEffect):
            raise TaskFailed("side_effect expects a SideEffect")
        if self._task is None:
            raise TaskFailed("side effects can only be registered from a task")
        if self._running_side_effect:
            raise TaskFailed("side effects cannot register other side effects")
        state = self._run_state.current
        if state is None:
            raise TaskFailed("side effects can only be registered from a task")
        self._validate_side_effect(state, effect)
        state.side_effects[effect.func.__name__] = effect
        state.pending_effects.append(effect)

    def _validate_side_effect(self, state: BundleState, side_effect: SideEffect) -> None:
        func_name = side_effect.func.__name__
        if any(task.func.__name__ == func_name for task in state.bundle.tasks):
            raise TaskFailed(f"side effect function {func_name!r} collides with a task in bundle {state.bundle.id!r}")
        existing = state.side_effects.get(func_name)
        if existing is not None and existing is not side_effect:
            raise TaskFailed(f"duplicate side effect function name in bundle {state.bundle.id!r}: {func_name}")

    def _take_side_effects(self) -> list[SideEffect]:
        """Return and clear the side effects registered by the current task."""
        state = self._run_state.current
        if state is None:
            return []
        effects = list(state.pending_effects)
        state.pending_effects.clear()
        return effects

    def _store_result(self, task: Task, result: TaskResult) -> None:
        """Store a task result in its bundle's runtime state."""
        state = self._run_state.for_bundle(task.bundle)
        state.results[task.id] = result

    def ensure_sudo(self, reason: str | None = None) -> None:
        """Revalidate sudo, prompting only when cached credentials expired."""
        if reason is None:
            reason = self._sudo_reason()
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
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        sudo: bool = False,
        timeout: float = 600,
        check: bool = True,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command and return its completed process.

        This is a pure command runner: it never records changes, evaluates
        guards, or handles dry-run. Only commands run with ``sudo=True``
        request sudo credentials; cached credentials are checked again before
        every privileged operation.
        """
        if sudo:
            self.ensure_sudo()

        return self.command.run(
            argv,
            cwd=cwd,
            env=env,
            sudo=sudo,
            timeout=timeout,
            check=check,
            stdin=stdin,
        )

    def probe(self, argv: str | Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Run a query command without failing on a nonzero exit."""
        kwargs.pop("check", None)
        return self.run(argv, check=False, **kwargs)

    def skip(self, reason: str = "") -> Never:
        """Skip the current task before making any changes."""
        raise SkipTask(reason)

    def fail(self, reason: str) -> Never:
        """Fail the current task with the given reason."""
        raise TaskFailed(reason)

    @overload
    def changed(self, result: CallResult) -> CallResult: ...

    @overload
    def changed(
        self,
        *,
        action: str | None = None,
        changed: bool = True,
        summary: str,
        **data: Any,
    ) -> CallResult: ...

    def changed(
        self,
        result: CallResult | None = None,
        *,
        action: str | None = None,
        changed: bool = True,
        summary: str = "",
        **data: Any,
    ) -> CallResult:
        """Record one module-level call for the current task and return it.

        Pass an existing ``CallResult`` or keyword data to build one. Module
        methods pass ``action="<module>.<method>"``; user task code may omit it
        and gets ``task.<function name>``.
        """
        if result is None:
            result = CallResult(
                action=self._default_action() if action is None else action,
                changed=changed,
                summary=summary,
                **data,
            )
        self._calls.append(result)
        return result

    def _default_action(self) -> str:
        if self._task is not None:
            return f"task.{self._task.func.__name__}"
        return "task"

    def note(self, detail: str) -> None:
        """Attach an informational note to the current task line."""
        self._notes.append(detail)

    def memo[T](self, key: str, loader: Callable[[], T]) -> T:
        """Compute a value once per run and return the cached result."""
        if key not in self._memo:
            self._memo[key] = loader()
        return cast(T, self._memo[key])

    def close(self) -> None:
        """Close the privileged worker, if it was started."""
        try:
            self.privileged_worker.close()
        except Exception:  # noqa: BLE001 - cleanup must not mask the run
            if self.verbose:
                traceback.print_exc()

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
        require_text(key, "key")
        self._memo[key] = value

    @overload
    def get(self, key: str) -> Any: ...

    @overload
    def get[T](self, key: str, default: T) -> T: ...

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Return a value stored by :meth:`set`, :meth:`input`, or :meth:`memo`.

        Raises ``TaskFailed`` when the key is missing and no default is given.
        """
        require_text(key, "key")
        if key in self._memo:
            return self._memo[key]
        if default is _MISSING:
            raise TaskFailed(f"no stored value for key: {key}")
        return default
