import os
import stat
import threading

import pytest

from kitup._privileged import PrivilegedWorker
from kitup._privileged import client as client_module
from kitup._privileged.protocol import Request, Response
from kitup._utils.file_primitives import stat_path
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


def test_worker_rejects_oversized_header_before_start(ctx, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_HEADER_BYTES", 10)
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="too large"):
            worker.request(Request("stat", {"path": str(ctx.root)}))
        assert worker._process is None
    finally:
        worker.close()


def test_worker_closes_on_fatal_error(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(
        client_module,
        "_embedded_script",
        lambda: (
            """\
import sys
print('ready', flush=True)
sys.stdin.readline()
print('{"error":"fatal boom","fatal":true}', flush=True)
"""
        ),
    )
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="fatal boom"):
            worker.request(Request("stat", {"path": str(tmp_path)}))
        assert worker._process is None
    finally:
        worker.close()


def test_worker_reports_startup_error(ctx, monkeypatch):
    monkeypatch.setattr(
        client_module,
        "_embedded_script",
        lambda: 'import sys\nprint(\'{"error":"boom"}\', flush=True)\n',
    )
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="boom"):
            worker.start()
    finally:
        worker.close()


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
                        "mode": 0o600,
                    },
                    content_length=5,
                ),
                content=b"data\n",
            ).result
            == {}
        )
        info = worker.request(Request("stat", {"path": str(path)})).result
        assert info["exists"] and info["is_file"] and info["mode"] == 0o600
        assert info["size"] == 5 and info["mtime_ns"] > 0

        read = worker.request(Request("read", {"path": str(path)}))
        assert read.result["is_file"] is True
        assert read.content == b"data\n"

        assert worker.request(Request("remove", {"path": str(path)})).result == {}
        assert not path.exists()
    finally:
        worker.close()


def test_worker_reads_and_writes_empty_files(ctx, tmp_path):
    path = tmp_path / "empty"
    worker = PrivilegedWorker(ctx)

    try:
        worker.request(Request("write", {"path": str(path)}, content_length=0), content=b"")
        read = worker.request(Request("read", {"path": str(path)}))
        assert read.content == b""
        assert read.content_length == 0
    finally:
        worker.close()


def test_worker_round_trips_content_larger_than_the_header_limit(ctx, tmp_path):
    path = tmp_path / "large"
    content = b"x" * (2 * 1024 * 1024)
    worker = PrivilegedWorker(ctx)

    try:
        worker.request(Request("write", {"path": str(path)}, content_length=len(content)), content=content)
        read = worker.request(Request("read", {"path": str(path)}))
        assert read.content == content
        assert read.content_length == len(content)
    finally:
        worker.close()


def test_worker_snapshot_skips_content_on_size_mismatch(ctx, tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"data\n")
    worker = PrivilegedWorker(ctx)

    try:
        response = worker.request(Request("snapshot", {"path": str(path), "content_size": 5}))
        assert response.result["is_file"] is True
        assert response.content == b"data\n"

        skipped = worker.request(Request("snapshot", {"path": str(path), "content_size": 1}))
        assert skipped.result["is_file"] is True
        assert skipped.content is None

        forced = worker.request(Request("snapshot", {"path": str(path), "content_size": 1, "diff": True}))
        assert forced.content == b"data\n"

        missing = worker.request(Request("snapshot", {"path": str(tmp_path / "missing")}))
        assert missing.result == {"exists": False}
        assert missing.content is None
    finally:
        worker.close()


