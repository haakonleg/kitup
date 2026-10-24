"""Result and change-tracking data structures used by the runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .registry import Task


@dataclass(frozen=True)
class Change:
    """One module-reported change: structured data plus a short summary."""

    data: dict[str, Any]
    summary: str


class Status(StrEnum):
    OK = "ok"
    CHANGED = "changed"
    SKIPPED = "skipped"
    FAILED = "failed"
    NOT_RUN = "not run"


@dataclass
class TaskResult:
    task: Task
    status: Status
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    detail: str = ""
    duration: float = 0.0
    error: BaseException | None = None


@dataclass
class RunSummary:
    """Aggregate counts for one run."""

    counts: dict[Status, int] = field(default_factory=dict)

    def add(self, status: Status, count: int = 1) -> None:
        self.counts[status] = self.counts.get(status, 0) + count
