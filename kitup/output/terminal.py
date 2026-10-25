"""Terminal width and line-clearing helpers."""

from __future__ import annotations

import os
from typing import TextIO

DEFAULT_COLUMNS = 80
MIN_COLUMNS = 20


def terminal_columns(stream: TextIO) -> int:
    """Return a usable terminal width, falling back to 80."""
    try:
        columns = os.get_terminal_size(stream.fileno()).columns
    except OSError:
        columns = DEFAULT_COLUMNS
    return columns if columns >= MIN_COLUMNS else DEFAULT_COLUMNS


def clear_lines(stream: TextIO, count: int = 1) -> None:
    """Erase ``count`` lines above the cursor and clear to the screen end."""
    if count <= 0 or not stream.isatty():
        return
    stream.write(f"\033[{count}A\r\033[J")
    stream.flush()


def clear_input(stream: TextIO, prompt: str, value: str) -> None:
    """Erase an input prompt and its echoed value, including wrapped lines."""
    if not stream.isatty():
        return
    columns = terminal_columns(stream)
    lines = max(1, (len(prompt) + len(value) + columns - 1) // columns)
    clear_lines(stream, lines)
