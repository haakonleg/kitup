"""Animation definitions for the terminal output layer."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Animation:
    """A transient line renderer and its refresh timing."""

    render: Callable[[float, int], str]
    delay: float
    interval: float