def test_worker_compare_action(ctx, tmp_path):
    source = tmp_path / "source"
    source.write_text("data\n")
    same = tmp_path / "same"
    same.write_text("data\n")
    different = tmp_path / "different"
    different.write_text("other\n")
    link = tmp_path / "link"
    link.symlink_to(source)
    directory = tmp_path / "directory"
    directory.mkdir()
    worker = PrivilegedWorker(ctx)

    try:
        result = worker.request(Request("compare", {"source": str(source), "path": str(same)})).result
        assert result["equal"] is True
        assert result["source"]["is_file"] is True
        assert result["target"]["is_file"] is True

        assert (
            worker.request(Request("compare", {"source": str(source), "path": str(different)})).result["equal"] is False
        )
        assert worker.request(Request("compare", {"source": str(source), "path": str(link)})).result["equal"] is False
        assert (
            worker.request(Request("compare", {"source": str(source), "path": str(directory)})).result["equal"] is False
        )
        missing = worker.request(Request("compare", {"source": str(source), "path": str(tmp_path / "missing")})).result
        assert missing["equal"] is False
        assert missing["target"] is None
    finally:
        worker.close()


def test_worker_compare_rejects_non_regular_sources(ctx, tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    link = tmp_path / "link"
    link.symlink_to(directory)
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="regular file"):
            worker.request(Request("compare", {"source": str(directory), "path": str(tmp_path / "dest")}))
        with pytest.raises(TaskFailed, match="regular file"):
            worker.request(Request("compare", {"source": str(link), "path": str(tmp_path / "dest")}))
    finally:
        worker.close()


def test_worker_copy_action(ctx, tmp_path):
    source = tmp_path / "source"
    source.write_text("data\n")
    source.chmod(0o640)
    destination = tmp_path / "destination"
    worker = PrivilegedWorker(ctx)

    try:
        assert worker.request(Request("copy", {"source": str(source), "path": str(destination)})).result == {}
        assert destination.read_text() == "data\n"
        assert stat.S_IMODE(destination.stat().st_mode) == 0o640

        worker.request(Request("copy", {"source": str(source), "path": str(destination), "mode": 0o600}))
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    finally:
        worker.close()


