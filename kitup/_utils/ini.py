"""Pure INI-style line editing helpers shared by task modules.

All functions operate on line lists produced by ``str.splitlines(keepends=True)``
and never touch the filesystem or the task context. Option values are either a
single line or, for appendable options, several lines rendered as indented
continuation lines (standard INI multi-line syntax).
"""

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal, NamedTuple

from ..errors import TaskFailed
from ..validation import require_collection, require_encodable, require_regex, require_text


class OptionEntry(NamedTuple):
    """One active option entry: ``line_index``, ``name``, ``value_lines``, and ``end``."""

    line_index: int
    name: str
    value_lines: list[str]
    end: int


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
    headers, and lines without a delimiter. Both ``=`` and ``:`` are
    delimiters. The value is everything after the first
    delimiter, stripped; inline comments stay part of the value.
    """
    text = _line_body(line)
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
    """Classify ``lines`` into section headers and option entries.

    Return ``(headers, entries)``: ``headers`` holds the indices of section
    header lines and each entry is an :class:`OptionEntry` with ``line_index`` the
    option line's position and ``end`` just past its continuation lines. A
    line indented deeper than the preceding option or header line continues
    that entry's value (so an indented ``[::1]`` item is a value, not a
    header); otherwise a bracket line starts a section and any other line
    with a separator starts a new option, however deeply the key itself is
    indented. Blank and comment lines never start an entry and do not end
    one either.
    """
    headers: list[int] = []
    entries: list[OptionEntry] = []
    current: OptionEntry | None = None
    indent_level = 0
    for index, line in enumerate(lines):
        body = _line_body(line)
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
    """Return the active option entries in ``lines[start:end]``.

    Each entry's ``line_index`` is the option line's position in ``lines``,
    ``value_lines`` is the option's value split into stripped lines (the text
    after the delimiter on the option line, plus any deeper-indented
    continuation lines), and ``end`` is the index just past the entry. Blank
    lines, comments, and section headers never start an entry.
    """
    _, entries = _scan(lines)
    return [entry for entry in entries if start <= entry.line_index < end]


def section_re(section: str) -> re.Pattern[str]:
    """Return the regex that matches the header line of ``section``."""
    return re.compile(r"^\s*\[\s*" + re.escape(section) + r"\s*\]")


def option_re(option: str) -> re.Pattern[str]:
    """Return the regex that matches active option lines for ``option``."""
    return re.compile(r"^\s*" + re.escape(option) + r"\s*[=:]")


def canonical_option_re(option: str) -> re.Pattern[str]:
    """Return a regex matching ``option`` lines in any normalized form.

    pip reads config keys case-insensitively, treats runs of ``-``, ``_``, and
    ``.`` within a name as equivalent, and accepts both ``=`` and ``:`` as
    delimiters (standard INI semantics plus pip's own key normalization).
    """
    parts = option.split("-")
    body = r"[-_.]+".join(re.escape(part) for part in parts)
    return re.compile(r"^\s*" + body + r"\s*[=:]", re.IGNORECASE)


def render_option(option: str, value: str | Sequence[str]) -> str:
    """Return the canonical ``option = value`` line(s), newline included.

    A single-line value renders as ``option = value``. A multi-line value
    (appendable options such as pip's ``find-links``) renders as an
    ``option =`` header followed by one indented continuation line per item,
    matching standard INI multi-line syntax.
    """
    value_lines = [value] if isinstance(value, str) else list(value)
    if len(value_lines) == 1:
        return f"{option} = {value_lines[0]}\n"
    return f"{option} =\n" + "".join(f"    {line}\n" for line in value_lines)


def section_spans(lines: list[str], section: str | None) -> list[tuple[int, int]]:
    """Return the body spans ``(start, end)`` of all sections named ``section``.

    A span ends at the next section header or at the end of the file. With
    ``section=None`` the single span is the preamble before the first header.
    Header detection rule: a bracket line indented deeper than the preceding
    option line is a continuation value, not a header.
    """
    headers, _ = _scan(lines)
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
    option entry; a multi-line value is matched as its lines joined with
    newlines.
    """
    parsed = [(entry.name, "\n".join(entry.value_lines)) for entry in option_entries(lines, start, end)]
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
    value: str | Sequence[str],
    match: Mapping[str, str] | None,
    option_pattern: re.Pattern[str] | None = None,
) -> tuple[list[str], _SetAction]:
    """Set ``option`` to ``value`` in the selected section of ``lines``.

    ``value`` is a single line or, for appendable options, a sequence of lines
    rendered as indented continuation lines (see :func:`render_option`).

    The selected section is the first span whose body satisfies ``match``, or
    the first same-named span when ``match`` is ``None``. An existing option
    entry whose value lines already equal ``value`` is kept as-is; otherwise
    the first matching entry is replaced with the canonical rendered block
    and duplicate matching entries (including their continuation lines) are
    dropped. A missing option is inserted after the last non-blank,
    non-comment line of the section, and a missing section is appended at the
    end of the file.

    When ``match`` is given and no span matches, the new section is appended
    only if the new rendered option block itself satisfies ``match`` —
    otherwise the section could not be found on the next run and would be
    duplicated every time, so ``TaskFailed`` is raised instead.

    Returns ``(new_lines, action)`` where ``action`` is ``"added"`` when the
    pair was newly inserted (including a newly appended section),
    ``"replaced"`` when existing lines were rewritten, and ``"unchanged"``
    when there was nothing to do; in the last case the input list is
    returned as-is.
    """
    value_lines = [value.strip()] if isinstance(value, str) else [item.strip() for item in value]
    spans = section_spans(lines, section)
    selected = _select_span(lines, spans, match)
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
    entries = [
        entry for entry in option_entries(lines, start, end) if pattern.match(lines[entry.line_index]) is not None
    ]
    if entries:
        first = entries[0]
        if first.value_lines == value_lines and len(entries) == 1:
            return lines, "unchanged"
        drop: set[int] = set()
        for entry in entries:
            drop.update(range(entry.line_index, entry.end))
        block = render_option(option, value_lines).splitlines(keepends=True)
        new_lines = [line for index, line in enumerate(lines) if index not in drop]
        # every dropped index is >= first.line_index, so the block lands exactly there
        new_lines[first.line_index : first.line_index] = block
        return new_lines, "replaced"
    insert_at = start
    for index in range(start, end):
        body = _line_body(lines[index]).strip()
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
    """Return ``lines`` without the active ``option`` entries in the selected section.

    Continuation lines of a removed option are removed together with its
    option line. The input list is returned unchanged when there is nothing
    to remove.
    """
    spans = section_spans(lines, section)
    selected = _select_span(lines, spans, match)
    if selected is None:
        return lines
    start, end = selected
    pattern = option_pattern if option_pattern is not None else option_re(option)
    drop: set[int] = set()
    for entry in option_entries(lines, start, end):
        if pattern.match(lines[entry.line_index]) is not None:
            drop.update(range(entry.line_index, entry.end))
    if not drop:
        return lines
    return [line for index, line in enumerate(lines) if index not in drop]


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


def require_ini_value_list(values: Iterable[str]) -> list[str]:
    """Validate that ``values`` is an ordered sequence and return it as a list.

    Only sequences (list, tuple) are accepted: sets, dicts, generators, and
    other unordered iterables are rejected because their iteration order can
    differ between runs, which would make writes non-idempotent.
    """
    if isinstance(values, str):
        raise TaskFailed("ini value list must not be a single string")
    if not isinstance(values, Sequence):
        raise TaskFailed("ini value list must be a sequence (list or tuple), not an unordered iterable")
    return list(values)


def require_ini_values(values: Iterable[str]) -> list[str]:
    """Validate a multi-line (appendable) option value and return it as a list.

    Each item becomes one continuation line when rendered, so items must be
    non-empty, UTF-8-encodable, single-line strings.
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
