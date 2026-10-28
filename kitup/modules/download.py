"""Remote file download over HTTP(S)."""

from __future__ import annotations

import hashlib
import http.client
import io
import math
import os
import posixpath
import re
import ssl
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, build_opener
from urllib.request import Request as URLRequest

from .._utils import file_ops
from .._utils.file_helper import apply_metadata, copy_path, digest_path
from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_metadata, require_mode, require_path

_CHUNK_SIZE = 1024 * 1024
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]+$")


@dataclass(frozen=True)
class _Checksum:
    algorithm: str
    digest: str


def to_file(
    ctx: TaskContext,
    url: str,
    dest: str | Path,
    *,
    checksum: str | None = None,
    force: bool = False,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30,
    validate_certs: bool = True,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> None:
    """Download ``url`` to ``dest``, replacing it only when needed.

    ``dest`` may be an existing directory or end with ``/``, in which case the
    URL basename is used. Existing files are left alone unless ``force`` is
    true or a supplied ``checksum`` does not match. Downloads are written to a
    temporary file and installed atomically.
    """
    url, parts = _validate_url(url)
    if not isinstance(dest, (str, Path)):
        raise TaskFailed("download destination must be a path")
    expected = _parse_checksum(checksum)
    request_headers = _build_headers(headers)
    _validate_timeout(timeout)
    _validate_boolean(validate_certs, "validate_certs")
    require_metadata(owner, group)
    require_mode(mode)
    _validate_boolean(force, "force")

    target, info = _resolve_destination(ctx, dest, parts, sudo=sudo)
    _validate_destination_info(target, info)
    display = _display_url(parts)

    if not _needs_download(ctx, target, info, force=force, checksum=expected, sudo=sudo):
        _reconcile_metadata(ctx, target, info, mode, owner, group, sudo=sudo)
        return

    file_ops.require_parent(ctx, target, sudo=sudo)
    if ctx.dry_run:
        file_ops.resolve_metadata(owner, group)
        _record_download(ctx, display, target, expected)
        return

    temporary = _download_to_temp(
        url,
        display,
        request_headers,
        timeout,
        validate_certs,
        expected,
    )
    try:
        if info is not None and _content_matches(ctx, target, temporary, sudo=sudo):
            _reconcile_metadata(ctx, target, info, mode, owner, group, sudo=sudo)
            return
        install_mode = mode if mode is not None else (0o644 if info is None else None)
        _record_download(ctx, display, target, expected)
        _install(ctx, temporary, target, install_mode, owner, group, sudo=sudo)
    finally:
        temporary.unlink(missing_ok=True)


def to_bytes(
    ctx: TaskContext,
    url: str,
    *,
    checksum: str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30,
    validate_certs: bool = True,
    max_bytes: int | None = _DEFAULT_MAX_BYTES,
) -> bytes:
    """Download ``url`` into memory and return its raw bytes.

    ``max_bytes`` limits the response size. Set it to ``None`` to disable the
    limit. Prefer :func:`to_file` for large files.
    """
    url, parts = _validate_url(url)
    expected = _parse_checksum(checksum)
    request_headers = _build_headers(headers)
    _validate_timeout(timeout)
    _validate_boolean(validate_certs, "validate_certs")
    limit = _validate_max_bytes(max_bytes)
    display = _display_url(parts)

    buffer = io.BytesIO()
    with _open_url(url, display, request_headers, timeout, validate_certs) as response:
        _read_response(response, buffer, expected=expected, max_bytes=limit, display=display)
    return buffer.getvalue()


def _download_to_temp(
    url: str,
    display: str,
    headers: Mapping[str, str],
    timeout: float,
    validate_certs: bool,
    expected: _Checksum | None,
) -> Path:
    handle, name = tempfile.mkstemp(prefix="kitup-download-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            with _open_url(url, display, headers, timeout, validate_certs) as response:
                _read_response(
                    response,
                    stream,
                    expected=expected,
                    max_bytes=None,
                    display=display,
                )
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _open_url(
    url: str,
    display: str,
    headers: Mapping[str, str],
    timeout: float,
    validate_certs: bool,
) -> Any:
    opener = build_opener(HTTPSHandler(context=_ssl_context(validate_certs)))
    opener.addheaders = []
    request = URLRequest(url, headers=dict(headers), method="GET")
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as exc:
        exc.close()
        raise TaskFailed(f"download failed for {display}: HTTP {exc.code} {exc.reason}") from exc
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TaskFailed(f"download timed out after {timeout:g}s: {display}") from exc
        raise TaskFailed(f"download failed for {display}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise TaskFailed(f"download timed out after {timeout:g}s: {display}") from exc
    except OSError as exc:
        raise TaskFailed(f"download failed for {display}: {exc}") from exc
    except ValueError as exc:
        raise TaskFailed(f"invalid download request for {display}: {exc}") from exc


def _read_response(
    response: Any,
    sink: BinaryIO,
    *,
    expected: _Checksum | None,
    max_bytes: int | None,
    display: str,
) -> None:
    hasher = hashlib.new(expected.algorithm) if expected is not None else None
    content_length = _content_length(response)
    if (
        max_bytes is not None
        and content_length is not None
        and content_length > max_bytes
    ):
        raise TaskFailed(
            f"download exceeds max_bytes ({content_length} > {max_bytes}): {display}"
        )

    total = 0
    while True:
        try:
            chunk = response.read(_CHUNK_SIZE)
        except TimeoutError as exc:
            raise TaskFailed(f"download timed out while reading: {display}") from exc
        except http.client.IncompleteRead as exc:
            raise TaskFailed(f"incomplete download for {display}: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise TaskFailed(f"download failed for {display}: {exc}") from exc
        if not chunk:
            break
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise TaskFailed(f"download exceeds max_bytes ({max_bytes}): {display}")
        try:
            sink.write(chunk)
        except OSError as exc:
            raise TaskFailed(f"could not write download for {display}: {exc}") from exc
        if hasher is not None:
            hasher.update(chunk)

    if content_length is not None and total != content_length:
        raise TaskFailed(
            f"incomplete download for {display}: expected {content_length} bytes, got {total}"
        )
    if expected is not None:
        if hasher is None:
            raise TaskFailed(f"could not verify checksum for {display}")
        actual = hasher.hexdigest().lower()
        if actual != expected.digest:
            raise TaskFailed(
                f"checksum mismatch for {display}: expected {expected.algorithm}:"
                f"{expected.digest}, got {expected.algorithm}:{actual}"
            )


def _content_length(response: Any) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError:
        return None
    return length if length >= 0 else None


def _ssl_context(validate_certs: bool) -> ssl.SSLContext:
    if validate_certs:
        return ssl.create_default_context()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _resolve_destination(
    ctx: TaskContext,
    dest: str | Path,
    parts: SplitResult,
    *,
    sudo: bool,
) -> tuple[Path, file_ops.FileInfo | None]:
    require_path(dest, "download destination")
    target = Path(dest)
    if isinstance(dest, str) and dest.endswith("/"):
        target = target / _url_filename(parts)
        return target, file_ops.stat(ctx, target, sudo=sudo)
    info = file_ops.stat(ctx, target, sudo=sudo)
    if info is not None and info.is_dir and not info.is_symlink:
        target = target / _url_filename(parts)
        return target, file_ops.stat(ctx, target, sudo=sudo)
    return target, info


def _url_filename(parts: SplitResult) -> str:
    path = parts.path
    if not path or path.endswith("/"):
        raise TaskFailed(f"cannot derive a filename from URL: {_display_url(parts)}")
    name = posixpath.basename(unquote(path))
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
        raise TaskFailed(f"cannot derive a safe filename from URL: {_display_url(parts)}")
    return name


def _validate_url(url: str) -> tuple[str, SplitResult]:
    if not isinstance(url, str):
        raise TaskFailed("URL must be a string")
    url = url.strip()
    if not url:
        raise TaskFailed("URL must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise TaskFailed("URL must not contain control characters")
    if any(character.isspace() for character in url):
        raise TaskFailed("URL must not contain whitespace")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise TaskFailed(
            f"unsupported URL scheme: {parts.scheme or '<missing>'}; "
            "only http and https are supported"
        )
    if not parts.netloc:
        raise TaskFailed(f"URL has no host: {_display_url(parts)}")
    try:
        if not parts.hostname:
            raise ValueError("missing host")
        _ = parts.port
    except ValueError as exc:
        raise TaskFailed(f"invalid URL: {exc}") from exc
    return url, parts


def _display_url(parts: SplitResult) -> str:
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _parse_checksum(value: str | None) -> _Checksum | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskFailed("checksum must be a string")
    value = value.strip()
    if not value:
        raise TaskFailed("checksum must not be empty")
    algorithm, separator, digest = value.partition(":")
    if not separator or not algorithm or not digest:
        raise TaskFailed("checksum must use algorithm:digest format")
    if "://" in digest:
        raise TaskFailed("checksum URLs are not supported; provide an inline digest")
    algorithm = algorithm.lower()
    digest = digest.lower()
    try:
        hasher = hashlib.new(algorithm)
    except ValueError as exc:
        raise TaskFailed(f"unsupported checksum algorithm: {algorithm}") from exc
    if hasher.digest_size <= 0:
        raise TaskFailed(f"unsupported checksum algorithm: {algorithm}")
    if len(digest) != hasher.digest_size * 2 or _HEX_DIGEST.fullmatch(digest) is None:
        raise TaskFailed(f"invalid {algorithm} checksum digest: {digest}")
    return _Checksum(algorithm, digest)


def _build_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if headers is not None:
        if not isinstance(headers, Mapping):
            raise TaskFailed("headers must be a mapping")
        for name, value in headers.items():
            if not isinstance(name, str) or not name:
                raise TaskFailed("header names must be non-empty strings")
            if _HEADER_NAME.fullmatch(name) is None:
                raise TaskFailed(f"invalid header name: {name!r}")
            if not isinstance(value, str):
                raise TaskFailed(f"header {name!r} value must be a string")
            if any(
                ord(character) < 32 and character != "\t" for character in value
            ) or "\x7f" in value:
                raise TaskFailed(f"header {name!r} value must not contain control characters")
            values[name] = value
    return values


def _validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TaskFailed("timeout must be a positive number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise TaskFailed("timeout must be a positive number")


def _validate_max_bytes(max_bytes: int | None) -> int | None:
    if max_bytes is None:
        return None
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise TaskFailed("max_bytes must be a positive integer or None")
    if max_bytes <= 0:
        raise TaskFailed("max_bytes must be a positive integer or None")
    return max_bytes


def _validate_boolean(value: bool, label: str) -> None:
    if not isinstance(value, bool):
        raise TaskFailed(f"{label} must be a boolean")


def _validate_destination_info(target: Path, info: file_ops.FileInfo | None) -> None:
    if info is None:
        return
    if info.is_symlink:
        raise TaskFailed(f"refusing to replace symlink destination: {target}")
    if info.is_dir:
        raise TaskFailed(f"download destination is a directory: {target}")
    if not info.is_file:
        raise TaskFailed(f"download destination is not a regular file: {target}")


def _needs_download(
    ctx: TaskContext,
    target: Path,
    info: file_ops.FileInfo | None,
    *,
    force: bool,
    checksum: _Checksum | None,
    sudo: bool,
) -> bool:
    if info is None or force:
        return True
    if checksum is None:
        return False
    return _digest(ctx, target, checksum.algorithm, sudo=sudo) != checksum.digest


def _content_matches(
    ctx: TaskContext,
    target: Path,
    temporary: Path,
    *,
    sudo: bool,
) -> bool:
    try:
        remote_digest = digest_path(temporary, "sha256")
    except (OSError, ValueError) as exc:
        raise TaskFailed(f"could not checksum downloaded file: {exc}") from exc
    return _digest(ctx, target, "sha256", sudo=sudo) == remote_digest


def _digest(ctx: TaskContext, path: Path, algorithm: str, *, sudo: bool) -> str:
    if file_ops.privileged(ctx, sudo):
        result = file_ops.worker_request(ctx, "digest", path=str(path), algorithm=algorithm)
        return str(result["digest"]).lower()
    try:
        return digest_path(path, algorithm).lower()
    except (OSError, ValueError) as exc:
        raise TaskFailed(f"could not checksum {path}: {exc}") from exc


def _reconcile_metadata(
    ctx: TaskContext,
    target: Path,
    info: file_ops.FileInfo | None,
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    if info is None or (mode is None and owner is None and group is None):
        return
    resolved_mode = info.mode if mode is None else mode
    if not file_ops.metadata_differs(info, resolved_mode, owner, group):
        return
    if ctx.dry_run:
        file_ops.resolve_metadata(owner, group)
    ctx.changed(
        {"path": str(target), "action": "metadata"},
        f"update metadata for {target}",
    )
    if ctx.dry_run:
        return
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(
            ctx,
            "metadata",
            path=str(target),
            mode=resolved_mode,
            owner=owner,
            group=group,
        )
        return
    try:
        apply_metadata(target, resolved_mode, owner, group, info.as_dict(), preserve=True)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not update metadata for {target}: {exc}") from exc


def _install(
    ctx: TaskContext,
    source: Path,
    target: Path,
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    *,
    sudo: bool,
) -> None:
    if file_ops.privileged(ctx, sudo):
        file_ops.worker_request(
            ctx,
            "copy",
            source=str(source),
            path=str(target),
            mode=mode,
            owner=owner,
            group=group,
        )
        return
    try:
        copy_path(
            source,
            target,
            mode=mode,
            owner=owner,
            group=group,
        )
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not install {target}: {exc}") from exc


def _record_download(
    ctx: TaskContext,
    display: str,
    target: Path,
    checksum: _Checksum | None,
) -> None:
    data: dict[str, Any] = {
        "url": display,
        "path": str(target),
        "action": "download",
    }
    if checksum is not None:
        data["checksum"] = f"{checksum.algorithm}:{checksum.digest}"
    ctx.changed(data, f"download {display} -> {target}")
