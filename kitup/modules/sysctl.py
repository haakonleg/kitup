"""Linux sysctl parameter management."""

from __future__ import annotations

import re
from pathlib import Path

from .._utils import file_ops
from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_state, require_text

_KEY_PATTERN = r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*"


def key(
    ctx: TaskContext,
    name: str,
    value: str | float | bool | None = None,
    *,
    state: str = "present",
    file: str | Path | None = None,
) -> None:
    """Set a sysctl key at runtime and optionally persist it in a file.

    ``file=None`` changes only the runtime value. When ``file`` is an absolute
    path, a ``name = value`` entry is also managed in that file.
    ``state="absent"`` requires ``file`` and only edits the file.
    """
    require_state(state, "sysctl")
    key_name = _normalize_name(name)
    target = _resolve_file(file)

    if state == "absent":
        if target is None:
            raise TaskFailed("state 'absent' requires a sysctl file")
        _remove_entry(ctx, target, key_name)
        return

    if value is None:
        raise TaskFailed("value is required when state is present")
    desired = _format_value(value)

    current = _read_runtime(ctx, key_name)
    if target is not None:
        _write_entry(ctx, target, key_name, desired)
    if _normalize_ws(current) != _normalize_ws(desired):
        ctx.changed({"key": key_name, "value": desired}, f"set {key_name}={desired}")
        ctx.run(["sysctl", "-w", f"{key_name}={desired}"], sudo=True, record=False)


def _normalize_name(name: str) -> str:
    require_text(name, "sysctl key")
    normalized = name.strip().replace("/", ".")
    if re.fullmatch(_KEY_PATTERN, normalized) is None:
        raise TaskFailed(f"invalid sysctl key: {name!r}")
    return normalized


def _resolve_file(file: str | Path | None) -> Path | None:
    if file is None:
        return None
    target = Path(file)
    if not target.is_absolute():
        raise TaskFailed("sysctl file must be an absolute path")
    return target


def _format_value(value: str | float | bool) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if "\n" in value or "\r" in value or "\0" in value:
            raise TaskFailed("sysctl value must not contain newlines or null bytes")
        return value
    raise TaskFailed(f"unsupported sysctl value type: {type(value).__name__}")


def _read_runtime(ctx: TaskContext, name: str) -> str:
    result = ctx.probe(["sysctl", "-n", name], sudo=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "key unavailable"
        raise TaskFailed(f"sysctl {name}: {detail}")
    return result.stdout.strip()


def _normalize_ws(value: str) -> str:
    return " ".join(value.split())


def _entry_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"^\s*" + re.escape(name) + r"\s*=")


def _write_entry(ctx: TaskContext, target: Path, name: str, desired: str) -> None:
    pattern = _entry_pattern(name)
    text = _read_text(ctx, target)
    lines = [line for line in text.splitlines(keepends=True) if not pattern.search(line)]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{name} = {desired}\n")
    _apply_text(
        ctx,
        target,
        text,
        "".join(lines),
        summary=f"set {name} in {target}",
        data={"path": str(target), "key": name, "action": "edit"},
    )


def _remove_entry(ctx: TaskContext, target: Path, name: str) -> None:
    pattern = _entry_pattern(name)
    text = _read_text(ctx, target)
    new_text = "".join(line for line in text.splitlines(keepends=True) if not pattern.search(line))
    _apply_text(
        ctx,
        target,
        text,
        new_text,
        summary=f"remove {name} from {target}",
        data={"path": str(target), "key": name, "action": "edit", "state": "absent"},
    )


def _apply_text(
    ctx: TaskContext,
    target: Path,
    old_text: str,
    new_text: str,
    *,
    summary: str,
    data: dict[str, object],
) -> None:
    if new_text == old_text:
        return
    file_ops.apply_bytes(
        ctx,
        target,
        new_text.encode(),
        mode=None,
        owner=None,
        group=None,
        summary=summary,
        data=data,
        sudo=True,
    )


def _read_text(ctx: TaskContext, path: Path) -> str:
    info = file_ops.stat(ctx, path, sudo=True)
    if info is None:
        return ""
    if info.is_dir:
        raise TaskFailed(f"path is a directory: {path}")
    if info.is_symlink:
        raise TaskFailed(f"refusing to edit symlink: {path}")
    if not info.is_file:
        raise TaskFailed(f"path is not a regular file: {path}")
    try:
        return file_ops.read_bytes(ctx, path, info, sudo=True).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TaskFailed(f"not a UTF-8 text file: {path}") from exc
