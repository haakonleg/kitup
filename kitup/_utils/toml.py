"""Line-based TOML editing helpers shared by task modules.

The pure functions operate on line lists produced by :func:`split_lines` and never
touch the filesystem: a small scanner classifies table headers and key/value
entries (including multi-line arrays and strings), and the editors change one key
— or remove a whole table — while preserving unrelated lines and comments.
Dotted keys resolve against existing sub-table headers, and every edit is
re-parsed with ``tomllib`` before it is returned, so an edit can never produce
invalid TOML. Array-of-tables (``[[...]]``) cannot be edited line-based and are
rejected with a clear error. :func:`apply_toml_key` is the context-aware wrapper
for modules that edit TOML files, handling file creation, dry-run, and
privileges.
"""

import json
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, NamedTuple

from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult
from . import file_actions
from .text import LINE_BREAKS, line_body
from .validation import require_text

type TomlValue = str | bool | int | float | Iterable[str]
type _KeyAction = Literal["added", "replaced", "unchanged"]

_BARE_KEY_RE: Final = re.compile(r"[A-Za-z0-9_-]+")
_KEY_PART: Final = r'(?:[A-Za-z0-9_-]+|"(?:[^"\\\n]|\\.)*"|\'[^\'\n]*\')'
_KEY_LINE_RE: Final = re.compile(rf"^\s*(?P<key>{_KEY_PART}(?:\s*\.\s*{_KEY_PART})*)\s*=")
_TABLE_HEADER_RE: Final = re.compile(r"^\[(?P<name>[^\]\n]+?)\]\s*(?:#.*)?$")
_AOT_HEADER_RE: Final = re.compile(r"^\[\[(?P<name>[^\]\n]+?)\]\]\s*(?:#.*)?$")


class _Header(NamedTuple):
    """One table header line: ``line_index``, dotted table ``name``, and AoT flag."""

    line_index: int
    name: str
    is_array: bool


class _Entry(NamedTuple):
    """One key/value entry: first line, end (just past the last line), table, key, raw value."""

    line_index: int
    end: int
    table: str | None
    key: str
    value: str


class _ValueState(NamedTuple):
    """Scanner state while consuming a (possibly multi-line) value: bracket depth and quote."""

    depth: int
    quote: str | None


def split_lines(text: str) -> list[str]:
    r"""Split ``text`` into lines on ``\n`` only, keeping the line endings.

    Unlike ``str.splitlines``, Unicode line separators (U+2028, U+0085, ...)
    are not treated as breaks, so they can appear raw in TOML strings and
    comments without producing phantom entries.
    """
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def newline_of(lines: list[str]) -> str:
    r"""Return the dominant line ending of ``lines``: ``"\r\n"`` or ``"\n"``."""
    if lines and sum(line.endswith("\r\n") for line in lines) * 2 >= len(lines):
        return "\r\n"
    return "\n"


