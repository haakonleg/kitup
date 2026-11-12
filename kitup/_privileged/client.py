"""Unprivileged client and embedded script builder for the privileged worker."""

import contextlib
import queue
import subprocess
import sys
import threading
from collections import deque
from dataclasses import replace
from functools import lru_cache
from importlib import resources
from typing import IO, TYPE_CHECKING, override

from .. import PACKAGE_NAME
from ..errors import TaskFailed
from .protocol import MAX_HEADER_BYTES, READY, Request, Response

if TYPE_CHECKING:
    from ..context import TaskContext


class _ResponseReader(threading.Thread):
    """Read framed responses from the worker without blocking the request path."""

    def __init__(self, stream: IO[bytes]) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._queue: queue.Queue[Response | str | None] = queue.Queue()
        self.done = threading.Event()

    @override
    def run(self) -> None:
        try:
            while True:
                line = self._stream.readline(MAX_HEADER_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_HEADER_BYTES:
                    self._queue.put(Response(error="response header too large", fatal=True))
                    break
                text = line.strip()
                if not text:
                    self._queue.put(Response(error="empty response header", fatal=True))
                    break
                if text == READY.encode():
                    self._queue.put(READY)
                    continue
                try:
                    response = Response.decode(text)
                except ValueError as exc:
                    self._queue.put(Response(error=f"invalid privileged worker response: {exc}", fatal=True))
                    break
                if response.content_length is not None:
                    content = self._read_exact(response.content_length)
                    if content is None:
                        self._queue.put(Response(error="truncated privileged worker response", fatal=True))
                        break
                    response.content = content
                self._queue.put(response)
        except OSError, ValueError:
            pass
        finally:
            self._queue.put(None)
            self.done.set()

    def _read_exact(self, size: int) -> bytes | None:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self._stream.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read(self, timeout: float) -> Response | str | None:
        return self._queue.get(timeout=timeout)


class _LineReader(threading.Thread):
    """Read stderr lines from the worker and keep a diagnostic tail."""

    def __init__(self, stream: IO[bytes]) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._tail: deque[str] = deque(maxlen=100)
        self.done = threading.Event()

    @override
    def run(self) -> None:
        try:
            for line in self._stream:
                self._tail.append(line.decode("utf-8", errors="replace"))
        except OSError, ValueError:
            pass
        finally:
            self.done.set()

    @property
    def tail(self) -> str:
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
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout: _ResponseReader | None = None
        self._stderr: _LineReader | None = None
        self._closed = False
        self._lock = threading.RLock()

    def request(self, request: Request, *, content: bytes | None = None) -> Response:
        """Send one request and return its response, raising on worker errors."""
        if content is not None:
            request = replace(request, content_length=len(content))
        elif request.content_length:
            raise TaskFailed("privileged worker request declares content but none was provided")

        header = request.encode().encode("utf-8") + b"\n"
        if len(header) > MAX_HEADER_BYTES:
            raise TaskFailed(
                f"privileged worker request header too large ({len(header)} bytes; limit {MAX_HEADER_BYTES})"
            )

        with self._lock:
            self._start_locked()
            process = self._process
            if process is None or process.stdin is None:
                raise TaskFailed("privileged worker is not running")
            try:
                process.stdin.write(header)
                if content:
                    process.stdin.write(content)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._discard_locked()
                raise TaskFailed(f"privileged worker is not available: {exc}") from exc

            try:
                response = self._read_response(self.timeout)
            except TaskFailed:
                self._discard_locked()
                raise

            if response.error is not None:
                if response.fatal:
                    self._discard_locked()
                raise TaskFailed(f"privileged worker {request.action} failed: {response.error}")
            return response

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
        try:
            self._spawn_locked()
        except TaskFailed:
            if self._closed or self.ctx.sudo.credentials_valid():
                raise
            self.ctx.ensure_sudo()
            self._spawn_locked()

    def _spawn_locked(self) -> None:
        argv = [sys.executable, "-I", "-u", "-c", _embedded_script()]
        if not self.ctx.sudo.is_root:
            argv = self.ctx.sudo.wrap(argv)

        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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

        self._stdout = _ResponseReader(process.stdout)
        self._stderr = _LineReader(process.stderr)
        self._stdout.start()
        self._stderr.start()

        try:
            self._read_ready(self.startup_timeout)
        except TaskFailed:
            self._discard_locked()
            raise

    def _read_ready(self, timeout: float) -> None:
        item = self._read_item(timeout)
        if item == READY:
            return
        if isinstance(item, Response) and item.error is not None:
            raise TaskFailed(f"privileged worker failed to start: {item.error}")
        raise TaskFailed(f"unexpected privileged worker startup response: {item!r}")

    def _read_response(self, timeout: float) -> Response:
        item = self._read_item(timeout)
        if isinstance(item, str):
            raise TaskFailed(f"unexpected privileged worker response: {item!r}")
        return item

    def _read_item(self, timeout: float) -> Response | str:
        if self._stdout is None:
            raise TaskFailed("privileged worker is not running")
        try:
            item = self._stdout.read(timeout)
        except queue.Empty:
            raise TaskFailed(f"timed out waiting for privileged worker after {timeout:g}s") from None
        if item is None:
            raise self._exit_error()
        return item

    def _exit_error(self) -> TaskFailed:
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
        return TaskFailed(message)

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
        except OSError, ValueError:
            pass

        exited = False
        try:
            process.wait(timeout=1)
            exited = True
        except OSError, subprocess.TimeoutExpired:
            try:
                process.terminate()
                process.wait(timeout=1)
                exited = True
            except OSError, subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=1)
                    exited = True
                except OSError, subprocess.TimeoutExpired:
                    pass

        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()
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
    primitives = _read_source(f"{PACKAGE_NAME}._utils", "file_primitives.py")
    worker = _read_source(__package__, "worker.py")
    return f"{protocol.strip()}\n\n{primitives.strip()}\n\n{worker.strip()}\n\nmain()\n"


def _read_source(package: str, name: str) -> str:
    try:
        return resources.files(package).joinpath(name).read_text(encoding="utf-8")
    except (ImportError, OSError) as exc:
        raise TaskFailed(f"embedded source is unavailable: {package}/{name}") from exc
