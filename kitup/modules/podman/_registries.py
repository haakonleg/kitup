"""Podman registry drop-ins and registry authentication."""

from collections.abc import Iterable, Sequence
from pathlib import Path

from ..._utils import file_ops
from ...context import TaskContext
from ...errors import TaskFailed
from ...validation import (
    State,
    normalize_strings,
    require_bool,
    require_mode,
    require_path,
    require_scope,
    require_state,
    require_text,
    require_texts,
    unique_strings,
)
from ._common import (
    default_dir,
    podman,
    require_object_name,
    toml_string,
)


def registry(
    ctx: TaskContext,
    name: str,
    *,
    state: State = "present",
    location: str | None = None,
    prefix: str | None = None,
    insecure: bool = False,
    blocked: bool = False,
    mirrors: str | Iterable[str] = (),
    scope: str = "system",
    path: str | Path | None = None,
    mode: int | None = 0o644,
) -> None:
    """Ensure a ``registries.conf.d`` drop-in exists or is absent.

    The parent directory must already exist; create it with
    :func:`kitup.modules.files.directory` first.
    """
    require_scope(scope, "podman", is_root=ctx.sudo.is_root)
    require_state(state, "registry")
    require_object_name(name, "registry name")
    require_bool(insecure, "registry insecure")
    require_bool(blocked, "registry blocked")
    require_mode(mode)

    sudo = scope == "system"
    target = _registry_path(name, scope=scope, path=path)
    if state == "absent":
        info = file_ops.stat(ctx, target, sudo=sudo)
        file_ops.remove(
            ctx,
            target,
            info,
            force=False,
            sudo=sudo,
            summary=f"remove registry {name} from {target}",
            data={"path": str(target), "registry": name, "action": "registry"},
            allow_directory=False,
        )
        return

    if location is None or not location.strip():
        raise TaskFailed("registry location is required when state is present")
    if prefix is not None:
        require_text(prefix, "registry prefix")
    wanted_mirrors = unique_strings(normalize_strings(mirrors))
    require_texts(wanted_mirrors, "registry mirror")

    content = _registry_document(
        location,
        prefix=prefix,
        insecure=insecure,
        blocked=blocked,
        mirrors=wanted_mirrors,
    )
    file_ops.apply_bytes(
        ctx,
        target,
        content.encode(),
        mode=mode,
        owner=None,
        group=None,
        summary=f"write registry {name} to {target}",
        data={"path": str(target), "registry": name, "action": "registry"},
        sudo=sudo,
    )


def login(
    ctx: TaskContext,
    registry: str,
    *,
    username: str,
    password: str,
    authfile: str | Path | None = None,
    tls_verify: bool | None = None,
    force: bool = False,
    sudo: bool = False,
) -> None:
    """Log in to a container registry using a password from stdin."""
    require_text(registry, "registry")
    require_text(username, "registry username")
    require_text(password, "registry password")
    if any(character in password for character in ("\n", "\r", "\0")):
        raise TaskFailed("registry password must not contain newlines or null bytes")
    if authfile is not None:
        require_path(authfile, "registry authfile")
    if tls_verify is not None:
        require_bool(tls_verify, "registry tls_verify")
    require_bool(force, "registry force")

    podman_command = podman(ctx)
    if (
        not force
        and _login_status(
            ctx,
            podman_command,
            registry,
            authfile=authfile,
            sudo=sudo,
        )
        == username
    ):
        return

    ctx.changed(
        {"registry": registry, "username": username, "action": "login"},
        f"login to {registry} as {username}",
    )
    command = [podman_command, "login", f"--username={username}", "--password-stdin"]
    if authfile is not None:
        command.append(f"--authfile={authfile}")
    if tls_verify is not None:
        command.append(f"--tls-verify={'true' if tls_verify else 'false'}")
    command.append(registry)
    ctx.run(command, stdin=f"{password}\n", sudo=sudo, record=False)


def logout(
    ctx: TaskContext,
    registry: str,
    *,
    authfile: str | Path | None = None,
    sudo: bool = False,
) -> None:
    """Log out of a container registry."""
    require_text(registry, "registry")
    if authfile is not None:
        require_path(authfile, "registry authfile")

    podman_command = podman(ctx)
    if _login_status(ctx, podman_command, registry, authfile=authfile, sudo=sudo) is None:
        return

    ctx.changed(
        {"registry": registry, "action": "logout"},
        f"logout from {registry}",
    )
    command = [podman_command, "logout"]
    if authfile is not None:
        command.append(f"--authfile={authfile}")
    command.append(registry)
    ctx.run(command, sudo=sudo, record=False)


def _registry_path(name: str, *, scope: str, path: str | Path | None) -> Path:
    if path is None:
        directory = default_dir(
            scope,
            "/etc/containers/registries.conf.d",
            Path(".config/containers/registries.conf.d"),
        )
        return directory / f"{name}.conf"
    target = Path(path)
    if not str(target).endswith(".conf"):
        target = Path(f"{target}.conf")
    return target


def _registry_document(
    location: str,
    *,
    prefix: str | None,
    insecure: bool,
    blocked: bool,
    mirrors: Sequence[str],
) -> str:
    lines = [
        "# Managed by kitup.",
        "[[registry]]",
        f"location = {toml_string(location)}",
    ]
    if prefix is not None:
        lines.append(f"prefix = {toml_string(prefix)}")
    lines.append(f"insecure = {'true' if insecure else 'false'}")
    lines.append(f"blocked = {'true' if blocked else 'false'}")
    for mirror in mirrors:
        lines.extend(["", "[[registry.mirror]]", f"location = {toml_string(mirror)}"])
    return "\n".join(lines) + "\n"


def _login_status(
    ctx: TaskContext,
    podman_command: str,
    registry: str,
    *,
    authfile: str | Path | None,
    sudo: bool,
) -> str | None:
    command = [podman_command, "login", "--get-login"]
    if authfile is not None:
        command.append(f"--authfile={authfile}")
    command.append(registry)
    result = ctx.probe(command, sudo=sudo)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