def toml_string(value: str) -> str:
    """Render a TOML-compatible basic string."""
    # json.dumps escapes the C0 controls but not DEL (U+007F), which TOML forbids raw
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_value(value: object) -> str:
    """Render a typed value as TOML value text.

    Strings render as basic strings (via :func:`toml_string`), booleans as
    ``true``/``false``, integers and floats in decimal form, and sequences of
    strings as a single-line array (``[]`` when empty). Anything else is
    rejected.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        require_text(value, "toml value")
        return toml_string(value)
    if isinstance(value, Sequence):
        items = list(value)
        for item in items:
            if not isinstance(item, str):
                raise TaskFailed("toml array items must be strings")
            require_text(item, "toml array item")
        return "[" + ", ".join(toml_string(item) for item in items) + "]"
    if isinstance(value, Mapping):
        raise TaskFailed(f"unsupported toml value type: {type(value).__name__}")
    if isinstance(value, Iterable):
        raise TaskFailed("toml array value must be a sequence (list or tuple), not an unordered iterable")
    raise TaskFailed(f"unsupported toml value type: {type(value).__name__}")


def require_toml_key(key: str, label: str = "toml key") -> str:
    """Validate a dotted bare TOML key (``a.b-c``) and return it normalized."""
    if not isinstance(key, str):
        raise TaskFailed(f"{label} must be a string")
    require_text(key, label)
    if any(char in key for char in LINE_BREAKS) or "\0" in key:
        raise TaskFailed(f"{label} must not contain line breaks or null bytes")
    parts: list[str] = []
    for part in key.split("."):
        stripped = part.strip()
        if _BARE_KEY_RE.fullmatch(stripped) is None:
            raise TaskFailed(f"{label} must use bare key parts (letters, digits, '_', '-'): {key!r}")
        parts.append(stripped)
    return ".".join(parts)


def array_items(lines: list[str], table: str | None, key: str) -> list[str] | None:
    """Return the string items of the array at ``table``/``key``, or ``None`` when absent.

    Raise ``TaskFailed`` when the entry exists but is not an array of strings.
    """
    _, entries, table, key = _scan_resolved(lines, table, key)
    entry = _find_entry(entries, table, key)
    if entry is None:
        return None
    return _parse_array(entry.value, key, table)


def set_key(lines: list[str], table: str | None, key: str, value_text: str) -> tuple[list[str], _KeyAction]:
    """Set ``key`` to the rendered ``value_text`` in ``table`` of ``lines``.

    An existing entry with an equal parsed value is kept as-is (``"unchanged"``);
    otherwise the first matching entry is replaced (duplicates are dropped) and a
    missing entry is inserted at the end of the table, creating the table — or the
    file — when needed. A dotted ``key`` resolves against sub-table headers and
    top-level dotted entries when present, so the edit never collides with an
    existing table form. Returns ``(new_lines, action)``.
    """
    headers, entries, table, key = _scan_resolved(lines, table, key)
    span = _table_span(lines, headers, table)
    matches = [entry for entry in entries if entry.table == table and entry.key == key]
    rendered = f"{key} = {value_text}{newline_of(lines)}"
    if matches:
        first = matches[0]
        if len(matches) == 1 and _value_equals(first, value_text):
            return lines, "unchanged"
        drop = _entry_indices(matches)
        new_lines = [line for index, line in enumerate(lines) if index not in drop]
        # every dropped index is >= first.line_index, so the block lands exactly there
        new_lines[first.line_index : first.line_index] = [rendered]
        _validate_edit(new_lines, table, key, _checked_value(value_text, key), present=True)
        return new_lines, "replaced"
    new_lines = _insert_block(lines, span, table, [rendered], newline_of(lines))
    _validate_edit(new_lines, table, key, _checked_value(value_text, key), present=True)
    return new_lines, "added"


def remove_key(lines: list[str], table: str | None, key: str) -> list[str]:
    """Return ``lines`` without the ``key`` entry in ``table``, unchanged when absent."""
    headers, entries, table, key = _scan_resolved(lines, table, key)
    _table_span(lines, headers, table)  # rejects array-of-tables targets
    drop = _entry_indices([entry for entry in entries if entry.table == table and entry.key == key])
    if not drop:
        return lines
    new_lines = [line for index, line in enumerate(lines) if index not in drop]
    _validate_edit(new_lines, table, key, None, present=False)
    return new_lines


def remove_table(lines: list[str], table: str) -> list[str]:
    """Return ``lines`` without the whole ``table``, unchanged when absent.

    Removes the ``[table]`` header and every line up to the next header —
    comments inside the span go with it — or, for a table declared only
    through top-level dotted keys (``tool.uv.x = ...``), those entries.
    Array-of-tables (``[[...]]``) targets are rejected.
    """
    headers, entries = _scan(lines)
    span = _table_span(lines, headers, table)  # rejects array-of-tables targets
    new_lines: list[str]
    if span is not None:
        header = next(item for item in headers if item.name == table and not item.is_array)
        start, end = header.line_index, span[1]
        new_lines = [line for index, line in enumerate(lines) if not start <= index < end]
    else:
        drop = _entry_indices(
            entry
            for entry in entries
            if entry.table is None and (entry.key == table or entry.key.startswith(table + "."))
        )
        if not drop:
            return lines
        new_lines = [line for index, line in enumerate(lines) if index not in drop]
    _validate_table_absent(new_lines, table)
    return new_lines


def set_array(lines: list[str], table: str | None, key: str, items: list[str]) -> tuple[list[str], _KeyAction]:
    """Set the string array at ``table``/``key`` to ``items`` (verbatim strings).

    The rendered entries use TOML basic strings. An existing entry whose parsed
    items already equal ``items`` is kept as-is; a modified entry keeps its
    original single- or multi-line style. Returns ``(new_lines, action)``.
    """
    headers, entries, table, key = _scan_resolved(lines, table, key)
    span = _table_span(lines, headers, table)
    entry = _find_entry(entries, table, key)
    rendered_items = [toml_string(item) for item in items]
    nl = newline_of(lines)
    if entry is not None:
        if _parse_array(entry.value, key, table) == items:
            return lines, "unchanged"
        block = _render_array_block(key, rendered_items, multiline=entry.end - entry.line_index > 1, nl=nl)
        new_lines = list(lines)
        new_lines[entry.line_index : entry.end] = block
        _validate_edit(new_lines, table, key, items, present=True)
        return new_lines, "replaced"
    block = _render_array_block(key, rendered_items, multiline=len(items) > 1, nl=nl)
    new_lines = _insert_block(lines, span, table, block, nl)
    _validate_edit(new_lines, table, key, items, present=True)
    return new_lines, "added"


def apply_toml_key(
    ctx: TaskContext,
    target: Path,
    *,
    table: str | None,
    key: str | None,
    value: TomlValue | None,
    action: str,
    sudo: bool,
    create: bool = True,
    rendered: str | None = None,
) -> CallResult:
    """Apply one TOML edit to ``target`` and return the resulting call.

    ``value`` is the typed value to write (rendered with :func:`toml_value`), or
    ``None`` to remove. With ``value=None`` and a ``key``, the key is removed;
    with ``value=None`` and no ``key``, the whole ``table`` is removed.
    ``rendered`` optionally passes the value already rendered by
    :func:`toml_value`, so callers that validate before touching the filesystem
    do not render twice. ``table`` is the dotted table path, or ``None`` for
    top-level keys. Sequence values are written with :func:`set_array`, keeping
    a multi-line array's layout. A missing file is created (parents ``0o755``,
    file ``0o644``) unless ``create=False``, which fails instead; an absent edit
    on a missing file is a no-op. The returned call is not recorded; the caller
    records it with ``ctx.changed``.
    """
    snapshot = file_actions.read_snapshot(ctx, target, sudo=sudo)
    exists = snapshot.info is not None
    if not create and not exists:
        raise TaskFailed(f"file does not exist and create=False: {target}")
    text = snapshot.text(target)
    lines = split_lines(text)
    where = f"[{table}] of {target}" if table is not None else str(target)
    stored: object = value
    if isinstance(value, Sequence) and not isinstance(value, str):
        stored = list(value)
    data: dict[str, object] = {"path": str(target), "table": table, "key": key, "value": stored}

    new_lines: list[str]
    toml_action: str
    if value is not None:
        if key is None:
            raise TaskFailed("setting a toml value requires a key")
        value_text = rendered if rendered is not None else toml_value(value)
        if isinstance(value, Sequence) and not isinstance(value, str):
            new_lines, toml_action = _set_array_value(lines, table, key, list(value), value_text)
        else:
            new_lines, toml_action = set_key(lines, table, key, value_text)
        summary = f"set {key} in {where}"
        unchanged_summary = f"ensure {key} is {value_text} in {where}"
    elif key is not None:
        new_lines = remove_key(lines, table, key)
        toml_action = "removed"
        summary = f"remove {key} from {where}"
        unchanged_summary = f"ensure {key} is absent from {where}"
    elif table is not None:
        new_lines = remove_table(lines, table)
        toml_action = "removed"
        summary = f"remove table [{table}] from {target}"
        unchanged_summary = f"ensure table [{table}] is absent from {target}"
    else:
        raise TaskFailed("toml edit requires a key or a table")

    if new_lines == lines:
        # the editor made no change; never rewrite just to add a trailing newline
        data["toml_action"] = "unchanged"
        return CallResult(action=action, changed=False, summary=unchanged_summary, **data)
    new_text = "".join(new_lines)
    if new_text and not new_text.endswith("\n"):
        new_text += newline_of(lines)
    data["toml_action"] = toml_action
    if not exists:
        file_actions.ensure_parent(ctx, target, sudo=sudo)
    return file_actions.apply_text(
        ctx,
        target,
        new_text,
        snapshot=snapshot,
        action=action,
        summary=summary,
        data=data,
        sudo=sudo,
    )


def _set_array_value(
    lines: list[str],
    table: str | None,
    key: str,
    items: list[str],
    value_text: str,
) -> tuple[list[str], _KeyAction]:
    """Set an array value, keeping an existing array entry's layout.

    A missing key is written single-line via :func:`set_key`; an existing
    array entry keeps its single- or multi-line style via :func:`set_array`.
    A non-array entry is simply replaced.
    """
    try:
        existing = array_items(lines, table, key)
    except TaskFailed:
        existing = None  # a non-array entry is replaced wholesale
    if existing is None:
        return set_key(lines, table, key, value_text)
    return set_array(lines, table, key, items)


def _quote_run(text: str, index: int) -> int:
    """Return the length of the run of ``text[index]`` starting at ``index``."""
    char = text[index]
    end = index + 1
    while end < len(text) and text[end] == char:
        end += 1
    return end - index


def _scan_chunk(text: str, state: _ValueState) -> _ValueState:
    """Consume one line of a value, tracking bracket depth, strings, and comments."""
    depth, quote = state
    index = 0
    while index < len(text):
        if quote is not None:
            if quote in ('"', '"""') and text[index] == "\\":
                index += 2
                continue
            if text.startswith(quote, index):
                run = _quote_run(text, index)
                if len(quote) == 3 and run < 3:
                    # one or two quote chars inside a triple-quoted string are content
                    index += run
                    continue
                # a run of >= 3 quotes closes a triple-quoted string; any extra
                # quotes in the run are content (e.g. """say "hi"""")
                index += run if len(quote) == 3 else 1
                quote = None
                continue
            index += 1
            continue
        if text.startswith('"""', index):
            quote = '"""'
            index += 3
            continue
        if text.startswith("'''", index):
            quote = "'''"
            index += 3
            continue
        char = text[index]
        if char == '"':
            quote = '"'
        elif char == "'":
            quote = "'"
        elif char == "#":
            break  # comment: the rest of the line is not part of the value
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        index += 1
    return _ValueState(depth, quote)


