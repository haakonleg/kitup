"""RPM package, package-group, and repository management."""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_collection, require_path, require_text


def packages(
    ctx: TaskContext,
    names: Iterable[str] | str,
    *,
    state: str = "present",
    group: bool = False,
    weak_deps: bool = False,
) -> None:
    """Ensure packages or package groups are present or absent."""
    if isinstance(names, str):
        names = [names]
    wanted = _dedupe(names)
    require_collection(wanted, "package names")
    for name in wanted:
        require_text(name, "package name")
    if state == "present":
        _ensure_present(ctx, wanted, group=group, weak_deps=weak_deps)
    elif state == "absent":
        _ensure_absent(ctx, wanted, group=group)
    else:
        raise TaskFailed(f"invalid package state: {state!r}")


def _ensure_absent(ctx: TaskContext, names: list[str], *, group: bool) -> None:
    installed = _installed(ctx, names, group)
    if not installed:
        return

    summary = " ".join(f"-{name}" for name in installed)
    ctx.changed({"removed": installed, "group": group}, summary)
    if group:
        command = [_dnf_command(ctx), "group", "remove", "-y", *installed]
    else:
        command = [_dnf_command(ctx), "remove", "-y", *installed]
    ctx.run(command, sudo=True, record=False)

    if ctx.dry_run:
        return
    remaining = _installed(ctx, installed, group)
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
    if result.returncode != 0:
        raise TaskFailed(
            "could not list unneeded packages",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    packages = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not packages:
        return

    summary = " ".join(f"-{name}" for name in packages)
    ctx.changed({"removed": packages}, summary)
    ctx.run([_dnf_command(ctx), "autoremove", "-y"], sudo=True, record=False)


def copr(ctx: TaskContext, project: str, *, state: str = "present") -> None:
    """Ensure a COPR project is enabled or removed."""
    if state not in {"present", "absent"}:
        raise TaskFailed(f"invalid COPR state: {state!r}")
    require_text(project, "COPR project")

    result = ctx.probe([_dnf_command(ctx), "copr", "list"], sudo=False)
    if result.returncode != 0:
        raise TaskFailed(
            "could not list COPR repositories",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )
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
    if state not in {"present", "absent"}:
        raise TaskFailed(f"invalid repository state: {state!r}")

    if state == "present":
        if source is None:
            raise TaskFailed("repository source is required when state is present")
        require_path(source, "repository source")
        if name is not None:
            require_text(name, "repository name")
        filename = _repo_filename(source, name)
        path = _repository_path(ctx, filename)
        if path.exists():
            return
        ctx.changed(
            {"repository": str(source), "path": str(path)},
            f"add repository {filename}",
        )
        ctx.run(_add_repository_command(ctx, source, filename), sudo=True, record=False)
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
    wanted = _dedupe(sources)
    require_collection(wanted, "RPM sources")
    for source in wanted:
        require_path(source, "RPM source")

    labels = [Path(urlparse(source).path).name or source for source in wanted]
    ctx.changed({"sources": wanted}, f"install {', '.join(labels)}")
    ctx.run([_dnf_command(ctx), "install", "-y", *wanted], sudo=True, record=False)


def _ensure_present(
    ctx: TaskContext,
    names: list[str],
    *,
    group: bool,
    weak_deps: bool,
) -> None:
    installed = set(_installed(ctx, names, group))
    missing = [name for name in names if name not in installed]
    if not missing:
        return

    summary = " ".join(f"+{name}" for name in missing)
    ctx.changed({"added": missing, "group": group}, summary)

    if group:
        command = [_dnf_command(ctx), "group", "install", "-y", *missing]
    else:
        command = [_dnf_command(ctx), "install", "-y"]
        if not weak_deps:
            command.append("--setopt=install_weak_deps=False")
        command.extend(missing)
    ctx.run(command, sudo=True, record=False)

    if ctx.dry_run:
        return
    installed = set(_installed(ctx, missing, group))
    remaining = [name for name in missing if name not in installed]
    if remaining:
        raise TaskFailed(f"packages still missing after install: {', '.join(remaining)}")


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


def _installed(ctx: TaskContext, names: list[str], group: bool) -> list[str]:
    if group:
        return _installed_groups(ctx, names)
    return [name for name in names if _rpm_installed(ctx, name)]


def _installed_groups(ctx: TaskContext, names: list[str]) -> list[str]:
    result = ctx.probe([_dnf_command(ctx), "group", "list", "--installed"], sudo=False)
    if result.returncode != 0:
        raise TaskFailed(
            "could not list installed package groups",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )
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


def _dedupe(names: Iterable[str | Path]) -> list[str]:
    return list(dict.fromkeys(str(name) for name in names))


def _dnf_command(ctx: TaskContext) -> str:
    """Return the dnf5 executable or fail clearly when it is unavailable."""
    command = ctx.memo("dnf-command", lambda: shutil.which("dnf5"))
    if command is None:
        raise TaskFailed("dnf5 is required but was not found")
    return command


def _rpm_installed(ctx: TaskContext, name: str) -> bool:
    """Return whether an RPM package is installed."""
    return ctx.probe(["rpm", "-q", name], sudo=False).returncode == 0
