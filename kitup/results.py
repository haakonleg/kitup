"""Result and call-tracking data structures used by the runner."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .registry import Task


class CallResult(dict[str, Any]):
    """Result of a module call, with change data inlined.

    ``action``, ``changed``, and ``summary`` are reserved keys. ``action``
    identifies the API method as ``"<module>.<method>"``. Every other keyword
    argument is stored as a top-level data field and is also available as an
    attribute.
    """

    def __init__(
        self,
        *,
        action: str,
        changed: bool,
        summary: str,
        **data: Any,
    ) -> None:
        if not isinstance(action, str) or not action:
            raise ValueError("action must be a non-empty string")
        super().__init__(action=action, changed=changed, summary=summary, **data)

    @property
    def action(self) -> str:
        """API method that produced the call, as ``"<module>.<method>"``."""
        return str(self["action"])

    @property
    def changed(self) -> bool:
        """Whether the call changed something."""
        return bool(self["changed"])

    @property
    def summary(self) -> str:
        """Short human-readable description of the call."""
        return str(self["summary"])

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


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
    calls: list[CallResult] = field(default_factory=list)
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
    def changes(self) -> list[CallResult]:
        """Return the calls that changed something."""
        return [call for call in self.calls if call.changed]

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
