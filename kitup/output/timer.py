"""Pausable monotonic timing for transient output."""

from __future__ import annotations

import time
from collections.abc import Callable


class Stopwatch:
    """Measure elapsed time while allowing explicit pauses."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._paused = 0.0
        self._pause_started: float | None = None

    def reset(self) -> None:
        """Restart the stopwatch with no paused time."""
        self._started = self._clock()
        self._paused = 0.0
        self._pause_started = None

    def pause(self) -> None:
        """Stop accumulating time until :meth:`resume` is called."""
        if self._pause_started is None:
            self._pause_started = self._clock()

    def resume(self) -> None:
        """Resume accumulating time after a pause."""
        if self._pause_started is None:
            return
        self._paused += self._clock() - self._pause_started
        self._pause_started = None

    def elapsed(self) -> float:
        """Return elapsed time, excluding paused intervals."""
        now = self._clock()
        paused = self._paused
        if self._pause_started is not None:
            paused += now - self._pause_started
        return max(0.0, now - self._started - paused)
