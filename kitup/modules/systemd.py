"""Systemd unit management."""

from __future__ import annotations

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_text

_ENABLED_STATES = {
    "enabled",
    "enabled-runtime",
    "static",
    "alias",
    "indirect",
    "generated",
}

_PAST_TENSE = {
    "enable": "enabled",
    "disable": "disabled",
    "start": "started",
    "stop": "stopped",
}


def unit(
    ctx: TaskContext,
    unit: str,
    *,
    enabled: bool | None = None,
    state: str | None = None,
    scope: str = "system",
) -> None:
    """Ensure a unit's enabled and active state."""
    require_text(unit, "systemd unit")
    base = _command(ctx, scope)
    desired = None if state is None else _desired_active(state)

    if enabled is not None:
        current_enabled = _is_enabled(ctx, base, unit)
        if current_enabled != enabled:
            _converge(ctx, base, unit, "enable" if enabled else "disable", scope)

    if desired is not None:
        active = _is_active(ctx, base, unit)
        if active != desired:
            _converge(ctx, base, unit, "start" if desired else "stop", scope)


def daemon_reload(ctx: TaskContext, *, scope: str = "system") -> None:
    """Reload systemd manager configuration."""
    _reload(ctx, scope)


def _converge(
    ctx: TaskContext,
    base: list[str],
    unit: str,
    action: str,
    scope: str,
) -> None:
    ctx.changed(
        {"unit": unit, "scope": scope, "action": action},
        f"{_PAST_TENSE[action]} {unit}",
    )
    ctx.run([*base, action, unit], sudo=scope == "system", record=False)


def _reload(ctx: TaskContext, scope: str) -> None:
    base = _command(ctx, scope)
    ctx.run([*base, "daemon-reload"], sudo=scope == "system")


def _command(ctx: TaskContext, scope: str) -> list[str]:
    if scope == "system":
        return ["systemctl"]
    if scope == "user":
        if ctx.sudo.is_root:
            raise TaskFailed("user-scoped systemd cannot run as root")
        return ["systemctl", "--user"]
    raise TaskFailed(f"invalid systemd scope: {scope!r}")


def _is_enabled(ctx: TaskContext, base: list[str], unit: str) -> bool:
    result = ctx.probe([*base, "is-enabled", unit], sudo=False)
    return result.returncode == 0 and result.stdout.strip() in _ENABLED_STATES


def _is_active(ctx: TaskContext, base: list[str], unit: str) -> bool:
    result = ctx.probe([*base, "is-active", unit], sudo=False)
    return result.returncode == 0 and result.stdout.strip() == "active"


def _desired_active(state: str) -> bool:
    if state == "started":
        return True
    if state == "stopped":
        return False
    raise TaskFailed(f"invalid systemd state: {state!r}")
