from __future__ import annotations

import base64
import os
import stat
import threading

import pytest

from kitup._privileged import PrivilegedWorker
from kitup._privileged import client as client_module
from kitup._privileged.protocol import Request, Response
from kitup._privileged.worker import _read_limited
from kitup._utils.file_helper import stat_path
from kitup.errors import TaskFailed
from kitup.modules import files

from .helpers import FakeSudo, PassthroughSudo, make_context


@pytest.fixture
def ctx(tmp_path):
    context = make_context(tmp_path, sudo=PassthroughSudo())
    try:
        yield context
    finally:
        context.close()


def test_worker_stays_in_parent_session(ctx):
    worker = PrivilegedWorker(ctx)

    try:
        worker.start()
        process = worker._process
        assert process is not None
        assert os.getsid(process.pid) == os.getsid(0)
    finally:
        worker.close()


def test_worker_rejects_oversized_request_before_start(ctx, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_MESSAGE_BYTES", 10)
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="too large"):
            worker.request(Request("stat", {"path": str(ctx.config_dir)}))
        assert worker._process is None
    finally:
        worker.close()


def test_worker_closes_on_fatal_error(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(
        client_module,
        "_embedded_script",
        lambda: """\
import sys
print('ready', flush=True)
sys.stdin.readline()
print('{"error":"fatal boom","fatal":true}', flush=True)
""",
    )
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="fatal boom"):
            worker.request(Request("stat", {"path": str(tmp_path)}))
        assert worker._process is None
    finally:
        worker.close()


def test_read_limited_rejects_large_file(tmp_path):
    path = tmp_path / "large"
    path.write_bytes(b"x" * 1025)

    with pytest.raises(ValueError, match="too large"):
        _read_limited(str(path), 1024)
    assert _read_limited(str(path), 1025) == b"x" * 1025


def test_worker_file_actions(ctx, tmp_path):
    path = tmp_path / "file"
    worker = PrivilegedWorker(ctx)

    try:
        assert (
            worker.request(
                Request(
                    "write",
                    {
                        "path": str(path),
                        "content": base64.b64encode(b"data\n").decode(),
                        "mode": 0o600,
                    },
                )
            )
            == {}
        )
        info = worker.request(Request("stat", {"path": str(path)}))
        assert info["exists"] and info["is_file"] and info["mode"] == 0o600

        read = worker.request(Request("read", {"path": str(path)}))
        assert base64.b64decode(read["content"]) == b"data\n"

        assert worker.request(Request("remove", {"path": str(path)})) == {}
        assert not path.exists()
    finally:
        worker.close()


def test_worker_move_action(ctx, tmp_path):
    source = tmp_path / "source"
    source.write_text("data\n")
    destination = tmp_path / "destination"
    worker = PrivilegedWorker(ctx)

    try:
        assert (
            worker.request(
                Request("move", {"source": str(source), "path": str(destination)})
            )
            == {}
        )
        assert not source.exists()
        assert destination.read_text() == "data\n"
    finally:
        worker.close()


def test_worker_directory_metadata_and_symlink(ctx, tmp_path):
    directory = tmp_path / "dir"
    link = tmp_path / "link"
    worker = PrivilegedWorker(ctx)

    try:
        assert (
            worker.request(Request("mkdir", {"path": str(directory), "mode": 0o700}))
            == {}
        )
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

        assert (
            worker.request(Request("metadata", {"path": str(directory), "mode": 0o750}))
            == {}
        )
        assert stat.S_IMODE(directory.stat().st_mode) == 0o750

        assert (
            worker.request(Request("symlink", {"path": str(link), "target": "target"}))
            == {}
        )
        assert link.is_symlink()
    finally:
        worker.close()


def test_worker_rejects_unknown_action_and_stays_alive(ctx, tmp_path):
    path = tmp_path / "file"
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="unknown privileged action"):
            worker.request(Request("nope", {}))

        worker.request(
            Request("write", {"path": str(path), "content": "", "mode": 0o600})
        )
        assert path.exists()
    finally:
        worker.close()


def test_worker_reports_malformed_request_lines(ctx):
    worker = PrivilegedWorker(ctx)
    worker.start()
    process = worker._process
    assert process is not None and process.stdin is not None

    try:
        process.stdin.write("not json\n")
        process.stdin.flush()
        response = Response.decode(worker._read_line(worker.timeout))
        assert response.error is not None
        assert "invalid JSON" in response.error

        process.stdin.write("\n")
        process.stdin.flush()
        response = Response.decode(worker._read_line(worker.timeout))
        assert response.error == "empty request line"
        assert response.fatal is True
    finally:
        worker.close()


def test_worker_start_failure_includes_stderr_and_exit_code(ctx, monkeypatch):
    monkeypatch.setattr(
        client_module,
        "_embedded_script",
        lambda: (
            "import sys\n"
            "print('startup boom', file=sys.stderr, flush=True)\n"
            "sys.exit(7)\n"
        ),
    )
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed) as excinfo:
            worker.start()
        message = str(excinfo.value)
        assert "startup boom" in message
        assert "exit code 7" in message
    finally:
        worker.close()


