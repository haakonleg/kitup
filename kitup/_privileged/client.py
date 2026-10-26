"""Unprivileged client and embedded script builder for the privileged worker."""

from __future__ import annotations

import contextlib
import pkgutil
import queue
import subprocess
import sys
import threading
from collections import deque
from functools import lru_cache
from typing import IO, TYPE_CHECKING, Any

from ..errors import TaskFailed
from .protocol import MAX_MESSAGE_BYTES, READY, Request, Response

if TYPE_CHECKING:
    from ..context import TaskContext


class _Reader(threading.Thread):
    """Read lines from a worker pipe without blocking the request path."""

    def __init__(self, stream: IO[str], *, queue_lines: bool) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._queue: queue.Queue[str | None] | None = (
            queue.Queue() if queue_lines else None
        )
        self._tail: deque[str] | None = None if queue_lines else deque(maxlen=100)
        self.done = threading.Event()

    def run(self) -> None:
        try:
            for line in self._stream:
                if self._tail is not None:
                    self._tail.append(line)
                if self._queue is not None:
                    self._queue.put(line)
        except (OSError, ValueError):
            pass
        finally:
            if self._queue is not None:
                self._queue.put(None)
            self.done.set()

    def readline(self, timeout: float) -> str | None:
        if self._queue is None:
            raise RuntimeError("reader is not configured for line reads")
        return self._queue.get(timeout=timeout)

    @property
    def tail(self) -> str:
        if self._tail is None:
            return ""
        return "".join(self._tail)


class PrivilegedWorker:
    """Lazily started, long-lived privileged worker process."""

    def __init__(
        self,
        ctx: TaskContext,
        *,
        timeout: float = 600,
        startup_timeout: float = 30,
    ) -> None:
        self.ctx = ctx
        self.timeout = timeout
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen[str] | None = None
        self._stdout: _Reader | None = None
        self._stderr: _Reader | None = None
        self._closed = False
        self._lock = threading.RLock()

    def request(self, request: Request) -> Any:
        """Send one request and return its result, raising on worker errors."""
        payload = request.encode()
        size = len(payload.encode("utf-8")) + 1
        if size > MAX_MESSAGE_BYTES:
            raise TaskFailed(
                f"privileged worker request too large ({size} bytes; "
                f"limit {MAX_MESSAGE_BYTES})"
            )

        with self._lock:
            self._start_locked()
            process = self._process
            if process is None or process.stdin is None:
                raise TaskFailed("privileged worker is not running")
            try:
                process.stdin.write(payload + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._discard_locked()
                raise TaskFailed(f"privileged worker is not available: {exc}") from exc

            try:
                line = self._read_line(self.timeout)
            except TaskFailed:
                self._discard_locked()
                raise

            try:
                response = Response.decode(line)
            except ValueError as exc:
                self._discard_locked()
                raise TaskFailed(f"invalid privileged worker response: {exc}") from exc

            if response.error is not None:
                if response.fatal:
                    self._discard_locked()
                raise TaskFailed(
                    f"privileged worker {request.action} failed: {response.error}"
                )
            return response.result

    def start(self) -> None:
        """Start the worker on first use and wait for its ready token."""
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._process is not None:
            return
        if self._closed:
            raise TaskFailed("privileged worker is closed")

        self.ctx.ensure_sudo()
        argv = [sys.executable, "-I", "-u", "-c", _embedded_script()]
        if not self.ctx.sudo.is_root:
            argv = self.ctx.sudo.wrap(argv)

        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                close_fds=True,
                # Keep the parent session so sudo's tty-based credential cache
                # remains valid, while still isolating the worker from Ctrl-C.
                process_group=0,
            )
        except OSError as exc:
            raise TaskFailed(f"could not start privileged worker: {exc}") from exc

        self._process = process
        if process.stdout is None or process.stderr is None:
            self._discard_locked()
            raise TaskFailed("privileged worker pipes are unavailable")

        self._stdout = _Reader(process.stdout, queue_lines=True)
        self._stderr = _Reader(process.stderr, queue_lines=False)
        self._stdout.start()
        self._stderr.start()

        try:
            line = self._read_line(self.startup_timeout)
        except TaskFailed:
            self._discard_locked()
            raise
        if line.strip() != READY:
            self._discard_locked()
            raise TaskFailed(f"unexpected privileged worker startup response: {line.strip()!r}")

    def _read_line(self, timeout: float) -> str:
        if self._stdout is None:
            raise TaskFailed("privileged worker is not running")
        try:
            line = self._stdout.readline(timeout)
        except queue.Empty:
            raise TaskFailed(
                f"timed out waiting for privileged worker after {timeout:g}s"
            ) from None
        if line is None:
            if self._stderr is not None:
                self._stderr.done.wait(timeout=0.5)
            detail = self._stderr.tail.strip() if self._stderr is not None else ""
            process = self._process
            code = process.poll() if process is not None else None
            message = "privileged worker exited unexpectedly"
            if code is not None:
                message = f"{message} (exit code {code})"
            if detail:
                message = f"{message}: {detail}"
            raise TaskFailed(message)
        return line

    def close(self) -> None:
        """Close stdin and reap the worker, falling back to signals."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._discard_locked()

    def _discard_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            self._stdout = None
            self._stderr = None
            return

        try:
            if process.stdin is not None:
                process.stdin.close()
        except (OSError, ValueError):
            pass

        exited = False
        try:
            process.wait(timeout=1)
            exited = True
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.terminate()
                process.wait(timeout=1)
                exited = True
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=1)
                    exited = True
                except (OSError, subprocess.TimeoutExpired):
                    pass

        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        self._stdout = None
        self._stderr = None
        if not exited:
            # The old root worker may still be alive; do not risk spawning a
            # second one. Future requests fail closed.
            self._closed = True

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


@lru_cache(maxsize=1)
def _embedded_script() -> str:
    protocol = _read_source(__package__, "protocol.py")
    helper = _read_source("kitup._utils", "file_helper.py")
    worker = _read_source(__package__, "worker.py")
    return f"{protocol.strip()}\n\n{helper.strip()}\n\n{worker.strip()}\n\nmain()\n"


def _read_source(package: str, name: str) -> str:
    data = pkgutil.get_data(package, name)
    if data is None:
        raise TaskFailed(f"embedded source is unavailable: {package}/{name}")
    return data.decode("utf-8")
