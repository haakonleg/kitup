"""Shared test doubles and builders."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any

from kitup.context import TaskContext
from kitup.registry import Bundle


class FakeCommandRunner:
    """Records commands and returns a scripted result."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.responses: list[
            Callable[[list[str]], int | subprocess.CompletedProcess[str]]
        ] = []

    def run(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = list(argv)
        self.calls.append((command, kwargs))
        response = self.responses.pop(0)(command) if self.responses else self.returncode
        if isinstance(response, subprocess.CompletedProcess):
            return response
        return subprocess.CompletedProcess(command, response, self.stdout, self.stderr)


def completed(
    argv: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Build a scripted command result for FakeCommandRunner."""
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class FakeSudo:
    """Minimal PrivilegeManager stand-in."""

    def __init__(self, *, is_root: bool = False) -> None:
        self.is_root = is_root
        self.refresh_count = 0
        self.validations = 0
        self.reasons: list[str | None] = []
        self.valid = False

    def refresh(self, reason: str | None = None) -> None:
        self.refresh_count += 1
        self.reasons.append(reason)
        self.valid = True

    def credentials_valid(self) -> bool:
        return self.valid

    def validate(self) -> None:
        self.validations += 1

    def wrap(self, argv: list[str]) -> list[str]:
        return argv if self.is_root else ["sudo", "-n", "--", *argv]


class PassthroughSudo(FakeSudo):
    """Sudo stand-in that lets tests execute helper commands locally."""

    def wrap(self, argv: list[str]) -> list[str]:
        return argv


def make_context(
    tmp_path,
    *,
    dry_run: bool = False,
    diff: bool = False,
    verbose: bool = False,
    command: FakeCommandRunner | None = None,
    sudo: FakeSudo | None = None,
    config: SimpleNamespace | None = None,
    facts: dict[str, Any] | None = None,
    output=None,
) -> TaskContext:
    return TaskContext(
        dry_run=dry_run,
        diff=diff,
        verbose=verbose,
        config_dir=tmp_path,
        config=config or SimpleNamespace(),
        facts=facts or {},
        sudo=sudo or FakeSudo(),  # type: ignore[arg-type]
        command=command or FakeCommandRunner(),  # type: ignore[arg-type]
        output=output,
    )


def add_task(
    bundle: Bundle,
    func: Callable[[TaskContext], None],
    *,
    name: str | None = None,
    tags: Sequence[str] = (),
) -> Callable[[TaskContext], None]:
    return bundle.task(name, tags=tags)(func)
