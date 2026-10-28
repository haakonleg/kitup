from __future__ import annotations

import http.server
import json
import threading
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

from kitup.errors import TaskFailed
from kitup.modules import github_release

from .helpers import make_context

DIGEST = "sha256:" + "a" * 64


def asset_payload(
    name: str,
    *,
    asset_id: int = 1,
    digest: str | None = DIGEST,
) -> dict[str, object]:
    return {
        "id": asset_id,
        "name": name,
        "browser_download_url": f"https://example.test/releases/{name}",
        "size": 1234,
        "content_type": "application/gzip",
        "digest": digest,
        "download_count": 7,
        "label": None,
        "state": "uploaded",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def release_payload(
    tag: str = "v1.2.3",
    *,
    assets: list[dict[str, object]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    if assets is None:
        assets = [
            asset_payload("tool-linux-x86_64.tar.gz", asset_id=1),
            asset_payload("tool-linux-aarch64.tar.gz", asset_id=2),
            asset_payload("tool-checksums.txt", asset_id=3, digest=None),
        ]
    payload: dict[str, object] = {
        "id": 42,
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": "Release notes",
        "html_url": f"https://github.com/owner/repo/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "created_at": "2026-01-01T00:00:00Z",
        "published_at": "2026-01-01T00:10:00Z",
        "updated_at": "2026-01-01T00:20:00Z",
        "tarball_url": f"https://api.github.com/repos/owner/repo/tarball/{tag}",
        "zipball_url": f"https://api.github.com/repos/owner/repo/zipball/{tag}",
        "assets": assets,
    }
    payload.update(overrides)
    return payload


class ServerState:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.latest_payload = release_payload()
        self.tag_payloads: dict[str, dict[str, object]] = {}

    def header(self, index: int, name: str) -> str | None:
        for key, value in self.headers[index].items():
            if key.lower() == name.lower():
                return value
        return None


@pytest.fixture
def api_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    state = ServerState()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.paths.append(self.path)
            state.headers.append(dict(self.headers.items()))

            parts = self.path.split("/")
            owner = parts[2] if len(parts) > 2 else ""
            if owner == "rate":
                self._send(
                    403,
                    b"",
                    {
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "1700000000",
                    },
                )
                return
            if owner == "bad-json":
                self._send(200, b"{not json")
                return
            if owner == "bad-schema":
                self._send(200, b"{}")
                return
            if owner == "generic-error":
                self._send(500, b"server error")
                return
            if owner == "missing":
                self._send(404, b"")
                return

            if self.path == "/repos/owner/repo/releases/latest":
                self._send_json(200, state.latest_payload)
                return

            prefix = "/repos/owner/repo/releases/tags/"
            if self.path.startswith(prefix):
                tag = unquote(self.path.removeprefix(prefix))
                if tag == "missing":
                    self._send(404, b"")
                    return
                payload = state.tag_payloads.get(tag, release_payload(tag))
                self._send_json(200, payload)
                return

            self._send(404, b"")

        def _send_json(self, status: int, payload: object) -> None:
            self._send(status, json.dumps(payload).encode(), {"Content-Type": "application/json"})

        def _send(
            self,
            status: int,
            body: bytes,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
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
    monkeypatch.setattr(
        github_release,
        "_API_URL",
        f"http://127.0.0.1:{server.server_port}",
    )
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_latest_returns_release_and_assets(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path)

    release = github_release.latest(ctx, "owner/repo")

    assert release.project == "owner/repo"
    assert release.id == 42
    assert release.tag == "v1.2.3"
    assert release.name == "Release v1.2.3"
    assert release.body == "Release notes"
    assert release.url == "https://github.com/owner/repo/releases/tag/v1.2.3"
    assert release.draft is False
    assert release.prerelease is False
    assert release.immutable is True
    assert release.published_at == "2026-01-01T00:10:00Z"
    assert release.tarball_url == "https://api.github.com/repos/owner/repo/tarball/v1.2.3"
    assert [asset.name for asset in release.assets] == [
        "tool-linux-x86_64.tar.gz",
        "tool-linux-aarch64.tar.gz",
        "tool-checksums.txt",
    ]

    asset = release.asset(r"^tool-linux-x86_64\.tar\.gz$")
    assert asset.url == "https://example.test/releases/tool-linux-x86_64.tar.gz"
    assert asset.digest == DIGEST
    assert asset.size == 1234
    assert asset.content_type == "application/gzip"
    assert asset.download_count == 7
    assert release.assets[-1].digest is None

    assert state.paths == ["/repos/owner/repo/releases/latest"]
    assert state.header(0, "Accept") == "application/vnd.github+json"
    assert state.header(0, "X-GitHub-Api-Version") == "2022-11-28"
    user_agent = state.header(0, "User-Agent") or ""
    assert user_agent.startswith("Python-urllib/")
    assert "kitup" not in user_agent.lower()
    assert state.header(0, "Authorization") is None
    assert ctx.changes == []


def test_by_tag_encodes_the_tag(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path)

    release = github_release.by_tag(ctx, "owner/repo", "v1/2")

    assert release.tag == "v1/2"
    assert state.paths == ["/repos/owner/repo/releases/tags/v1%2F2"]


def test_missing_release_is_a_task_failure(tmp_path, api_server):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="not found"):
        github_release.by_tag(ctx, "owner/repo", "missing")

    with pytest.raises(TaskFailed, match="not found"):
        github_release.latest(ctx, "missing/repo")


def test_rate_limit_failure_includes_reset_time(tmp_path, api_server):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed) as error:
        github_release.latest(ctx, "rate/repo")

    message = str(error.value)
    assert "rate limit" in message
    assert "resets at" in message
    assert "unauthenticated" in message


def test_rate_limit_helpers_accept_missing_response_headers():
    forbidden = HTTPError("https://api.github.com", 403, "Forbidden", None, None)
    too_many = HTTPError("https://api.github.com", 429, "Too Many Requests", None, None)

    assert github_release._is_rate_limited(forbidden) is False
    assert github_release._http_error_message(
        forbidden,
        "latest release for owner/repo",
    ) == ("GitHub API request failed for latest release for owner/repo: HTTP 403 Forbidden")
    assert github_release._http_error_message(too_many, "latest release for owner/repo") == (
        "GitHub API rate limit exceeded for latest release for owner/repo "
        "(unauthenticated requests are limited)"
    )


def test_invalid_json_is_a_task_failure(tmp_path, api_server):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid JSON"):
        github_release.latest(ctx, "bad-json/repo")


def test_malformed_payload_is_a_task_failure(tmp_path, api_server):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid GitHub release response"):
        github_release.latest(ctx, "bad-schema/repo")


def test_generic_http_error_is_a_task_failure(tmp_path, api_server):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="HTTP 500"):
        github_release.latest(ctx, "generic-error/repo")


def test_assets_matching_supports_regex(tmp_path, api_server):
    release = github_release.latest(make_context(tmp_path), "owner/repo")

    matches = release.assets_matching(r"^tool-linux-.*\.tar\.gz$")

    assert [asset.name for asset in matches] == [
        "tool-linux-x86_64.tar.gz",
        "tool-linux-aarch64.tar.gz",
    ]


def test_asset_fails_for_missing_or_ambiguous_matches(tmp_path, api_server):
    release = github_release.latest(make_context(tmp_path), "owner/repo")

    with pytest.raises(TaskFailed, match="no asset matching regex"):
        release.asset(r"^tool-windows-.*\.zip$")

    with pytest.raises(TaskFailed, match="multiple assets match regex"):
        release.asset(r"^tool-linux-.*\.tar\.gz$")


def test_by_asset_returns_a_single_regex_match(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path)

    result = github_release.by_asset(ctx, "owner/repo", r"^tool-linux-x86_64\.tar\.gz$")

    assert result.name == "tool-linux-x86_64.tar.gz"
    assert result.digest == DIGEST
    assert state.paths == ["/repos/owner/repo/releases/latest"]


def test_by_asset_uses_the_requested_tag(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path)

    result = github_release.by_asset(
        ctx,
        "owner/repo",
        r"^tool-checksums\.txt$",
        tag="v1.2.3",
    )

    assert result.name == "tool-checksums.txt"
    assert state.paths == ["/repos/owner/repo/releases/tags/v1.2.3"]


def test_by_asset_validates_the_regex_before_fetching(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="asset pattern must be a string"):
        github_release.by_asset(ctx, "owner/repo", 1)
    with pytest.raises(TaskFailed, match="asset pattern must not be empty"):
        github_release.by_asset(ctx, "owner/repo", "")
    with pytest.raises(TaskFailed, match="invalid asset regex"):
        github_release.by_asset(ctx, "owner/repo", "[")

    assert state.paths == []


def test_results_are_cached_per_run(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path)

    first = github_release.latest(ctx, "owner/repo")
    second = github_release.latest(ctx, "owner/repo")

    assert first is second
    assert state.paths == ["/repos/owner/repo/releases/latest"]


def test_dry_run_still_fetches_and_records_no_changes(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path, dry_run=True)

    release = github_release.latest(ctx, "owner/repo")

    assert release.tag == "v1.2.3"
    assert state.paths == ["/repos/owner/repo/releases/latest"]
    assert ctx.changes == []


def test_environment_tokens_are_ignored(tmp_path, api_server, monkeypatch):
    state = api_server
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("GH_TOKEN", "other-secret")

    github_release.latest(make_context(tmp_path), "owner/repo")

    assert state.header(0, "Authorization") is None


def test_arguments_are_validated_without_network_access(tmp_path, api_server):
    state = api_server
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="project must be a string"):
        github_release.latest(ctx, 1)
    with pytest.raises(TaskFailed, match="project must not be empty"):
        github_release.latest(ctx, " ")
    with pytest.raises(TaskFailed, match="owner/repo"):
        github_release.latest(ctx, "owner")
    with pytest.raises(TaskFailed, match="owner/repo"):
        github_release.latest(ctx, "owner/repo/extra")
    with pytest.raises(TaskFailed, match="whitespace"):
        github_release.latest(ctx, "own er/repo")

    with pytest.raises(TaskFailed, match="tag must be a string"):
        github_release.by_tag(ctx, "owner/repo", 1)
    with pytest.raises(TaskFailed, match="tag must not be empty"):
        github_release.by_tag(ctx, "owner/repo", " ")
    with pytest.raises(TaskFailed, match="whitespace"):
        github_release.by_tag(ctx, "owner/repo", "v1 2")

    with pytest.raises(TaskFailed, match="timeout"):
        github_release.latest(ctx, "owner/repo", timeout=0)
    with pytest.raises(TaskFailed, match="timeout"):
        github_release.latest(ctx, "owner/repo", timeout=True)

    assert state.paths == []
