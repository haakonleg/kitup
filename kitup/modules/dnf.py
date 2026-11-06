"""RPM package, package-group, and repository management."""

import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult
from ..validation import (
    State,
    normalize_strings,
    require_collection,
    require_path,
    require_state,
    require_success,
    require_text,
    require_texts,
    unique_strings,
)


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
    wanted = unique_strings(normalize_strings(names))
    require_collection(wanted, "package names")
    require_texts(wanted, "package name")

    excluded = _normalize_excludes(exclude)
    group_names, package_names = _split_names(wanted, group=group)
    require_state(state, "package")
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


def swap(
    ctx: TaskContext,
    old: str,
    new: str,
    *,
    allowerasing: bool = False,
) -> CallResult:
    """Replace ``old`` with ``new`` unless ``new`` is already installed."""
    require_text(old, "package to remove")
    require_text(new, "package to install")
    if _rpm_installed(ctx, new):
        return ctx.changed(
            action="dnf.swap",
            changed=False,
            summary=f"ensure {new} replaces {old}",
            removed=old,
            installed=new,
        )

    result = CallResult(
        action="dnf.swap",
        changed=True,
        summary=f"swap {old} -> {new}",
        removed=old,
        installed=new,
    )
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
            action="dnf.packages",
            changed=False,
            summary="ensure packages are absent: " + " ".join(_display_names(group_names, package_names)),
            removed=[],
            groups=[],
            packages=[],
        )

    removed = _affected_names(installed_groups, installed_packages)
    result = CallResult(
        action="dnf.packages",
        changed=True,
        summary=" ".join(f"-{name}" for name in removed),
        removed=removed,
        **_package_data(installed_groups, installed_packages),
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
    packages = _query_package_names(
        ctx,
        ["repoquery", "--unneeded", "--quiet", "--queryformat", "%{name}\n"],
        "could not list unneeded packages",
    )
    if not packages:
        return ctx.changed(
            action="dnf.autoremove",
            changed=False,
            summary="ensure no unneeded packages",
            removed=[],
        )

    summary = " ".join(f"-{name}" for name in packages)
    call = CallResult(action="dnf.autoremove", changed=True, summary=summary, removed=packages)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run([_dnf_command(ctx), "autoremove", "-y"], sudo=True)
    return ctx.changed(call)


def copr(ctx: TaskContext, project: str, *, state: State = "present") -> CallResult:
    """Ensure a COPR project is enabled or removed."""
    require_state(state, "COPR")
    require_text(project, "COPR project")

    result = ctx.probe([_dnf_command(ctx), "copr", "list"], sudo=False)
    require_success(result, "could not list COPR repositories")
    configured = _copr_configured(result.stdout, project)

    if configured == (state == "present"):
        desired = "enabled" if state == "present" else "removed"
        return ctx.changed(
            action="dnf.copr",
            changed=False,
            summary=f"ensure COPR {project} is {desired}",
            repository=project,
        )

    if state == "present":
        summary = f"enable COPR {project}"
        command = ["copr", "enable", "-y", project]
    else:
        summary = f"remove COPR {project}"
        command = ["copr", "remove", project]
    call = CallResult(
        action="dnf.copr",
        changed=True,
        summary=summary,
        repository=project,
    )
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
    """Ensure a repository from a .repo file is present or absent."""
    require_state(state, "repository")

    if state == "present":
        if source is None:
            raise TaskFailed("repository source is required when state is present")
        require_path(source, "repository source")
        if name is not None:
            require_text(name, "repository name")
        resolved_source = _resolve_dnf_source(ctx, source)
        filename = _repo_filename(resolved_source, name)
        path = _repository_path(ctx, filename)
        if path.exists():
            return ctx.changed(
                action="dnf.repository",
                changed=False,
                summary=f"ensure repository {filename}",
                repository=resolved_source,
                path=str(path),
            )
        call = CallResult(
            action="dnf.repository",
            changed=True,
            summary=f"add repository {filename}",
            repository=resolved_source,
            path=str(path),
        )
        command = _add_repository_command(ctx, resolved_source, filename)
    else:
        if name is None:
            raise TaskFailed("repository name is required when state is absent")
        require_text(name, "repository name")
        filename = _repo_filename(None, name)
        path = _repository_path(ctx, filename)
        if not path.exists():
            return ctx.changed(
                action="dnf.repository",
                changed=False,
                summary=f"ensure repository {filename} is absent",
                repository=filename,
                path=str(path),
            )
        call = CallResult(
            action="dnf.repository",
            changed=True,
            summary=f"remove repository {filename}",
            repository=filename,
            path=str(path),
        )
        command = ["rm", "-f", str(path)]

    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(command, sudo=True)
    return ctx.changed(call)


def rpm(ctx: TaskContext, sources: str | Path | Iterable[str | Path]) -> CallResult:
    """Install release RPMs from URLs or local paths."""
    if isinstance(sources, (str, Path)):
        sources = [sources]
    validated = unique_strings(sources)
    require_collection(validated, "RPM sources")
    for source in validated:
        require_path(source, "RPM source")

    wanted = unique_strings(_resolve_dnf_source(ctx, source) for source in validated)
    labels = [Path(urlparse(source).path).name or source for source in wanted]
    names = _rpm_source_names(ctx, wanted)
    missing = _missing_packages(ctx, names)
    if not missing:
        return ctx.changed(
            action="dnf.rpm",
            changed=False,
            summary=f"ensure {', '.join(labels)} installed",
            installed=[],
        )
    call = CallResult(
        action="dnf.rpm",
        changed=True,
        summary=f"install {', '.join(labels)}",
        installed=missing,
    )
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run([_dnf_command(ctx), "install", "-y", *wanted], sudo=True)
    return ctx.changed(call)


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
            action="dnf.packages",
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
        action="dnf.packages",
        changed=True,
        summary=" ".join(f"+{name}" for name in added),
        added=added,
        **_package_data(missing_groups, missing_packages),
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


def _resolve_dnf_source(ctx: TaskContext, source: str | Path) -> str:
    text = str(source)
    if urlparse(text).scheme:
        return text
    return str(ctx.resolve_source(text))


def _rpm_source_names(ctx: TaskContext, sources: list[str]) -> list[str]:
    names = _query_package_names(
        ctx,
        ["repoquery", "--queryformat", "%{name}\n", *sources],
        "could not query RPM sources",
    )
    if not names:
        raise TaskFailed("could not determine package names from RPM sources")
    return names


def _query_package_names(ctx: TaskContext, args: list[str], message: str) -> list[str]:
    result = ctx.probe([_dnf_command(ctx), *args], sudo=False)
    require_success(result, message)
    return unique_strings(line.strip() for line in result.stdout.splitlines() if line.strip())


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


def _package_data(groups: list[str], packages: list[str]) -> dict[str, list[str]]:
    if groups and packages:
        return {"groups": groups, "packages": packages}
    return {}


def _missing_group_names(ctx: TaskContext, names: list[str]) -> list[str]:
    installed = set(_installed_groups(ctx, names))
    return [name for name in names if name not in installed]


def _installed_groups(ctx: TaskContext, names: list[str]) -> list[str]:
    if not names:
        return []
    result = ctx.probe(
        [_dnf_command(ctx), "group", "list", "--installed", "--hidden"],
        sudo=False,
    )
    require_success(result, "could not list installed package groups")
    installed = _parse_installed_groups(result.stdout)
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
    command = ctx.memo("dnf-command", lambda: shutil.which("dnf5"))
    if command is None:
        raise TaskFailed("dnf5 is required but was not found")
    return command


def _rpm_installed(ctx: TaskContext, name: str) -> bool:
    """Return whether an RPM package is installed."""
    return ctx.probe(["rpm", "-q", name], sudo=False).returncode == 0


def _installed_packages(ctx: TaskContext, names: list[str]) -> list[str]:
    return [name for name in names if _rpm_installed(ctx, name)]


def _missing_packages(ctx: TaskContext, names: list[str]) -> list[str]:
    return [name for name in names if not _rpm_installed(ctx, name)]
