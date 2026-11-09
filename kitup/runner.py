"""Sequential, fail-fast task execution."""

import time
import traceback
from collections.abc import Sequence
from typing import Any

from .context import TaskContext
from .errors import PrivilegeError, SkipTask, TaskFailed
from .output import Output, bundle_label
from .registry import Bundle, SideEffect, Task, TaskSpec
from .resolver import BundlePlan
from .results import RunSummary, Status, TaskResult
from .runtime import BundleState


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
        """Execute the plan and return the process exit code."""
        start = time.monotonic()
        selected = [(bundle, self._select(bundle.tasks)) for bundle in self.plan.bundles]
        self.output.fit_task_names(task.name for _, tasks in selected for task in tasks)
        total = sum(len(tasks) for _, tasks in selected)
        completed = 0

        try:
            for bundle, tasks in selected:
                if not tasks:
                    continue
                self.output.bundle_header(bundle_label(bundle))
                state = self.context.run_state.for_bundle(bundle)

                for task in tasks:
                    self.output.task_start(task.name)
                    try:
                        result = self._run_task(task)
                    finally:
                        self.output.task_stop()
                    completed += 1
                    self._summary.add(result.status)
                    self.output.task(result)

                    pending = self.context.take_side_effects()
                    if result.status is Status.CHANGED:
                        immediate = [effect for effect in pending if not effect.one_shot]
                        if immediate:
                            code = self._run_side_effects(
                                state,
                                immediate,
                                start=start,
                                total=total,
                                completed=completed,
                            )
                            if code:
                                return code
                        state.deferred_effects.extend(effect for effect in pending if effect.one_shot)

                    if result.status is Status.FAILED:
                        self.output.failure(result)
                        return self._abort(task.name, start, total, completed)

                if state.deferred_effects:
                    code = self._run_side_effects(
                        state,
                        state.deferred_effects,
                        start=start,
                        total=total,
                        completed=completed,
                    )
                    if code:
                        return code
                    state.deferred_effects.clear()
        except KeyboardInterrupt:
            not_run = total - completed
            self._finish(start, not_run)
            self.output.interrupted(not_run)
            return 130
        finally:
            self.context.finish_run()

        self._finish(start)
        return 0

    def _finish(self, start: float, not_run: int = 0) -> None:
        if not_run:
            self._summary.add(Status.NOT_RUN, not_run)
        self.output.summary(self._summary.counts, time.monotonic() - start)

    def _abort(self, name: str, start: float, total: int, completed: int) -> int:
        not_run = total - completed
        self._finish(start, not_run)
        self.output.aborted(name, not_run)
        return 1

    def _select(self, tasks: Sequence[Task]) -> list[Task]:
        selected: list[Task] = []
        for task in tasks:
            if self.tags is not None and not (task.tags & self.tags):
                continue
            if self.skip_tags is not None and task.tags & self.skip_tags:
                continue
            selected.append(task)
        return selected

    def _run_side_effects(
        self,
        state: BundleState,
        side_effects: list[SideEffect],
        *,
        start: float,
        total: int,
        completed: int,
    ) -> int:
        self.output.fit_task_names(side_effect.name for side_effect in side_effects)

        for side_effect in side_effects:
            if side_effect.one_shot and side_effect in state.executed_effects:
                continue

            task = _side_effect_task(state.bundle, side_effect)

            self.output.task_start(side_effect.name, side_effect=True)
            try:
                result = self._run_task(task, is_side_effect=True)
            finally:
                self.output.task_stop()

            self._summary.add(result.status)
            self.output.task(result, side_effect=True)

            if result.status is Status.FAILED:
                self.output.failure(result)
                return self._abort(side_effect.name, start, total, completed)

            if side_effect.one_shot and result.status in (Status.OK, Status.CHANGED):
                state.executed_effects.add(side_effect)

        return 0

    def _run_task(self, task: Task, *, is_side_effect: bool = False) -> TaskResult:
        self.context.begin_task(task, is_side_effect=is_side_effect)
        status = Status.OK
        detail = ""
        error: BaseException | None = None
        value: Any = None

        try:
            value = task.func(self.context)
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

        calls = self.context.calls
        notes = [] if status is Status.SKIPPED else self.context.notes
        if status is Status.OK and any(call.changed for call in calls):
            status = Status.CHANGED

        result = TaskResult(
            task=task,
            status=status,
            calls=calls,
            notes=notes,
            detail=detail,
            duration=self.output.task_elapsed,
            error=error,
            value=value,
        )
        self.context.store_result(task, result)
        return result


def _side_effect_task(bundle: Bundle, side_effect: SideEffect) -> Task:
    """Wrap a side effect as a synthetic task owned by the registering bundle."""
    return Task(
        spec=TaskSpec(
            func=side_effect.func,
            name=side_effect.name,
            tags=frozenset(),
            description=side_effect.description,
            declared_in=None,
            order=0,
        ),
        bundle=bundle,
        bundle_path=bundle.path,
    )
