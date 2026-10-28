from __future__ import annotations

import hashlib
import http.server
import stat
import threading

import pytest

from kitup.errors import TaskFailed
from kitup.modules import download

from .helpers import make_context


class ServerState:
    def __init__(self) -> None:
        self.payload = b"downloaded\n"
        self.requests = 0
        self.headers: list[dict[str, str]] = []

@pytest.fixture
def http_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    state = ServerState()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests += 1
            state.headers.append(dict(self.headers.items()))
            if self.path == "/missing":
                self.send_error(404, "Not Found")
                return
            if self.path == "/truncated":
                body = b"short"
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(body)
                return

            body = state.payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01),
        daemon=True,
    )
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_to_file_downloads_and_is_idempotent(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    ctx = make_context(tmp_path)

    download.to_file(ctx, f"{base}/file.txt", target)

    assert target.read_bytes() == state.payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert state.requests == 1
    assert len(ctx.changes) == 1

    second = make_context(tmp_path)
    download.to_file(second, f"{base}/file.txt", target)

    assert state.requests == 1
    assert second.changes == []

def test_to_file_force_refreshes_only_changed_content(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    download.to_file(make_context(tmp_path), f"{base}/file.txt", target)

    same = make_context(tmp_path)
    download.to_file(same, f"{base}/file.txt", target, force=True)

    assert state.requests == 2
    assert same.changes == []

    state.payload = b"changed\n"
    changed = make_context(tmp_path)
    download.to_file(changed, f"{base}/file.txt", target, force=True)

    assert state.requests == 3
    assert target.read_bytes() == b"changed\n"
    assert len(changed.changes) == 1

def test_to_file_checksum_skips_matching_file(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    target.write_bytes(state.payload)
    checksum = f"sha256:{hashlib.sha256(state.payload).hexdigest()}"

    ctx = make_context(tmp_path)
    download.to_file(ctx, f"{base}/file.txt", target, checksum=checksum)

    assert state.requests == 0
    assert ctx.changes == []

def test_to_file_checksum_mismatch_replaces_file(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    target.write_bytes(b"old\n")
    checksum = f"sha256:{hashlib.sha256(state.payload).hexdigest()}"

    ctx = make_context(tmp_path)
    download.to_file(ctx, f"{base}/file.txt", target, checksum=checksum)

    assert target.read_bytes() == state.payload
    assert state.requests == 1
    assert len(ctx.changes) == 1

def test_to_file_bad_checksum_leaves_destination_untouched(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    target.write_bytes(b"keep\n")

    with pytest.raises(TaskFailed, match="checksum mismatch"):
        download.to_file(
            make_context(tmp_path),
            f"{base}/file.txt",
            target,
            checksum=f"sha256:{'0' * 64}",
        )

    assert target.read_bytes() == b"keep\n"
    assert state.requests == 1

def test_to_file_dry_run_does_not_fetch_or_write(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    ctx = make_context(tmp_path, dry_run=True)

    download.to_file(ctx, f"{base}/file.txt", target)

    assert state.requests == 0
    assert not target.exists()
    assert len(ctx.changes) == 1

def test_to_file_uses_url_basename_for_directory_destination(tmp_path, http_server):
    state, base = http_server
    directory = tmp_path / "downloads"
    directory.mkdir()

    download.to_file(make_context(tmp_path), f"{base}/file.txt", directory)

    assert (directory / "file.txt").read_bytes() == state.payload

def test_to_file_http_error_is_a_task_failure(tmp_path, http_server):
    _, base = http_server
    target = tmp_path / "file.txt"

    with pytest.raises(TaskFailed, match="HTTP 404"):
        download.to_file(make_context(tmp_path), f"{base}/missing", target)

    assert not target.exists()

def test_to_file_rejects_truncated_response(tmp_path, http_server):
    _, base = http_server
    target = tmp_path / "file.txt"

    with pytest.raises(TaskFailed, match="incomplete download"):
        download.to_file(make_context(tmp_path), f"{base}/truncated", target)

    assert not target.exists()

def test_to_file_rejects_symlink_destination(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "target"
    target.write_bytes(b"keep\n")
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(TaskFailed, match="symlink"):
        download.to_file(make_context(tmp_path), f"{base}/file.txt", link)

    assert state.requests == 0
    assert link.is_symlink()
    assert target.read_bytes() == b"keep\n"

def test_to_file_reconciles_metadata_without_download(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    target.write_bytes(state.payload)
    target.chmod(0o600)
    ctx = make_context(tmp_path)

    download.to_file(ctx, f"{base}/file.txt", target, mode=0o640)

    assert state.requests == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert len(ctx.changes) == 1

    second = make_context(tmp_path)
    download.to_file(second, f"{base}/file.txt", target, mode=0o640)

    assert second.changes == []

def test_to_file_validates_arguments_without_fetching(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="URL must not be empty"):
        download.to_file(ctx, "", target)
    with pytest.raises(TaskFailed, match="only http and https"):
        download.to_file(ctx, "file:///tmp/source", target)
    with pytest.raises(TaskFailed, match="algorithm:digest"):
        download.to_file(ctx, f"{base}/file.txt", target, checksum="sha256")
    with pytest.raises(TaskFailed, match="timeout"):
        download.to_file(ctx, f"{base}/file.txt", target, timeout=0)
    with pytest.raises(TaskFailed, match="header"):
        download.to_file(ctx, f"{base}/file.txt", target, headers={"X-Test": "a\nb"})
    with pytest.raises(TaskFailed, match="mode"):
        download.to_file(ctx, f"{base}/file.txt", target, mode=True)

    assert state.requests == 0

def test_to_file_creates_new_files_with_the_requested_mode(tmp_path, http_server):
    state, base = http_server
    target = tmp_path / "file.txt"

    download.to_file(make_context(tmp_path), f"{base}/file.txt", target, mode=0o600)

    assert target.read_bytes() == state.payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

def test_to_bytes_returns_content_and_verifies_checksum(tmp_path, http_server):
    state, base = http_server
    ctx = make_context(tmp_path)
    checksum = f"sha256:{hashlib.sha256(state.payload).hexdigest()}"

    content = download.to_bytes(ctx, f"{base}/file.txt", checksum=checksum)

    assert content == state.payload
    assert state.requests == 1
    assert ctx.changes == []

def test_to_bytes_enforces_max_bytes(tmp_path, http_server):
    _, base = http_server

    with pytest.raises(TaskFailed, match="max_bytes"):
        download.to_bytes(make_context(tmp_path), f"{base}/file.txt", max_bytes=4)
