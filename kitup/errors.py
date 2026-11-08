"""Exception types shared by the CLI, loader, runner, and modules."""


class KitupError(Exception):
    """Base class for expected, user-facing application errors."""


class UsageError(KitupError):
    """Invalid invocation, configuration, or bundle setup (exit code 2)."""


class VarsError(UsageError):
    """A variable source could not be loaded."""


class ProjectError(UsageError):
    """The project layout is missing, malformed, or defines no bundles."""


class PresetError(UsageError):
    """A preset file is missing, unreadable, or malformed."""


class BundleError(UsageError):
    """A bundle module or dependency reference is malformed."""


class ResolutionError(UsageError):
    """A requested bundle/dependency is unknown or dependencies form a cycle."""


class PrivilegeError(KitupError):
    """Sudo credentials are required but could not be obtained."""


class TaskFailed(KitupError):
    """A task (usually a module) failed in an expected way."""

    def __init__(
        self,
        message: str,
        *,
        command: list[str] | tuple[str, ...] | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.command = list(command) if command is not None else None
        self.stdout = stdout
        self.stderr = stderr


class SkipTask(Exception):
    """Raised by ``ctx.skip()`` to mark the current task as skipped."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
