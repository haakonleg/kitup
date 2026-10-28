"""Read public GitHub release metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote

from .._utils.http import open_url, read_bytes, validate_timeout
from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_text

_API_URL = "https://api.github.com"
_DEFAULT_TIMEOUT = 30
_MAX_BYTES = 10 * 1024 * 1024
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"

__all__ = ["Asset", "Release", "by_asset", "by_tag", "latest"]


@dataclass(frozen=True, slots=True)
class Asset:
    """One downloadable asset attached to a GitHub release."""

    id: int
    name: str
    url: str
    size: int
    content_type: str
    digest: str | None
    download_count: int
    label: str | None
    state: str
    created_at: str | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class Release:
    """Metadata for one public GitHub release."""

    project: str
    id: int
    tag: str
    name: str | None
    body: str | None
    url: str
    draft: bool
    prerelease: bool
    immutable: bool | None
    created_at: str | None
    published_at: str | None
    updated_at: str | None
    tarball_url: str | None
    zipball_url: str | None
    assets: tuple[Asset, ...]

    def assets_matching(self, pattern: str) -> tuple[Asset, ...]:
        """Return all assets whose names match a regular expression."""
        return _matching_assets(self, _compile_asset_pattern(pattern))

    def asset(self, pattern: str) -> Asset:
        """Return the single asset whose name matches a regular expression.

        When nothing matches, or more than one asset matches, the task fails and
        lists the relevant names.
        """
        return _single_asset(self, _compile_asset_pattern(pattern))


def latest(
    ctx: TaskContext,
    project: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Release:
    """Return the latest published full release for a public repository.

    GitHub defines the latest release as the most recent non-draft,
    non-prerelease release. Use :func:`by_tag` to fetch a prerelease.
    """
    project = _validate_project(project)
    validate_timeout(timeout)

    label = f"latest release for {project}"
    url = f"{_API_URL}/repos/{_project_path(project)}/releases/latest"
    key = _memo_key(project, None, timeout)
    return ctx.memo(
        key,
        lambda: _load_release(url, label, project, timeout),
    )


def by_tag(
    ctx: TaskContext,
    project: str,
    tag: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Release:
    """Return the published release for ``tag`` in a public repository."""
    project = _validate_project(project)
    tag = _validate_tag(tag)
    validate_timeout(timeout)

    label = f"release {tag!r} for {project}"
    encoded_tag = quote(tag, safe="")
    url = f"{_API_URL}/repos/{_project_path(project)}/releases/tags/{encoded_tag}"
    key = _memo_key(project, tag, timeout)
    return ctx.memo(
        key,
        lambda: _load_release(url, label, project, timeout),
    )


def by_asset(
    ctx: TaskContext,
    project: str,
    pattern: str,
    *,
    tag: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Asset:
    """Return the single asset whose name matches a regular expression.

    By default the release metadata comes from the latest release. Pass ``tag``
    to select a specific release instead.
    """
    regex = _compile_asset_pattern(pattern)
    if tag is None:
        release = latest(ctx, project, timeout=timeout)
    else:
        release = by_tag(ctx, project, tag, timeout=timeout)
    return _single_asset(release, regex)


def _compile_asset_pattern(pattern: str) -> re.Pattern[str]:
    if not isinstance(pattern, str):
        raise TaskFailed("asset pattern must be a string")
    require_text(pattern, "asset pattern")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise TaskFailed(f"invalid asset regex: {exc}") from exc


def _matching_assets(release: Release, regex: re.Pattern[str]) -> tuple[Asset, ...]:
    return tuple(asset for asset in release.assets if regex.search(asset.name))


def _single_asset(release: Release, regex: re.Pattern[str]) -> Asset:
    matches = _matching_assets(release, regex)
    if not matches:
        available = ", ".join(asset.name for asset in release.assets) or "none"
        raise TaskFailed(
            f"no asset matching regex {regex.pattern!r} in release {release.tag!r}; "
            f"available: {available}"
        )
    if len(matches) > 1:
        names = ", ".join(asset.name for asset in matches)
        raise TaskFailed(
            f"multiple assets match regex {regex.pattern!r} in release {release.tag!r}: {names}"
        )
    return matches[0]


def _load_release(
    url: str,
    label: str,
    project: str,
    timeout: float,
) -> Release:
    payload = _fetch_json(url, label, timeout)
    return _parse_release(project, payload)


def _fetch_json(
    url: str,
    label: str,
    timeout: float,
) -> Mapping[str, Any]:
    headers = {
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
    }
    try:
        response = open_url(
            url,
            headers=headers,
            timeout=timeout,
            label="GitHub API",
            context=label,
        )
    except HTTPError as exc:
        try:
            message = _http_error_message(exc, label)
        finally:
            exc.close()
        raise TaskFailed(message) from exc

    with response:
        body = read_bytes(
            response,
            max_bytes=_MAX_BYTES,
            label="GitHub API response",
            context=label,
        )
    return _decode_json(body, label)


def _decode_json(body: bytes, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskFailed(f"invalid JSON response from GitHub API for {label}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise TaskFailed(f"invalid GitHub release response for {label}: expected a JSON object")
    return payload


def _parse_release(project: str, payload: Mapping[str, Any]) -> Release:
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise _invalid_response("field 'assets' must be an array")

    return Release(
        project=project,
        id=_required_int(payload, "id"),
        tag=_required_str(payload, "tag_name"),
        name=_optional_str(payload, "name"),
        body=_optional_str(payload, "body"),
        url=_required_str(payload, "html_url"),
        draft=_required_bool(payload, "draft"),
        prerelease=_required_bool(payload, "prerelease"),
        immutable=_optional_bool(payload, "immutable"),
        created_at=_optional_str(payload, "created_at"),
        published_at=_optional_str(payload, "published_at"),
        updated_at=_optional_str(payload, "updated_at"),
        tarball_url=_optional_str(payload, "tarball_url"),
        zipball_url=_optional_str(payload, "zipball_url"),
        assets=tuple(_parse_asset(asset) for asset in assets),
    )


def _parse_asset(payload: Any) -> Asset:
    if not isinstance(payload, Mapping):
        raise _invalid_response("asset entries must be objects")

    return Asset(
        id=_required_int(payload, "id"),
        name=_required_str(payload, "name"),
        url=_required_str(payload, "browser_download_url"),
        size=_required_int(payload, "size"),
        content_type=_required_str(payload, "content_type"),
        digest=_optional_str(payload, "digest"),
        download_count=_required_int(payload, "download_count"),
        label=_optional_str(payload, "label"),
        state=_required_str(payload, "state"),
        created_at=_optional_str(payload, "created_at"),
        updated_at=_optional_str(payload, "updated_at"),
    )


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise _invalid_response(f"field {key!r} must be a string")
    return value


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid_response(f"field {key!r} must be a string or null")
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_response(f"field {key!r} must be an integer")
    return value


def _required_bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise _invalid_response(f"field {key!r} must be a boolean")
    return value


def _optional_bool(payload: Mapping[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _invalid_response(f"field {key!r} must be a boolean or null")
    return value


def _invalid_response(detail: str) -> TaskFailed:
    return TaskFailed(f"invalid GitHub release response: {detail}")


def _validate_project(value: str) -> str:
    if not isinstance(value, str):
        raise TaskFailed("project must be a string")
    project = value.strip()
    if not project:
        raise TaskFailed("project must not be empty")
    if _has_whitespace_or_control(project):
        raise TaskFailed("project must not contain whitespace or control characters")
    parts = project.split("/")
    if len(parts) != 2 or not all(parts) or any(part in {".", ".."} for part in parts):
        raise TaskFailed("project must use owner/repo format")
    return project


def _validate_tag(value: str) -> str:
    if not isinstance(value, str):
        raise TaskFailed("tag must be a string")
    require_text(value, "tag")
    if _has_whitespace_or_control(value):
        raise TaskFailed("tag must not contain whitespace or control characters")
    return value


def _project_path(project: str) -> str:
    owner, repo = project.split("/", 1)
    return f"{quote(owner, safe='')}/{quote(repo, safe='')}"


def _memo_key(
    project: str,
    tag: str | None,
    timeout: float,
) -> str:
    target = f"tag:{tag}" if tag is not None else "latest"
    return f"github_release:{project}:{target}:{timeout}"


def _has_whitespace_or_control(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    )


def _http_error_message(exc: HTTPError, label: str) -> str:
    if exc.code == 404:
        return f"GitHub release not found: {label}"
    if exc.code == 429 or (exc.code == 403 and _is_rate_limited(exc)):
        return (
            f"GitHub API rate limit exceeded for {label}"
            f"{_rate_limit_detail(exc)} (unauthenticated requests are limited)"
        )
    return f"GitHub API request failed for {label}: HTTP {exc.code} {exc.reason}"


def _is_rate_limited(exc: HTTPError) -> bool:
    headers = exc.headers
    if headers is None:
        return False
    return headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After") is not None


def _rate_limit_detail(exc: HTTPError) -> str:
    headers = exc.headers
    if headers is None:
        return ""
    retry_after = (headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return f"; retry after {retry_after} seconds"

    reset = (headers.get("X-RateLimit-Reset") or "").strip()
    if reset.isdigit():
        try:
            timestamp = int(reset)
            formatted = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OverflowError, OSError, ValueError:
            return ""
        return f"; rate limit resets at {formatted}"
    return ""