def test_worker_rejects_relative_path(ctx):
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="absolute"):
            worker.request(Request("stat", {"path": "relative"}))
    finally:
        worker.close()


class CaptureWorker:
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def request(self, request: Request):
        self.requests.append(request)
        if request.action == "stat":
            return stat_path(request.params["path"]) or {"exists": False}
        return {}


def test_privileged_files_normalize_relative_paths(tmp_path, monkeypatch):
    context = make_context(tmp_path, sudo=FakeSudo())
    capture = CaptureWorker()
    context.privileged_worker = capture  # type: ignore[assignment]
    monkeypatch.chdir(tmp_path)

    files.file(context, "relative.txt", content="data\n", sudo=True)

    assert capture.requests
    assert all(request.params["path"].startswith("/") for request in capture.requests)
    assert capture.requests[-1].params["path"] == str(tmp_path / "relative.txt")


def test_privileged_path_preserves_symlink_and_dotdot(ctx, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    (b / "data").mkdir(parents=True)
    (a / "link").symlink_to(b / "data", target_is_directory=True)
    path = a / "link" / ".." / "x"

    files.file(ctx, path, content="data\n", sudo=True)

    assert (b / "x").read_text() == "data\n"
    assert not (a / "x").exists()


def test_worker_invalid_response_closes(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(
        client_module,
        "_embedded_script",
        lambda: (
            "import sys\n"
            "print('ready', flush=True)\n"
            "sys.stdin.readline()\n"
            "print('not json', flush=True)\n"
        ),
    )
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="invalid privileged worker response"):
            worker.request(Request("stat", {"path": str(tmp_path)}))
        assert worker._process is None
    finally:
        worker.close()


def test_worker_rejects_invalid_mode_and_ids(ctx, tmp_path):
    path = tmp_path / "file"
    worker = PrivilegedWorker(ctx)

    try:
        for params in (
            {"path": str(path), "content": "", "mode": True},
            {"path": str(path), "content": "", "mode": -1},
            {"path": str(path), "content": "", "mode": 0o10000},
            {"path": str(path), "content": "", "owner": -1},
            {"path": str(path), "content": "", "owner": True},
        ):
            with pytest.raises(TaskFailed, match="mode|owner"):
                worker.request(Request("write", params))
        assert not path.exists()
    finally:
        worker.close()


def test_worker_reuses_one_process(ctx, tmp_path):
    worker = PrivilegedWorker(ctx)

    try:
        worker.request(Request("stat", {"path": str(tmp_path)}))
        process = worker._process
        assert process is not None
        pid = process.pid

        worker.request(Request("stat", {"path": str(tmp_path)}))
        assert worker._process is process
        assert worker._process.pid == pid
    finally:
        worker.close()


def test_worker_serializes_concurrent_requests(ctx, tmp_path):
    existing = tmp_path / "existing"
    existing.write_text("x")
    missing = tmp_path / "missing"
    worker = PrivilegedWorker(ctx)
    errors = []

    def check(path, expected):
        try:
            for _ in range(10):
                result = worker.request(Request("stat", {"path": str(path)}))
                if bool(result.get("exists")) != expected:
                    errors.append((str(path), result))
        except Exception as exc:  # noqa: BLE001 - surface thread failures
            errors.append(exc)

    threads = [
        threading.Thread(target=check, args=(existing, True)) for _ in range(4)
    ] + [
        threading.Thread(target=check, args=(missing, False)) for _ in range(4)
    ]

    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not errors
        assert all(not thread.is_alive() for thread in threads)
    finally:
        worker.close()


def test_worker_restarts_after_crash(ctx, tmp_path):
    worker = PrivilegedWorker(ctx)

    try:
        worker.request(Request("stat", {"path": str(tmp_path)}))
        first = worker._process
        assert first is not None
        first.kill()
        first.wait(timeout=5)

        with pytest.raises(TaskFailed, match="privileged worker"):
            worker.request(Request("stat", {"path": str(tmp_path)}))

        assert worker._process is None
        assert worker._closed is False

        result = worker.request(Request("stat", {"path": str(tmp_path)}))
        assert result["exists"] is True
        assert worker._process is not first
    finally:
        worker.close()


def test_worker_close_is_permanent(ctx, tmp_path):
    worker = PrivilegedWorker(ctx)

    worker.request(Request("stat", {"path": str(tmp_path)}))
    worker.close()

    with pytest.raises(TaskFailed, match="closed"):
        worker.request(Request("stat", {"path": str(tmp_path)}))


def test_worker_timeout_closes_process(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(
        client_module,
        "_embedded_script",
        lambda: (
            "import sys\n"
            "print('ready', flush=True)\n"
            "for _ in sys.stdin:\n"
            "    pass\n"
        ),
    )
    worker = PrivilegedWorker(ctx, timeout=0.2, startup_timeout=5)

    try:
        with pytest.raises(TaskFailed, match="timed out"):
            worker.request(Request("stat", {"path": str(tmp_path)}))
        assert worker._process is None
    finally:
        worker.close()


def test_unprivileged_operations_do_not_start_worker(tmp_path):
    context = make_context(tmp_path, sudo=FakeSudo())

    files.file(context, tmp_path / "file", content="data\n")

    assert context.privileged_worker._process is None
    context.close()



