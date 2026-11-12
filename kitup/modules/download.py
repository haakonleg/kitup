"""Remote file download over HTTP(S)."""

import hashlib
import io
import posixpath
import re
import zlib
from collections.abc import Mapping
from compression import zstd
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, BinaryIO, Final, Protocol
from urllib.error import HTTPError
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from .._utils import file_actions
from .._utils.file_actions import PathPolicy, require_path_kind
from .._utils.file_primitives import TempFileError, digest_path, replace_path, stage_path
from .._utils.http import open_url, read_response
from .._utils.paths import expand_path
from .._utils.timing import timed
from .._utils.validation import require_bool, require_mode, require_path, require_timeout
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult

_DEFAULT_MAX_BYTES: Final = 10 * 1024 * 1024
_ACCEPT_ENCODING: Final = "gzip, deflate, zstd"
_HEADER_NAME: Final = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEX_DIGEST: Final = re.compile(r"^[0-9a-f]+$")
_DECOMPRESSION_ERRORS: Final = (EOFError, zlib.error, zstd.ZstdError)


@dataclass(frozen=True, slots=True)
class _Checksum:
    algorithm: str
    digest: str


class _Decoder(Protocol):
    def decompress(self, data: bytes) -> bytes: ...

    def flush(self) -> bytes: ...

    @property
    def eof(self) -> bool: ...


class _ZlibDecoder:
    def __init__(self, wbits: int) -> None:
        self._decompressor = zlib.decompressobj(wbits)

    def decompress(self, data: bytes) -> bytes:
        return self._decompressor.decompress(data)

    def flush(self) -> bytes:
        return self._decompressor.flush()

    @property
    def eof(self) -> bool:
        return self._decompressor.eof


class _ZstdDecoder:
    def __init__(self) -> None:
        self._decompressor = zstd.ZstdDecompressor()

    def decompress(self, data: bytes) -> bytes:
        return self._decompressor.decompress(data)

    def flush(self) -> bytes:
        return b""

    @property
    def eof(self) -> bool:
        return self._decompressor.eof


class _HeadersWithoutLength:
    def __init__(self, headers: Any) -> None:
        self._headers = headers

    def get(self, name: str, default: Any = None) -> Any:
        if name.lower() == "content-length":
            return None
        return self._headers.get(name, default)


class _DecodedResponse:
    def __init__(self, response: Any, decoder: _Decoder) -> None:
        self._response = response
        self._decoder = decoder
        self.headers = _HeadersWithoutLength(response.headers)
        self._finished = False

    def read(self, size: int = -1) -> bytes:
        try:
            while not self._finished:
                raw = self._response.read(size)
                if raw:
                    data = self._decoder.decompress(raw)
                    if data:
                        return data
                    continue

                self._finished = True
                data = self._decoder.flush()
                if not self._decoder.eof:
                    raise TaskFailed("incomplete compressed download")
                if data:
                    return data
        except _DECOMPRESSION_ERRORS as exc:
            raise TaskFailed(f"could not decompress download: {exc}") from exc
        return b""


