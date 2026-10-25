"""RPM package, package-group, and repository management."""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import (
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
    state: str = "present",
    group: bool = False,
    weak_deps: bool = False,
    exclude: Iterable[str] | str | None = None,
) -> None:
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
        _ensure_present(
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
        _ensure_absent(
            ctx,
            group_names=group_names,
            package_names=package_names,
            explicit_group=group,
        )


def swap(
    ctx: TaskContext,
    old: str,
    new: str,
    *,
    allowerasing: bool = False,
) -> None:
    """Replace ``old`` with ``new`` unless ``new`` is already installed."""
    require_text(old, "package to remove")
    require_text(new, "package to install")
    if _rpm_installed(ctx, new):
        return

    ctx.changed(
        {"removed": old, "installed": new},
        f"swap {old} -> {new}",
    )
    command = [_dnf_command(ctx), "swap", "-y", old, new]
    if allowerasing:
        command.append("--allowerasing")
    ctx.run(command, sudo=True, record=False)

    if ctx.dry_run:
        return
    if not _rpm_installed(ctx, new):
        raise TaskFailed(f"package still missing after swap: {new}")


def _ensure_absent(
    ctx: TaskContext,
    *,
    group_names: list[str],
    package_names: list[str],
    explicit_group: bool,
) -> None:
    installed_groups = _installed_groups(ctx, group_names)
    installed_packages = [name for name in package_names if _rpm_installed(ctx, name)]
    if not installed_groups and not installed_packages:
        return

    if installed_groups and installed_packages:
        removed = _display_names(installed_groups, installed_packages)
        data: dict[str, Any] = {
            "removed": removed,
            "group": False,
            "groups": installed_groups,
            "packages": installed_packages,
        }
    elif installed_groups:
        removed = installed_groups
        data = {"removed": removed, "group": True}
    else:
        removed = installed_packages
        data = {"removed": removed, "group": False}
    ctx.changed(data, " ".join(f"-{name}" for name in removed))

    specs = _display_names(installed_groups, installed_packages)
    command: list[str]
    if explicit_group:
        command = [_dnf_command(ctx), "group", "remove", "-y", *installed_groups]
    else:
        command = [_dnf_command(ctx), "remove", "-y", *specs]
    ctx.run(command, sudo=True, record=False)

    if ctx.dry_run:
        return
    remaining_groups = _installed_groups(ctx, installed_groups)
    remaining_packages = [name for name in installed_packages if _rpm_installed(ctx, name)]
    remaining = _display_names(remaining_groups, remaining_packages)
    if remaining:
        raise TaskFailed(f"packages still installed after removal: {', '.join(remaining)}")


def autoremove(ctx: TaskContext) -> None:
    """Remove unused package dependencies."""
    result = ctx.probe(
        [
            _dnf_command(ctx),
            "repoquery",
            "--unneeded",
            "--quiet",
            "--queryformat",
            "%{name}\n",
        ],
        sudo=False,
    )
    require_success(result, "could not list unneeded packages")

    packages = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not packages:
        return

    summary = " ".join(f"-{name}" for name in packages)
    ctx.changed({"removed": packages}, summary)
    ctx.run([_dnf_command(ctx), "autoremove", "-y"], sudo=True, record=False)


def copr(ctx: TaskContext, project: str, *, state: str = "present") -> None:
    """Ensure a COPR project is enabled or removed."""
    require_state(state, "COPR")
    require_text(project, "COPR project")

    result = ctx.probe([_dnf_command(ctx), "copr", "list"], sudo=False)
    require_success(result, "could not list COPR repositories")
    configured = _copr_configured(result.stdout, project)

    if state == "present":
        if configured:
            return
        ctx.changed({"repository": project, "type": "copr"}, f"enable COPR {project}")
        ctx.run(
            [_dnf_command(ctx), "copr", "enable", "-y", project],
            sudo=True,
            record=False,
        )
        return

    if not configured:
        return
    ctx.changed({"repository": project, "type": "copr"}, f"remove COPR {project}")
    ctx.run(
        [_dnf_command(ctx), "copr", "remove", project],
        sudo=True,
        record=False,
    )


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
    state: str = "present",
    name: str | None = None,
) -> None:
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
            return
        ctx.changed(
            {"repository": resolved_source, "path": str(path)},
            f"add repository {filename}",
        )
        ctx.run(
            _add_repository_command(ctx, resolved_source, filename),
            sudo=True,
            record=False,
        )
        return

    if name is None:
        raise TaskFailed("repository name is required when state is absent")
    require_text(name, "repository name")
    filename = _repo_filename(None, name)
    path = _repository_path(ctx, filename)
    if not path.exists():
        return
    ctx.changed(
        {"repository": filename, "path": str(path)},
        f"remove repository {filename}",
    )
    ctx.run(["rm", "-f", str(path)], sudo=True, record=False)


def rpm(ctx: TaskContext, sources: str | Path | Iterable[str | Path]) -> None:
    """Install release RPMs from URLs or local paths."""
    if isinstance(sources, (str, Path)):
        sources = [sources]
    validated = unique_strings(sources)
    require_collection(validated, "RPM sources")
    for source in validated:
        require_path(source, "RPM source")

    wanted = unique_strings(_resolve_dnf_source(ctx, source) for source in validated)
    labels = [Path(urlparse(source).path).name or source for source in wanted]
    ctx.changed({"sources": wanted}, f"install {', '.join(labels)}")
    ctx.run([_dnf_command(ctx), "install", "-y", *wanted], sudo=True, record=False)


def _ensure_present(
    ctx: TaskContext,
    *,
    group_names: list[str],
    package_names: list[str],
    weak_deps: bool,
    exclude: list[str],
    explicit_group: bool,
) -> None:
    missing_groups = _missing_group_names(ctx, group_names)
    missing_packages = [name for name in package_names if not _rpm_installed(ctx, name)]
    if not missing_groups and not missing_packages:
        return

    if missing_groups and missing_packages:
        added = _display_names(missing_groups, missing_packages)
        data: dict[str, Any] = {
            "added": added,
            "group": False,
            "groups": missing_groups,
            "packages": missing_packages,
        }
    elif missing_groups:
        added = missing_groups
        data = {"added": added, "group": True}
    else:
        added = missing_packages
        data = {"added": added, "group": False}

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

    ctx.changed(data, " ".join(f"+{name}" for name in added))
    ctx.run(command, sudo=True, record=False)

    if ctx.dry_run:
        return
    remaining_groups = _missing_group_names(ctx, missing_groups)
    remaining_packages = [name for name in missing_packages if not _rpm_installed(ctx, name)]
    remaining = _display_names(remaining_groups, remaining_packages)
    if remaining:
        raise TaskFailed(f"packages still missing after install: {', '.join(remaining)}")


def _resolve_dnf_source(ctx: TaskContext, source: str | Path) -> str:
    text = str(source)
    if urlparse(text).scheme:
        return text
    return str(ctx.resolve_source(text))


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
