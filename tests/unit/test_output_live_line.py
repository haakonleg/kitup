import io
import threading

from kitup.output.animation import Animation
from kitup.output.live_line import LiveLine


def test_live_line_uses_the_injected_clock_for_the_initial_delay():
    ticks = iter(range(0, 1_000_000, 100))
    drawn = threading.Event()
    stream = io.StringIO()

    def render(elapsed, tick):
        drawn.set()
        return "frame"

    line = LiveLine(stream, clock=lambda: float(next(ticks)))
    line.start(Animation(render=render, delay=10.0, interval=0.01))
    try:
        assert drawn.wait(timeout=2)
    finally:
        line.stop()
