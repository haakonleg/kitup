"""Dictionary helpers with attribute access."""

from typing import Any


class AttrDict(dict[str, Any]):
    """Dictionary whose string keys are also available as attributes."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None
