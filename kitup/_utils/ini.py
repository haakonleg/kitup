"""Pure INI-style line editing helpers shared by task modules.

Operate on ``str.splitlines(keepends=True)`` output; multi-line option values
use indented continuation lines. No filesystem access.
"""

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal, NamedTuple

from ..errors import TaskFailed
from .text import LINE_BREAKS, line_body
from .validation import require_collection, require_encodable, require_regex, require_text


class OptionEntry(NamedTuple):
    """One active option entry: ``line_index``, ``name``, ``value_lines``, and ``end``."""

    line_index: int
    name: str
    value_lines: list[str]
    end: int


# A header line needs a closing bracket on the same line; section_re() agrees.
_HEADER_RE: Final = re.compile(r"^\s*\[[^\]\n]*\]")
_SECTION_FORBIDDEN: Final = "[]" + LINE_BREAKS + "\0"
_OPTION_FORBIDDEN: Final = "=[]" + LINE_BREAKS + "\0"
_VALUE_FORBIDDEN: Final = LINE_BREAKS + "\0"


def parse_option(line: str) -> tuple[str, str] | None:
    """Parse an active option line into a stripped ``(name, value)`` pair.

    Return ``None`` for blanks, comments, headers, and lines without a
    delimiter. ``=`` and ``:`` both delimit; inline comments stay in the value.
    """
    text = line_body(line)
    stripped = text.strip()
    if not stripped or stripped.startswith(("#", ";", "[")):
        return None
    positions = [index for index in (text.find("="), text.find(":")) if index != -1]
    if not positions:
        return None
    separator = min(positions)
    name, value = text[:separator], text[separator + 1 :]
    if not name.strip():
        return None
    return name.strip(), value.strip()


def _scan(lines: list[str]) -> tuple[list[int], list[OptionEntry]]:
    """Classify ``lines`` into section header indices and option entries.

    Return ``(headers, entries)``. A line indented deeper than the preceding
    entry continues its value, so an indented ``[::1]`` item is not a header.
    """
    headers: list[int] = []
    entries: list[OptionEntry] = []
    current: OptionEntry | None = None
    indent_level = 0
    for index, line in enumerate(lines):
        body = line_body(line)
        stripped = body.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        cur_indent = len(body) - len(body.lstrip())
        if current is not None and cur_indent > indent_level:
            current = OptionEntry(current.line_index, current.name, [*current.value_lines, stripped], index + 1)
            entries[-1] = current
            continue
        indent_level = cur_indent
        current = None
        if _HEADER_RE.match(stripped) is not None:
            headers.append(index)
            continue
        parsed = parse_option(line)
        if parsed is not None:
            current = OptionEntry(index, parsed[0], [parsed[1]], index + 1)
            entries.append(current)
    result: list[OptionEntry] = []
    for entry in entries:
        # An empty text after '=' on the option line only introduces the
        # continuation lines of an appendable option (e.g. pip's find-links).
        value_lines = (
            entry.value_lines[1:] if len(entry.value_lines) > 1 and entry.value_lines[0] == "" else entry.value_lines
        )
        result.append(OptionEntry(entry.line_index, entry.name, value_lines, entry.end))
    return headers, result


def option_entries(lines: list[str], start: int, end: int) -> list[OptionEntry]:
    """Return the option entries whose option line falls in ``lines[start:end]``."""
    _, entries = _scan(lines)
    return _entries_in(entries, start, end)


def _entries_in(entries: list[OptionEntry], start: int, end: int) -> list[OptionEntry]:
    """Return the entries whose option line falls in ``[start, end)``."""
    return [entry for entry in entries if start <= entry.line_index < end]


def section_re(section: str) -> re.Pattern[str]:
    """Return the regex that matches the header line of ``section``."""
    return re.compile(r"^\s*\[\s*" + re.escape(section) + r"\s*\]")


def option_re(option: str) -> re.Pattern[str]:
    """Return the regex that matches active option lines for ``option``."""
    return re.compile(r"^\s*" + re.escape(option) + r"\s*[=:]")


def canonical_option_re(option: str) -> re.Pattern[str]:
    """Return a regex matching ``option`` in pip's normalized forms.

    Runs of ``-``, ``_``, and ``.`` are equivalent, matching is
    case-insensitive, and both ``=`` and ``:`` delimit.
    """
    parts = option.split("-")
    body = r"[-_.]+".join(re.escape(part) for part in parts)
    return re.compile(r"^\s*" + body + r"\s*[=:]", re.IGNORECASE)


def render_option(option: str, value: str | Sequence[str]) -> str:
    """Render ``option = value`` line(s), newline included.

    Multi-line values render as an ``option =`` header plus indented lines.
    """
    value_lines = [value] if isinstance(value, str) else list(value)
    if len(value_lines) == 1:
        return f"{option} = {value_lines[0]}\n"
    return f"{option} =\n" + "".join(f"    {line}\n" for line in value_lines)


def section_spans(lines: list[str], section: str | None) -> list[tuple[int, int]]:
    """Return the body spans ``(start, end)`` of sections named ``section``.

    A span ends at the next header or EOF; ``section=None`` selects the preamble.
    """
    headers, _ = _scan(lines)
    return _spans_for(headers, lines, section)


def _spans_for(headers: list[int], lines: list[str], section: str | None) -> list[tuple[int, int]]:
    """Return section spans from an existing header scan."""
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
    """Return whether every ``match`` pair matches an entry in the span.

    Keys and values are regexes; multi-line values are joined with newlines.
    """
    _, entries = _scan(lines)
    return _match_entries(entries, start, end, _compile_match(match))


def _compile_match(match: Mapping[str, str]) -> list[tuple[re.Pattern[str], re.Pattern[str]]]:
    """Compile every ``match`` pair once for reuse across spans."""
    return [(re.compile(key), re.compile(value)) for key, value in match.items()]


def _match_entries(
    entries: list[OptionEntry],
    start: int,
    end: int,
    patterns: list[tuple[re.Pattern[str], re.Pattern[str]]],
) -> bool:
    """Return whether compiled ``patterns`` match the entries in ``[start, end)``."""
    parsed = [(entry.name, "\n".join(entry.value_lines)) for entry in _entries_in(entries, start, end)]
    return all(
        any(key.search(name) is not None and value.search(entry_value) is not None for name, entry_value in parsed)
        for key, value in patterns
    )


type _SetAction = Literal["added", "replaced", "unchanged"]


def set_option(
    lines: list[str],
    section: str | None,
    option: str,
    value: str | Sequence[str],
    match: Mapping[str, str] | None,
    option_pattern: re.Pattern[str] | None = None,
) -> tuple[list[str], _SetAction]:
    """Set ``option`` to ``value`` in the selected section of ``lines``.

    Select the first span satisfying ``match``, or the first same-named span.
    Keep an equal existing entry; otherwise replace the first match and drop
    duplicates. Insert a missing option at the end of the section, or append a
    new section. With ``match``, a new section must satisfy the match or
    ``TaskFailed`` is raised. Return ``(new_lines, action)``.
    """
    value_lines = [value.strip()] if isinstance(value, str) else [item.strip() for item in value]
    headers, entries = _scan(lines)
    spans = _spans_for(headers, lines, section)
    selected = _select_span(entries, spans, match)
    if selected is None:
        rendered = render_option(option, value_lines)
        rendered_lines = rendered.splitlines(keepends=True)
        if match is not None and not span_matches(rendered_lines, 0, len(rendered_lines), match):
            where = f"[{section}]" if section is not None else "the preamble"
            raise TaskFailed(
                f"no {where} section matches {dict(match)}; appending a new section holding only "
                f"{option} = {value_lines} would not satisfy the match and would duplicate on every run; "
                "set the matched option itself (use re.escape for literal match values) "
                "or create the section without match"
            )
        new_lines = list(lines)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        if section is not None:
            new_lines.append(f"[{section}]\n")
        new_lines.extend(rendered.splitlines(keepends=True))
        return new_lines, "added"
    start, end = selected
    pattern = option_pattern if option_pattern is not None else option_re(option)
    matches = [
        entry for entry in _entries_in(entries, start, end) if pattern.match(lines[entry.line_index]) is not None
    ]
    if matches:
        first = matches[0]
        if first.value_lines == value_lines and len(matches) == 1:
            return lines, "unchanged"
        drop: set[int] = set()
        for entry in matches:
            drop.update(range(entry.line_index, entry.end))
        block = render_option(option, value_lines).splitlines(keepends=True)
        new_lines = [line for index, line in enumerate(lines) if index not in drop]
        # every dropped index is >= first.line_index, so the block lands exactly there
        new_lines[first.line_index : first.line_index] = block
        return new_lines, "replaced"
    insert_at = start
    for index in range(start, end):
        body = line_body(lines[index]).strip()
        if body and not body.startswith(("#", ";")):
            insert_at = index + 1
    new_lines = list(lines)
    if insert_at > 0 and not new_lines[insert_at - 1].endswith("\n"):
        new_lines[insert_at - 1] += "\n"
    new_lines[insert_at:insert_at] = render_option(option, value_lines).splitlines(keepends=True)
    return new_lines, "added"


