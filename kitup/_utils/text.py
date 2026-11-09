"""Small text helpers shared by line-based file editors."""

from typing import Final

# Every character str.splitlines() treats as a line boundary: written tokens must
# not contain them, or the next read would split the line we just wrote.
LINE_BREAKS: Final = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def line_body(line: str) -> str:
    """Return ``line`` without its line ending."""
    return line.removesuffix("\n").removesuffix("\r")
