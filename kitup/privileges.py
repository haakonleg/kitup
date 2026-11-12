"""Lazy sudo handling."""

import getpass
import os
import shutil
import subprocess
import sys
from functools import cached_property
from typing import Final

from . import PACKAGE_NAME
from .errors import PrivilegeError
from .output.terminal import clear_lines, terminal_columns

_SUDO_PROMPT: Final = "sudo password: "
_SUDO_ERROR_PREFIX: Final = "sudo: "


class PrivilegeManager:
    """Validate and wrap commands that need root privileges.

    Commands are attempted with non-interactive sudo. When that fails because
    the cached credentials expired, the caller prompts and retries once.
    """

    @cached_property
    def is_root(self) -> bool:
        """Return whether the current process is running as root."""
        return os.geteuid() == 0

    def validate(self) -> None:
        """Ensure cached sudo credentials are valid, prompting when needed."""
        if self.credentials_valid():
            return
        self.authenticate()

    def authenticate(self, reason: str | None = None) -> None:
        """Prompt for a sudo password and validate it.

        ``reason`` is shown before the prompt. The password is read by the
        application and passed to non-interactive sudo on stdin, so sudo's own
        prompt never reaches the terminal. Outside a TTY, an expired cache is a
        hard error instead of hanging on an invisible password prompt.
        """
        if self.is_root:
            return
        self.require_available()

        if not sys.stdin.isatty():
            raise PrivilegeError(
                f"sudo credentials are required but stdin is not a TTY; run {PACKAGE_NAME} from a terminal"
            )

        password = self._prompt_password(reason)

        try:
            result = subprocess.run(
                ["sudo", "-S", "-p", "", "-v"],
                input=f"{password}\n",
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise PrivilegeError(f"could not run sudo: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            message = "sudo authentication failed"
            if detail:
                message = f"{message}: {detail}"
            raise PrivilegeError(message)

    def require_available(self) -> None:
        """Raise when sudo is needed but not installed."""
        if self.is_root:
            return
        if self._sudo is None:
            raise PrivilegeError("sudo is required by this operation but was not found")

    def credentials_valid(self) -> bool:
        """Return whether cached sudo credentials can be used without a prompt."""
        if self.is_root:
            return True
        if self._sudo is None:
            return False
        return self._cached_credentials_valid()

    def is_auth_failure(self, result: subprocess.CompletedProcess[str]) -> bool:
        """Return whether a command failed because cached credentials expired."""
        if result.returncode == 0:
            return False
        if not any(line.startswith(_SUDO_ERROR_PREFIX) for line in result.stderr.splitlines()):
            return False
        return not self.credentials_valid()

    def wrap(self, argv: list[str]) -> list[str]:
        """Return argv prefixed with non-interactive sudo when needed."""
        if self.is_root:
            return argv
        return ["sudo", "-n", "--", *argv]

    @cached_property
    def _sudo(self) -> str | None:
        """Return the cached path to sudo, or ``None`` when it is missing."""
        return shutil.which("sudo")

    def _prompt_password(self, reason: str | None) -> str:
        if reason:
            print(self._fit_reason(reason), file=sys.stderr, flush=True)
        try:
            password = getpass.getpass(_SUDO_PROMPT)
        except EOFError as exc:
            raise PrivilegeError("no sudo password received") from exc
        # getpass leaves the cursor on the line after the prompt.
        clear_lines(sys.stderr, 2 if reason else 1)
        return password

    @staticmethod
    def _fit_reason(reason: str) -> str:
        if not sys.stderr.isatty():
            return reason
        columns = terminal_columns(sys.stderr)
        if len(reason) <= columns:
            return reason
        return f"{reason[: columns - 3]}..."

    @staticmethod
    def _cached_credentials_valid() -> bool:
        result = subprocess.run(
            ["sudo", "-n", "-v"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
