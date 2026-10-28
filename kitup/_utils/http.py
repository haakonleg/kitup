"""Shared HTTP response reading helpers."""

from __future__ import annotations

import http.client
import io
import math
import ssl
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from ..errors import TaskFailed

_CHUNK_SIZE = 1024 * 1024


def open_url(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    validate_certs: bool = True,
    label: str,
    context: str,
) -> Any:
    """Open a GET request and translate transport errors into task failures.

    ``HTTPError`` is re-raised unchanged so callers can format protocol-specific
    responses such as GitHub rate limits.
    """
    # Keep the opener's default User-Agent header (Python-urllib/<version>).
    opener = build_opener(HTTPSHandler(context=_ssl_context(validate_certs)))
    request = Request(url, headers=dict(headers), method="GET")
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError:
        raise
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TaskFailed(f"{label} request timed out after {timeout:g}s for {context}") from exc
        raise TaskFailed(f"{label} request failed for {context}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise TaskFailed(f"{label} request timed out after {timeout:g}s for {context}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise TaskFailed(f"{label} request failed for {context}: {exc}") from exc
    except ValueError as exc:
        raise TaskFailed(f"invalid {label} request for {context}: {exc}") from exc


def validate_timeout(timeout: float) -> None:
    """Reject non-finite, non-numeric, or non-positive timeouts."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TaskFailed("timeout must be a positive number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise TaskFailed("timeout must be a positive number")


def read_response(
    response: Any,
    sink: BinaryIO,
    *,
    max_bytes: int | None,
    label: str,
    context: str,
    on_chunk: Callable[[bytes], None] | None = None,
) -> None:
    """Read a response into ``sink``, enforcing size and length checks."""
    content_length = _content_length(response)
    if max_bytes is not None and content_length is not None and content_length > max_bytes:
        raise TaskFailed(f"{label} exceeds max_bytes ({content_length} > {max_bytes}): {context}")

    total = 0
    while True:
        try:
            chunk = response.read(_CHUNK_SIZE)
        except TimeoutError as exc:
            raise TaskFailed(f"{label} timed out while reading: {context}") from exc
        except http.client.IncompleteRead as exc:
            raise TaskFailed(f"incomplete {label} for {context}: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise TaskFailed(f"{label} failed for {context}: {exc}") from exc
        if not chunk:
            break
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise TaskFailed(f"{label} exceeds max_bytes ({max_bytes}): {context}")
        try:
            sink.write(chunk)
        except OSError as exc:
            raise TaskFailed(f"could not write {label} for {context}: {exc}") from exc
        if on_chunk is not None:
            on_chunk(chunk)

    if content_length is not None and total != content_length:
        raise TaskFailed(
            f"incomplete {label} for {context}: expected {content_length} bytes, got {total}"
        )


def read_bytes(
    response: Any,
    *,
    max_bytes: int | None,
    label: str,
    context: str,
) -> bytes:
    """Read a response body into memory, enforcing an optional size limit."""
    buffer = io.BytesIO()
    read_response(
        response,
        buffer,
        max_bytes=max_bytes,
        label=label,
        context=context,
    )
    return buffer.getvalue()


def _ssl_context(validate_certs: bool) -> ssl.SSLContext:
    if validate_certs:
        return ssl.create_default_context()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _content_length(response: Any) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError:
        return None
    return length if length >= 0 else None
