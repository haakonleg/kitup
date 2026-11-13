"""Systemd unit management."""

from typing import Final, Literal

from .._utils.validation import require_bool, require_scope, require_state, require_text
from ..context import TaskContext
from ..errors import TaskFailed
from ..registry import module
from ..results import CallResult

module("systemd")


type SystemdState = Literal["started", "stopped"]

_ENABLED_STATES: Final = {
    "enabled",
    "enabled-runtime",
    "static",
    "alias",
    "indirect",
    "generated",
}

_PAST_TENSE: Final = {
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
    state: SystemdState | None = None,
    scope: str = "system",
) -> CallResult:
    """Ensure a unit's enabled and active state."""
    require_text(unit, "systemd unit")
    if enabled is None and state is None:
        raise TaskFailed("systemd unit requires enabled or state")
    if enabled is not None:
        require_bool(enabled, "systemd enabled")
    base = _command(ctx, scope)
    desired = None if state is None else _desired_active(state)

    actions: list[str] = []
    summaries: list[str] = []
    if enabled is not None:
        current_enabled = _is_enabled(ctx, base, unit)
        if current_enabled != enabled:
            action = "enable" if enabled else "disable"
            actions.append(action)
            summaries.append(f"{_PAST_TENSE[action]} {unit}")
            if not ctx.dry_run:
                ctx.run([*base, action, unit], sudo=scope == "system")

    if desired is not None:
        active = _is_active(ctx, base, unit)
        if active != desired:
            action = "start" if desired else "stop"
            actions.append(action)
            summaries.append(f"{_PAST_TENSE[action]} {unit}")
            if not ctx.dry_run:
                ctx.run([*base, action, unit], sudo=scope == "system")

    if not actions:
        wanted: list[str] = []
        if enabled is not None:
            wanted.append("enabled" if enabled else "disabled")
        if state is not None:
            wanted.append(state)
        summary = f"ensure {unit} is {' and '.join(wanted)}"
    else:
        summary = "; ".join(summaries)
    return ctx.changed(changed=bool(actions), summary=summary, unit=unit, scope=scope, actions=actions)


def daemon_reload(ctx: TaskContext, *, scope: str = "system") -> CallResult:
    """Reload systemd manager configuration."""
    base = _command(ctx, scope)
    if ctx.dry_run:
        return ctx.changed(summary=f"would reload systemd ({scope})", scope=scope)
    ctx.run([*base, "daemon-reload"], sudo=scope == "system")
    return ctx.changed(summary=f"reload systemd ({scope})", scope=scope)


def _command(ctx: TaskContext, scope: str) -> list[str]:
    require_scope(scope, "systemd", is_root=ctx.sudo.is_root)
    return ["systemctl"] if scope == "system" else ["systemctl", "--user"]


def _is_enabled(ctx: TaskContext, base: list[str], unit: str) -> bool:
    result = ctx.probe([*base, "is-enabled", unit], sudo=False)
    return result.returncode == 0 and result.stdout.strip() in _ENABLED_STATES


def _is_active(ctx: TaskContext, base: list[str], unit: str) -> bool:
    result = ctx.probe([*base, "is-active", unit], sudo=False)
    return result.returncode == 0 and result.stdout.strip() == "active"


def _desired_active(state: SystemdState) -> bool:
    require_state(state, "systemd", ("started", "stopped"))
    return state == "started"
