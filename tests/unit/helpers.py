"""Shared test doubles and builders."""

import http.server
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any

from kitup.commands import CommandRunner
from kitup.context import TaskContext
from kitup.errors import TaskFailed
from kitup.output import Output
from kitup.project import Project
from kitup.registry import Bundle
from kitup.results import CallResult
from kitup.vars import Vars


def call_data(result: CallResult) -> dict[str, Any]:
    """Return a call's data fields without the reserved keys."""
    return {key: value for key, value in result.items() if key not in {"action", "changed", "summary"}}


class FakeCommandRunner:
    """Records commands and returns a scripted result."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.responses: list[Callable[[list[str]], int | subprocess.CompletedProcess[str]]] = []

    def run(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = list(argv)
        self.calls.append((command, kwargs))
        response = self.responses.pop(0)(command) if self.responses else self.returncode
        result = (
            response
            if isinstance(response, subprocess.CompletedProcess)
            else subprocess.CompletedProcess(command, response, self.stdout, self.stderr)
        )
        if kwargs.get("check", True) and result.returncode != 0:
            raise TaskFailed(
                f"command failed with exit code {result.returncode}",
                command=command,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result


def completed(
    argv: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Build a scripted command result for FakeCommandRunner."""
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def response(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> Callable[[list[str]], subprocess.CompletedProcess[str]]:
    """Build a scripted response for ``FakeCommandRunner.responses``."""
    return lambda argv: completed(argv, returncode, stdout, stderr)


def argvs(command: FakeCommandRunner) -> list[list[str]]:
    """Return the argv of every command recorded by a fake runner."""
    return [argv for argv, _ in command.calls]


def plain_output() -> Output:
    """Return a non-interactive output sink for rendering assertions."""
    return Output(StringIO(), no_color=True)


@contextmanager
def serve_http(handler: type[http.server.BaseHTTPRequestHandler]) -> Iterator[str]:
    """Run a local HTTP server on a random port and yield its base URL."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    root: Path | None = None,
    project: Project | None = None,
    vars: Vars | None = None,
    facts: dict[str, Any] | None = None,
    bundle_vars: Mapping[Bundle, Vars] | None = None,
    output=None,
) -> TaskContext:
    root = tmp_path if root is None else root
    return TaskContext(
        dry_run=dry_run,
        diff=diff,
        verbose=verbose,
        root=root,
        project=project or Project(root=root),
        vars=vars or Vars(),
        facts=facts or {},
        sudo=sudo or FakeSudo(),  # type: ignore[arg-type]
        command=command or FakeCommandRunner(),  # type: ignore[arg-type]
        bundle_vars=bundle_vars,
        output=output,
    )


def privileged_context(tmp_path) -> TaskContext:
    """Return a context whose privileged commands run locally."""
    sudo = PassthroughSudo()
    return make_context(tmp_path, command=CommandRunner(sudo), sudo=sudo, output=plain_output())


def local_sudo_context(tmp_path, **kwargs) -> TaskContext:
    """Return a context where ``sudo=True`` file operations run locally in tests."""
    return make_context(tmp_path, sudo=FakeSudo(is_root=True), **kwargs)


def add_task(
    bundle: Bundle,
    func: Callable[[TaskContext], Any],
    *,
    name: str | None = None,
    tags: Sequence[str] = (),
) -> Callable[[TaskContext], Any]:
    if name is None:
        return bundle.task(tags=tags)(func)
    return bundle.task(name, tags=tags)(func)