def remove_option(
    lines: list[str],
    section: str | None,
    option: str,
    match: Mapping[str, str] | None,
    option_pattern: re.Pattern[str] | None = None,
) -> list[str]:
    """Return ``lines`` without the selected ``option`` entries and continuations."""
    headers, entries = _scan(lines)
    spans = _spans_for(headers, lines, section)
    selected = _select_span(entries, spans, match)
    if selected is None:
        return lines
    start, end = selected
    pattern = option_pattern if option_pattern is not None else option_re(option)
    drop: set[int] = set()
    for entry in _entries_in(entries, start, end):
        if pattern.match(lines[entry.line_index]) is not None:
            drop.update(range(entry.line_index, entry.end))
    if not drop:
        return lines
    return [line for index, line in enumerate(lines) if index not in drop]


def remove_section(lines: list[str], section: str, match: Mapping[str, str] | None) -> list[str]:
    """Return ``lines`` without the selected section, header included."""
    headers, entries = _scan(lines)
    spans = _spans_for(headers, lines, section)
    selected = _select_span(entries, spans, match)
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


def require_ini_value_list(values: Iterable[str]) -> list[str]:
    """Validate that ``values`` is an ordered sequence and return a list.

    Reject sets, dicts, generators, and other unordered iterables.
    """
    if isinstance(values, str):
        raise TaskFailed("ini value list must not be a single string")
    if not isinstance(values, Sequence):
        raise TaskFailed("ini value list must be a sequence (list or tuple), not an unordered iterable")
    return list(values)


def require_ini_values(values: Iterable[str]) -> list[str]:
    """Validate a multi-line option value and return its items.

    Each item becomes one continuation line, so items must be non-empty,
    UTF-8-encodable, single-line strings.
    """
    items = require_ini_value_list(values)
    require_collection(items, "ini value list")
    stripped_items: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise TaskFailed("ini value list items must be strings")
        require_text(item, "ini value list item")
        require_encodable(item, "ini value list item")
        if any(char in item for char in _VALUE_FORBIDDEN):
            raise TaskFailed("ini value list items must not contain line breaks or null bytes")
        stripped = item.strip()
        if stripped.startswith(("#", ";")):
            raise TaskFailed("ini value list items must not start with a comment character")
        stripped_items.append(stripped)
    return stripped_items


def require_ini_match(match: Mapping[str, str]) -> dict[str, str]:
    """Validate a regex ``match`` selector and return it as a plain dict."""
    if not isinstance(match, Mapping):
        raise TaskFailed("ini match must be a mapping of option names to values")
    require_collection(match, "ini match")
    for key, value in match.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TaskFailed("ini match keys and values must be strings")
        require_regex(key, "ini match key")
        require_regex(value, "ini match value")
    return dict(match)


def _select_span(
    entries: list[OptionEntry],
    spans: list[tuple[int, int]],
    match: Mapping[str, str] | None,
) -> tuple[int, int] | None:
    """Return the first span selected by ``match``, or the first span overall."""
    if match is None:
        return spans[0] if spans else None
    patterns = _compile_match(match)
    return next((span for span in spans if _match_entries(entries, span[0], span[1], patterns)), None)
