"""Length-framed protocol shared by the privileged worker and its client.

A message is one JSON header line followed by exactly ``content_length`` raw
bytes. This module is embedded into the worker with ``python -c``. Keep the
imports standard-library-only and avoid package imports or a ``__main__`` guard.
"""

import json
from dataclasses import dataclass
from typing import Any, Final, Self

READY: Final = "ready"
MAX_HEADER_BYTES: Final = 1024 * 1024


def _require_content_length(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _optional_content_length(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    return _require_content_length(value, name=name)


@dataclass(slots=True)
class Request:
    """One action request sent to the privileged worker."""

    action: str
    params: dict[str, Any]
    content_length: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action:
            raise ValueError("request action must be a non-empty string")
        if not isinstance(self.params, dict):
            raise ValueError("request params must be a mapping")  # noqa: TRY004
        _require_content_length(self.content_length, name="request content_length")

    @classmethod
    def decode(cls, line: str | bytes) -> Self:
        """Parse one request header line."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON request: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("request must be a JSON object")  # noqa: TRY004
        action = data.get("action")
        if not isinstance(action, str):
            raise ValueError("request action must be a non-empty string")  # noqa: TRY004
        content_length = _optional_content_length(data.get("content_length"), name="request content_length")
        return cls(action, data.get("params", {}), 0 if content_length is None else content_length)

    def encode(self) -> str:
        """Encode the request header as one compact JSON object."""
        data: dict[str, Any] = {"action": self.action, "params": self.params}
        if self.content_length:
            data["content_length"] = self.content_length
        return json.dumps(data, separators=(",", ":"))


@dataclass(slots=True)
class Response:
    """One result or error response from the privileged worker.

    ``content_length`` is ``None`` when the response has no payload and ``0``
    when the payload is empty. ``content`` is client-side state only; the worker
    writes the payload separately after the header.
    """

    result: Any = None
    error: str | None = None
    fatal: bool = False
    content_length: int | None = None
    content: bytes | None = None

    @classmethod
    def decode(cls, line: str | bytes) -> Self:
        """Parse one response header line."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON response: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("response must be a JSON object")  # noqa: TRY004
        error = data.get("error")
        if error is not None and not isinstance(error, str):
            raise ValueError("response error must be a string")
        fatal = data.get("fatal", False)
        if not isinstance(fatal, bool):
            raise ValueError("response fatal must be a boolean")  # noqa: TRY004
        content_length = _optional_content_length(data.get("content_length"), name="response content_length")
        return cls(
            result=data.get("result"),
            error=error,
            fatal=fatal,
            content_length=content_length,
        )

    def encode(self) -> str:
        """Encode the response header as one compact JSON object."""
        if self.error is not None:
            data: dict[str, Any] = {"error": self.error}
            if self.fatal:
                data["fatal"] = True
            return json.dumps(data, separators=(",", ":"))
        data = {}
        if self.result is not None:
            data["result"] = self.result
        if self.content_length is not None:
            data["content_length"] = self.content_length
        return json.dumps(data, separators=(",", ":"))
