"""Privileged worker server.

This module is embedded with ``protocol.py`` and ``_utils/file_primitives.py`` into a
single ``python -c`` script. Keep it standard-library-only, free of runtime
package imports, and without a ``__main__`` guard.
"""

# ruff: noqa: TC004 - these names come from the concatenated embedded sources

import os
import sys
from typing import TYPE_CHECKING, Any, BinaryIO, Final, NamedTuple

if TYPE_CHECKING:
    from .._utils.file_primitives import (
        CHUNK_SIZE,
        apply_metadata,
        atomic_write_path,
        compare_paths,
        copy_path,
        mkdir_path,
        move_path,
        read_regular_path,
        remove_path,
        stat_path,
        symlink_path,
    )
    from .protocol import MAX_HEADER_BYTES, READY, Request, Response, _require_content_length


class _Payload:
    """Read the raw request payload without buffering it in memory."""

    def __init__(self, size: int) -> None:
        self._remaining = size

    def read(self, size: int) -> bytes:
        if self._remaining <= 0:
            return b""
        size = min(size, self._remaining)
        data = sys.stdin.buffer.read(size)
        if not data:
            raise ValueError("unexpected end of request content")
        self._remaining -= len(data)
        return data

    def drain(self) -> None:
        while self._remaining > 0:
            chunk = sys.stdin.buffer.read(min(self._remaining, CHUNK_SIZE))
            if not chunk:
                self._remaining = 0
                return
            self._remaining -= len(chunk)


class _ActionResult(NamedTuple):
    """A handler result paired with raw response content."""

    result: Any
    content: bytes


def _path(params: dict[str, Any], key: str = "path") -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    if "\0" in value:
        raise ValueError(f"{key} must not contain NUL bytes")
    if not os.path.isabs(value):
        raise ValueError(f"{key} must be an absolute path: {value}")
    return value


def _target(params: dict[str, Any]) -> str:
    value = params.get("target")
    if not isinstance(value, str) or not value:
        raise ValueError("target must be a non-empty string")
    if "\0" in value:
        raise ValueError("target must not contain NUL bytes")
    return value


def _optional_mode(params: dict[str, Any]) -> int | None:
    value = params.get("mode")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("mode must be an integer")  # noqa: TRY004
    if not 0 <= value <= 0o7777:
        raise ValueError("mode must be between 0 and 0o7777")
    return value


def _optional_size(params: dict[str, Any], key: str) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    return _require_content_length(value, name=key)


def _identity(params: dict[str, Any], key: str) -> str | int | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a user/group name or numeric ID")  # noqa: TRY004
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{key} must be non-negative")
        return value
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{key} must not be empty")
        return value
    raise ValueError(f"{key} must be a user/group name or numeric ID")


def _boolean(params: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")  # noqa: TRY004
    return value


def stat_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    result = stat_path(_path(params), follow=_boolean(params, "follow"))
    return result if result is not None else {"exists": False}


def read_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any] | _ActionResult:
    path = _path(params)
    result = stat_path(path)
    if result is None:
        return {"exists": False}
    if result["is_file"]:
        return _ActionResult(result, read_regular_path(path))
    return result


def snapshot_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any] | _ActionResult:
    path = _path(params)
    content_size = _optional_size(params, "content_size")
    diff = _boolean(params, "diff")
    result = stat_path(path)
    if result is None:
        return {"exists": False}
    if not result["is_file"]:
        return result
    if content_size is not None and result["size"] != content_size and not diff:
        return result
    return _ActionResult(result, read_regular_path(path))


def write_action(params: dict[str, Any], payload: _Payload) -> dict[str, Any]:
    def write(stream: BinaryIO) -> None:
        while True:
            chunk = payload.read(CHUNK_SIZE)
            if not chunk:
                return
            stream.write(chunk)

    atomic_write_path(
        _path(params),
        write,
        mode=_optional_mode(params),
        owner=_identity(params, "owner"),
        group=_identity(params, "group"),
    )
    return {}


def compare_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    source = _path(params, "source")
    path = _path(params)
    source_info = stat_path(source)
    if source_info is None or not source_info["is_file"]:
        raise ValueError(f"copy source is not a regular file: {source}")
    target_info = stat_path(path)
    equal = False
    if target_info is not None and target_info["is_file"] and source_info["size"] == target_info["size"]:
        equal = compare_paths(source, path)
    return {"source": source_info, "target": target_info, "equal": equal}


def copy_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    copy_path(
        _path(params, "source"),
        _path(params),
        mode=_optional_mode(params),
        owner=_identity(params, "owner"),
        group=_identity(params, "group"),
    )
    return {}


def move_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    move_path(_path(params, "source"), _path(params))
    return {}


def mkdir_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    mkdir_path(
        _path(params),
        mode=_optional_mode(params),
        owner=_identity(params, "owner"),
        group=_identity(params, "group"),
    )
    return {}


def metadata_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    apply_metadata(
        _path(params),
        _optional_mode(params),
        _identity(params, "owner"),
        _identity(params, "group"),
    )
    return {}


def symlink_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    symlink_path(_target(params), _path(params))
    return {}


def remove_action(params: dict[str, Any], _payload: _Payload) -> dict[str, Any]:
    remove_path(_path(params), force=_boolean(params, "force"))
    return {}


ACTIONS: Final = {
    "stat": stat_action,
    "read": read_action,
    "snapshot": snapshot_action,
    "write": write_action,
    "compare": compare_action,
    "copy": copy_action,
    "move": move_action,
    "mkdir": mkdir_action,
    "metadata": metadata_action,
    "symlink": symlink_action,
    "remove": remove_action,
}


def handle_request(request: Request, payload: _Payload) -> Any:
    handler = ACTIONS.get(request.action)
    if handler is None:
        raise ValueError(f"unknown privileged action: {request.action!r}")
    return handler(request.params, payload)


def _send_response(response: Response) -> bool:
    try:
        sys.stdout.buffer.write(response.encode().encode("utf-8") + b"\n")
        if response.content:
            sys.stdout.buffer.write(response.content)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        return False
    return True


def _send_ready() -> bool:
    try:
        sys.stdout.buffer.write((READY + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        return False
    return True


def main() -> None:
    if not _send_ready():
        return
    while True:
        raw = sys.stdin.buffer.readline(MAX_HEADER_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_HEADER_BYTES:
            _send_response(
                Response(
                    error=f"request header too large (limit {MAX_HEADER_BYTES} bytes)",
                    fatal=True,
                )
            )
            return
        line = raw.strip()
        if not line:
            _send_response(Response(error="empty request line", fatal=True))
            return
        try:
            request = Request.decode(line)
        except Exception as exc:  # noqa: BLE001 - malformed headers are fatal
            _send_response(Response(error=f"{type(exc).__name__}: {exc}", fatal=True))
            return

        payload = _Payload(request.content_length)
        try:
            result = handle_request(request, payload)
            payload.drain()
            if isinstance(result, _ActionResult):
                response = Response(
                    result=result.result,
                    content_length=len(result.content),
                    content=result.content,
                )
            else:
                response = Response(result=result)
        except Exception as exc:  # noqa: BLE001 - report any request failure
            payload.drain()
            response = Response(error=f"{type(exc).__name__}: {exc}")
        if not _send_response(response):
            return
