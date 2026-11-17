"""Line-oriented terminal output with optional ANSI colors."""

import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Final, TextIO

from .. import PACKAGE_NAME, __version__
from .._utils.text import truncate
from ..registry import Bundle
from ..results import Status, TaskResult
from .animation import Animation
from .live_line import LiveLine
from .terminal import clear_input as clear_terminal_input
from .terminal import terminal_columns
from .timer import Stopwatch

NAME_WIDTH: Final = 44
STATUS_WIDTH: Final = 9
CHANGE_COUNT_WIDTH: Final = 4
STATUS_SEGMENT_WIDTH: Final = STATUS_WIDTH + 1 + CHANGE_COUNT_WIDTH
DURATION_WIDTH: Final = len("  (00.00 s)")
NAME_GAP: Final = 4
MIN_NAME_WIDTH: Final = 10
SUB_INDENT: Final = 4
SIDE_EFFECT_INDENT: Final = 6
DIFF_EXTRA_INDENT: Final = 2
MAX_FAILURE_LINES: Final = 20
SPINNER_FRAMES: Final = ("|", "/", "-", "\\")
SPINNER_DELAY: Final = 0.1
SPINNER_INTERVAL: Final = 0.1

_GREEN: Final = "\033[32m"
_YELLOW: Final = "\033[33m"
_CYAN: Final = "\033[36m"
_RED: Final = "\033[31m"
_MAGENTA: Final = "\033[35m"
_BOLD: Final = "\033[1m"
_BOLD_RED: Final = "\033[1;31m"
_DIM: Final = "\033[2m"
_RESET: Final = "\033[0m"

_SKIPPED_GLYPH: Final = "–"  # noqa: RUF001 - intentional terminal glyph
_NOTE_MARKER: Final = "›"  # noqa: RUF001 - intentional terminal glyph
_CHANGE_COUNT_MARKER: Final = "×"  # noqa: RUF001 - intentional terminal glyph

_COLORS: Final = {
    Status.OK: _GREEN,
    Status.CHANGED: _YELLOW,
    Status.SKIPPED: _CYAN,
    Status.FAILED: _BOLD_RED,
}
_GLYPHS: Final = {
    Status.OK: "✓",
    Status.CHANGED: "▲",
    Status.SKIPPED: _SKIPPED_GLYPH,
    Status.FAILED: "✗",
}


def bundle_label(bundle: Bundle) -> str:
    """Return the standard bundle label: ``name (id)`` when a name is set."""
    if bundle.name and bundle.name != bundle.id:
        return f"{bundle.name} ({bundle.id})"
    return bundle.id


def _not_run_suffix(not_run: int) -> str:
    """Return the trailing not-run sentence for abort and interrupt messages."""
    return f" {not_run} task(s) not run." if not_run else ""