def _scan(lines: list[str]) -> tuple[list[_Header], list[_Entry]]:
    """Classify ``lines`` into table headers and key/value entries.

    Entry values may span several lines (multi-line arrays, inline tables,
    triple-quoted strings); the scanner consumes those continuation lines so a
    bracket inside a value is never mistaken for a table header.
    """
    headers: list[_Header] = []
    entries: list[_Entry] = []
    table: str | None = None
    index = 0
    while index < len(lines):
        body = line_body(lines[index])
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        aot = _AOT_HEADER_RE.match(stripped)
        if aot is not None:
            name = _join_dotted(aot.group("name"))
            headers.append(_Header(index, name, is_array=True))
            table = name
            index += 1
            continue
        header = _TABLE_HEADER_RE.match(stripped)
        if header is not None:
            name = _join_dotted(header.group("name"))
            headers.append(_Header(index, name, is_array=False))
            table = name
            index += 1
            continue
        key_match = _KEY_LINE_RE.match(body)
        if key_match is None:
            index += 1
            continue
        key = _join_dotted(key_match.group("key"))
        state = _ValueState(0, None)
        parts: list[str] = []
        start = index
        while index < len(lines):
            chunk = body[key_match.end() :] if index == start else line_body(lines[index])
            parts.append(chunk)
            state = _scan_chunk(chunk, state)
            index += 1
            if state.depth == 0 and state.quote is None:
                break
        entries.append(_Entry(start, index, table, key, "\n".join(parts)))
    return headers, entries


