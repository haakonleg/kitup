"""Write Podman Quadlet ``.container`` files."""

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..._utils import file_ops
from ...context import TaskContext
from ...errors import TaskFailed
from ...results import CallResult
from ...validation import require_mode, require_non_negative_int, require_scope, require_text
from ._common import (
    NATIVE_QUADLET_KEYS,
    QUADLET_KEY,
    PodmanOption,
    command_parts,
    default_dir,
    option_args,
    require_object_name,
    require_unit_name,
)


def quadlet(
    ctx: TaskContext,
    name: str,
    *,
    image: str,
    dest: str | Path | None = None,
    scope: str = "system",
    command: str | Sequence[str] | None = None,
    options: Mapping[str, PodmanOption] | None = None,
    extra_options: Mapping[str, str | Sequence[str]] | None = None,
    install_target: str | None = None,
    stop_timeout: int | None = None,
    mode: int | None = 0o644,
) -> CallResult:
    """Write a Quadlet ``.container`` file.

    The parent directory must already exist. The Quadlet is written but not
    reloaded; use :func:`kitup.modules.systemd.daemon_reload` afterwards.
    """
    require_scope(scope, "podman", is_root=ctx.sudo.is_root)
    require_object_name(name, "container name")
    require_text(image, "container image")
    require_mode(mode)
    if stop_timeout is not None:
        require_non_negative_int(stop_timeout, "container stop_timeout")

    if install_target is None:
        target = "multi-user.target" if scope == "system" else "default.target"
    else:
        target = install_target
        if target != "":
            require_unit_name(target, "quadlet install_target")

    sudo = scope == "system"
    destination = (
        Path(dest)
        if dest is not None
        else default_dir(
            scope,
            "/etc/containers/systemd",
            Path(".config/containers/systemd"),
        )
    )
    path = destination / f"{name}.container"

    exec_line = _quadlet_exec(command)
    option_lines = [f"PodmanArgs={shlex.quote(token)}" for token in option_args(options)]
    extra_lines = _quadlet_extra_options(extra_options)
    content = _quadlet_document(
        name,
        image,
        exec_line=exec_line,
        stop_timeout=stop_timeout,
        option_lines=option_lines,
        extra_lines=extra_lines,
        install_target=target,
    )
    return ctx.changed(
        file_ops.apply_bytes(
            ctx,
            path,
            content.encode(),
            action="podman.quadlet",
            mode=mode,
            owner=None,
            group=None,
            summary=f"write quadlet {name} to {path}",
            unchanged_summary=f"ensure quadlet {name} in {path}",
            data={"path": str(path), "container": name},
            sudo=sudo,
        )
    )


def _quadlet_exec(command: str | Sequence[str] | None) -> str | None:
    parts = command_parts(command, label="quadlet command", split_string=False)
    if parts is None:
        return None
    if isinstance(command, str):
        return parts[0]
    return shlex.join(parts)


def _quadlet_extra_options(
    extra_options: Mapping[str, str | Sequence[str]] | None,
) -> list[str]:
    if extra_options is None:
        return []
    if not isinstance(extra_options, Mapping):
        raise TaskFailed("extra_options must be a mapping")

    lines: list[str] = []
    for key, value in extra_options.items():
        if not QUADLET_KEY.fullmatch(key):
            raise TaskFailed(f"invalid quadlet option name: {key!r}")
        if key in NATIVE_QUADLET_KEYS:
            raise TaskFailed(f"quadlet option is managed by kitup: {key}")
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, Sequence):
            values = list(value)
        else:
            raise TaskFailed(f"quadlet option {key} must be a string or a sequence of strings")
        for item in values:
            if not isinstance(item, str):
                raise TaskFailed(f"quadlet option {key} must contain strings")
            if "\n" in item or "\r" in item:
                raise TaskFailed(f"quadlet option {key} must not contain newlines")
            lines.append(f"{key}={item}")
    return lines


def _quadlet_document(
    name: str,
    image: str,
    *,
    exec_line: str | None,
    stop_timeout: int | None,
    option_lines: Sequence[str],
    extra_lines: Sequence[str],
    install_target: str,
) -> str:
    lines = [
        "[Unit]",
        f"Description=kitup managed container {name}",
        "",
        "[Container]",
        f"ContainerName={name}",
        f"Image={image}",
    ]
    if exec_line is not None:
        lines.append(f"Exec={exec_line}")
    if stop_timeout is not None:
        lines.append(f"StopTimeout={stop_timeout}")
    lines.extend(option_lines)
    lines.extend(extra_lines)
    lines.extend(["", "[Service]", "Restart=always"])
    if install_target != "":
        lines.extend(["", "[Install]", f"WantedBy={install_target}"])
    return "\n".join(lines) + "\n"
