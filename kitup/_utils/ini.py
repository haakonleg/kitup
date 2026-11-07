"""Pure INI-style line editing helpers shared by task modules.

All functions operate on line lists produced by ``str.splitlines(keepends=True)``
and never touch the filesystem or the task context.
"""

import re
from bisect import bisect_right
from collections.abc import Mapping
from typing import Final, Literal

from ..errors import TaskFailed
from ..validation import require_collection, require_encodable, require_regex, require_text

# A header line needs a closing bracket on the same line; section_re() agrees.
_HEADER_RE: Final = re.compile(r"^\s*\[[^\]\n]*\]")
# Every character str.splitlines() treats as a line boundary: written tokens must
# not contain them, or the next read would split the line we just wrote.
_LINE_BREAKS: Final = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
_SECTION_FORBIDDEN: Final = "[]" + _LINE_BREAKS + "\0"
_OPTION_FORBIDDEN: Final = "=[]" + _LINE_BREAKS + "\0"
_VALUE_FORBIDDEN: Final = _LINE_BREAKS + "\0"


def _line_body(line: str) -> str:
    """Return ``line`` without its line ending."""
    return line.removesuffix("\n").removesuffix("\r")


def parse_option(line: str) -> tuple[str, str] | None:
    """Parse an active option line into a stripped ``(name, value)`` pair.

    Return ``None`` for blank lines, comments (``#`` or ``;``), section
    headers, and lines without an ``=`` separator. The value is everything
    after the first ``=``, stripped; inline comments stay part of the value.
    """
    text = _line_body(line)
    stripped = text.strip()
    if not stripped or stripped.startswith(("#", ";", "[")):
        return None
    name, separator, value = text.partition("=")
    if not separator or not name.strip():
        return None
    return name.strip(), value.strip()


def section_re(section: str) -> re.Pattern[str]:
    """Return the regex that matches the header line of ``section``."""
    return re.compile(r"^\s*\[\s*" + re.escape(section) + r"\s*\]")


def option_re(option: str) -> re.Pattern[str]:
    """Return the regex that matches active option lines for ``option``."""
    return re.compile(r"^\s*" + re.escape(option) + r"\s*=")


def render_option(option: str, value: str) -> str:
    """Return the canonical ``option = value`` line, newline included."""
    return f"{option} = {value}\n"


def section_spans(lines: list[str], section: str | None) -> list[tuple[int, int]]:
    """Return the body spans ``(start, end)`` of all sections named ``section``.

    A span ends at the next section header or at the end of the file. With
    ``section=None`` the single span is the preamble before the first header.
    """
    headers = [index for index, line in enumerate(lines) if _HEADER_RE.match(line) is not None]
    if section is None:
        return [(0, headers[0] if headers else len(lines))]
    header = section_re(section)
    spans: list[tuple[int, int]] = []
    for index in headers:
        if header.match(lines[index]) is None:
            continue
        pos = bisect_right(headers, index)
        end = headers[pos] if pos < len(headers) else len(lines)
        spans.append((index + 1, end))
    return spans


def span_matches(lines: list[str], start: int, end: int, match: Mapping[str, str]) -> bool:
    """Return whether every ``match`` pair is satisfied within the span.

    Both the key and the value of a pair are regular expressions applied with
    ``re.search`` to the stripped option name and value of the same active
    option line.
    """
    parsed = [entry for line in lines[start:end] if (entry := parse_option(line)) is not None]
    for key, value in match.items():
        key_pattern = re.compile(key)
        value_pattern = re.compile(value)
        if not any(
            key_pattern.search(name) is not None and value_pattern.search(entry_value) is not None
            for name, entry_value in parsed
        ):
            return False
    return True


type _SetAction = Literal["added", "replaced", "unchanged"]


