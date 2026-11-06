"""Generate systemd units for Podman containers."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from ..._utils import file_ops
from ...context import TaskContext
from ...errors import TaskFailed
from ...results import CallResult
from ...validation import (
    require_bool,
    require_choice,
    require_mode,
    require_non_negative_int,
    require_scope,
    require_success,
    require_text,
)
from ._common import (
    RESTART_POLICIES,
    default_dir,
    env_args,
    podman,
    unit_names,
)


def generate_systemd(
    ctx: TaskContext,
    name: str,
    *,
    dest: str | Path | None = None,
    scope: str = "system",
    new: bool = False,
    restart_policy: str | None = None,
    restart_sec: int | None = None,
    start_timeout: int | None = None,
    stop_timeout: int | None = None,
    container_prefix: str | None = None,
    separator: str | None = None,
    env: Mapping[str, str | int | float] | None = None,
    wants: str | Iterable[str] = (),
    requires: str | Iterable[str] = (),
    after: str | Iterable[str] = (),
    mode: int | None = 0o644,
) -> CallResult:
    """Generate a systemd unit for a container.

    ``podman generate systemd`` is deprecated upstream in favor of Quadlet.
    The generated unit is written but not reloaded; use
    :func:`kitup.modules.systemd.daemon_reload` afterwards. Podman's
    generated header, which contains a timestamp, is omitted so repeated runs
    produce identical content.
    """
    require_scope(scope, "podman", is_root=ctx.sudo.is_root)
    require_text(name, "container name")
    require_bool(new, "systemd new")
    require_mode(mode)
    if restart_policy is not None:
        require_choice(restart_policy, RESTART_POLICIES, "systemd restart policy")
    for value, label in (
        (restart_sec, "systemd restart_sec"),
        (start_timeout, "systemd start_timeout"),
        (stop_timeout, "systemd stop_timeout"),
    ):
        if value is not None:
            require_non_negative_int(value, label)
    if container_prefix is not None:
        require_text(container_prefix, "systemd container_prefix")
    if separator is not None:
        require_text(separator, "systemd separator")

    sudo = scope == "system"
    destination = (
        Path(dest) if dest is not None else default_dir(scope, "/etc/systemd/system", Path(".config/systemd/user"))
    )
    prefix = container_prefix or "container"
    separator_value = separator or "-"
    filename = f"{prefix}{separator_value}{name}.service"
    if "/" in filename:
        raise TaskFailed(f"invalid systemd unit filename: {filename}")

    environment_args = env_args(env)
    wants_units = unit_names(wants, "systemd wants")
    requires_units = unit_names(requires, "systemd requires")
    after_units = unit_names(after, "systemd after")

    podman_command = podman(ctx)
    command = [podman_command, "generate", "systemd", "--name"]
    if new:
        command.append("--new")
    command.append("--no-header")
    if restart_policy is not None:
        command.append(f"--restart-policy={restart_policy}")
    if restart_sec is not None:
        command.append(f"--restart-sec={restart_sec}")
    if start_timeout is not None:
        command.append(f"--start-timeout={start_timeout}")
    if stop_timeout is not None:
        command.append(f"--stop-timeout={stop_timeout}")
    if container_prefix is not None:
        command.append(f"--container-prefix={container_prefix}")
    if separator is not None:
        command.append(f"--separator={separator_value}")
    command.extend(environment_args)
    command.extend(f"--wants={unit}" for unit in wants_units)
    command.extend(f"--requires={unit}" for unit in requires_units)
    command.extend(f"--after={unit}" for unit in after_units)
    command.append(name)

    result = ctx.probe(command, sudo=sudo)
    require_success(result, f"could not generate systemd unit for {name}")
    if not result.stdout.strip():
        raise TaskFailed(f"podman generated an empty systemd unit for {name}")

    target = destination / filename
    return ctx.changed(
        file_ops.apply_bytes(
            ctx,
            target,
            result.stdout.encode(),
            action="podman.generate_systemd",
            mode=mode,
            owner=None,
            group=None,
            summary=f"write systemd unit for {name} to {target}",
            unchanged_summary=f"ensure systemd unit for {name} in {target}",
            data={"path": str(target), "container": name},
            sudo=sudo,
        )
    )
