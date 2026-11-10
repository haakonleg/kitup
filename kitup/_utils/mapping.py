"""Dictionary helpers with attribute access."""

from collections.abc import Iterator, Mapping
from typing import Any, override


def _get_attribute(mapping: Mapping[str, Any], name: str) -> Any:
    """Return a mapping value by name, raising ``AttributeError`` when absent."""
    try:
        return mapping[name]
    except KeyError:
        raise AttributeError(name) from None


class AttrDict(dict[str, Any]):
    """Dictionary whose string keys are also available as attributes."""

    def __getattr__(self, name: str) -> Any:
        return _get_attribute(self, name)


class AttrMapping(Mapping[str, Any]):
    """Read-only mapping whose string keys are also available as attributes."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)

    @override
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    @override
    def __len__(self) -> int:
        return len(self._data)

    def __getattr__(self, name: str) -> Any:
        return _get_attribute(self, name)

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"


def wrap_attr(value: Any, *, frozen: bool = False) -> Any:
    """Return a value with nested plain dictionaries made attribute-accessible.

    Exact type checks keep subclasses such as ``defaultdict`` and named tuples
    intact. Lists and tuples are copied so their dictionary items are wrapped.
    """
    mapping_type = AttrMapping if frozen else AttrDict
    if type(value) is dict:
        return mapping_type({key: wrap_attr(item, frozen=frozen) for key, item in value.items()})
    if type(value) is list:
        return [wrap_attr(item, frozen=frozen) for item in value]
    if type(value) is tuple:
        return tuple(wrap_attr(item, frozen=frozen) for item in value)
    return value
