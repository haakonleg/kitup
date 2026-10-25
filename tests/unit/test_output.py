from __future__ import annotations

from io import StringIO

from kitup.output.animation import Animation
from kitup.output.live_line import LiveLine
from kitup.output.output import Output
from kitup.output.terminal import clear_lines, terminal_columns
from kitup.output.timer import Stopwatch


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTTY(StringIO):
    def isatty(self) -> bool:
        return True


def test_stopwatch_excludes_paused_time():
    clock = FakeClock()
    timer = Stopwatch(clock)

    clock.advance(1)
    assert timer.elapsed() == 1.0

    timer.pause()
    clock.advance(5)
    assert timer.elapsed() == 1.0

    timer.resume()
    clock.advance(2)
    assert timer.elapsed() == 3.0


def test_output_elapsed_since_excludes_interactive_time():
    clock = FakeClock()
    output = Output(StringIO(), no_color=True, clock=clock)
    started = clock()

    clock.advance(1)
    with output.interactive():
        clock.advance(5)
    clock.advance(2)

    assert output.elapsed_since(started) == 3.0


def test_terminal_columns_falls_back_without_a_terminal_size():
    assert terminal_columns(FakeTTY()) == 80


def test_clear_lines_erases_the_requested_lines():
    stream = FakeTTY()

    clear_lines(stream, 2)

    assert stream.getvalue() == "\033[2A\r\033[J"


def test_clear_lines_is_a_noop_without_a_tty():
    stream = StringIO()

    clear_lines(stream, 2)

    assert stream.getvalue() == ""


def test_clear_input_erases_prompt_and_echoed_value():
    stream = FakeTTY()
    output = Output(stream, no_color=True)

    output.clear_input("Value: ", "abc")

    assert stream.getvalue() == "\033[1A\r\033[J"


def test_clear_input_counts_wrapped_lines():
    stream = FakeTTY()
    output = Output(stream, no_color=True)

    output.clear_input("x" * 100, "abc")

    assert stream.getvalue() == "\033[2A\r\033[J"


def test_clear_input_is_a_noop_without_a_tty():
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.clear_input("Value: ", "abc")

    assert stream.getvalue() == ""


def test_live_line_elapsed_excludes_suspended_time():
    clock = FakeClock()
    live = LiveLine(StringIO(), clock=clock)
    live.start(Animation(lambda elapsed, tick: f"{elapsed:.1f}", 3600, 3600))

    clock.advance(1)
    assert live.elapsed == 1.0

    live.suspend()
    clock.advance(5)
    assert live.elapsed == 1.0

    live.resume()
    clock.advance(2)
    assert live.elapsed == 3.0

    live.stop()
