"""Helpers for locating required external commands."""

import shutil

from ..context import TaskContext
from ..errors import TaskFailed


def require_command(ctx: TaskContext, name: str, *, memo_key: str | None = None, hint: str = "") -> str:
    """Return an executable path, failing clearly when it is not on ``PATH``."""
    key = memo_key or f"{name}-command"
    command = ctx.memo(key, lambda: shutil.which(name))
    if command is None:
        message = f"{name} is required but was not found"
        if hint:
            message = f"{message} ({hint})"
        raise TaskFailed(message)
    return command
