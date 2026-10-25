"""Reusable worker for rendering transient terminal animation lines."""

from __future__ import annotations

import threading
import time
from typing import TextIO

from .animation import Animation


class LiveLine:
    """Render one animation line on a reusable worker thread."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._animation: Animation | None = None
        self._started = 0.0
        self._tick = 0
        self._drawn = False
        self._suspended = 0

    def start(self, animation: Animation) -> None:
        with self._condition:
            self._animation = animation
            self._started = time.monotonic()
            self._tick = 0
            self._drawn = False
            self._ensure_thread()
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            if self._animation is None:
                return
            self._animation = None
            if self._drawn:
                self._clear()
                self.stream.flush()
                self._drawn = False
            self._condition.notify()

    def suspend(self) -> None:
        with self._condition:
            self._suspended += 1
            if self._suspended > 1:
                return
            if self._drawn:
                self._clear()
                self.stream.flush()
                self._drawn = False
            self._condition.notify()

    def resume(self) -> None:
        with self._condition:
            if self._suspended == 0:
                return
            self._suspended -= 1
            if self._suspended == 0:
                self._condition.notify()

    def write(self, text: str = "") -> None:
        # Only the runner toggles the animation state, so checking without the
        # lock is safe and keeps ordinary output uncontended.
        if self._animation is None or self._suspended or not self._drawn:
            print(text, file=self.stream)
            return
        with self._condition:
            if self._animation is None or self._suspended or not self._drawn:
                print(text, file=self.stream)
                return
            self._clear()
            print(text, file=self.stream)
            self._draw()

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="kitup-animation"
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._animation is None:
                    self._condition.wait()
                animation = self._animation

                deadline = time.monotonic() + animation.delay
                while self._animation is animation:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)

                while self._animation is animation:
                    if self._suspended == 0:
                        self._draw()
                        self._tick += 1
                    self._condition.wait(animation.interval)

    def _draw(self) -> None:
        animation = self._animation
        if animation is None:
            return
        elapsed = time.monotonic() - self._started
        self.stream.write(f"\r{animation.render(elapsed, self._tick)}\033[K")
        self.stream.flush()
        self._drawn = True

    def _clear(self) -> None:
        self.stream.write("\r\033[K")
