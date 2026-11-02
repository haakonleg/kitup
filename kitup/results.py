"""Result and change-tracking data structures used by the runner."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .registry import Task


@dataclass(frozen=True, slots=True)
class Change:
    """One module-reported change: structured data plus a short summary."""

    data: dict[str, Any]
    summary: str


class Status(StrEnum):
    """Final status of a task."""

    OK = "ok"
    CHANGED = "changed"
    SKIPPED = "skipped"
    FAILED = "failed"
    NOT_RUN = "not run"


@dataclass(slots=True)
class TaskResult:
    """Outcome and timing for one executed task."""

    task: Task
    status: Status
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    detail: str = ""
    duration: float = 0.0
    error: BaseException | None = None
    value: Any = None

    @property
    def changed(self) -> bool:
        """Whether the task reported at least one change."""
        return self.status is Status.CHANGED

    @property
    def ok(self) -> bool:
        """Whether the task completed successfully."""
        return self.status in (Status.OK, Status.CHANGED)

    @property
    def skipped(self) -> bool:
        """Whether the task was skipped."""
        return self.status is Status.SKIPPED

    @property
    def failed(self) -> bool:
        """Whether the task failed."""
        return self.status is Status.FAILED


@dataclass(slots=True)
class RunSummary:
    """Aggregate counts for one run."""

    counts: dict[Status, int] = field(default_factory=dict)

    def add(self, status: Status, count: int = 1) -> None:
        """Add a count for the given status."""
        self.counts[status] = self.counts.get(status, 0) + count