def set_option(
    lines: list[str],
    section: str | None,
    option: str,
    value: str,
    match: Mapping[str, str] | None,
) -> tuple[list[str], _SetAction]:
    """Set ``option`` to ``value`` in the selected section of ``lines``.

    The selected section is the first span whose body satisfies ``match``, or
    the first same-named span when ``match`` is ``None``. An existing option
    line whose stripped value already equals ``value`` is kept as-is;
    otherwise the first matching line is replaced with the canonical
    ``option = value`` line and duplicate matching lines are dropped. A
    missing option is inserted after the last non-blank, non-comment line of
    the section, and a missing section is appended at the end of the file.

    When ``match`` is given and no span matches, the new section is appended
    only if the new ``option = value`` line itself satisfies ``match`` —
    otherwise the section could not be found on the next run and would be
    duplicated every time, so ``TaskFailed`` is raised instead.

    Returns ``(new_lines, action)`` where ``action`` is ``"added"`` when the
    pair was newly inserted (including a newly appended section),
    ``"replaced"`` when existing lines were rewritten, and ``"unchanged"``
    when there was nothing to do; in the last case the input list is
    returned as-is.
    """
    value = value.strip()
    spans = section_spans(lines, section)
    selected = _select_span(lines, spans, match)
    if selected is None:
        rendered = render_option(option, value)
        if match is not None and not span_matches([rendered], 0, 1, match):
            where = f"[{section}]" if section is not None else "the preamble"
            raise TaskFailed(
                f"no {where} section matches {dict(match)}; appending a new section holding only "
                f"{option} = {value} would not satisfy the match and would duplicate on every run; "
                "set the matched option itself (use re.escape for literal match values) "
                "or create the section without match"
            )
        new_lines = list(lines)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        if section is not None:
            new_lines.append(f"[{section}]\n")
        new_lines.append(rendered)
        return new_lines, "added"
    start, end = selected
    pattern = option_re(option)
    indices = [index for index in range(start, end) if pattern.match(lines[index]) is not None]
    if indices:
        first = indices[0]
        parsed = parse_option(lines[first])
        if parsed is not None and parsed[1] == value and len(indices) == 1:
            return lines, "unchanged"
        new_lines = list(lines)
        new_lines[first] = render_option(option, value)
        for index in reversed(indices[1:]):
            del new_lines[index]
        return new_lines, "replaced"
    insert_at = start
    for index in range(start, end):
        body = _line_body(lines[index]).strip()
        if body and not body.startswith(("#", ";")):
            insert_at = index + 1
    new_lines = list(lines)
    if insert_at > 0 and not new_lines[insert_at - 1].endswith("\n"):
        new_lines[insert_at - 1] += "\n"
    new_lines.insert(insert_at, render_option(option, value))
    return new_lines, "added"


def remove_option(
    lines: list[str],
    section: str | None,
    option: str,
    match: Mapping[str, str] | None,
) -> list[str]:
    """Return ``lines`` without the active ``option`` lines in the selected section.

    The input list is returned unchanged when there is nothing to remove.
    """
    spans = section_spans(lines, section)
    selected = _select_span(lines, spans, match)
    if selected is None:
        return lines
    start, end = selected
    pattern = option_re(option)
    return [line for index, line in enumerate(lines) if not (start <= index < end and pattern.match(line) is not None)]


def remove_section(lines: list[str], section: str, match: Mapping[str, str] | None) -> list[str]:
    """Return ``lines`` without the selected section, header line included.

    The input list is returned unchanged when there is nothing to remove.
    """
    spans = section_spans(lines, section)
    selected = _select_span(lines, spans, match)
    if selected is None:
        return lines
    start, end = selected
    return lines[: start - 1] + lines[end:]


def require_ini_section(section: str) -> str:
    """Validate a section name and return it stripped."""
    if not isinstance(section, str):
        raise TaskFailed("ini section must be a string")
    require_text(section, "ini section")
    require_encodable(section, "ini section")
    if any(char in section for char in _SECTION_FORBIDDEN):
        raise TaskFailed("ini section must not contain brackets, null bytes, or line breaks")
    return section.strip()


def require_ini_option(option: str) -> str:
    """Validate an option name and return it stripped."""
    if not isinstance(option, str):
        raise TaskFailed("ini option must be a string")
    require_text(option, "ini option")
    require_encodable(option, "ini option")
    if any(char in option for char in _OPTION_FORBIDDEN):
        raise TaskFailed("ini option must not contain '=', brackets, null bytes, or line breaks")
    stripped = option.strip()
    if stripped.startswith(("#", ";")):
        raise TaskFailed("ini option must not start with a comment character")
    return stripped


def require_ini_value(value: str) -> str:
    """Validate an option value and return it stripped."""
    if not isinstance(value, str):
        raise TaskFailed("ini value must be a string")
    require_text(value, "ini value")
    require_encodable(value, "ini value")
    if any(char in value for char in _VALUE_FORBIDDEN):
        raise TaskFailed("ini value must not contain line breaks or null bytes")
    return value.strip()


def require_ini_match(match: Mapping[str, str]) -> dict[str, str]:
    """Validate a ``match`` selector and return it as a plain dict.

    Keys and values must be non-empty regular expression strings; invalid
    patterns raise ``TaskFailed``.
    """
    if not isinstance(match, Mapping):
        raise TaskFailed("ini match must be a mapping of option names to values")
    require_collection(match, "ini match")
    for key, value in match.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TaskFailed("ini match keys and values must be strings")
        require_regex(key, "ini match key")
        require_regex(value, "ini match value")
        try:
            re.compile(key)
            re.compile(value)
        except re.error as exc:
            raise TaskFailed(f"invalid ini match regex: {exc}") from exc
    return dict(match)


def _select_span(
    lines: list[str],
    spans: list[tuple[int, int]],
    match: Mapping[str, str] | None,
) -> tuple[int, int] | None:
    """Return the first span selected by ``match``, or the first span overall."""
    if match is None:
        return spans[0] if spans else None
    return next((span for span in spans if span_matches(lines, span[0], span[1], match)), None)
