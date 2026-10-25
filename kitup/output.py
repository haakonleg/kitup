"""Line-oriented terminal output with optional ANSI colors."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from typing import Any, TextIO

from . import __version__
from .results import Status, TaskResult

NAME_WIDTH = 44
STATUS_WIDTH = 9
SPINNER_FRAMES = ("|", "/", "-", "\\")
SPINNER_DELAY = 0.1
SPINNER_INTERVAL = 0.1

_COLORS = {
    Status.OK: "\033[32m",
    Status.CHANGED: "\033[33m",
    Status.SKIPPED: "\033[36m",
    Status.FAILED: "\033[31m",
}
_RESET = "\033[0m"
_DIM = "\033[2m"


class Output:
    """Small formatter shared by the CLI and runner."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        no_color: bool = False,
        verbose: bool = False,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.verbose = verbose
        self._interactive = self.stream.isatty()
        self.no_color = no_color or bool(os.environ.get("NO_COLOR")) or not self._interactive
        self.name_width = NAME_WIDTH

        self._spinner_condition = threading.Condition()
        self._spinner_thread: threading.Thread | None = None
        self._spinner_active = False
        self._spinner_drawn = False
        self._spinner_name = ""
        self._spinner_started = 0.0
        self._spinner_frame = 0
        self._spinner_generation = 0

    def _paint(self, text: str, code: str) -> str:
        if self.no_color or not code:
            return text
        return f"{code}{text}{_RESET}"

    def write(self, text: str = "") -> None:
        # Only the runner toggles spinner state, so this uncontended check is
        # safe and avoids taking the lock for ordinary output.
        if not self._spinner_active or not self._spinner_drawn:
            print(text, file=self.stream)
            return
        with self._spinner_condition:
            if not self._spinner_active or not self._spinner_drawn:
                print(text, file=self.stream)
                return
            self._erase_spinner()
            print(text, file=self.stream)
            self._draw_spinner()

    def banner(self, facts: Mapping[str, Any]) -> None:
        parts = [f"kitup {__version__}", _platform_label(facts)]
        if desktop := _desktop_label():
            parts.append(desktop)
        self.write("  ·  ".join(part for part in parts if part))

    def bundle_header(self, name: str) -> None:
        self.write()
        self.write(f"bundle: {name}")

    def fit_task_names(self, names: Iterable[str]) -> None:
        """Widen the task name column so statuses stay aligned."""
        self.name_width = max(
            NAME_WIDTH, max((len(name) for name in names), default=0) + 2
        )

    def task_start(self, name: str) -> None:
        """Show an animated running line until :meth:`task_stop` is called."""
        if not self._interactive:
            return
        with self._spinner_condition:
            self._spinner_name = name
            self._spinner_started = time.monotonic()
            self._spinner_frame = 0
            self._spinner_drawn = False
            self._spinner_generation += 1
            self._spinner_active = True
            self._start_spinner()
            self._spinner_condition.notify()

    def task_stop(self) -> None:
        """Stop the running indicator and clear its line."""
        with self._spinner_condition:
            if not self._spinner_active:
                return
            self._spinner_active = False
            if self._spinner_drawn:
                self._erase_spinner()
                self.stream.flush()
                self._spinner_drawn = False
            self._spinner_condition.notify()

    def _start_spinner(self) -> None:
        if self._spinner_thread is not None:
            return
        self._spinner_thread = threading.Thread(
            target=self._spin, daemon=True, name="kitup-spinner"
        )
        self._spinner_thread.start()

    def _spin(self) -> None:
        while True:
            with self._spinner_condition:
                while not self._spinner_active:
                    self._spinner_condition.wait()
                generation = self._spinner_generation

                # Delay the first frame so very fast tasks do not flicker.
                self._spinner_condition.wait(SPINNER_DELAY)
                if not self._spinner_active or generation != self._spinner_generation:
                    continue
                self._spinner_frame = 0
                self._draw_spinner()

                while True:
                    self._spinner_condition.wait(SPINNER_INTERVAL)
                    if (
                        not self._spinner_active
                        or generation != self._spinner_generation
                    ):
                        break
                    self._spinner_frame += 1
                    self._draw_spinner()

    def _draw_spinner(self) -> None:
        frame = SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
        elapsed = time.monotonic() - self._spinner_started
        width = max(self.name_width, len(self._spinner_name) + 2)
        status = self._paint(frame.ljust(2), _DIM)
        duration = self._paint(f"  ({elapsed:.2f} s)", _DIM)
        self.stream.write(f"\r  {self._spinner_name:<{width}}{status}{duration}\033[K")
        self.stream.flush()
        self._spinner_drawn = True

    def _erase_spinner(self) -> None:
        self.stream.write("\r\033[K")

    def task(self, result: TaskResult) -> None:
        detail = self._task_detail(result)
        status_text = result.status.value
        if detail:
            status_text = status_text.ljust(STATUS_WIDTH)
        status = self._paint(status_text, _COLORS.get(result.status, ""))
        width = max(self.name_width, len(result.task.name) + 2)
        line = f"  {result.task.name:<{width}}{status}{detail}"
        duration = self._paint(f"  ({result.duration:.2f} s)", _DIM)
        self.write(f"{line}{duration}")
        if self.verbose and result.task.description:
            self.write(self._paint(f"    {result.task.description}", _DIM))

    def _task_detail(self, result: TaskResult) -> str:
        if result.detail:
            detail = result.detail
        elif result.changes:
            if self.verbose:
                detail = "; ".join(str(change.data) for change in result.changes)
            else:
                detail = "; ".join(change.summary for change in result.changes)
        else:
            detail = ""

        if result.notes:
            note = "; ".join(result.notes)
            detail = f"{detail}; {note}" if detail else note
        return detail

    def failure(self, result: TaskResult) -> None:
        error = result.error
        if error is None:
            return
        message = getattr(error, "message", str(error))
        self.write(f"error: {message}")

        command = getattr(error, "command", None)
        if command:
            self.write(f"command: {' '.join(command)}")

        stderr = getattr(error, "stderr", "") or ""
        if stderr.strip():
            lines = stderr.rstrip().splitlines()
            if not self.verbose:
                lines = lines[-20:]
            for line in lines:
                self.write(f"  {line}")

    def summary(self, counts: Mapping[Status, int], duration: float) -> None:
        parts = [
            f"{counts.get(Status.CHANGED, 0)} changed",
            f"{counts.get(Status.OK, 0)} ok",
        ]
        if counts.get(Status.SKIPPED, 0):
            parts.append(f"{counts[Status.SKIPPED]} skipped")
        parts.extend(
            [
                f"{counts.get(Status.FAILED, 0)} failed",
                f"{counts.get(Status.NOT_RUN, 0)} not run",
            ]
        )
        self.write()
        self.write(f"Summary: {', '.join(parts)} — {duration:.1f} s")

    def aborted(self, task_name: str, not_run: int) -> None:
        suffix = f" {not_run} task(s) not run." if not_run else ""
        self.write(f'Aborted after failure in "{task_name}".{suffix}')

    def interrupted(self, not_run: int) -> None:
        suffix = f" {not_run} task(s) not run." if not_run else ""
        self.write(f"Interrupted.{suffix}")

    def command(
        self,
        argv: list[str] | tuple[str, ...],
        duration: float,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.write(self._paint(f"  $ {' '.join(argv)}", _DIM))
        self.write(self._paint(f"    -> exit {returncode} in {duration:.2f} s", _DIM))
        self._output_block("stdout", stdout)
        self._output_block("stderr", stderr)

    def _output_block(self, label: str, text: str) -> None:
        lines = text.rstrip("\n").splitlines()
        if not lines:
            return
        self.write(f"    {label}:")
        for line in lines:
            self.write(f"      {line}")


def _platform_label(facts: Mapping[str, Any]) -> str:
    os_release = facts.get("os_release") or {}

    pretty_name = str(os_release.get("PRETTY_NAME") or "").strip()
    if pretty_name:
        return pretty_name

    name = str(os_release.get("NAME") or facts.get("distro_id") or "").strip()
    version = str(facts.get("version_id") or os_release.get("VERSION_ID") or "").strip()
    label = " ".join(part for part in (name, version) if part)

    variant = str(os_release.get("VARIANT") or "").strip()
    if variant and variant.lower() not in label.lower():
        label = f"{label} ({variant})" if label else variant
    return label


def _desktop_label() -> str:
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").replace(":", ";").split(";")
    if any(name.strip().upper() == "GNOME" for name in desktops):
        return "GNOME"
    return ""
