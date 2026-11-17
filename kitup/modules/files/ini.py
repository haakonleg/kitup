"""Idempotent INI-style file editing."""

from collections.abc import Mapping
from pathlib import Path

from ..._utils import file_actions
from ..._utils import ini as ini_ops
from ..._utils.paths import expand_path
from ..._utils.validation import State, require_bool, require_path, require_state
from ...context import current_context
from ...errors import TaskFailed
from ...results import CallResult


def ini(
    path: str | Path,
    *,
    section: str | None = None,
    option: str | None = None,
    value: str | None = None,
    match: Mapping[str, str] | None = None,
    state: State = "present",
    create: bool = True,
    sudo: bool = False,
) -> CallResult:
    """Set or remove one option in an INI-style file, section by section.

    Sections are matched case-sensitively as ``[section]`` headers; with
    ``section=None`` the preamble before the first header is edited. Options
    are written canonically as ``option = value``. ``match`` selects one of
    several same-named sections and is never written to the file.
    """
    ctx = current_context()
    require_state(state, "ini")
    require_path(path, "ini path")
    require_bool(create, "ini create")
    require_bool(sudo, "ini sudo")
    if section is not None:
        section = ini_ops.require_ini_section(section)
    if option is not None:
        option = ini_ops.require_ini_option(option)
    if match is not None:
        if section is None:
            raise TaskFailed("ini match requires a section")
        match = ini_ops.require_ini_match(match)
    # Validate the state-dependent arguments before any filesystem access.
    set_value: str | None = None
    if state == "present":
        if option is None:
            raise TaskFailed("ini state 'present' requires an option")
        if value is None:
            raise TaskFailed("ini state 'present' requires a value")
        set_value = ini_ops.require_ini_value(value)
    elif section is None and option is None:
        raise TaskFailed("ini state 'absent' requires a section or an option")
    elif value is not None:
        raise TaskFailed("ini state 'absent' does not take a value")

    return ctx.changed(
        file_actions.apply_ini(
            ctx,
            expand_path(path, error=TaskFailed),
            section=section,
            option=option,
            value=set_value,
            match=match,
            create=create,
            sudo=sudo,
        )
    )