class Output:
    """Small formatter shared by the CLI and runner.

    Terminal animations are suspended inside :meth:`interactive`, allowing
    external prompts to own the terminal.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        no_color: bool = False,
        verbose: bool = False,
        dry_run: bool = False,
        stderr: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self.verbose = verbose
        self.dry_run = dry_run
        self.is_interactive = self.stream.isatty()
        self.stderr_is_interactive = self.stderr.isatty()
        self._color_disabled = no_color or bool(os.environ.get("NO_COLOR"))
        self.no_color = self._color_disabled or not self.is_interactive
        self.name_width = NAME_WIDTH

        self._live = LiveLine(self.stream, clock=clock)
        self._task_timer = Stopwatch(clock)
        self._interactive_depth = 0

    def _paint(self, text: str, code: str, *, stderr: bool = False) -> str:
        if not code:
            return text
        if stderr:
            if self._color_disabled or not self.stderr_is_interactive:
                return text
        elif self.no_color:
            return text
        return f"{code}{text}{_RESET}"

    def dim(self, text: str) -> str:
        """Return ``text`` dimmed when color is enabled."""
        return self._paint(text, _DIM)

    def bold(self, text: str) -> str:
        """Return ``text`` in bold when color is enabled."""
        return self._paint(text, _BOLD)

    def write(self, text: str = "") -> None:
        """Write one line of output."""
        self._live.write(text)

    def warning(self, message: str, *, to_stderr: bool = True) -> None:
        """Write a yellow ``warning:`` line to stderr or the main stream."""
        text = f"warning: {message}"
        if to_stderr:
            print(self._paint(text, _YELLOW, stderr=True), file=self.stderr)
        else:
            self.write(self._paint(text, _YELLOW))

    def error(self, message: str, *, prefix: bool = True) -> None:
        """Write a bold red error line to stderr."""
        text = f"error: {message}" if prefix else message
        print(self._paint(text, _BOLD_RED, stderr=True), file=self.stderr)

    def banner(self, facts: Mapping[str, Any] | None = None) -> None:
        """Print the version and platform banner."""
        parts = [f"{PACKAGE_NAME} {__version__}"]
        if facts is not None:
            parts.append(_platform_label(facts))
        if desktop := _desktop_label():
            parts.append(desktop)
        self.write("  ·  ".join(part for part in parts if part))

    def dry_run_banner(self) -> None:
        """Print the dry-run banner before the run starts."""
        self.write(self._paint("dry run: no changes will be applied", _MAGENTA))

    def bundle_header(self, name: str) -> None:
        """Print a bold bundle header."""
        self.write()
        self.write(self._paint(f"bundle: {name}", _BOLD))

    def bundle_rollup(self, bundle_id: str, counts: Mapping[Status, int], duration: float) -> None:
        """Print a dim per-bundle summary line."""
        changed = counts.get(Status.CHANGED, 0)
        changed_label = "would change" if self.dry_run else "changed"
        parts = [f"{changed} {changed_label}", f"{counts.get(Status.OK, 0)} ok"]
        if skipped := counts.get(Status.SKIPPED, 0):
            parts.append(f"{skipped} skipped")
        self.write(self._paint(f"↳ {bundle_id}: {', '.join(parts)} — {duration:.1f} s", _DIM))

    def fit_task_names(self, names: Iterable[str]) -> None:
        """Widen the task name column so statuses stay aligned."""
        longest = max((len(name) for name in names), default=0)
        self.name_width = max(self.name_width, NAME_WIDTH, longest + NAME_GAP)

    def task_start(self, name: str, *, side_effect: bool = False, progress: tuple[int, int] | None = None) -> None:
        """Start timing a task and show its animated running line."""
        self._task_timer.reset()
        if self._interactive_depth:
            self._task_timer.pause()
        if not self.is_interactive:
            return
        self._live.start(self._task_spinner(name, side_effect=side_effect, progress=progress))

    def task_stop(self) -> None:
        """Stop the running indicator and clear its line."""
        self._live.stop()

    @contextmanager
    def interactive(self) -> Iterator[None]:
        """Suspend animations and task timing during an interactive prompt."""
        if self._interactive_depth == 0:
            self._task_timer.pause()
        self._interactive_depth += 1
        self._live.suspend()
        try:
            yield
        finally:
            self._live.resume()
            self._interactive_depth -= 1
            if self._interactive_depth == 0:
                self._task_timer.resume()

    @property
    def task_elapsed(self) -> float:
        """Return the current task duration, excluding prompts."""
        return self._task_timer.elapsed()

    def clear_input(self, prompt: str, value: str) -> None:
        """Erase an input prompt and its echoed value from the terminal."""
        clear_terminal_input(self.stream, prompt, value)

    def _progress_prefix(self, progress: tuple[int, int] | None, *, side_effect: bool) -> str:
        if progress is None:
            return "↳ " if side_effect else ""
        index, total = progress
        width = len(f"[{total}/{total}] ")
        if side_effect:
            return "↳".ljust(width)
        return f"[{index}/{total}] ".ljust(width)

    def _painted_progress_prefix(self, progress: tuple[int, int] | None, *, side_effect: bool) -> str:
        raw = self._progress_prefix(progress, side_effect=side_effect)
        return self._paint(raw, _DIM) if raw else ""

    def _name_width(self, progress: tuple[int, int] | None, *, side_effect: bool) -> int:
        if not self.is_interactive:
            return self.name_width
        columns = terminal_columns(self.stream)
        prefix_width = len(self._progress_prefix(progress, side_effect=side_effect))
        overhead = 2 + prefix_width + STATUS_SEGMENT_WIDTH + DURATION_WIDTH + 2
        available = columns - overhead
        if available < MIN_NAME_WIDTH:
            return self.name_width
        return min(self.name_width, available)

    def _name_field(self, name: str, progress: tuple[int, int] | None, *, side_effect: bool) -> str:
        width = self._name_width(progress, side_effect=side_effect)
        if width < self.name_width:
            name = truncate(name, max(1, width - NAME_GAP))
        return f"{name:<{width}}"

    def _task_spinner(
        self, name: str, *, side_effect: bool = False, progress: tuple[int, int] | None = None
    ) -> Animation:
        prefix = self._painted_progress_prefix(progress, side_effect=side_effect)

        def render(elapsed: float, tick: int) -> str:
            frame = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
            name_field = self._name_field(name, progress, side_effect=side_effect)
            status = self._paint(frame.ljust(STATUS_SEGMENT_WIDTH), _DIM)
            duration = self._paint(f"  ({elapsed:.2f} s)", _DIM)
            return f"  {prefix}{name_field}{status}{duration}"

        return Animation(render, SPINNER_DELAY, SPINNER_INTERVAL)

    def task(self, result: TaskResult, *, side_effect: bool = False, progress: tuple[int, int] | None = None) -> None:
        """Print the final status line, change sub-lines, and diffs for a task."""
        prefix = self._painted_progress_prefix(progress, side_effect=side_effect)
        name_field = self._name_field(result.task.name, progress, side_effect=side_effect)
        status = self._status_segment(result)
        duration = self._paint(f"  ({result.duration:.2f} s)", _DIM)
        line = f"  {prefix}{name_field}{status}{duration}"
        if result.status is Status.SKIPPED and result.detail:
            line = f"{line}  {result.detail}"
        self.write(line)

        if self.verbose and result.task.description:
            description = result.task.description.replace("\n", "\n    ")
            self.write(self._paint(f"    {description}", _DIM))

        indent = SIDE_EFFECT_INDENT if side_effect else SUB_INDENT
        for change in self._change_lines(result, indent):
            self.write(change)
        for diff in result.diffs:
            self.write(self._diff_line(diff, indent + DIFF_EXTRA_INDENT))

    def _status_segment(self, result: TaskResult) -> str:
        count = len(result.changes)
        dry_run_change = self.dry_run and result.status is Status.CHANGED
        word = "would" if dry_run_change else result.status.value
        color = _MAGENTA if dry_run_change else _COLORS.get(result.status, "")
        glyph = _GLYPHS.get(result.status, "") if self.is_interactive else ""
        label = f"{glyph} {word}" if glyph else word
        status = self._paint(label.ljust(STATUS_WIDTH), color)
        if count:
            times = self._paint(f"{_CHANGE_COUNT_MARKER}{count}".ljust(CHANGE_COUNT_WIDTH), color)
        else:
            times = " " * CHANGE_COUNT_WIDTH
        return f"{status} {times}"

    def _change_lines(self, result: TaskResult, indent: int) -> list[str]:
        lines: list[str] = []
        pad = " " * indent
        calls = result.calls if self.verbose else result.changes
        for call in calls:
            if call.changed:
                marker_color = _MAGENTA if self.dry_run else _YELLOW
                marker = self._paint("+", marker_color)
                lines.append(f"{pad}{marker} {self._paint(call.action, _DIM)}: {call.summary}")
            else:
                lines.append(self._paint(f"{pad}· {call.action}: {call.summary}", _DIM))
        lines.extend(f"{pad}{self._paint(_NOTE_MARKER, _CYAN)} {self._paint(note, _CYAN)}" for note in result.notes)
        return lines

    def _diff_line(self, line: str, indent: int) -> str:
        pad = " " * indent
        if line.startswith(("---", "+++")):
            return f"{pad}{self._paint(line, _BOLD)}"
        if line.startswith("@@"):
            return f"{pad}{self._paint(line, _CYAN)}"
        if line.startswith("+"):
            return f"{pad}{self._paint(line, _GREEN)}"
        if line.startswith("-"):
            return f"{pad}{self._paint(line, _RED)}"
        return f"{pad}{line}"

    def failure(self, result: TaskResult) -> None:
        """Print failure details for a task."""
        error = result.error
        if error is None:
            return
        message = result.detail or getattr(error, "message", str(error))
        self.write(self._paint(f"error: {message}", _BOLD_RED))

        command = getattr(error, "command", None)
        if command:
            self.write(self._paint(f"command: {' '.join(command)}", _DIM))

        stdout = getattr(error, "stdout", "") or ""
        if stdout.strip():
            self._failure_output_block("stdout", stdout)

        stderr = getattr(error, "stderr", "") or ""
        if stderr.strip():
            self._failure_output_block("stderr", stderr)

    def _failure_output_block(self, label: str, text: str) -> None:
        """Print a trimmed captured-output block."""
        self.write(f"{label}:")
        lines = text.rstrip().splitlines()
        if not self.verbose and len(lines) > MAX_FAILURE_LINES:
            omitted = len(lines) - MAX_FAILURE_LINES
            lines = lines[-MAX_FAILURE_LINES:]
            self.write(self._paint(f"  … ({omitted} earlier lines omitted)", _DIM))
        for line in lines:
            self.write(f"  {line}")

    def summary(self, counts: Mapping[Status, int], duration: float) -> None:
        """Print the run summary with per-status colors."""
        changed = counts.get(Status.CHANGED, 0)
        ok = counts.get(Status.OK, 0)
        skipped = counts.get(Status.SKIPPED, 0)
        failed = counts.get(Status.FAILED, 0)
        not_run = counts.get(Status.NOT_RUN, 0)

        parts: list[str] = []
        if changed:
            changed_label = f"{changed} would change" if self.dry_run else f"{changed} changed"
            parts.append(self._paint(changed_label, _MAGENTA if self.dry_run else _YELLOW))
        parts.append(self._paint(f"{ok} ok", _GREEN))
        if skipped:
            parts.append(self._paint(f"{skipped} skipped", _CYAN))
        if failed:
            parts.append(self._paint(f"{failed} failed", _BOLD_RED))
        if not_run:
            parts.append(f"{not_run} not run")
        if not (changed or failed or not_run):
            parts.append("nothing changed")

        label = self._paint("Summary:", _BOLD)
        self.write()
        self.write(f"{label} {', '.join(parts)} — {duration:.1f} s")

    def aborted(self, task_name: str, not_run: int) -> None:
        """Print the abort message after a failed task."""
        self.write(self._paint(f'Aborted after failure in "{task_name}".{_not_run_suffix(not_run)}', _RED))

    def interrupted(self, not_run: int) -> None:
        """Print the interrupted-run message."""
        self.write(self._paint(f"Interrupted.{_not_run_suffix(not_run)}", _RED))

    def command(
        self, argv: list[str] | tuple[str, ...], duration: float, returncode: int, stdout: str = "", stderr: str = ""
    ) -> None:
        """Print a command and its captured output when verbose."""
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
