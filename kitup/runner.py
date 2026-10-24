"""Sequential, fail-fast task execution."""

from __future__ import annotations

import time
import traceback

from .context import TaskContext
from .errors import PrivilegeError, SkipTask, TaskFailed
from .output import Output
from .registry import Task
from .resolver import BundlePlan
from .results import RunSummary, Status, TaskResult


class Runner:
    """Execute a resolved plan, aborting on the first failed task."""

    def __init__(
        self,
        plan: BundlePlan,
        context: TaskContext,
        output: Output,
        *,
        tags: frozenset[str] | None = None,
        skip_tags: frozenset[str] | None = None,
    ) -> None:
        self.plan = plan
        self.context = context
        self.output = output
        self.tags = tags
        self.skip_tags = skip_tags
        self._summary = RunSummary()

    def run(self) -> int:
        start = time.monotonic()
        selected = [(bundle, self._select(bundle.tasks)) for bundle in self.plan.bundles]
        total = sum(len(tasks) for _, tasks in selected)
        completed = 0

        try:
            for bundle, tasks in selected:
                if not tasks:
                    continue
                self.output.bundle_header(bundle.name)
                for task in tasks:
                    result = self._run_task(task)
                    completed += 1
                    self._summary.add(result.status)
                    self.output.task(result)

                    if result.status is Status.FAILED:
                        self.output.failure(result)
                        not_run = total - completed
                        self._finish(start, not_run)
                        self.output.aborted(task.name, not_run)
                        return 1
        except KeyboardInterrupt:
            not_run = total - completed
            self._finish(start, not_run)
            self.output.interrupted(not_run)
            return 130

        self._finish(start)
        return 0

    def _finish(self, start: float, not_run: int = 0) -> None:
        if not_run:
            self._summary.add(Status.NOT_RUN, not_run)
        self.output.summary(self._summary.counts, time.monotonic() - start)

    def _select(self, tasks: list[Task]) -> list[Task]:
        selected: list[Task] = []
        for task in tasks:
            if self.tags is not None and not (task.tags & self.tags):
                continue
            if self.skip_tags is not None and task.tags & self.skip_tags:
                continue
            selected.append(task)
        return selected

    def _run_task(self, task: Task) -> TaskResult:
        started = time.monotonic()
        self.context.begin_task(task)
        status = Status.OK
        detail = ""
        error: BaseException | None = None

        try:
            # Root tasks refresh credentials just before they start, not once per run.
            if task.needs_sudo:
                self.context.ensure_sudo(
                    f'Task "{task.name}" requires sudo privileges; '
                    "you may be prompted for your password."
                )
            task.func(self.context)
        except SkipTask as exc:
            status = Status.SKIPPED
            detail = exc.reason
            error = exc
        except (TaskFailed, PrivilegeError) as exc:
            status = Status.FAILED
            detail = str(exc)
            error = exc
        except Exception as exc:  # noqa: BLE001 - tasks may raise anything
            if self.context.verbose:
                traceback.print_exc()
            status = Status.FAILED
            detail = f"{type(exc).__name__}: {exc}"
            error = exc

        if status is Status.SKIPPED:
            changes = []
            notes = []
        else:
            changes = self.context.changes
            notes = self.context.notes
            if status is Status.OK and changes:
                status = Status.CHANGED

        return TaskResult(
            task=task,
            status=status,
            changes=changes,
            notes=notes,
            detail=detail,
            duration=time.monotonic() - started,
            error=error,
        )
