"""Flatpak remote repository and package management."""

from __future__ import annotations

import shutil
from collections.abc import Iterable

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import (
    normalize_strings,
    require_collection,
    require_scope,
    require_state,
    require_success,
    require_text,
    require_texts,
    unique_strings,
)


def remote(
    ctx: TaskContext,
    name: str,
    url: str | None = None,
    *,
    state: str = "present",
    scope: str = "system",
    gpg_verify: bool = True,
) -> None:
    """Ensure a Flatpak remote repository is present or absent."""
    require_state(state, "flatpak")
    flag = _scope_flag(ctx, scope)
    require_text(name, "flatpak remote name")

    if state == "present":
        if url is None:
            raise TaskFailed("flatpak remote URL is required when state is present")
        require_text(url, "flatpak remote URL")
        if name in _remote_names(ctx, flag):
            return

        ctx.changed(
            {"remote": name, "url": url, "scope": scope},
            f"add remote {name}",
        )
        command = [
            _flatpak(ctx),
            "remote-add",
            "--if-not-exists",
            flag,
        ]
        if not gpg_verify:
            command.append("--no-gpg-verify")
        command.extend([name, url])
        ctx.run(command, sudo=scope == "system", record=False)
        return

    if name not in _remote_names(ctx, flag):
        return

    ctx.changed(
        {"remote": name, "scope": scope},
        f"remove remote {name}",
    )
    ctx.run(
        [_flatpak(ctx), "remote-delete", flag, name],
        sudo=scope == "system",
        record=False,
    )


def packages(
    ctx: TaskContext,
    names: Iterable[str] | str,
    *,
    state: str = "present",
    scope: str = "system",
    remote: str | None = None,
) -> None:
    """Ensure Flatpak applications or runtimes are present or absent."""
    require_state(state, "flatpak")
    flag = _scope_flag(ctx, scope)
    if remote is not None:
        require_text(remote, "flatpak remote")
        if state == "absent":
            raise TaskFailed("remote is only supported when state is 'present'")

    wanted = unique_strings(normalize_strings(names))
    require_collection(wanted, "flatpak refs")
    require_texts(wanted, "flatpak ref")

    installed = _installed_refs(ctx, flag)

    if state == "present":
        missing = [name for name in wanted if not _matching_refs(name, installed)]
        if not missing:
            return
        ctx.changed(
            {"added": missing, "scope": scope},
            " ".join(f"+{name}" for name in missing),
        )
        command = [
            _flatpak(ctx),
            "install",
            "-y",
            "--noninteractive",
            flag,
        ]
        if remote is not None:
            command.append(remote)
        command.extend(missing)
        ctx.run(command, sudo=scope == "system", record=False)
        if ctx.dry_run:
            return
        remaining_installed = _installed_refs(ctx, flag)
        remaining = [
            name for name in missing if not _matching_refs(name, remaining_installed)
        ]
        if remaining:
            raise TaskFailed(
                f"flatpak refs still missing after install: {', '.join(remaining)}"
            )
        return

    present = [name for name in wanted if _matching_refs(name, installed)]
    if not present:
        return
    targets = unique_strings(
        ref for name in present for ref in _matching_refs(name, installed)
    )
    ctx.changed(
        {"removed": targets, "scope": scope},
        " ".join(f"-{name}" for name in targets),
    )
    ctx.run(
        [_flatpak(ctx), "uninstall", "-y", "--noninteractive", flag, *targets],
        sudo=scope == "system",
        record=False,
    )
    if ctx.dry_run:
        return
    remaining_installed = _installed_refs(ctx, flag)
    remaining = [
        name for name in present if _matching_refs(name, remaining_installed)
    ]
    if remaining:
        raise TaskFailed(
            f"flatpak refs still installed after removal: {', '.join(remaining)}"
        )


def _scope_flag(ctx: TaskContext, scope: str) -> str:
    require_scope(scope, "flatpak", is_root=ctx.sudo.is_root)
    return "--system" if scope == "system" else "--user"


def _flatpak(ctx: TaskContext) -> str:
    command = ctx.memo("flatpak-command", lambda: shutil.which("flatpak"))
    if command is None:
        raise TaskFailed("flatpak is required but was not found")
    return command


def _remote_names(ctx: TaskContext, flag: str) -> list[str]:
    result = ctx.probe(
        [_flatpak(ctx), "remotes", flag, "--columns=name"],
        sudo=False,
    )
    require_success(result, "could not list flatpak remotes")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _installed_refs(ctx: TaskContext, flag: str) -> list[tuple[str, str]]:
    result = ctx.probe(
        [_flatpak(ctx), "list", flag, "--columns=application,ref"],
        sudo=False,
    )
    require_success(result, "could not list installed flatpak refs")

    installed: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        application, separator, ref = line.partition("\t")
        if not separator:
            raise TaskFailed(
                "could not parse flatpak list output",
                command=result.args,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        installed.append((application, ref))
    return installed


def _matching_refs(name: str, installed: list[tuple[str, str]]) -> list[str]:
    if "/" in name:
        wanted = _strip_kind(name)
        return [ref for _, ref in installed if ref == wanted]
    return [ref for application, ref in installed if application == name]


def _strip_kind(ref: str) -> str:
    for prefix in ("app/", "runtime/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref
