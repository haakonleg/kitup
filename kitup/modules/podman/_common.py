"""Shared types, validation, and command helpers for the Podman modules."""

import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal

from ..._utils.executables import require_command
from ..._utils.validation import normalize_strings, require_success, require_text, require_texts, unique_strings
from ...context import TaskContext
from ...errors import TaskFailed

type PullPolicy = Literal["missing", "always", "never"]
type ContainerState = Literal["present", "started", "stopped", "absent"]
type PodmanOption = str | int | float | bool | Sequence[str | int | float] | Mapping[str, str | int | float]

PULL_POLICIES: Final[tuple[PullPolicy, ...]] = ("missing", "always", "never")
CONTAINER_STATES: Final[tuple[ContainerState, ...]] = ("present", "started", "stopped", "absent")
RESTART_POLICIES: Final[tuple[str, ...]] = (
    "no",
    "on-success",
    "on-failure",
    "on-abnormal",
    "on-watchdog",
    "on-abort",
    "always",
)
CONTAINER_STATUSES: Final[frozenset[str]] = frozenset(
    {"created", "initialized", "running", "stopped", "exited", "paused", "removing", "stopping"}
)
_CONTAINER_MISSING_MARKERS: Final[tuple[str, ...]] = ("no such container", "no container with name or id")
IMAGE_TRANSPORTS: Final[tuple[str, ...]] = (
    "containers-storage:",
    "dir:",
    "docker://",
    "docker-archive:",
    "docker-daemon:",
    "oci:",
    "oci-archive:",
    "sif:",
)
OBJECT_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
UNIT_NAME: Final = re.compile(r"[A-Za-z0-9@_.:-]+")
ENV_KEY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
QUADLET_KEY: Final = re.compile(r"[A-Za-z][A-Za-z0-9]*")
NATIVE_QUADLET_KEYS: Final[frozenset[str]] = frozenset({"ContainerName", "Image", "Exec", "StopTimeout", "PodmanArgs"})


def podman(ctx: TaskContext) -> str:
    """Return the Podman executable path, failing when it is missing."""
    return require_command(ctx, "podman")


def require_object_name(value: str, label: str) -> None:
    """Reject names that are unsafe in file and resource names."""
    if not OBJECT_NAME.fullmatch(value):
        raise TaskFailed(f"invalid {label}: {value!r}")


def require_unit_name(value: str, label: str) -> None:
    """Reject an invalid systemd unit name."""
    if not UNIT_NAME.fullmatch(value):
        raise TaskFailed(f"invalid {label}: {value!r}")


def default_dir(scope: str, system_path: str, user_subpath: Path) -> Path:
    """Return the system or user directory for a scope."""
    if scope == "system":
        return Path(system_path)
    return Path.home() / user_subpath


def option_args(options: Mapping[str, PodmanOption] | None) -> list[str]:
    """Encode a Podman option mapping as command-line arguments."""
    if options is None:
        return []
    if not isinstance(options, Mapping):
        raise TaskFailed("options must be a mapping")

    arguments: list[str] = []
    for raw_key, value in options.items():
        key = raw_key.strip().lstrip("-").replace("_", "-")
        if not key:
            raise TaskFailed(f"invalid option name: {raw_key!r}")
        flag = f"--{key}"
        if isinstance(value, bool):
            arguments.append(flag if value else f"{flag}=false")
        elif isinstance(value, Mapping):
            arguments.extend(f"{flag}={item_key}={scalar(item_value)}" for item_key, item_value in value.items())
        elif isinstance(value, Sequence) and not isinstance(value, str):
            arguments.extend(f"{flag}={scalar(item)}" for item in value)
        else:
            arguments.append(f"{flag}={scalar(value)}")
    return arguments


def scalar(value: object) -> str:
    """Render a scalar option value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise TaskFailed(f"unsupported option value: {value!r}")


def is_transport_reference(name: str) -> bool:
    """Return whether an image name uses an explicit containers transport."""
    return name.startswith(IMAGE_TRANSPORTS)


def image_exists(ctx: TaskContext, podman_command: str, name: str, *, sudo: bool) -> bool:
    """Return whether an image exists locally."""
    result = ctx.probe([podman_command, "image", "exists", name], sudo=sudo)
    if result.returncode == 1:
        return False
    require_success(result, f"could not check image: {name}")
    return True


def image_id(ctx: TaskContext, podman_command: str, name: str, *, sudo: bool) -> str:
    """Return an image ID, failing when the image cannot be inspected."""
    return _inspect_image_id(ctx, podman_command, name, sudo=sudo)


def image_id_or_none(
    ctx: TaskContext,
    podman_command: str,
    name: str,
    *,
    sudo: bool,
) -> str | None:
    """Return an image ID, or ``None`` when the image is missing."""
    if not image_exists(ctx, podman_command, name, sudo=sudo):
        return None
    return _inspect_image_id(ctx, podman_command, name, sudo=sudo)


def _inspect_image_id(ctx: TaskContext, podman_command: str, name: str, *, sudo: bool) -> str:
    """Inspect an image and return its ID."""
    result = ctx.probe(
        [podman_command, "image", "inspect", "--format", "{{.Id}}", name],
        sudo=sudo,
    )
    require_success(result, f"could not inspect image: {name}")
    return result.stdout.strip()


def container_status(
    ctx: TaskContext,
    podman_command: str,
    name: str,
    *,
    sudo: bool,
) -> str | None:
    """Return a container status, or ``None`` when it does not exist."""
    result = ctx.probe(
        [podman_command, "container", "inspect", "--format", "{{.State.Status}}", name],
        sudo=sudo,
    )
    if result.returncode != 0:
        missing = any(marker in result.stderr.lower() for marker in _CONTAINER_MISSING_MARKERS)
        if result.returncode == 125 and missing:
            return None
        require_success(result, f"could not inspect container: {name}")
    return result.stdout.strip()


def command_parts(
    command: str | Sequence[str] | None,
    *,
    label: str,
    split_string: bool,
) -> list[str] | None:
    """Normalize a command into argv parts, or ``None`` when absent."""
    if command is None:
        return None
    if isinstance(command, str):
        if split_string:
            try:
                parts = shlex.split(command)
            except ValueError as exc:
                raise TaskFailed(f"could not parse {label}: {exc}") from exc
        else:
            require_text(command, label)
            parts = [command]
    else:
        parts = [str(part) for part in command]
    if not parts:
        raise TaskFailed(f"{label} must not be empty")
    return parts


def container_command(command: str | Sequence[str] | None) -> list[str]:
    """Normalize a container command into an argv list."""
    return command_parts(command, label="container command", split_string=True) or []


def build_arg_args(build_args: Mapping[str, str | int | float] | None) -> list[str]:
    """Encode build arguments as ``--build-arg`` pairs."""
    return _mapping_args(
        build_args,
        mapping_label="build_args",
        name_label="build argument",
        flag="--build-arg",
        separate_flag=True,
    )


def env_args(env: Mapping[str, str | int | float] | None) -> list[str]:
    """Encode environment variables as ``--env`` pairs."""
    return _mapping_args(
        env,
        mapping_label="env",
        name_label="environment variable",
        flag="--env",
        separate_flag=False,
    )


def _mapping_args(
    values: Mapping[str, str | int | float] | None,
    *,
    mapping_label: str,
    name_label: str,
    flag: str,
    separate_flag: bool,
) -> list[str]:
    """Encode a validated key/value mapping as command-line arguments."""
    if values is None:
        return []
    if not isinstance(values, Mapping):
        raise TaskFailed(f"{mapping_label} must be a mapping")
    arguments: list[str] = []
    for key, value in values.items():
        if not ENV_KEY.fullmatch(key):
            raise TaskFailed(f"invalid {name_label} name: {key!r}")
        rendered = f"{key}={scalar(value)}"
        if separate_flag:
            arguments.extend([flag, rendered])
        else:
            arguments.append(f"{flag}={rendered}")
    return arguments


def unit_names(values: str | Iterable[str], label: str) -> list[str]:
    """Normalize, deduplicate, and validate systemd unit names."""
    units = unique_strings(normalize_strings(values))
    require_texts(units, label)
    for unit in units:
        require_unit_name(unit, label)
    return units