def _find_entry(entries: list[_Entry], table: str | None, key: str) -> _Entry | None:
    """Return the first entry for ``table``/``key``."""
    return next((entry for entry in entries if entry.table == table and entry.key == key), None)


def _scan_resolved(
    lines: list[str],
    table: str | None,
    key: str,
) -> tuple[list[_Header], list[_Entry], str | None, str]:
    """Scan ``lines`` and resolve a dotted ``key`` against existing sub-table and dotted-key forms."""
    headers, entries = _scan(lines)
    resolved_table, resolved_key = _resolve_target(headers, entries, table, key)
    return headers, entries, resolved_table, resolved_key


def _entry_indices(entries: Iterable[_Entry]) -> set[int]:
    """Return the line indices covered by the given entries (first line through end)."""
    return {index for entry in entries for index in range(entry.line_index, entry.end)}


def _table_span(lines: list[str], headers: list[_Header], table: str | None) -> tuple[int, int] | None:
    """Return the body span ``(start, end)`` of ``table``, or ``None`` when absent.

    ``table=None`` selects the top-level preamble before the first header. An
    array-of-tables header for ``table`` raises ``TaskFailed``.
    """
    if table is None:
        first = headers[0].line_index if headers else len(lines)
        return (0, first)
    header = None
    for candidate in headers:
        if candidate.name == table:
            if candidate.is_array:
                raise TaskFailed(
                    f"array-of-tables [[{table}]] cannot be edited line-based; "
                    "edit the file with the files module instead"
                )
            header = candidate
    if header is None:
        return None
    end = next((item.line_index for item in headers if item.line_index > header.line_index), len(lines))
    return (header.line_index + 1, end)


def _insert_block(
    lines: list[str],
    span: tuple[int, int] | None,
    table: str | None,
    block: list[str],
    nl: str,
) -> list[str]:
    """Insert ``block`` at the end of the table span, creating the table when missing."""
    new_lines = list(lines)
    if span is None:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += nl
        if table is not None:
            new_lines.append(f"[{table}]{nl}")
        new_lines.extend(block)
        return new_lines
    start, end = span
    insert_at = start
    for index in range(start, end):
        body = line_body(lines[index]).strip()
        if body and not body.startswith("#"):
            insert_at = index + 1
    if insert_at > 0 and not new_lines[insert_at - 1].endswith("\n"):
        new_lines[insert_at - 1] += nl
    new_lines[insert_at:insert_at] = block
    return new_lines


def _render_array_block(key: str, rendered_items: list[str], *, multiline: bool, nl: str) -> list[str]:
    """Render a ``key = [...]`` block, single-line or one item per line."""
    if not rendered_items:
        return [f"{key} = []{nl}"]
    if not multiline:
        return [f"{key} = [{', '.join(rendered_items)}]{nl}"]
    block = [f"{key} = [{nl}"]
    block.extend(f"    {item},{nl}" for item in rendered_items)
    block.append(f"]{nl}")
    return block


def _parse_array(raw: str, key: str, table: str | None) -> list[str]:
    """Parse a raw TOML value that must be an array of strings."""
    try:
        parsed = _parse_value(raw)
    except tomllib.TOMLDecodeError as exc:
        raise TaskFailed(f"could not parse the value of {key!r} in table {table!r}: {exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise TaskFailed(f"{key!r} in table {table!r} is not an array of strings")
    return parsed


def _parse_value(raw: str) -> object:
    """Parse a raw TOML value (``key = <raw>`` form) with stdlib ``tomllib``."""
    return tomllib.loads(f"_ = {raw}\n")["_"]


def _value_equals(entry: _Entry, value_text: str) -> bool:
    """Return whether the entry's raw value parses equal to ``value_text``, type included.

    The type check keeps ``true`` distinct from ``1`` and ``1`` from ``1.0``.
    """
    try:
        left = _parse_value(entry.value)
        right = _parse_value(value_text)
    except tomllib.TOMLDecodeError:
        return False
    return type(left) is type(right) and left == right


def _resolve_target(
    headers: list[_Header],
    entries: list[_Entry],
    table: str | None,
    key: str,
) -> tuple[str | None, str]:
    """Resolve a dotted ``key`` against existing sub-table headers and top-level dotted entries.

    Setting ``pip.no-cache`` in ``tool.uv`` targets ``no-cache`` in an existing
    ``[tool.uv.pip]`` table when present; a ``tool.uv`` table declared through
    top-level dotted keys (``tool.uv.x = ...``) is edited in that form rather
    than by adding a colliding ``[tool.uv]`` header.
    """
    prefix = table.split(".") if table is not None else []
    names = {header.name for header in headers if not header.is_array}
    parts = key.split(".")
    for length in range(len(parts) - 1, 0, -1):
        candidate = ".".join([*prefix, *parts[:length]])
        if candidate in names:
            return candidate, ".".join(parts[length:])
    if table is not None:
        table_key = ".".join(prefix)
        if table_key not in names and any(
            entry.table is None and (entry.key == table_key or entry.key.startswith(table_key + "."))
            for entry in entries
        ):
            return None, ".".join([*prefix, *parts])
    return table, key


def _checked_value(value_text: str, key: str) -> object:
    """Parse rendered ``value_text`` for post-edit validation."""
    try:
        return _parse_value(value_text)
    except tomllib.TOMLDecodeError as exc:
        raise TaskFailed(f"invalid rendered value for {key!r}: {exc}") from exc


def _validate_edit(
    new_lines: list[str],
    table: str | None,
    key: str,
    expected: object,
    *,
    present: bool,
) -> None:
    """Assert that an edited document parses and holds ``key`` at ``table`` as expected.

    Every editor result is checked with stdlib ``tomllib`` before it can be
    written: a line-based edit that would produce invalid TOML (e.g. a dotted
    key colliding with a table header) raises instead of corrupting the file.
    """
    text = "".join(new_lines)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TaskFailed(f"refusing to write invalid TOML ({exc}): the edit target was {table}.{key}") from exc
    node: object = data
    for part in [*(table.split(".") if table is not None else []), *key.split(".")]:
        if not isinstance(node, dict) or part not in node:
            if present:
                raise TaskFailed(f"internal error: {key!r} is missing from the edited TOML at {table!r}")
            return
        node = node[part]
    if not present:
        raise TaskFailed(f"internal error: {key!r} is still present at {table!r} after removal")
    if type(node) is not type(expected) or node != expected:
        raise TaskFailed(f"internal error: {key!r} at {table!r} was written as {node!r}, expected {expected!r}")


def _validate_table_absent(new_lines: list[str], table: str) -> None:
    """Assert that an edited document parses and no longer holds ``table``."""
    text = "".join(new_lines)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TaskFailed(f"refusing to write invalid TOML ({exc}): the edit target was [{table}]") from exc
    node: object = data
    for part in table.split("."):
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    raise TaskFailed(f"internal error: table {table!r} is still present after removal")


def _split_dotted(text: str) -> list[str]:
    """Split a dotted key or table name on dots outside quotes."""
    parts: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    for char in text:
        if quote is not None:
            buffer.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            buffer.append(char)
        elif char == ".":
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    parts.append("".join(buffer))
    return [_unquote_part(part.strip()) for part in parts]


def _unquote_part(part: str) -> str:
    """Return one key part without its surrounding quotes, when quoted."""
    if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
        if part[0] == '"':
            try:
                decoded = json.loads(part)
            except json.JSONDecodeError:
                return part[1:-1]
            return decoded if isinstance(decoded, str) else part[1:-1]
        return part[1:-1]
    return part


def _join_dotted(text: str) -> str:
    """Return the dotted name with each part unquoted, joined by dots."""
    return ".".join(_split_dotted(text))
