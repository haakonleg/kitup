"""uv project dependencies, per-user CLI tools, and uv configuration files.

All operations use uv's native, project-centric interface (``uv sync``,
``uv tool``, ``uv.toml``) rather than the ``uv pip`` compatibility layer.
``dependencies`` and ``tools`` run with the invoking user's privileges — uv
projects, tools, and user config all live under the user's home — so manage
root-owned project directories by running kitup as root. Only ``config`` takes
``sudo``, for the root-owned ``/etc/uv/uv.toml``.
"""

import os
import re
import subprocess
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from .._utils import file_actions
from .._utils import toml as toml_ops
from .._utils.executables import require_command
from .._utils.paths import expand_path
from .._utils.pep508 import Requirement, normalize_dist_name, normalize_specifier, parse_requirement
from .._utils.validation import (
    State,
    normalize_extra_args,
    normalize_strings,
    require_bool,
    require_choice,
    require_collection,
    require_path,
    require_state,
    require_text,
    require_texts,
    unique_strings,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult

type UvState = Literal["present", "absent", "latest"]

_SYSTEM_CONFIG_PATH: Final = Path("/etc/uv/uv.toml")
_CONFIG_SCOPES: Final = ("system", "user", "project")
_UV_STATES: Final[tuple[UvState, ...]] = ("present", "absent", "latest")
_TOOL_LINE_RE: Final = re.compile(r"^(?P<name>\S+) v(?P<version>\S+)")


def dependencies(
    ctx: TaskContext,
    names: Iterable[str] | str,
    *,
    project: str | Path,
    state: UvState = "present",
    group: str | None = None,
    python: str | None = None,
    extra_args: Iterable[str] | str | None = None,
) -> CallResult:
    """Ensure PEP 508 dependencies of a uv project are declared, up to date, or removed.

    ``names`` are registry requirement strings (``"requests>=2"``, ``"psycopg[binary]"``)
    written verbatim into the project's ``pyproject.toml`` — ``[project.dependencies]``,
    or the ``<group>`` key of ``[dependency-groups]`` (PEP 735) when ``group`` is
    given. URLs, VCS, and local paths are rejected (they need ``[tool.uv.sources]``
    entries), as are uv tool ``@`` specifiers (``name@latest`` is not valid in a
    dependency table); existing non-registry entries in the table are left
    untouched. ``state="present"`` adds or replaces entries whose PEP 503-normalized
    name matches with a different specifier; ``state="latest"`` additionally upgrades
    bare names via ``uv sync --upgrade-package`` and detects an available newer
    version with an upgrade-aware ``--check`` probe; ``state="absent"`` removes
    entries by name. ``project`` is the project directory; a missing one is
    initialized with ``uv init --bare`` (never for ``state="absent"``). After editing
    ``pyproject.toml``, ``uv sync --project`` converges ``uv.lock`` and ``.venv``;
    convergence is probed with the stable ``uv sync --check`` / ``uv lock --check``
    exit codes plus the existence of the project environment, and a post-sync check
    must pass. ``python`` is passed as ``--python`` to select the project interpreter.
    ``extra_args`` are extra resolver options for ``uv sync``/``uv lock``, e.g.
    ``["--no-index", "--find-links=/srv/wheels"]``. Note that ``uv sync`` re-resolves
    during ``--check``, so an offline run can report a change even when correct.
    No ``sudo``: run kitup as root for root-owned project directories.
    """
    wanted = unique_strings(normalize_strings(names))
    require_collection(wanted, "dependency names")
    require_texts(wanted, "dependency name")
    require_state(state, "uv", allowed=_UV_STATES)
    project_path = _normalize_project(project)
    if group is not None:
        group = _require_group_name(group)
    if python is not None:
        require_text(python, "python")
    extra = normalize_extra_args(extra_args)
    requirements = [_require_project_requirement(name, state=state) for name in wanted]

    pyproject = project_path / "pyproject.toml"
    project_info = file_actions.stat(ctx, project_path, sudo=False, follow=True)
    if project_info is not None and not project_info.is_dir:
        raise TaskFailed(f"project path is not a directory: {project_path}")
    pyproject_info = file_actions.stat(ctx, pyproject, sudo=False)
    if pyproject_info is None and state == "absent":
        # nothing to remove from a missing project; no uv needed
        return ctx.changed(_absent_no_change(wanted, project_path, state))

    initialized = _init_project(ctx, pyproject_info, pyproject, project_path, python)
    snapshot, lines, table, key = _read_table(ctx, pyproject, initialized=initialized, state=state, group=group)
    current_raw = toml_ops.array_items(lines, table, key)
    if state == "absent" and group is not None and current_raw is None:
        # the group is not defined, so there is nothing to remove and a
        # `uv sync --group <name>` probe would fail with "Group not defined"
        return ctx.changed(_absent_no_change(wanted, project_path, state))
    current = current_raw if current_raw is not None else []
    desired, added, removed = _diff_dependencies(current, requirements, state=state)
    edits_needed = desired != current

    uv = _uv_bin(ctx)
    group_args = ["--group", group] if group is not None else []
    sync_base = [uv, "sync", "--project", str(project_path), *group_args, *_python_args(python)]
    lock_base = [uv, "lock", "--project", str(project_path)]
    upgrade_args = _upgrade_package_args(requirements, state)
    # for state="latest" the pre-probe is upgrade-aware, so a merely available
    # newer version is detected; the post-sync checks stay plain
    sync_probe = _check_args(sync_base, extra, upgrade_args)
    lock_probe = _check_args(lock_base, extra, upgrade_args)
    check_args = _check_args(sync_base, extra)
    lock_args = _check_args(lock_base, extra)
    # An edit forces a sync, so only probe convergence when nothing is being edited.
    converged = not edits_needed and _project_converged(
        ctx, project_path, sync_probe, lock_probe, initialized=initialized, dry_run=ctx.dry_run
    )

    if converged:
        desired_state = "up to date" if state == "latest" else state
        data = _call_data("dependencies", wanted, state, project=str(project_path))
        return ctx.changed(
            CallResult(
                action="uv.dependencies",
                changed=False,
                summary=f"ensure dependencies are {desired_state}: " + " ".join(wanted),
                **data,
            )
        )

    parts: list[str] = []
    if initialized:
        parts.append(f"init project {project_path}")
    if state == "absent":
        parts.extend(f"-{entry}" for entry in removed)
    else:
        parts.extend(f"+{requirement}" for requirement in added)
    parts.append(f"sync {project_path}")
    data = _call_data("dependencies", wanted, state, project=str(project_path), added=added, removed=removed)
    result = CallResult(action="uv.dependencies", changed=True, summary="; ".join(parts), **data)
    if ctx.dry_run:
        return ctx.changed(result)

    if edits_needed:
        _write_table(ctx, pyproject, snapshot, lines, table, key, desired, result.summary, data)
    _sync_project(ctx, sync_base, upgrade_args, extra, project_path, check_args, lock_args)
    return ctx.changed(result)


def tools(
    ctx: TaskContext,
    names: Iterable[str] | str,
    *,
    state: UvState = "present",
    python: str | None = None,
    extra_args: Iterable[str] | str | None = None,
) -> CallResult:
    """Ensure per-user uv CLI tools are installed, up to date, or uninstalled.

    ``names`` are registry requirement strings (``"ruff"``, ``"httpie==3.2"``),
    including uv's ``{package}@{version}`` / ``{package}@latest`` specifiers; URLs
    and paths are rejected. ``state="present"`` installs missing tools and reinstalls
    a tool whose requested specifier, extras, or marker differ from the installed
    one (compared normalized, so spacing and ``@``-form differences don't reinstall),
    but leaves a satisfied bare name alone; ``state="latest"`` additionally refreshes
    installed tools — with ``uv tool upgrade``, or ``uv tool install <name>@latest``
    only when the tool was installed with an exact ``==`` pin, which ``uv tool
    upgrade`` refuses to move; ``state="absent"`` uninstalls by name. Reinstalling a
    tool that was installed with ``--with`` requirements under a different specifier
    is rejected. Installed tools are detected from the ``uv-receipt.toml`` receipts
    in ``uv tool dir``, and upgrades are confirmed by comparing ``uv tool list``
    versions before and after.
    ``python`` is passed as ``--python`` to select the tool environment interpreter.
    ``extra_args`` are extra options for ``uv tool install`` and ``uv tool upgrade``,
    e.g. ``["--no-index", "--find-links=/srv/wheels"]``. No ``sudo``: uv tools are
    per-user by design (XDG directories under ``$HOME``).
    """
    wanted = unique_strings(normalize_strings(names))
    require_collection(wanted, "tool names")
    require_texts(wanted, "tool name")
    require_state(state, "uv", allowed=_UV_STATES)
    if python is not None:
        require_text(python, "python")
    extra = normalize_extra_args(extra_args)
    requirements = [_require_tool_requirement(name, state=state) for name in wanted]

    uv = _uv_bin(ctx)
    tools_dir = _tools_dir(ctx, uv)
    installed = _installed_tools(ctx, tools_dir)
    python_args = ["--python", python] if python is not None else []

    if state == "absent":
        targets = [req for req in requirements if req.normalized in installed]
        if not targets:
            return ctx.changed(_tools_no_change(wanted, state))
        removed = [req.name for req in targets]
        result = _tools_changed(wanted, state, removed)
        if ctx.dry_run:
            return ctx.changed(result)
        ctx.run([uv, "tool", "uninstall", *removed])
        post = _installed_tools(ctx, tools_dir)
        remaining = [req.name for req in targets if req.normalized in post]
        if remaining:
            raise TaskFailed(f"tools still installed after removal: {', '.join(remaining)}")
        return ctx.changed(result)

    install: list[str] = []  # requirements for `uv tool install`
    upgrade: list[str] = []  # dist names for `uv tool upgrade`
    for req in requirements:
        current = installed.get(req.normalized)
        if current is None:
            install.append(req.source)
        elif not req.is_bare and not req.matches(current.requirement):
            _reject_tool_reinstall(req, current)
            install.append(req.source)
        elif state == "latest":
            if req.is_bare and current.requirement.specifier.startswith("=="):
                # uv tool upgrade refuses to move an exact pin; @latest re-resolves it
                install.append(f"{req.name}@latest")
            else:
                upgrade.append(req.name)
    if not install and not upgrade:
        return ctx.changed(_tools_no_change(wanted, state))

    # dist names of installed tools a "latest" run refreshes, confirmed by version change
    refresh = [req.name for req in requirements if state == "latest" and req.normalized in installed]
    changed_items = [name.removesuffix("@latest") for name in install]
    if ctx.dry_run:
        if state == "latest" and upgrade:
            try:
                outdated = _outdated_tools(ctx, uv)
            except TaskFailed as exc:
                # Offline or custom-index runs may not be able to query the
                # index; fall back to the previous over-approximation.
                ctx.note(
                    f"could not check for outdated uv tools; reporting installed tools as potentially outdated: {exc}"
                )
            else:
                refresh = [name for name in refresh if normalize_dist_name(name) in outdated]
            changed_items = unique_strings([*changed_items, *refresh])
        if not changed_items:
            return ctx.changed(_tools_no_change(wanted, state))
        return ctx.changed(_tools_changed(wanted, state, changed_items))

    versions_before = _tool_versions(ctx, uv) if state == "latest" else {}
    # `uv tool install` takes one package, but uninstall and upgrade accept multiple names.
    for name in install:
        ctx.run([uv, "tool", "install", *python_args, *extra, name])
    if upgrade:
        ctx.run([uv, "tool", "upgrade", *python_args, *extra, *upgrade])

    post = _installed_tools(ctx, tools_dir)
    missing = [name for name in install if parse_requirement(name, tool=True).normalized not in post]
    if missing:
        raise TaskFailed(f"tools still not installed after install: {', '.join(missing)}")
    if state == "latest":
        versions_after = _tool_versions(ctx, uv)
        changed_items.extend(
            name
            for name in refresh
            if versions_before.get(normalize_dist_name(name)) != versions_after.get(normalize_dist_name(name))
        )
        changed_items = unique_strings(changed_items)
        if not changed_items:
            return ctx.changed(_tools_no_change(wanted, state))
    return ctx.changed(_tools_changed(wanted, state, changed_items))


def config(
    ctx: TaskContext,
    option: str,
    value: str | bool | float | Iterable[str] | None = None,  # noqa: FBT001 - positional value matches the module API
    *,
    state: State = "present",
    scope: Literal["system", "user", "project"] = "user",
    project: str | Path | None = None,
    path: str | Path | None = None,
    sudo: bool | None = None,
) -> CallResult:
    """Set or remove one option in a uv configuration file.

    uv reads ``uv.toml`` files at three levels — system (``$XDG_CONFIG_DIRS/uv/uv.toml``
    or ``/etc/uv/uv.toml``), user (``$XDG_CONFIG_HOME/uv/uv.toml`` or
    ``~/.config/uv/uv.toml``), and project (a project-level ``uv.toml``, or the
    ``[tool.uv]`` table of the project's ``pyproject.toml``) — with precedence
    project > user > system for scalar settings; array settings are concatenated
    instead, project-first. A project-level ``uv.toml`` takes precedence over
    ``[tool.uv]`` in ``pyproject.toml``, so ``scope="project"`` can be a no-op when
    both exist. ``scope`` selects the default target file; pass an explicit
    ``path`` to edit any other TOML file (a ``pyproject.toml`` path automatically
    uses its ``[tool.uv]`` table, a ``uv.toml`` path its top-level keys), and
    ``project`` with ``scope="project"`` for a project's ``[tool.uv]`` table.
    ``option`` is a (possibly dotted) bare TOML key such as ``index-url``; a dotted
    option resolves into an existing sub-table (e.g. ``pip.no-cache`` into
    ``[tool.uv.pip]``) when that header exists.
    ``value`` follows TOML types: a string renders as a basic string, a boolean as
    ``true``/``false``, an integer or float in decimal form, and a list or tuple of
    strings as an array (``[]`` when empty). A missing file is created. ``sudo``
    defaults to ``True`` for ``scope="system"`` without an explicit ``path``;
    ``scope="user"`` rejects ``sudo=True`` without a ``path`` (it would write a
    root-owned file into the user's home) and cannot run as root at all; with an
    explicit ``path``, ``sudo=True`` is permitted like in ``pip.config``.
    Array-of-tables (``[[tool.uv.index]]``) cannot be edited and are rejected.
    """
    require_state(state, "config")
    require_choice(scope, _CONFIG_SCOPES, "uv config scope")
    option = toml_ops.require_toml_key(option, "uv config option")
    if sudo is not None:
        require_bool(sudo, "config sudo")
    else:
        sudo = path is None and scope == "system"
    if scope == "user" and path is None:
        if sudo:
            raise TaskFailed(
                "user-scoped uv config cannot use sudo: it would write a root-owned file into the user's home"
            )
        if ctx.sudo.is_root:
            raise TaskFailed("user-scoped uv config cannot run as root")
    if scope == "project" and project is None:
        raise TaskFailed("project scope requires a project directory")
    target, table = _config_target(scope=scope, project=project, path=path)

    rendered: str | None = None
    if state == "present":
        if value is None:
            raise TaskFailed("config state 'present' requires a value")
        rendered = toml_ops.toml_value(value)  # validate the type before touching the filesystem
    elif value is not None:
        raise TaskFailed("config state 'absent' does not take a value")

    return ctx.changed(
        toml_ops.apply_toml_key(
            ctx,
            target,
            table=table,
            key=option,
            value=value,
            action="uv.config",
            sudo=sudo,
            rendered=rendered,
        )
    )


def _absent_no_change(wanted: list[str], project: Path, state: UvState) -> CallResult:
    """Build the ``changed=False`` result for ``state="absent"`` with nothing to remove."""
    return CallResult(
        action="uv.dependencies",
        changed=False,
        summary="ensure dependencies are absent: " + " ".join(wanted),
        **_call_data("dependencies", wanted, state, project=str(project)),
    )


def _tools_no_change(wanted: list[str], state: UvState) -> CallResult:
    """Build the ``changed=False`` result for ``uv.tools`` when the desired state already holds."""
    desired = "up to date" if state == "latest" else state
    return CallResult(
        action="uv.tools",
        changed=False,
        summary=f"ensure tools are {desired}: " + " ".join(wanted),
        **_call_data("tools", wanted, state),
    )


def _tools_changed(wanted: list[str], state: UvState, items: list[str]) -> CallResult:
    """Build the ``changed=True`` result for ``uv.tools`` from the added or removed items."""
    sign = "-" if state == "absent" else "+"
    return CallResult(
        action="uv.tools",
        changed=True,
        summary=" ".join(f"{sign}{item}" for item in items),
        **_call_data("tools", wanted, state, added=items, removed=items),
    )


def _check_args(base: list[str], extra: list[str], upgrade: list[str] | None = None) -> list[str]:
    """Build a ``--check`` probe argv from a command base, extra args, and optional upgrade args."""
    return [*base, *extra, *(upgrade or []), "--check"]


def _init_project(
    ctx: TaskContext,
    pyproject_info: file_actions.FileInfo | None,
    pyproject: Path,
    project_path: Path,
    python: str | None,
) -> bool:
    """Initialize a missing project with ``uv init --bare``; return whether it was initialized."""
    if pyproject_info is not None:
        return False
    if ctx.dry_run:
        return True
    uv = _uv_bin(ctx)
    ctx.run([uv, "init", "--bare", *_python_args(python), str(project_path)])
    if file_actions.stat(ctx, pyproject, sudo=False) is None:
        raise TaskFailed(f"uv init did not create {pyproject}")
    return True


def _read_table(
    ctx: TaskContext,
    pyproject: Path,
    *,
    initialized: bool,
    state: UvState,
    group: str | None,
) -> tuple[file_actions.FileSnapshot, list[str], str, str]:
    """Read ``pyproject.toml`` and return ``(snapshot, lines, table, key)`` for the edited table."""
    snapshot = file_actions.read_snapshot(ctx, pyproject, sudo=False)
    text = snapshot.text(pyproject)
    if not initialized and state != "absent" and group is None:
        _require_project_table(text, pyproject)
    table = "dependency-groups" if group is not None else "project"
    key = group if group is not None else "dependencies"
    return snapshot, toml_ops.split_lines(text), table, key


def _project_converged(
    ctx: TaskContext,
    project_path: Path,
    sync_probe: list[str],
    lock_probe: list[str],
    *,
    initialized: bool,
    dry_run: bool,
) -> bool:
    """Return whether the project is converged: the environment exists and both ``--check`` probes pass."""
    if initialized and dry_run:
        return False  # the project does not exist yet, so there is nothing to probe
    return (
        _project_env_exists(ctx, project_path)
        and ctx.probe(sync_probe).returncode == 0
        and ctx.probe(lock_probe).returncode == 0
    )


def _write_table(
    ctx: TaskContext,
    pyproject: Path,
    snapshot: file_actions.FileSnapshot,
    lines: list[str],
    table: str,
    key: str,
    desired: list[str],
    summary: str,
    data: dict[str, object],
) -> None:
    """Write the edited dependency table into ``pyproject.toml``."""
    new_lines, _ = toml_ops.set_array(lines, table, key, desired)
    new_text = "".join(new_lines)
    if new_text and not new_text.endswith("\n"):
        new_text += toml_ops.newline_of(lines)
    file_actions.apply_text(
        ctx,
        pyproject,
        new_text,
        snapshot=snapshot,
        action="uv.dependencies",
        summary=summary,
        data=data,
        sudo=False,
    )


def _sync_project(
    ctx: TaskContext,
    sync_base: list[str],
    upgrade_args: list[str],
    extra: list[str],
    project_path: Path,
    check_args: list[str],
    lock_args: list[str],
) -> None:
    """Run ``uv sync`` and verify that both plain ``--check`` probes pass afterwards."""
    ctx.run([*sync_base, *upgrade_args, *extra])
    sync_check = ctx.probe(check_args)
    lock_check = ctx.probe(lock_args)
    if sync_check.returncode != 0 or lock_check.returncode != 0:
        raise TaskFailed(
            f"project is not synchronized after uv sync: {project_path}",
            command=check_args,
            stdout=sync_check.stdout,
            stderr=sync_check.stderr,
        )


def _uv_bin(ctx: TaskContext) -> str:
    """Return the uv executable path, failing when it is missing."""
    return require_command(ctx, "uv", memo_key="uv-bin", hint="https://docs.astral.sh/uv/")


def _require_uv(ctx: TaskContext, argv: list[str], *, failure: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only uv command that must succeed.

    A failing command is re-checked with ``uv --version`` so that a missing or
    broken uv is reported as such, not as a command failure.
    """
    result = ctx.probe(argv)
    if result.returncode != 0:
        if ctx.probe([argv[0], "--version"]).returncode != 0:
            raise TaskFailed("uv could not run — is uv installed and on PATH?")
        raise TaskFailed(failure, command=argv, stdout=result.stdout, stderr=result.stderr)
    return result


def _normalize_project(project: str | Path) -> Path:
    require_path(project, "project")
    return expand_path(project, error=TaskFailed)


def _python_args(python: str | None) -> list[str]:
    return ["--python", python] if python is not None else []


def _require_group_name(group: str) -> str:
    """Validate a dependency group name: a single bare TOML key part (dots would nest a table)."""
    key = toml_ops.require_toml_key(group, "dependency group")
    if "." in key:
        raise TaskFailed(f"dependency group must be a single key, not dotted: {group!r}")
    return key


def _require_project_requirement(name: str, *, state: UvState) -> Requirement:
    """Validate a project dependency entry and return it parsed.

    Project dependencies reject ``@`` specifiers: ``name@latest`` and
    ``name@<version>`` are uv *tool* syntax and invalid in ``pyproject.toml``
    dependency tables.
    """
    return _parse_registry_requirement(name, state=state, tool=False)


def _require_tool_requirement(name: str, *, state: UvState) -> Requirement:
    """Validate a uv tool requirement (``@`` specifiers allowed) and return it parsed."""
    return _parse_registry_requirement(name, state=state, tool=True)


def _parse_registry_requirement(name: str, *, state: UvState, tool: bool) -> Requirement:
    """Parse a registry requirement, mapping URL/path errors to the ``state="absent"`` message."""
    try:
        return parse_requirement(name, tool=tool)
    except TaskFailed as exc:
        if state == "absent":
            raise TaskFailed(f"state='absent' supports package names only, not URLs or paths: {name!r}") from exc
        raise


def _require_project_table(text: str, pyproject: Path) -> None:
    """Require an existing ``[project]`` table when editing ``[project.dependencies]``."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TaskFailed(f"could not parse {pyproject}: {exc}") from exc
    if not isinstance(data.get("project"), dict):
        raise TaskFailed(
            f"{pyproject} has no [project] table; add one (or run 'uv init --bare' in a fresh directory) "
            "before managing its dependencies"
        )


def _project_env_exists(ctx: TaskContext, project_path: Path) -> bool:
    """Return whether the project environment (``.venv`` or ``UV_PROJECT_ENVIRONMENT``) exists."""
    configured = os.environ.get("UV_PROJECT_ENVIRONMENT", "")
    env = expand_path(configured, error=TaskFailed) if configured else project_path / ".venv"
    if not env.is_absolute():
        env = project_path / env
    info = file_actions.stat(ctx, env, sudo=False, follow=True)
    return info is not None and info.is_dir


def _call_data(
    field: str,
    wanted: list[str],
    state: UvState,
    *,
    added: list[str] | None = None,
    removed: list[str] | None = None,
    **fields: object,
) -> dict[str, object]:
    """Build the flat call data for one ``uv`` module invocation (``dependencies`` or ``tools``)."""
    data: dict[str, object] = {field: wanted, **fields}
    if state == "absent":
        data["removed"] = removed if removed is not None else []
    else:
        data["added"] = added if added is not None else []
    return data


def _try_parse_requirement(item: str) -> Requirement | None:
    """Parse a pyproject entry, returning ``None`` for non-registry entries (URLs, paths)."""
    try:
        return parse_requirement(item, tool=False)
    except TaskFailed:
        return None


def _diff_dependencies(
    current: list[str],
    wanted: list[Requirement],
    *,
    state: UvState,
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(desired, added, removed)`` for the pyproject table diff.

    ``present``/``latest`` add missing requirements and replace entries whose
    normalized name matches with a different verbatim string (all duplicates of
    the name are reconciled); ``absent`` removes entries by normalized name.
    Non-registry entries (URLs, paths, direct references) are preserved verbatim
    and never matched. Current entries are parsed once.
    """
    parsed = [_try_parse_requirement(item) for item in current]
    if state == "absent":
        wanted_names = {req.normalized for req in wanted}
        removed: list[str] = []
        desired: list[str] = []
        for item, req in zip(current, parsed, strict=True):
            if req is not None and req.normalized in wanted_names:
                removed.append(item)
            else:
                desired.append(item)
        return desired, [], removed
    by_name: dict[str, list[int]] = {}
    for index, req in enumerate(parsed):
        if req is not None:
            by_name.setdefault(req.normalized, []).append(index)
    desired = list(current)
    dropped: set[int] = set()
    added: list[str] = []
    for req in wanted:
        positions = by_name.get(req.normalized)
        if not positions:
            by_name[req.normalized] = [len(desired)]
            desired.append(req.source)
            added.append(req.source)
        elif len(positions) == 1 and desired[positions[0]] == req.source:
            continue
        else:
            first = positions[0]
            desired[first] = req.source
            dropped.update(positions[1:])
            by_name[req.normalized] = [first]
            added.append(req.source)
    if dropped:
        desired = [item for index, item in enumerate(desired) if index not in dropped]
    return desired, added, []


def _upgrade_package_args(wanted: list[Requirement], state: UvState) -> list[str]:
    """Build ``--upgrade-package`` args for bare names when ``state="latest"``."""
    if state != "latest":
        return []
    args: list[str] = []
    for req in wanted:
        if req.is_bare:
            args.extend(["--upgrade-package", req.name])
    return args


def _tools_dir(ctx: TaskContext, uv: str) -> Path:
    """Return the uv tools directory, memoized per run."""

    def load() -> Path:
        result = _require_uv(ctx, [uv, "tool", "dir"], failure="could not determine the uv tools directory")
        path = result.stdout.strip()
        if not path:
            raise TaskFailed("uv tool dir printed no path")
        return Path(path)

    return ctx.memo("uv-tools-dir", load)


@dataclass(frozen=True, slots=True)
class InstalledTool:
    """One installed uv tool: its own receipt requirement plus any ``--with`` requirement names."""

    requirement: Requirement
    extra_requirements: tuple[str, ...]


def _reject_tool_reinstall(req: Requirement, current: InstalledTool) -> None:
    """Reject changing the specifier of a tool installed with extra ``--with`` requirements."""
    if current.extra_requirements:
        raise TaskFailed(
            f"tool {req.name} was installed with additional requirements "
            f"({', '.join(current.extra_requirements)}); refusing to reinstall it with a different specifier"
        )


def _receipt_requirement(item: Mapping[str, object]) -> Requirement:
    """Build a ``Requirement`` from one ``uv-receipt.toml`` requirements entry."""
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise TaskFailed("malformed requirement name")
    specifier = item.get("specifier")
    if specifier is not None and not isinstance(specifier, str):
        raise TaskFailed("malformed requirement specifier")
    extras_raw = item.get("extras")
    extras: tuple[str, ...] = ()
    if extras_raw is not None:
        if not isinstance(extras_raw, list) or not all(isinstance(extra, str) for extra in extras_raw):
            raise TaskFailed("malformed requirement extras")
        extras = tuple(sorted(normalize_dist_name(extra) for extra in extras_raw))
    marker = item.get("marker")
    if marker is not None and not isinstance(marker, str):
        raise TaskFailed("malformed requirement marker")
    specifier_text = normalize_specifier(specifier or "")
    return Requirement(
        source=name,
        name=name,
        specifier=specifier_text,
        extras=extras,
        marker=marker.strip() if isinstance(marker, str) else "",
    )


def _receipt_name(item: object) -> str | None:
    """Return the normalized name of a receipt requirements entry, when well-formed."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name:
        return None
    return normalize_dist_name(name)


def _installed_tools(ctx: TaskContext, tools_dir: Path) -> dict[str, InstalledTool]:
    """Return normalized tool name -> installed tool, read from ``uv-receipt.toml`` receipts.

    The environment directory name is the normalized tool name; the receipt's
    ``requirements`` may hold additional packages (``--with``), so the tool's own
    entry is the one matching the directory name. Unreadable receipts are skipped
    with a note instead of aborting every call.
    """
    installed: dict[str, InstalledTool] = {}
    if not tools_dir.is_dir():
        return installed
    try:
        children = sorted(tools_dir.iterdir())
    except OSError as exc:
        raise TaskFailed(f"could not list the uv tools directory {tools_dir}: {exc}") from exc
    for child in children:
        receipt = child / "uv-receipt.toml"
        if not child.is_dir() or not receipt.is_file():
            continue
        try:
            data = tomllib.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            ctx.note(f"skipping unreadable uv tool receipt {receipt}: {exc}")
            continue
        tool = data.get("tool")
        requirements = tool.get("requirements") if isinstance(tool, dict) else None
        if not isinstance(requirements, list) or not requirements:
            ctx.note(f"skipping uv tool receipt without requirements: {receipt}")
            continue
        name_key = normalize_dist_name(child.name)
        own = next((item for item in requirements if _receipt_name(item) == name_key), None)
        if own is None:
            ctx.note(f"skipping uv tool receipt {receipt}: no requirement matches the environment name {child.name!r}")
            continue
        try:
            requirement = _receipt_requirement(own)
        except TaskFailed as exc:
            ctx.note(f"skipping uv tool receipt {receipt}: {exc}")
            continue
        extra = tuple(
            parsed for item in requirements if (parsed := _receipt_name(item)) is not None and parsed != name_key
        )
        installed[name_key] = InstalledTool(requirement=requirement, extra_requirements=extra)
    return installed


def _tool_versions(ctx: TaskContext, uv: str) -> dict[str, str]:
    """Return normalized tool name -> installed version, parsed from ``uv tool list``."""
    result = _require_uv(ctx, [uv, "tool", "list"], failure="could not list uv tools")
    return _parse_tool_list(result.stdout)


def _outdated_tools(ctx: TaskContext, uv: str) -> set[str]:
    """Return normalized names of installed tools with a newer version available."""
    result = _require_uv(ctx, [uv, "tool", "list", "--outdated"], failure="could not list outdated uv tools")
    return set(_parse_tool_list(result.stdout))


def _parse_tool_list(output: str) -> dict[str, str]:
    """Parse ``uv tool list`` output into normalized name -> installed version."""
    versions: dict[str, str] = {}
    for line in output.splitlines():
        match = _TOOL_LINE_RE.match(line.strip())
        if match is not None:
            versions[normalize_dist_name(match.group("name"))] = match.group("version")
    return versions


def _config_target(
    *,
    scope: str,
    project: str | Path | None,
    path: str | Path | None,
) -> tuple[Path, str | None]:
    """Resolve the uv config file and TOML table to edit for the given scope."""
    if path is not None:
        require_path(path, "config path")
        target = expand_path(path, error=TaskFailed)
        # a pyproject.toml holds uv settings in its [tool.uv] table; a uv.toml at the top level
        return target, "tool.uv" if target.name == "pyproject.toml" else None
    if scope == "system":
        return _SYSTEM_CONFIG_PATH, None
    if scope == "user":
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "")
        base = expand_path(xdg_config_home, error=TaskFailed) if xdg_config_home else Path.home() / ".config"
        return base / "uv" / "uv.toml", None
    if project is None:
        raise TaskFailed("project scope requires a project directory")
    require_path(project, "project")
    return expand_path(project, error=TaskFailed) / "pyproject.toml", "tool.uv"
