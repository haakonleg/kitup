"""Podman container lifecycle management."""

from collections.abc import Mapping, Sequence

from ..._utils.validation import (
    require_bool,
    require_non_negative_int,
    require_state,
    require_text,
    require_timeout,
)
from ...context import TaskContext
from ...errors import TaskFailed
from ...results import CallResult
from ._common import (
    CONTAINER_STATES,
    CONTAINER_STATUSES,
    ContainerState,
    PodmanOption,
    container_command,
    container_status,
    option_args,
    podman,
    require_object_name,
)


def container(
    ctx: TaskContext,
    name: str,
    *,
    state: ContainerState = "started",
    image: str | None = None,
    command: str | Sequence[str] | None = None,
    options: Mapping[str, PodmanOption] | None = None,
    recreate: bool = False,
    stop_timeout: int | None = None,
    timeout: float = 600,
    sudo: bool = False,
) -> CallResult:
    """Ensure a container is created, started, stopped, or absent.

    Existing containers are never modified. Use ``recreate=True`` to apply a
    changed image, command, or options. ``state="started"`` verifies that the
    container is running immediately after start; it is not suitable for
    one-shot containers.
    """
    require_object_name(name, "container name")
    require_state(state, "container", CONTAINER_STATES)
    require_bool(recreate, "container recreate")
    require_bool(sudo, "container sudo")
    require_timeout(timeout)
    if stop_timeout is not None:
        require_non_negative_int(stop_timeout, "container stop_timeout")
    if recreate and state not in ("present", "started"):
        raise TaskFailed("recreate is only supported when state is 'present' or 'started'")
    if image is not None:
        require_text(image, "container image")

    podman_command = podman(ctx)
    status = container_status(ctx, podman_command, name, sudo=sudo)
    if status == "paused":
        raise TaskFailed(f"container is paused and cannot be managed: {name}")
    if status is not None and status not in CONTAINER_STATUSES:
        raise TaskFailed(f"unexpected container status for {name}: {status!r}")

    actions: list[str] = []
    summaries: list[str] = []
    if recreate:
        if image is None:
            raise TaskFailed("container image is required when recreate is True")
        if status is not None:
            actions.append("remove")
            summaries.append(f"remove container {name}")
            if not ctx.dry_run:
                _remove_container(ctx, podman_command, name, timeout=timeout, sudo=sudo)
        status = None

    if status is None:
        if state in ("present", "started"):
            if image is None:
                raise TaskFailed(f"container image is required to create {name}")
            actions.append("create")
            summaries.append(f"create container {name}")
            if not ctx.dry_run:
                _create_container(
                    ctx,
                    podman_command,
                    name,
                    image,
                    command=command,
                    options=options,
                    stop_timeout=stop_timeout,
                    timeout=timeout,
                    sudo=sudo,
                )
            if state == "started":
                actions.append("start")
                summaries.append(f"start container {name}")
                if not ctx.dry_run:
                    _start_container(ctx, podman_command, name, timeout=timeout, sudo=sudo)
    elif state == "started":
        if status != "running":
            actions.append("start")
            summaries.append(f"start container {name}")
            if not ctx.dry_run:
                _start_container(ctx, podman_command, name, timeout=timeout, sudo=sudo)
    elif state == "stopped":
        if status == "running":
            actions.append("stop")
            summaries.append(f"stop container {name}")
            if not ctx.dry_run:
                _stop_container(
                    ctx,
                    podman_command,
                    name,
                    stop_timeout=stop_timeout,
                    timeout=timeout,
                    sudo=sudo,
                )
    elif state == "absent":
        actions.append("remove")
        summaries.append(f"remove container {name}")
        if not ctx.dry_run:
            _remove_container(ctx, podman_command, name, timeout=timeout, sudo=sudo)

    return ctx.changed(
        action="podman.container",
        changed=bool(actions),
        summary="; ".join(summaries) if summaries else f"ensure container {name} is {state}",
        container=name,
        image=image,
        actions=actions,
    )


def _create_container(
    ctx: TaskContext,
    podman_command: str,
    name: str,
    image: str,
    *,
    command: str | Sequence[str] | None,
    options: Mapping[str, PodmanOption] | None,
    stop_timeout: int | None,
    timeout: float,
    sudo: bool,
) -> None:
    arguments = [podman_command, "create", "--name", name]
    if stop_timeout is not None:
        arguments.append(f"--stop-timeout={stop_timeout}")
    arguments.extend(option_args(options))
    arguments.append(image)
    arguments.extend(container_command(command))

    ctx.run(arguments, timeout=timeout, sudo=sudo)
    if container_status(ctx, podman_command, name, sudo=sudo) is None:
        raise TaskFailed(f"container is missing after create: {name}")


def _start_container(
    ctx: TaskContext,
    podman_command: str,
    name: str,
    *,
    timeout: float,
    sudo: bool,
) -> None:
    ctx.run([podman_command, "start", name], timeout=timeout, sudo=sudo)
    status = container_status(ctx, podman_command, name, sudo=sudo)
    if status is None:
        raise TaskFailed(f"container is missing after start: {name}")
    if status != "running":
        raise TaskFailed(
            f"container is not running after start: {name} (status: {status}); "
            "if this is a one-shot container, use state='present'"
        )


def _stop_container(
    ctx: TaskContext,
    podman_command: str,
    name: str,
    *,
    stop_timeout: int | None,
    timeout: float,
    sudo: bool,
) -> None:
    arguments = [podman_command, "stop"]
    if stop_timeout is not None:
        arguments.append(f"--time={stop_timeout}")
    arguments.append(name)
    ctx.run(arguments, timeout=timeout, sudo=sudo)
    if container_status(ctx, podman_command, name, sudo=sudo) == "running":
        raise TaskFailed(f"container did not stop: {name}")


def _remove_container(
    ctx: TaskContext,
    podman_command: str,
    name: str,
    *,
    timeout: float,
    sudo: bool,
) -> None:
    ctx.run(
        [podman_command, "rm", "--force", name],
        timeout=timeout,
        sudo=sudo,
    )
    if container_status(ctx, podman_command, name, sudo=sudo) is not None:
        raise TaskFailed(f"container is still present after removal: {name}")