def test_worker_copy_replaces_symlink_and_rejects_directories(ctx, tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")
    target = tmp_path / "target"
    target.write_text("old\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()
    worker = PrivilegedWorker(ctx)

    try:
        worker.request(Request("copy", {"source": str(source), "path": str(link)}))
        assert not link.is_symlink()
        assert link.read_text() == "new\n"
        assert target.read_text() == "old\n"

        with pytest.raises(TaskFailed, match="directory"):
            worker.request(Request("copy", {"source": str(source), "path": str(directory)}))
        with pytest.raises(TaskFailed, match="regular file"):
            worker.request(Request("copy", {"source": str(tmp_path / "missing"), "path": str(tmp_path / "dest")}))
    finally:
        worker.close()


def test_worker_move_action(ctx, tmp_path):
    source = tmp_path / "source"
    source.write_text("data\n")
    destination = tmp_path / "destination"
    worker = PrivilegedWorker(ctx)

    try:
        assert worker.request(Request("move", {"source": str(source), "path": str(destination)})).result == {}
        assert not source.exists()
        assert destination.read_text() == "data\n"
    finally:
        worker.close()


def test_worker_directory_metadata_and_symlink(ctx, tmp_path):
    directory = tmp_path / "dir"
    link = tmp_path / "link"
    worker = PrivilegedWorker(ctx)

    try:
        assert worker.request(Request("mkdir", {"path": str(directory), "mode": 0o700})).result == {}
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

        assert worker.request(Request("metadata", {"path": str(directory), "mode": 0o750})).result == {}
        assert stat.S_IMODE(directory.stat().st_mode) == 0o750

        assert worker.request(Request("symlink", {"path": str(link), "target": "target"})).result == {}
        assert link.is_symlink()
    finally:
        worker.close()


def test_worker_rejects_unknown_action_and_stays_alive(ctx, tmp_path):
    path = tmp_path / "file"
    worker = PrivilegedWorker(ctx)

    try:
        with pytest.raises(TaskFailed, match="unknown privileged action"):
            worker.request(Request("nope", {}))

        worker.request(Request("write", {"path": str(path), "mode": 0o600}, content_length=0), content=b"")
        assert path.exists()
    finally:
        worker.close()


def test_worker_reports_malformed_request_lines(ctx):
    worker = PrivilegedWorker(ctx)
    worker.start()
    process = worker._process
    assert process is not None and process.stdin is not None

    try:
        process.stdin.write(b"not json\n")
        process.stdin.flush()
        response = worker._read_response(worker.timeout)
        assert response.error is not None
        assert "invalid JSON" in response.error
        assert response.fatal is True
    finally:
        worker.close()


def test_worker_reports_empty_request_lines(ctx):
    worker = PrivilegedWorker(ctx)
    worker.start()
    process = worker._process
    assert process is not None and process.stdin is not None

    try:
        process.stdin.write(b"\n")
        process.stdin.flush()
        response = worker._read_response(worker.timeout)
        assert response.error == "empty request line"
        assert response.fatal is True
    finally:
        worker.close()


def test_worker_start_failure_includes_stderr_and_exit_code(ctx, monkeypatch):
    monkeypatch.setattr(
        client_module,
        "_embedded_script",
        lambda: "import sys\nprint('startup boom', file=sys.stderr, flush=True)\nsys.exit(7)\n",
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

    def request(self, request: Request, *, content=None):
        self.requests.append(request)
        if request.action == "stat":
            return Response(result=stat_path(request.params["path"]) or {"exists": False})
        if request.action == "snapshot":
            path = request.params["path"]
            info = stat_path(path)
            if info is None:
                return Response(result={"exists": False})
            return Response(result=info, content_length=0, content=b"")
        return Response(result={})


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
        lambda: "import sys\nprint('ready', flush=True)\nsys.stdin.readline()\nprint('not json', flush=True)\n",
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
            {"path": str(path), "mode": True},
            {"path": str(path), "mode": -1},
            {"path": str(path), "mode": 0o10000},
            {"path": str(path), "owner": -1},
            {"path": str(path), "owner": True},
        ):
            with pytest.raises(TaskFailed, match=r"mode|owner"):
                worker.request(Request("write", params, content_length=0), content=b"")
        assert not path.exists()
    finally:
        worker.close()


def test_worker_rejects_invalid_content_size(ctx, tmp_path):
    path = tmp_path / "file"
    path.write_text("data\n")
    worker = PrivilegedWorker(ctx)

    try:
        for value in (-1, True, "1", 1.5):
            with pytest.raises(TaskFailed, match="content_size"):
                worker.request(Request("snapshot", {"path": str(path), "content_size": value}))
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
                result = worker.request(Request("stat", {"path": str(path)})).result
                if bool(result.get("exists")) != expected:
                    errors.append((str(path), result))
        except Exception as exc:  # noqa: BLE001 - surface thread failures
            errors.append(exc)

    threads = [threading.Thread(target=check, args=(existing, True)) for _ in range(4)] + [
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

        result = worker.request(Request("stat", {"path": str(tmp_path)})).result
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
        lambda: "import sys\nprint('ready', flush=True)\nfor _ in sys.stdin:\n    pass\n",
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


def test_worker_start_reprompts_when_sudo_expires(tmp_path, monkeypatch):
    class ExpiringSudo(PassthroughSudo):
        def __init__(self) -> None:
            super().__init__()
            self.checks = 0

        def credentials_valid(self) -> bool:
            self.checks += 1
            return self.checks == 1

    sudo = ExpiringSudo()
    context = make_context(tmp_path, sudo=sudo)
    worker = PrivilegedWorker(context)
    attempts = []
    spawn = worker._spawn_locked

    def flaky() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise TaskFailed("privileged worker exited unexpectedly: sudo: a password is required")
        spawn()

    monkeypatch.setattr(worker, "_spawn_locked", flaky)
    try:
        worker.start()
        assert len(attempts) == 2
        assert sudo.refresh_count == 1
    finally:
        worker.close()
        context.close()