def _decoder_for(response: Any) -> _Decoder | None:
    value = response.headers.get("Content-Encoding")
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskFailed("content encoding must be a string")
    encoding = value.strip().lower()
    if not encoding or encoding == "identity":
        return None
    if encoding == "gzip":
        return _ZlibDecoder(16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        return _ZlibDecoder(zlib.MAX_WBITS)
    if encoding == "zstd":
        return _ZstdDecoder()
    raise TaskFailed(f"unsupported content encoding: {encoding}")


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
) -> CallResult:
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
    require_timeout(timeout)
    require_bool(validate_certs, "validate_certs")
    require_mode(mode)
    require_bool(force, "force")

    target, info = _resolve_destination(dest, parts)
    require_path_kind(target, info, policy=PathPolicy.FILE, label="download destination")
    display = _display_url(parts)

    if not _needs_download(target, info, force=force, checksum=expected):
        return ctx.changed(
            _reconcile_metadata(
                ctx,
                target,
                info,
                mode,
                display=display,
                unchanged_summary=f"ensure download {display} -> {target}",
            )
        )

    file_actions.require_parent_local(target)
    if ctx.dry_run:
        return ctx.changed(
            _record_download(
                display,
                target,
                checksum_dest=None,
                duration=0.0,
                size=_file_size(target),
            )
        )

    with timed() as timing:
        temporary, checksum_dest = _download_to_temp(
            url,
            display,
            request_headers,
            timeout,
            expected,
            target.parent,
            validate_certs=validate_certs,
        )
    duration = timing.duration
    try:
        try:
            size = temporary.stat().st_size
        except OSError as exc:
            raise TaskFailed(f"could not inspect downloaded file: {exc}") from exc
        if info is not None and info.size == size and _content_matches(target, info, checksum_dest):
            return ctx.changed(
                _reconcile_metadata(
                    ctx,
                    target,
                    info,
                    mode,
                    display=display,
                    unchanged_summary=f"ensure download {display} -> {target}",
                    checksum_dest=checksum_dest,
                    duration=duration,
                    size=size,
                )
            )
        result = _record_download(
            display,
            target,
            checksum_dest=checksum_dest,
            duration=duration,
            size=size,
        )
        try:
            replace_path(temporary, target, mode=mode)
        except ValueError as exc:
            raise TaskFailed(str(exc)) from exc
        except OSError as exc:
            raise TaskFailed(f"could not install {target}: {exc}") from exc
        return ctx.changed(result)
    finally:
        temporary.unlink(missing_ok=True)


