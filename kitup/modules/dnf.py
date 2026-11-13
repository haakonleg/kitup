"""RPM package, package-group, repository, and dnf.conf configuration management."""

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

from .._utils import file_actions
from .._utils import ini as ini_ops
from .._utils.executables import require_command
from .._utils.paths import expand_path
from .._utils.validation import (
    State,
    normalize_strings,
    require_bool,
    require_collection,
    require_path,
    require_state,
    require_success,
    require_text,
    require_texts,
    unique_strings,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..registry import module
from ..results import CallResult, format_items

module("dnf")


_DEFAULT_CONFIG_PATH: Final = Path("/etc/dnf/dnf.conf")
_MISSING_PACKAGE: Final = re.compile(r"^package (.+) is not installed$")


def packages(
    ctx: TaskContext,
    names: Iterable[str] | str,
    *,
    state: State = "present",
    group: bool = False,
    weak_deps: bool = False,
    exclude: Iterable[str] | str | None = None,
) -> CallResult:
    """Ensure packages or package groups are present or absent.

    Names beginning with ``@`` are treated as package groups.
    """
    require_state(state, "package")
    wanted = unique_strings(normalize_strings(names))
    require_collection(wanted, "package names")
    require_texts(wanted, "package name")

    excluded = _normalize_excludes(exclude)
    group_names, package_names = _split_names(wanted, group=group)
    if state == "present":
        result = _ensure_present(
            ctx,
            group_names=group_names,
            package_names=package_names,
            weak_deps=weak_deps,
            exclude=excluded,
            explicit_group=group,
        )
    else:
        if excluded:
            raise TaskFailed("exclude is only supported when state is 'present'")
        result = _ensure_absent(
            ctx,
            group_names=group_names,
            package_names=package_names,
            explicit_group=group,
        )
    return ctx.changed(result)


def swap(ctx: TaskContext, old: str, new: str, *, allowerasing: bool = False) -> CallResult:
    """Replace ``old`` with ``new`` unless ``new`` is already installed."""
    require_text(old, "package to remove")
    require_text(new, "package to install")
    if _rpm_installed(ctx, new):
        return ctx.changed(changed=False, summary=f"ensure {new} replaces {old}", removed=old, installed=new)

    result = CallResult(changed=True, summary=f"swap {old} -> {new}", removed=old, installed=new)
    if ctx.dry_run:
        return ctx.changed(result)
    command = [_dnf_command(ctx), "swap", "-y", old, new]
    if allowerasing:
        command.append("--allowerasing")
    ctx.run(command, sudo=True)

    if not _rpm_installed(ctx, new):
        raise TaskFailed(f"package still missing after swap: {new}")
    return ctx.changed(result)


def _ensure_absent(
    ctx: TaskContext,
    *,
    group_names: list[str],
    package_names: list[str],
    explicit_group: bool,
) -> CallResult:
    installed_groups = _installed_groups(ctx, group_names)
    installed_packages = _installed_packages(ctx, package_names)
    if not installed_groups and not installed_packages:
        return CallResult(
            changed=False,
            summary="ensure packages are absent: " + " ".join(_display_names(group_names, package_names)),
            removed=[],
            groups=[],
            packages=[],
        )

    removed = _affected_names(installed_groups, installed_packages)
    result = CallResult(
        changed=True,
        summary=format_items(removed, sign="-"),
        removed=removed,
        groups=installed_groups,
        packages=installed_packages,
    )
    if ctx.dry_run:
        return result

    specs = _display_names(installed_groups, installed_packages)
    command: list[str]
    if explicit_group:
        command = [_dnf_command(ctx), "group", "remove", "-y", *installed_groups]
    else:
        command = [_dnf_command(ctx), "remove", "-y", *specs]
    ctx.run(command, sudo=True)

    remaining_groups = _installed_groups(ctx, installed_groups)
    remaining_packages = _installed_packages(ctx, installed_packages)
    remaining = _display_names(remaining_groups, remaining_packages)
    if remaining:
        raise TaskFailed(f"packages still installed after removal: {', '.join(remaining)}")
    return result


def autoremove(ctx: TaskContext) -> CallResult:
    """Remove unused package dependencies."""
    unneeded = _query_package_names(
        ctx,
        ["repoquery", "--unneeded", "--quiet", "--queryformat", "%{name}\n"],
        "could not list unneeded packages",
    )
    if not unneeded:
        return ctx.changed(changed=False, summary="ensure no unneeded packages", removed=[])

    summary = format_items(unneeded, sign="-")
    call = CallResult(changed=True, summary=summary, removed=unneeded)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run([_dnf_command(ctx), "autoremove", "-y"], sudo=True)
    return ctx.changed(call)


def copr(ctx: TaskContext, project: str, *, state: State = "present") -> CallResult:
    """Ensure a COPR project is enabled or removed."""
    require_state(state, "COPR")
    require_text(project, "COPR project")

    configured = _copr_configured(_dnf_probe(ctx, ["copr", "list"], "could not list COPR repositories"), project)

    if configured == (state == "present"):
        desired = "enabled" if state == "present" else "removed"
        return ctx.changed(changed=False, summary=f"ensure COPR {project} is {desired}", repository=project)

    if state == "present":
        summary = f"enable COPR {project}"
        command = ["copr", "enable", "-y", project]
    else:
        summary = f"remove COPR {project}"
        command = ["copr", "remove", project]
    call = CallResult(changed=True, summary=summary, repository=project)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run([_dnf_command(ctx), *command], sudo=True)
    return ctx.changed(call)


def _copr_configured(output: str, project: str) -> bool:
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        repo = parts[0]
        if repo == project or repo.endswith("/" + project):
            return True
    return False


def repository(
    ctx: TaskContext,
    source: str | Path | None = None,
    *,
    state: State = "present",
    name: str | None = None,
) -> CallResult:
    """Ensure a repository from a .repo file is present or absent.

    URLs are passed through, while relative local paths resolve from the
    current working directory.
    """
    require_state(state, "repository")

    if state == "present":
        if source is None:
            raise TaskFailed("repository source is required when state is present")
        require_path(source, "repository source")
        if name is not None:
            require_text(name, "repository name")
        resolved_source = _resolve_dnf_source(source)
        filename = _repo_filename(resolved_source, name)
        path = _repository_path(ctx, filename)
        if path.exists():
            call = CallResult(
                changed=False, summary=f"ensure repository {filename}", repository=resolved_source, path=str(path)
            )
            return ctx.changed(call)
        call = CallResult(
            changed=True, summary=f"add repository {filename}", repository=resolved_source, path=str(path)
        )
        command = _add_repository_command(ctx, resolved_source, filename)
    else:
        if name is None:
            raise TaskFailed("repository name is required when state is absent")
        require_text(name, "repository name")
        filename = _repo_filename(None, name)
        path = _repository_path(ctx, filename)
        if not path.exists():
            call = CallResult(
                changed=False, summary=f"ensure repository {filename} is absent", repository=filename, path=str(path)
            )
            return ctx.changed(call)
        call = CallResult(changed=True, summary=f"remove repository {filename}", repository=filename, path=str(path))
        command = ["rm", "-f", str(path)]

    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(command, sudo=True)
    return ctx.changed(call)


def rpm(ctx: TaskContext, sources: str | Path | Iterable[str | Path]) -> CallResult:
    """Install release RPMs from URLs or local paths.

    URLs are passed through, while relative local paths resolve from the
    current working directory.
    """
    if isinstance(sources, (str, Path)):
        sources = [sources]
    validated = unique_strings(sources)
    require_collection(validated, "RPM sources")
    for source in validated:
        require_path(source, "RPM source")

    wanted = unique_strings(_resolve_dnf_source(source) for source in validated)
    labels = [Path(urlparse(source).path).name or source for source in wanted]
    names = _rpm_source_names(ctx, wanted)
    missing = _missing_packages(ctx, names)
    if not missing:
        return ctx.changed(changed=False, summary=f"ensure {', '.join(labels)} installed", installed=[])
    call = CallResult(changed=True, summary=f"install {', '.join(labels)}", installed=missing)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run([_dnf_command(ctx), "install", "-y", *wanted], sudo=True)
    return ctx.changed(call)


def config(
    ctx: TaskContext,
    option: str,
    value: str | bool | int | Iterable[str] | None = None,  # noqa: FBT001 - positional value matches the module API
    *,
    section: str = "main",
    state: State = "present",
    path: str | Path | None = None,
    sudo: bool = True,
) -> CallResult:
    """Set or remove one option in a ``dnf.conf``-style INI file.

    Options are written canonically as ``option = value`` in ``[section]``.
    ``path`` defaults to ``/etc/dnf/dnf.conf``; pass an explicit path with
    ``sudo=False`` for user-scoped config files. A missing file is created.
    ``value`` may be a string, a boolean (written as ``True``/``False``), an
    integer, or an iterable of strings (written as a comma-separated list).
    """
    require_state(state, "config")
    require_bool(sudo, "config sudo")
    section = ini_ops.require_ini_section(section)
    option = ini_ops.require_ini_option(option)
    if path is not None:
        require_path(path, "config path")

    set_value: str | None = None
    if state == "present":
        if value is None:
            raise TaskFailed("config state 'present' requires a value")
        set_value = _normalize_config_value(value)
    elif value is not None:
        raise TaskFailed("config state 'absent' does not take a value")

    target = expand_path(path, error=TaskFailed) if path is not None else _DEFAULT_CONFIG_PATH
    return ctx.changed(
        file_actions.apply_ini(
            ctx,
            target,
            section=section,
            option=option,
            value=set_value,
            match=None,
            create=True,
            sudo=sudo,
        )
    )


def _ensure_present(
    ctx: TaskContext,
    *,
    group_names: list[str],
    package_names: list[str],
    weak_deps: bool,
    exclude: list[str],
    explicit_group: bool,
) -> CallResult:
    missing_groups = _missing_group_names(ctx, group_names)
    missing_packages = _missing_packages(ctx, package_names)
    if not missing_groups and not missing_packages:
        return CallResult(
            changed=False,
            summary="ensure packages are present: " + " ".join(_display_names(group_names, package_names)),
            added=[],
            groups=[],
            packages=[],
        )

    added = _affected_names(missing_groups, missing_packages)
    options: list[str] = []
    if not weak_deps:
        options.append("--setopt=install_weak_deps=False")
    options.extend(f"--exclude={item}" for item in exclude)

    specs = _display_names(missing_groups, missing_packages)
    command: list[str]
    if explicit_group:
        command = [
            _dnf_command(ctx),
            "group",
            "install",
            "-y",
            *options,
            *missing_groups,
        ]
    else:
        command = [_dnf_command(ctx), "install", "-y", *options, *specs]

    result = CallResult(
        changed=True,
        summary=format_items(added, sign="+"),
        added=added,
        groups=missing_groups,
        packages=missing_packages,
    )
    if ctx.dry_run:
        return result
    ctx.run(command, sudo=True)

    remaining_groups = _missing_group_names(ctx, missing_groups)
    remaining_packages = _missing_packages(ctx, missing_packages)
    remaining = _display_names(remaining_groups, remaining_packages)
    if remaining:
        raise TaskFailed(f"packages still missing after install: {', '.join(remaining)}")
    return result


def _resolve_dnf_source(source: str | Path) -> str:
    """Return a URL unchanged or expand a local path from the working directory."""
    text = str(source)
    if urlparse(text).scheme:
        return text
    return str(expand_path(text, error=TaskFailed))


def _rpm_source_names(ctx: TaskContext, sources: list[str]) -> list[str]:
    names = _query_package_names(
        ctx,
        ["repoquery", "--queryformat", "%{name}\n", *sources],
        "could not query RPM sources",
    )
    if not names:
        raise TaskFailed("could not determine package names from RPM sources")
    return names


def _dnf_probe(ctx: TaskContext, args: list[str], message: str) -> str:
    """Run a read-only dnf query and return its stdout."""
    result = ctx.probe([_dnf_command(ctx), *args], sudo=False)
    require_success(result, message)
    return result.stdout


def _query_package_names(ctx: TaskContext, args: list[str], message: str) -> list[str]:
    output = _dnf_probe(ctx, args, message)
    return unique_strings(line.strip() for line in output.splitlines() if line.strip())


def _repo_filename(source: str | Path | None, name: str | None) -> str:
    filename = Path(name).name if name else Path(urlparse(str(source or "")).path).name
    if not filename:
        raise TaskFailed(f"cannot determine repository filename from {source!r}")
    return filename if filename.endswith(".repo") else f"{filename}.repo"


def _repository_path(ctx: TaskContext, filename: str) -> Path:
    return ctx.memo("dnf-reposdir", lambda: _reposdir(ctx)) / filename


def _reposdir(ctx: TaskContext) -> Path:
    result = ctx.probe([_dnf_command(ctx), "--dump-main-config"], sudo=False)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "reposdir":
                first = value.split(",", 1)[0].strip()
                if first:
                    return Path(first)

    return Path("/etc/yum.repos.d")


def _add_repository_command(ctx: TaskContext, source: str | Path, filename: str) -> list[str]:
    return [
        _dnf_command(ctx),
        "config-manager",
        "addrepo",
        f"--from-repofile={source}",
        f"--save-filename={filename}",
    ]


def _normalize_excludes(exclude: Iterable[str] | str | None) -> list[str]:
    if exclude is None:
        return []
    values = unique_strings(normalize_strings(exclude))
    require_texts(values, "excluded package")
    return values


def _normalize_config_value(value: object) -> str:
    """Normalize a typed config value to its canonical DNF text form."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return ini_ops.require_ini_value(value)
    if isinstance(value, Iterable):
        items = ini_ops.require_ini_value_list(value)
        if not all(isinstance(item, str) for item in items):
            raise TaskFailed("config value list items must be strings")
        require_collection(items, "config value")
        require_texts(items, "config value item")
        return ", ".join(unique_strings(items))
    raise TaskFailed(f"unsupported config value type: {type(value).__name__}")


def _split_names(names: list[str], *, group: bool) -> tuple[list[str], list[str]]:
    group_names: list[str] = []
    package_names: list[str] = []
    for name in names:
        if group or name.startswith("@"):
            candidate = name.removeprefix("@")
            require_text(candidate, "group name")
            group_names.append(candidate)
        else:
            package_names.append(name)
    return unique_strings(group_names), unique_strings(package_names)


def _display_names(groups: list[str], packages: list[str]) -> list[str]:
    return [f"@{name}" for name in groups] + list(packages)


def _affected_names(groups: list[str], packages: list[str]) -> list[str]:
    if groups and packages:
        return _display_names(groups, packages)
    return groups or packages


def _missing_group_names(ctx: TaskContext, names: list[str]) -> list[str]:
    installed = set(_installed_groups(ctx, names))
    return [name for name in names if name not in installed]


def _installed_groups(ctx: TaskContext, names: list[str]) -> list[str]:
    if not names:
        return []
    output = _dnf_probe(ctx, ["group", "list", "--installed", "--hidden"], "could not list installed package groups")
    installed = _parse_installed_groups(output)
    return [name for name in names if name in installed]


def _parse_installed_groups(output: str) -> set[str]:
    groups: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("ID ", "Groups:")):
            continue
        match = re.match(r"^(\S+)\s+(.*?)\s+(yes|no)$", stripped)
        if match:
            groups.add(match.group(1))
            groups.add(match.group(2).strip())
        else:
            groups.add(stripped)
    return groups


def _dnf_command(ctx: TaskContext) -> str:
    """Return the dnf5 executable or fail clearly when it is unavailable."""
    return require_command(ctx, "dnf5")


def _rpm_installed(ctx: TaskContext, name: str) -> bool:
    """Return whether an RPM package is installed."""
    return name in _query_installed_packages(ctx, [name])


def _installed_packages(ctx: TaskContext, names: list[str]) -> list[str]:
    installed = _query_installed_packages(ctx, names)
    return [name for name in names if name in installed]


def _missing_packages(ctx: TaskContext, names: list[str]) -> list[str]:
    installed = _query_installed_packages(ctx, names)
    return [name for name in names if name not in installed]


def _query_installed_packages(ctx: TaskContext, names: list[str]) -> set[str]:
    """Return the subset of ``names`` that is installed, using one ``rpm`` call."""
    if not names:
        return set()
    result = ctx.probe(["rpm", "-q", "--queryformat", "%{NAME}\n", *names], sudo=False)
    missing: set[str] = set()
    for line in (*result.stdout.splitlines(), *result.stderr.splitlines()):
        match = _MISSING_PACKAGE.match(line.strip())
        if match:
            missing.add(match.group(1))
    if result.returncode != 0 and not missing:
        detail = result.stderr.strip() or result.stdout.strip()
        message = f"could not query installed packages: {detail}" if detail else "could not query installed packages"
        raise TaskFailed(message)
    return set(names) - missing
