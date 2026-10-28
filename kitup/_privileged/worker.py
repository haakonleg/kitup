"""Privileged worker server.

This module is embedded with ``protocol.py`` and ``_utils/file_helper.py`` into a
single ``python -c`` script. Keep it standard-library-only, free of runtime
package imports, and without a ``__main__`` guard.
"""

# ruff: noqa: TC004 - these names come from the concatenated embedded sources

import base64
import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .._utils.file_helper import (
        apply_metadata,
        mkdir_path,
        move_path,
        remove_path,
        stat_path,
        symlink_path,
        write_path,
    )
    from .protocol import MAX_MESSAGE_BYTES, READY, Request, Response

MAX_READ_BYTES = 32 * 1024 * 1024


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


def _boolean(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")  # noqa: TRY004
    return value


def stat_action(params: dict[str, Any]) -> dict[str, Any]:
    result = stat_path(_path(params), follow=_boolean(params, "follow"))
    return result if result is not None else {"exists": False}


def read_action(params: dict[str, Any]) -> dict[str, Any]:
    path = _path(params)
    result = stat_path(path)
    if result is None:
        return {"exists": False}
    if result["is_file"]:
        result["content"] = base64.b64encode(_read_limited(path, MAX_READ_BYTES)).decode(
            "ascii"
        )
    return result


def _read_limited(path: str, limit: int) -> bytes:
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(
            f"file is too large to read through privileged worker (limit {limit} bytes)"
        )
    return data


def write_action(params: dict[str, Any]) -> dict[str, Any]:
    content = params.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a base64 string")  # noqa: TRY004
    write_path(
        _path(params),
        base64.b64decode(content, validate=True),
        mode=_optional_mode(params),
        owner=_identity(params, "owner"),
        group=_identity(params, "group"),
    )
    return {}


def move_action(params: dict[str, Any]) -> dict[str, Any]:
    move_path(_path(params, "source"), _path(params))
    return {}


def mkdir_action(params: dict[str, Any]) -> dict[str, Any]:
    mkdir_path(
        _path(params),
        mode=_optional_mode(params),
        owner=_identity(params, "owner"),
        group=_identity(params, "group"),
    )
    return {}


def metadata_action(params: dict[str, Any]) -> dict[str, Any]:
    apply_metadata(
        _path(params),
        _optional_mode(params),
        _identity(params, "owner"),
        _identity(params, "group"),
    )
    return {}


def symlink_action(params: dict[str, Any]) -> dict[str, Any]:
    symlink_path(_target(params), _path(params))
    return {}


def remove_action(params: dict[str, Any]) -> dict[str, Any]:
    remove_path(_path(params), force=_boolean(params, "force"))
    return {}


ACTIONS = {
    "stat": stat_action,
    "read": read_action,
    "write": write_action,
    "move": move_action,
    "mkdir": mkdir_action,
    "metadata": metadata_action,
    "symlink": symlink_action,
    "remove": remove_action,
}


def handle_request(request: Request) -> Any:
    handler = ACTIONS.get(request.action)
    if handler is None:
        raise ValueError(f"unknown privileged action: {request.action!r}")
    return handler(request.params)


def _send(response: Response) -> bool:
    try:
        sys.stdout.buffer.write(response.encode().encode("utf-8") + b"\n")
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
        raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_MESSAGE_BYTES:
            _send(
                Response(
                    error=f"request too large (limit {MAX_MESSAGE_BYTES} bytes)",
                    fatal=True,
                )
            )
            return
        line = raw.strip()
        if not line:
            _send(Response(error="empty request line", fatal=True))
            return
        try:
            request = Request.decode(line)
            result = handle_request(request)
            response = Response(result=result)
        except Exception as exc:  # noqa: BLE001 - report any request failure
            response = Response(error=f"{type(exc).__name__}: {exc}")
        if not _send(response):
            return
