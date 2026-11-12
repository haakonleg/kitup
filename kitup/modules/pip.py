"""Python package (pip) and pip.conf configuration management."""

import json
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Final, Literal

from .._utils import file_actions
from .._utils import ini as ini_ops
from .._utils.paths import expand_path
from .._utils.pep508 import dist_name_of, normalize_dist_name, parse_requirement
from .._utils.validation import (
    State,
    normalize_extra_args,
    normalize_strings,
    require_bool,
    require_choice,
    require_collection,
    require_path,
    require_state,
    require_texts,
    unique_strings,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult, format_items

type PipState = Literal["present", "absent", "latest"]
type VenvState = Literal["exists", "created", "would-create", "missing"]

_GLOBAL_CONFIG_PATH: Final = Path("/etc/pip.conf")
_PYTHON_CANDIDATES: Final = ("python3", "python")
_CONFIG_SCOPES: Final = ("global", "user", "site")
_PIP_STATES: Final[tuple[PipState, ...]] = ("present", "absent", "latest")
_VENV_PYTHON: Final = Path("bin") / "python"
_VENV_CFG: Final = Path("pyvenv.cfg")


def packages(
    ctx: TaskContext,
    names: Iterable[str] | str,
    *,
    state: PipState = "present",
    venv: str | Path | None = None,
    python: str | None = None,
    extra_args: Iterable[str] | str | None = None,
    sudo: bool = False,
) -> CallResult:
    """Ensure Python packages are installed, up to date, or removed with pip.

    ``names`` may contain version specifiers, extras, VCS or archive URLs,
    and local paths; pip resolves them. ``state="present"`` installs missing
    packages and sets the installed version to match a requested version
    specifier, upgrading or downgrading as needed, but leaves a satisfied
    bare name alone; ``state="latest"`` also updates bare names to the newest
    available version.
    ``state="absent"`` uninstalls the named packages when present; entries
    must be package names — URLs and local paths are rejected. Avoid
    ``extra_args`` that force reinstallation regardless of the installed
    state (``--force-reinstall``, ``--ignore-installed``, ``--target``): the
    post-install check cannot confirm convergence with them. ``venv``
    scopes all operations to a virtual environment, which is created with
    ``python -m venv`` when missing (an existing non-venv directory is
    rejected). Without a venv the system Python is used, selected with
    ``python`` (discovered when ``None``). ``sudo=True`` runs venv creation,
    the changes, and the state checks with privilege, so the checks see the
    same environment as the changes (otherwise packages in the invoking
    user's user site would shadow the system Python's state).
    """
    wanted = unique_strings(normalize_strings(names))
    require_collection(wanted, "package names")
    require_texts(wanted, "package name")
    require_state(state, "pip", allowed=_PIP_STATES)
    require_bool(sudo, "sudo")
    extra = normalize_extra_args(extra_args)
    if state == "absent":
        for name in wanted:
            _require_absent_name(name)
    venv = _normalize_venv(venv)
    venv_state = _prepare_venv(ctx, venv, python, state=state, sudo=sudo)

    pip = _pip_prefix(ctx, python, venv)
    if state == "absent":
        result = _ensure_absent(
            ctx,
            pip=pip,
            wanted=wanted,
            extra=extra,
            sudo=sudo,
            venv=venv,
            venv_state=venv_state,
        )
    else:
        result = _ensure_present(
            ctx,
            pip=pip,
            wanted=wanted,
            extra=extra,
            state=state,
            sudo=sudo,
            venv=venv,
            venv_state=venv_state,
        )
    return ctx.changed(result)


def config(
    ctx: TaskContext,
    option: str,
    value: str | bool | int | Iterable[str] | None = None,  # noqa: FBT001 - positional value matches the module API
    *,
    section: str = "global",
    state: State = "present",
    scope: Literal["global", "user", "site"] = "user",
    venv: str | Path | None = None,
    path: str | Path | None = None,
    sudo: bool | None = None,
) -> CallResult:
    """Set or remove one option in a pip configuration file.

    pip reads INI files at three levels: global (``/etc/pip.conf``), user
    (``$XDG_CONFIG_HOME/pip/pip.conf`` or ``~/.config/pip/pip.conf``), and site
    (``<venv>/pip.conf``). ``scope`` selects the default target file;
    pass an explicit ``path`` to edit any other file. The ``[global]`` section
    holds options shared by all pip commands; per-command sections such as
    ``[install]`` override it. Option names are matched and written in pip's
    canonical form (lowercase, dashes); existing lines in other cases, with
    ``_``/``.``, or with a ``:`` delimiter are matched too. ``value`` follows
    pip's config syntax: a boolean is written as ``true``/``false``, an
    integer in decimal form, and a list of strings as a multi-line appendable
    value with one item per line. A missing file is created. ``sudo`` defaults
    to ``True`` for ``scope="global"`` without an explicit ``path``;
    ``scope="user"`` rejects ``sudo=True`` (it would write a root-owned file
    into the user's home) and cannot run as root at all.
    """
    require_state(state, "config")
    require_choice(scope, _CONFIG_SCOPES, "pip config scope")
    section = ini_ops.require_ini_section(section)
    # pip reads option names case-insensitively with '-'/'_'/'.' equivalent and
    # accepts ':' as a delimiter, so match and write the canonical dashed form
    option = ini_ops.require_ini_option(_canonical_option(option))
    if sudo is not None:
        require_bool(sudo, "config sudo")
    else:
        sudo = path is None and scope == "global"
    if sudo and path is None and scope == "user":
        raise TaskFailed(
            "user-scoped pip config cannot use sudo: it would write a root-owned file into the user's home"
        )
    target = _config_target(ctx, scope=scope, venv=venv, path=path, sudo=sudo)

    set_value: str | list[str] | None = None
    if state == "present":
        if value is None:
            raise TaskFailed("config state 'present' requires a value")
        set_value = _normalize_config_value(value)
    elif value is not None:
        raise TaskFailed("config state 'absent' does not take a value")

    snapshot = file_actions.read_snapshot(ctx, target, sudo=sudo)
    if state == "present" and snapshot.info is None:
        file_actions.ensure_parent(ctx, target, sudo=sudo)

    return ctx.changed(
        file_actions.apply_ini(
            ctx,
            target,
            section=section,
            option=option,
            value=set_value,
            match=None,
            create=True,
            action="pip.config",
            sudo=sudo,
            option_pattern=ini_ops.canonical_option_re(option),
            snapshot=snapshot,
        )
    )


def _ensure_present(
    ctx: TaskContext,
    *,
    pip: list[str],
    wanted: list[str],
    extra: list[str],
    state: PipState,
    sudo: bool,
    venv: Path | None,
    venv_state: VenvState,
) -> CallResult:
    # A missing venv only happens in dry-run; everything would then be
    # installed into the fresh environment.
    upgrade = state == "latest"
    pending = (
        wanted if venv_state == "would-create" else _install_report(ctx, pip, wanted, extra, upgrade=upgrade, sudo=sudo)
    )
    if not pending:
        desired = "up to date" if upgrade else "present"
        return _packages_result(
            changed=False,
            summary=f"ensure packages are {desired}: " + " ".join(wanted),
            wanted=wanted,
            venv=venv,
            added=[],
        )

    prefix = f"create venv {venv}; " if venv_state in ("created", "would-create") else ""
    result = _packages_result(
        changed=True,
        summary=prefix + format_items(pending, sign="+"),
        wanted=wanted,
        venv=venv,
        added=pending,
    )
    if ctx.dry_run:
        return result

    ctx.run([*pip, *_pip_install_args(wanted, extra, upgrade=upgrade, report=False)], sudo=sudo)

    remaining = _install_report(ctx, pip, wanted, extra, upgrade=upgrade, sudo=sudo)
    if remaining:
        raise TaskFailed(f"packages still pending after install: {', '.join(remaining)}")
    return result


def _ensure_absent(
    ctx: TaskContext,
    *,
    pip: list[str],
    wanted: list[str],
    extra: list[str],
    sudo: bool,
    venv: Path | None,
    venv_state: VenvState,
) -> CallResult:
    installed: set[str] = set() if venv_state != "exists" else _installed_names(ctx, pip, sudo=sudo)
    targets: list[str] = []
    for name in wanted:
        dist = dist_name_of(name)
        if normalize_dist_name(dist) in installed:
            targets.append(dist)
    targets = unique_strings(targets)
    if not targets:
        return _packages_result(
            changed=False,
            summary="ensure packages are absent: " + " ".join(wanted),
            wanted=wanted,
            venv=venv,
            removed=[],
        )

    result = _packages_result(
        changed=True,
        summary=format_items(targets, sign="-"),
        wanted=wanted,
        venv=venv,
        removed=targets,
    )
    if ctx.dry_run:
        return result

    ctx.run([*pip, "uninstall", "-y", *extra, *targets], sudo=sudo)
    installed_after = _installed_names(ctx, pip, sudo=sudo)
    remaining = [name for name in targets if normalize_dist_name(name) in installed_after]
    if remaining:
        raise TaskFailed(f"packages still installed after removal: {', '.join(remaining)}")
    return result


def _pip_install_args(names: list[str], extra: list[str], *, upgrade: bool, report: bool) -> list[str]:
    """Build the argv after ``<python> -m pip`` for an install or its dry-run report probe."""
    args = ["install"]
    if report:
        args.extend(["--dry-run", "--quiet", "--report", "-"])
    if upgrade:
        args.append("--upgrade")
    args.extend(extra)
    args.extend(names)
    return args


def _install_report(
    ctx: TaskContext,
    pip: list[str],
    names: list[str],
    extra: list[str],
    *,
    upgrade: bool,
    sudo: bool,
) -> list[str]:
    """Return the distribution names a ``pip install`` would still install."""
    report = _pip_json(
        ctx,
        pip,
        _pip_install_args(names, extra, upgrade=upgrade, report=True),
        "pip install report",
        failure="could not resolve pip requirements (pip >= 23.0 is required for --dry-run --report)",
        sudo=sudo,
        expect=dict,
    )
    if report.get("version") != "1":
        raise TaskFailed(f"unsupported pip install report version: {report.get('version')!r} (pip >= 23.0 required)")
    installs = report.get("install")
    if not isinstance(installs, list):
        raise TaskFailed("unexpected pip install report format (install is not a list)")
    pending: list[str] = []
    for item in installs:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not name:
            raise TaskFailed(f"malformed entry in pip install report: {item!r}")
        pending.append(name)
    return unique_strings(pending)


def _installed_names(ctx: TaskContext, pip: list[str], *, sudo: bool) -> set[str]:
    """Return the PEP 503-normalized names of the packages installed in the environment.

    ``--local`` excludes system-site packages of a ``--system-site-packages``
    venv: those belong to the system Python, not to the venv being managed.
    """
    data = _pip_json(
        ctx,
        pip,
        ["list", "--local", "--format=json"],
        "pip list output",
        failure="could not list installed pip packages",
        sudo=sudo,
        expect=list,
    )
    names: set[str] = set()
    for item in data:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name:
            raise TaskFailed(f"malformed entry in pip list output: {item!r}")
        names.add(normalize_dist_name(name))
    return names


def _pip_json[T](
    ctx: TaskContext,
    pip: list[str],
    args: list[str],
    label: str,
    *,
    failure: str,
    sudo: bool,
    expect: type[T],
) -> T:
    """Run a read-only pip command and return its parsed, shape-checked JSON stdout.

    The probe runs with the same privilege as the changes so that the checked
    state matches the environment the changes act on (with ``sudo=True``, a
    package in the invoking user's user site would otherwise shadow the system
    Python's state). A failing command is re-checked with ``pip --version`` so
    that a missing pip is reported as such, not as a too-old pip.
    """
    command = [*pip, *args]
    result = ctx.probe(command, sudo=sudo)
    if result.returncode != 0:
        if ctx.probe([*pip, "--version"], sudo=sudo).returncode != 0:
            raise TaskFailed(
                f"pip could not run via '{' '.join(pip)}' — is pip installed for this Python? (pip >= 23.0 is required)"
            )
        raise TaskFailed(failure)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TaskFailed(f"could not parse output of {' '.join(command)}") from exc
    if not isinstance(data, expect):
        kind = "object" if expect is dict else "array"
        raise TaskFailed(f"unexpected {label} format (not a JSON {kind})")
    return data


def _pip_prefix(ctx: TaskContext, python: str | None, venv: Path | None) -> list[str]:
    """Return the argv prefix that runs pip in the selected environment."""
    if venv is not None:
        return [str(venv / _VENV_PYTHON), "-m", "pip"]
    return [_python_bin(ctx, python), "-m", "pip"]


def _python_bin(ctx: TaskContext, python: str | None) -> str:
    """Resolve the Python interpreter, discovering the system one when ``None``."""

    def load() -> str | None:
        if python is None:
            for candidate in _PYTHON_CANDIDATES:
                found = shutil.which(candidate)
                if found is not None:
                    return found
            return None
        if os.sep in python or (os.altsep is not None and os.altsep in python) or python.startswith("~"):
            expanded = expand_path(python, error=TaskFailed)
            return str(expanded) if expanded.exists() else None
        return shutil.which(python)

    resolved = ctx.memo(f"pip-python:{python}", load)
    if resolved is None:
        if python is None:
            raise TaskFailed("no Python interpreter found (tried: " + ", ".join(_PYTHON_CANDIDATES) + ")")
        raise TaskFailed(f"Python interpreter not found: {python}")
    return resolved


def _normalize_venv(venv: str | Path | None) -> Path | None:
    if venv is None:
        return None
    require_path(venv, "venv")
    return expand_path(venv, error=TaskFailed)


def _prepare_venv(
    ctx: TaskContext,
    venv: Path | None,
    python: str | None,
    *,
    state: PipState,
    sudo: bool,
) -> VenvState:
    """Create a missing venv unless ``state="absent"``; return the resulting venv state."""
    if venv is None or _is_venv(ctx, venv, sudo=sudo):
        return "exists"
    if file_actions.stat(ctx, venv, sudo=sudo) is not None:
        raise TaskFailed(f"path exists and is not a virtualenv: {venv}")
    if state == "absent":
        return "missing"
    if ctx.dry_run:
        return "would-create"
    ctx.run([_python_bin(ctx, python), "-m", "venv", str(venv)], sudo=sudo)
    if not _is_venv(ctx, venv, sudo=sudo):
        raise TaskFailed(f"venv was not created: {venv}")
    return "created"


def _is_venv(ctx: TaskContext, venv: Path, *, sudo: bool) -> bool:
    """Return whether ``venv`` is a real virtualenv, checked with matching privilege."""
    return (
        file_actions.stat(ctx, venv / _VENV_PYTHON, sudo=sudo) is not None
        and file_actions.stat(ctx, venv / _VENV_CFG, sudo=sudo) is not None
    )


def _packages_result(
    *,
    changed: bool,
    summary: str,
    wanted: list[str],
    venv: Path | None,
    added: list[str] | None = None,
    removed: list[str] | None = None,
) -> CallResult:
    """Build the flat ``pip.packages`` call result for one invocation."""
    data: dict[str, object] = {"packages": wanted}
    if venv is not None:
        data["venv"] = str(venv)
    if added is not None:
        data["added"] = added
    if removed is not None:
        data["removed"] = removed
    return CallResult(action="pip.packages", changed=changed, summary=summary, **data)


def _require_absent_name(name: str) -> None:
    """Reject URL and path entries, which have no removable distribution name."""
    try:
        parse_requirement(name)
    except TaskFailed as exc:
        raise TaskFailed(f"state='absent' supports package names only, not URLs or paths: {name!r}") from exc


def _config_target(
    ctx: TaskContext,
    *,
    scope: str,
    venv: str | Path | None,
    path: str | Path | None,
    sudo: bool,
) -> Path:
    """Resolve the pip config file to edit for the given scope."""
    if path is not None:
        require_path(path, "config path")
        return expand_path(path, error=TaskFailed)
    if scope == "global":
        return _GLOBAL_CONFIG_PATH
    if scope == "user":
        if ctx.sudo.is_root:
            raise TaskFailed("user-scoped pip config cannot run as root")
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "")
        base = expand_path(xdg_config_home, error=TaskFailed) if xdg_config_home else Path.home() / ".config"
        return base / "pip" / "pip.conf"
    if venv is None:
        raise TaskFailed("site scope requires a venv")
    require_path(venv, "venv")
    venv_path = expand_path(venv, error=TaskFailed)
    if not _is_venv(ctx, venv_path, sudo=sudo):
        raise TaskFailed(f"site scope requires an existing virtualenv: {venv_path}")
    return venv_path / "pip.conf"


def _canonical_option(option: str) -> str:
    """Canonicalize a pip config option name the way pip reads it (lowercase, dashes)."""
    return re.sub(r"[-_.]+", "-", option).lower().lstrip("-")


def _normalize_config_value(value: object) -> str | list[str]:
    """Normalize a typed config value to pip's canonical config syntax."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return ini_ops.require_ini_value(value)
    if isinstance(value, Iterable):
        return ini_ops.require_ini_values(value)
    raise TaskFailed(f"unsupported config value type: {type(value).__name__}")
