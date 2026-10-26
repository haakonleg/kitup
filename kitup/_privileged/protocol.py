"""NDJSON protocol shared by the privileged worker and its client.

This module is embedded into the worker with ``python -c``. Keep the imports
standard-library-only and avoid ``kitup`` imports or a ``__main__`` guard.
"""

import json
from dataclasses import dataclass
from typing import Any

READY = "ready"
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


@dataclass
class Request:
    """One action request sent to the privileged worker."""

    action: str
    params: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action:
            raise ValueError("request action must be a non-empty string")
        if not isinstance(self.params, dict):
            raise ValueError("request params must be a mapping")  # noqa: TRY004

    @classmethod
    def decode(cls, line: str | bytes) -> Request:
        """Parse one NDJSON request line."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON request: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("request must be a JSON object")  # noqa: TRY004
        action = data.get("action")
        if not isinstance(action, str):
            raise ValueError("request action must be a non-empty string")  # noqa: TRY004
        return cls(action, data.get("params", {}))

    def encode(self) -> str:
        """Encode the request as one nested JSON object."""
        return json.dumps(
            {"action": self.action, "params": self.params},
            separators=(",", ":"),
        )


@dataclass
class Response:
    """One result or error response from the privileged worker."""

    result: Any = None
    error: str | None = None
    fatal: bool = False

    @classmethod
    def decode(cls, line: str | bytes) -> Response:
        """Parse one NDJSON response line."""
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
        return cls(result=data.get("result"), error=error, fatal=fatal)

    def encode(self) -> str:
        """Encode the response as one compact JSON object."""
        if self.error is not None:
            data: dict[str, Any] = {"error": self.error}
            if self.fatal:
                data["fatal"] = True
            return json.dumps(data, separators=(",", ":"))
        if self.result is not None:
            return json.dumps({"result": self.result}, separators=(",", ":"))
        return "{}"
