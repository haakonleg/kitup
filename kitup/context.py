"""Per-run task context exposed to bundle task code."""

import builtins
import subprocess
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from functools import wraps
from pathlib import Path
from types import FrameType
from typing import Any, Final, Never, cast, overload

from ._privileged.client import PrivilegedWorker
from ._utils.paths import OverlayPath, expand_path, real_path
from ._utils.validation import require_text
from .commands import CommandRunner, raise_for_status
from .errors import SkipTask, TaskFailed
from .output import Output
from .privileges import PrivilegeManager
from .project import FILES_DIR, Project
from .registry import Bundle, SideEffect, Task, action_prefix
from .results import CallResult, TaskResult
from .runtime import BundleState, RunState
from .variables import Vars

_MISSING: Final = object()


def _memoized[K, T](store: dict[K, Any], key: K, loader: Callable[[], T]) -> T:
    """Return the cached value for ``key``, computing it with ``loader`` once."""
    if key not in store:
        store[key] = loader()
    return cast(T, store[key])


def memo[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """Cache the result of ``func`` once per run, keyed by its arguments.

    Arguments and keyword arguments must be hashable; otherwise ``TaskFailed``
    is raised.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        ctx = current_context()
        key: tuple[object, ...] = (func, args, tuple(sorted(kwargs.items())))
        try:
            hash(key)
        except TypeError as exc:
            raise TaskFailed("memo arguments must be hashable") from exc
        return _memoized(ctx._memo_calls, key, lambda: func(*args, **kwargs))

    return wrapper


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
        self._diffs: list[str] = []
        self._memo: dict[str, Any] = {}
        self._memo_calls: dict[tuple[object, ...], Any] = {}
        self._run_state = RunState()
        self._task_name = ""
        self._bundle: Bundle | None = None
        self._bundle_path: Path | None = None
        self._task: Task | None = None
        self._running_side_effect = False
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
    def diffs(self) -> list[str]:
        """Return the diff lines buffered for the current task."""
        return list(self._diffs)

    @property
    def run_state(self) -> RunState:
        """Return the run-scoped state shared with the runner."""
        return self._run_state

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

    @property
    def files(self) -> OverlayPath:
        """Return the merged view of the bundle's ``files`` directory and its overlays.

        The project's ``<root>/files/<bundle id>/`` directory and any
        ``--files-path`` overlays override the bundle's own ``files/`` directory
        per entry. Extra overlays win over the project's own overlay, and later
        ``--files-path`` values win over earlier ones. The view is read-only, and
        its path string is a synthetic prefix that never exists on disk; pass
        entries to module functions such as ``files.copy()`` or read them with
        ``Path`` methods.
        """
        bundle = self.bundle
        if self._bundle_path is None or not bundle.id:
            raise TaskFailed(f"no files for bundle {bundle.id!r}")
        candidates = (
            *(root / bundle.id for root in reversed(self.project.files_roots)),
            self.root / FILES_DIR / bundle.id,
            self._bundle_path / FILES_DIR,
        )
        roots = tuple(root for root in candidates if root.is_dir())
        if not roots:
            raise TaskFailed(f"no files for bundle {bundle.id!r}")
        return OverlayPath.merged(roots, prefix=f"/@kitup-merged/{bundle.id}")

    def resolve_source(self, path: str | Path) -> Path:
        """Resolve a module source path against the bundle's merged files view.

        Absolute paths are returned unchanged. A bare name or a ``files/...``
        path resolves inside :attr:`files`, so project overlays apply. Any other
        relative path resolves against the bundle directory without overlays.
        """
        source = expand_path(real_path(path), error=TaskFailed)
        if source.is_absolute():
            return source
        if self._bundle_path is None:
            raise TaskFailed(f"cannot resolve relative source path: {source}")
        parts = source.parts
        if len(parts) == 1:
            return real_path(self.files / source)
        if parts and parts[0] == FILES_DIR:
            return real_path(self.files / Path(*parts[1:]))
        return self._bundle_path / source

    def source_label(self, source: str | Path) -> str:
        """Return a concise CLI label for a module source argument.

        Relative paths are shown as given, with a leading ``files/`` lookup
        prefix removed. ``ctx.files`` entries keep their path within the merged
        view, and absolute paths are returned unchanged.
        """
        if isinstance(source, OverlayPath):
            relative = source.relative
            return str(relative) if relative.parts else source.name
        path = Path(source)
        if path.is_absolute():
            return str(path)
        parts = path.parts
        if len(parts) > 1 and parts[0] == FILES_DIR:
            return str(Path(*parts[1:]))
        return str(path)

    def begin_task(self, task: Task, *, is_side_effect: bool = False) -> None:
        """Reset task-scoped state and enter the task's bundle."""
        self._calls.clear()
        self._notes.clear()
        self._diffs.clear()
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

    def take_side_effects(self) -> list[SideEffect]:
        """Return and clear the side effects registered by the current task."""
        state = self._run_state.current
        if state is None:
            return []
        effects = list(state.pending_effects)
        state.pending_effects.clear()
        return effects

    def store_result(self, task: Task, result: TaskResult) -> None:
        """Store a task result in its bundle's runtime state."""
        state = self._run_state.for_bundle(task.bundle)
        state.results[task.id] = result

    def finish_run(self) -> None:
        """Leave the current bundle when the run ends."""
        self._run_state.current = None

    def ensure_sudo(self, reason: str | None = None) -> None:
        """Ensure sudo credentials are valid, prompting when needed."""
        if reason is None:
            reason = self._sudo_reason()
        if self.sudo.credentials_valid():
            return
        self._prompt_sudo(reason)

    def _prompt_sudo(self, reason: str) -> None:
        if self.output is not None:
            with self.output.interactive():
                self.sudo.authenticate(reason)
        else:
            self.sudo.authenticate(reason)

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
        request sudo credentials; a command that fails because the credentials
        expired is retried once after prompting.
        """

        def execute() -> subprocess.CompletedProcess[str]:
            return self.command.run(
                argv,
                cwd=cwd,
                env=env,
                sudo=sudo,
                timeout=timeout,
                check=False,
                stdin=stdin,
            )

        result = execute()
        if sudo and self.sudo.is_auth_failure(result):
            self._prompt_sudo(self._sudo_reason())
            result = execute()
        if check:
            raise_for_status(result)
        return result

    def probe(self, argv: str | Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Run a query command without failing on a nonzero exit or start error."""
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
    def changed(self, *, changed: bool = True, summary: str, **data: Any) -> CallResult: ...

    def changed(
        self, result: CallResult | None = None, *, changed: bool = True, summary: str = "", **data: Any
    ) -> CallResult:
        """Record one module-level call for the current task and return it.

        Pass an existing ``CallResult`` or keyword data to build one. The
        action is inferred from the calling function: a module registered with
        :func:`kitup.module` yields ``"<module>.<function>"``, while task code
        gets ``task.<function name>``. Pass a ``CallResult`` with an explicit
        action to override it.
        """
        frame = sys._getframe(1)
        if result is None:
            result = CallResult(action=self._default_action(frame), changed=changed, summary=summary, **data)
        elif not result.action:
            result["action"] = self._default_action(frame)
        self._calls.append(result)
        return result

    def _default_action(self, frame: FrameType) -> str:
        prefix = action_prefix(frame.f_globals.get("__name__", ""))
        if prefix is not None:
            return f"{prefix}.{frame.f_code.co_name}"
        if self._task is not None:
            return f"task.{self._task.func.__name__}"
        return "task"

    def note(self, detail: str) -> None:
        """Attach an informational note to the current task line."""
        self._notes.append(detail)

    def add_diff(self, line: str) -> None:
        """Buffer one diff line for rendering under the current task."""
        self._diffs.append(line)

    def memo[T](self, key: str, loader: Callable[[], T]) -> T:
        """Compute a value once per run and return the cached result."""
        require_text(key, "memo key")
        return _memoized(self._memo, key, loader)

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


_current: ContextVar[TaskContext | None] = ContextVar("kitup_current_context", default=None)


def current_context() -> TaskContext:
    """Return the context of the task currently executing.

    Module functions and tasks call this to reach the run's :class:`TaskContext`
    without receiving it as an argument. Raises ``TaskFailed`` when called
    outside a running task.
    """
    context = _current.get()
    if context is None:
        raise TaskFailed("no active task context; module functions and tasks must run inside a task")
    return context


def set_current_context(context: TaskContext | None) -> Token[TaskContext | None]:
    """Make ``context`` the active task context, or clear it with ``None``.

    Returns a token for :func:`reset_current_context`.
    """
    return _current.set(context)


def reset_current_context(token: Token[TaskContext | None]) -> None:
    """Restore the task context to its value before ``set_current_context``."""
    _current.reset(token)
