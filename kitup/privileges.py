"""Lazy sudo handling."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .errors import PrivilegeError


class PrivilegeManager:
    """Validate, refresh, and wrap commands that need root privileges."""

    @property
    def is_root(self) -> bool:
        return os.geteuid() == 0

    def validate(self) -> None:
        """Validate credentials up front (used by ``--sudo``)."""
        self.refresh()

    def refresh(self) -> None:
        """Ensure cached sudo credentials are valid, prompting when needed.

        Outside a TTY, an expired cache is a hard error instead of hanging on
        an invisible password prompt.
        """
        if self.is_root:
            return

        if shutil.which("sudo") is None:
            raise PrivilegeError("sudo is required for this task but was not found")

        if self._cached_credentials_valid():
            return

        if not sys.stdin.isatty():
            raise PrivilegeError(
                "sudo credentials are required but stdin is not a TTY; "
                "run kitup from a terminal"
            )

        try:
            result = subprocess.run(["sudo", "-v"], check=False)
        except OSError as exc:
            raise PrivilegeError(f"could not run sudo: {exc}") from exc
        if result.returncode != 0:
            raise PrivilegeError("sudo authentication failed")

    def wrap(self, argv: list[str]) -> list[str]:
        """Return argv prefixed with non-interactive sudo when needed."""
        if self.is_root:
            return argv
        return ["sudo", "-n", "--", *argv]

    @staticmethod
    def _cached_credentials_valid() -> bool:
        result = subprocess.run(
            ["sudo", "-n", "-v"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