def to_bytes(
    ctx: TaskContext,  # noqa: ARG001 - part of the module API convention
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
    require_timeout(timeout)
    require_bool(validate_certs, "validate_certs")
    limit = _validate_max_bytes(max_bytes)
    display = _display_url(parts)

    buffer = io.BytesIO()
    with _open_url(url, display, request_headers, timeout, validate_certs=validate_certs) as response:
        _read_response(response, buffer, expected=expected, max_bytes=limit, display=display)
    return buffer.getvalue()


def _download_to_temp(
    url: str,
    display: str,
    headers: Mapping[str, str],
    timeout: float,
    expected: _Checksum | None,
    directory: Path,
    *,
    validate_certs: bool,
) -> tuple[Path, str]:
    """Download ``url`` to a staged file beside ``directory``."""
    checksum_dest = ""

    def write(stream: BinaryIO) -> None:
        nonlocal checksum_dest
        with _open_url(url, display, headers, timeout, validate_certs=validate_certs) as response:
            checksum_dest = _read_response(
                response,
                stream,
                expected=expected,
                max_bytes=None,
                display=display,
            )

    try:
        temporary = stage_path(directory, write, prefix=".kitup-download-")
    except TempFileError as exc:
        raise TaskFailed(str(exc)) from exc
    return Path(temporary), checksum_dest


def _open_url(
    url: str,
    display: str,
    headers: Mapping[str, str],
    timeout: float,
    *,
    validate_certs: bool,
) -> Any:
    try:
        return open_url(
            url,
            headers=headers,
            timeout=timeout,
            validate_certs=validate_certs,
            label="download",
            context=display,
        )
    except HTTPError as exc:
        exc.close()
        raise TaskFailed(f"download failed for {display}: HTTP {exc.code} {exc.reason}") from exc


def _read_response(
    response: Any,
    sink: BinaryIO,
    *,
    expected: _Checksum | None,
    max_bytes: int | None,
    display: str,
) -> str:
    decoder = _decoder_for(response)
    if decoder is not None:
        response = _DecodedResponse(response, decoder)

    dest_hasher = hashlib.sha256()
    expected_hasher = hashlib.new(expected.algorithm) if expected is not None else None

    def update(chunk: bytes) -> None:
        dest_hasher.update(chunk)
        if expected_hasher is not None:
            expected_hasher.update(chunk)

    read_response(
        response,
        sink,
        max_bytes=max_bytes,
        label="download",
        context=display,
        on_chunk=update,
    )

    if expected is not None:
        if expected_hasher is None:
            raise TaskFailed(f"could not verify checksum for {display}")
        actual = expected_hasher.hexdigest().lower()
        if actual != expected.digest:
            raise TaskFailed(
                f"checksum mismatch for {display}: expected {expected.algorithm}:"
                f"{expected.digest}, got {expected.algorithm}:{actual}"
            )
    return dest_hasher.hexdigest()


def _resolve_destination(
    dest: str | Path,
    parts: SplitResult,
) -> tuple[Path, file_actions.FileInfo | None]:
    require_path(dest, "download destination")
    target = expand_path(dest, error=TaskFailed)
    if isinstance(dest, str) and dest.endswith("/"):
        target = target / _url_filename(parts)
        return target, file_actions.stat_local(target)
    info = file_actions.stat_local(target)
    if info is not None and info.is_dir and not info.is_symlink:
        target = target / _url_filename(parts)
        return target, file_actions.stat_local(target)
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
        raise TaskFailed(f"unsupported URL scheme: {parts.scheme or '<missing>'}; only http and https are supported")
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
            if any(ord(character) < 32 and character != "\t" for character in value) or "\x7f" in value:
                raise TaskFailed(f"header {name!r} value must not contain control characters")
            values[name] = value
    if not any(name.lower() == "accept-encoding" for name in values):
        values["Accept-Encoding"] = _ACCEPT_ENCODING
    return values


def _validate_max_bytes(max_bytes: int | None) -> int | None:
    if max_bytes is None:
        return None
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise TaskFailed("max_bytes must be a positive integer or None")
    return max_bytes


@cache
def _digest(path: Path, _mtime_ns: int, _size: int, algorithm: str) -> str:
    """Hash a file whose metadata identifies the content being hashed."""
    return digest_path(path, algorithm)


def _needs_download(
    target: Path,
    info: file_actions.FileInfo | None,
    *,
    force: bool,
    checksum: _Checksum | None,
) -> bool:
    if info is None or force:
        return True
    if checksum is None:
        return False
    try:
        return _digest(target, info.mtime_ns, info.size, checksum.algorithm) != checksum.digest
    except (OSError, ValueError) as exc:
        raise TaskFailed(f"could not checksum {target}: {exc}") from exc


def _content_matches(target: Path, info: file_actions.FileInfo, checksum_dest: str) -> bool:
    try:
        return _digest(target, info.mtime_ns, info.size, "sha256") == checksum_dest
    except (OSError, ValueError) as exc:
        raise TaskFailed(f"could not checksum downloaded file: {exc}") from exc


def _reconcile_metadata(
    ctx: TaskContext,
    target: Path,
    info: file_actions.FileInfo | None,
    mode: int | None,
    *,
    display: str,
    checksum_dest: str | None = None,
    duration: float = 0.0,
    size: int | None = None,
    unchanged_summary: str | None = None,
) -> CallResult:
    return file_actions.reconcile_metadata(
        ctx,
        target,
        info,
        action="download.to_file",
        mode=mode,
        owner=None,
        group=None,
        summary=f"update metadata for {target}",
        unchanged_summary=unchanged_summary or f"ensure metadata for {target}",
        data=_download_data(
            display,
            target,
            checksum_dest=checksum_dest,
            duration=duration,
            size=_file_size(target) if size is None else size,
        ),
        sudo=False,
    )


def _record_download(
    display: str,
    target: Path,
    *,
    checksum_dest: str | None,
    duration: float,
    size: int,
) -> CallResult:
    return _download_result(
        display,
        target,
        changed=True,
        summary=f"download {display} -> {target}",
        checksum_dest=checksum_dest,
        duration=duration,
        size=size,
    )


def _download_result(
    display: str,
    target: Path,
    *,
    changed: bool,
    summary: str,
    checksum_dest: str | None,
    duration: float,
    size: int,
) -> CallResult:
    return CallResult(
        action="download.to_file",
        changed=changed,
        summary=summary,
        **_download_data(display, target, checksum_dest=checksum_dest, duration=duration, size=size),
    )


def _download_data(
    display: str,
    target: Path,
    *,
    checksum_dest: str | None,
    duration: float,
    size: int,
) -> dict[str, object]:
    return {
        "url": display,
        "dest": str(target),
        "checksum_dest": None if checksum_dest is None else f"sha256:{checksum_dest}",
        "duration": duration,
        "size": size,
    }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
