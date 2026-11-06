"""Timing helpers for recorded calls."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class Timing:
    """UTC boundaries and elapsed seconds for an operation."""

    start: datetime
    end: datetime | None = None
    duration: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Return ``start``, ``end``, and ``duration`` for a call result."""
        if self.end is None:
            raise RuntimeError("timing is not complete")
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration": self.duration,
        }


@contextmanager
def timed() -> Iterator[Timing]:
    """Measure UTC boundaries and monotonic elapsed time for a block."""
    timing = Timing(start=datetime.now(UTC))
    clock = time.monotonic()
    try:
        yield timing
    finally:
        timing.end = datetime.now(UTC)
        timing.duration = time.monotonic() - clock
