"""Flatpak remote repository and package management."""

from __future__ import annotations

import shutil
from collections.abc import Iterable

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_collection, require_text

_STATES = {"present", "absent"}


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
    _require_state(state)
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
    _require_state(state)
    flag = _scope_flag(ctx, scope)
    if remote is not None:
        require_text(remote, "flatpak remote")
        if state == "absent":
            raise TaskFailed("remote is only supported when state is 'present'")

    wanted = _dedupe([names] if isinstance(names, str) else names)
    require_collection(wanted, "flatpak refs")
    for name in wanted:
        require_text(name, "flatpak ref")

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
    targets = _dedupe(
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


def _require_state(state: str) -> None:
    if state not in _STATES:
        raise TaskFailed(f"invalid flatpak state: {state!r}")


def _scope_flag(ctx: TaskContext, scope: str) -> str:
    if scope == "system":
        return "--system"
    if scope == "user":
        if ctx.sudo.is_root:
            raise TaskFailed("user-scoped flatpak cannot run as root")
        return "--user"
    raise TaskFailed(f"invalid flatpak scope: {scope!r}")


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
    if result.returncode != 0:
        raise TaskFailed(
            "could not list flatpak remotes",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _installed_refs(ctx: TaskContext, flag: str) -> list[tuple[str, str]]:
    result = ctx.probe(
        [_flatpak(ctx), "list", flag, "--columns=application,ref"],
        sudo=False,
    )
    if result.returncode != 0:
        raise TaskFailed(
            "could not list installed flatpak refs",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )

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


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))
