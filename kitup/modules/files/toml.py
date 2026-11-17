"""Idempotent TOML file editing."""

from pathlib import Path

from ..._utils import toml as toml_ops
from ..._utils.paths import expand_path
from ..._utils.validation import State, require_bool, require_path, require_state
from ...context import current_context
from ...errors import TaskFailed
from ...results import CallResult


def toml(
    path: str | Path,
    *,
    table: str | None = None,
    key: str | None = None,
    value: toml_ops.TomlValue | None = None,
    state: State = "present",
    create: bool = True,
    sudo: bool = False,
) -> CallResult:
    """Set or remove one key in a TOML file, table by table.

    ``table`` is a dotted table path (``tool.uv``); ``None`` edits the
    top-level keys before the first header. ``key`` may be dotted
    (``pip.no-cache``) and resolves into an existing sub-table
    (``[tool.uv.pip]``) when that header exists. ``value`` follows TOML
    types: a string renders as a basic string, a boolean as ``true`` or
    ``false``, an integer or float in decimal form, and a list or tuple of
    strings as an array (a multi-line array keeps its layout). Existing
    entries are matched by parsed value, so spacing and quote style do not
    cause rewrites; a missing table is appended at the end of the file.
    Array-of-tables (``[[...]]``) cannot be edited and are rejected.
    ``state="absent"`` removes the ``key`` — or the whole ``table``,
    comments included, when ``key`` is ``None`` — and takes no ``value``.
    ``create=False`` fails when the file does not exist; otherwise a missing
    file is created with mode ``0o644`` (parent directories ``0o755``), and
    an absent edit on a missing file is a no-op. ``sudo=True`` edits through
    the privileged worker.
    """
    ctx = current_context()
    require_state(state, "toml")
    require_path(path, "toml path")
    require_bool(create, "toml create")
    require_bool(sudo, "toml sudo")
    if table is not None:
        table = toml_ops.require_toml_key(table, "toml table")
    if key is not None:
        key = toml_ops.require_toml_key(key, "toml key")
    # Validate the state-dependent arguments before any filesystem access.
    rendered: str | None = None
    if state == "present":
        if key is None:
            raise TaskFailed("toml state 'present' requires a key")
        if value is None:
            raise TaskFailed("toml state 'present' requires a value")
        rendered = toml_ops.toml_value(value)  # validate the type before touching the filesystem
    elif key is None and table is None:
        raise TaskFailed("toml state 'absent' requires a key or a table")
    elif value is not None:
        raise TaskFailed("toml state 'absent' does not take a value")

    return ctx.changed(
        toml_ops.apply_toml_key(
            ctx,
            expand_path(path, error=TaskFailed),
            table=table,
            key=key,
            value=value,
            create=create,
            sudo=sudo,
            rendered=rendered,
        )
    )
