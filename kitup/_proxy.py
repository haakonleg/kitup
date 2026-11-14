"""Ambient proxies that resolve against the current task context."""

from collections.abc import Callable, Iterator, Mapping
from typing import Any, Final, cast, override

from .context import TaskContext, current_context
from .errors import TaskFailed
from .variables import Vars


class _Proxy:
    """Forward attribute, item, iteration, and comparison access to a target.

    Used for the ``ctx``, ``vars``, and ``facts`` proxies. The target is
    resolved on every operation, so a proxy always reflects the current task.
    """

    __slots__ = ("_resolver",)

    def __init__(self, resolver: Callable[[], Any]) -> None:
        self._resolver = resolver

    def _resolve(self) -> Any:
        return object.__getattribute__(self, "_resolver")()

    def __getattr__(self, name: str) -> Any:
        try:
            target = self._resolve()
        except TaskFailed as exc:
            # Attribute access must raise AttributeError so getattr/hasattr
            # keep working on modules that import the proxy.
            raise AttributeError(str(exc)) from exc
        return getattr(target, name)

    def __getitem__(self, key: Any) -> Any:
        return self._resolve()[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __contains__(self, item: Any) -> bool:
        return item in self._resolve()

    def __bool__(self) -> bool:
        return bool(self._resolve())

    @override
    def __eq__(self, other: object) -> bool:
        return bool(self._resolve() == other)

    @override
    def __hash__(self) -> int:
        return hash(self._resolve())

    @override
    def __repr__(self) -> str:
        try:
            return repr(self._resolve())
        except TaskFailed:
            return f"<{type(self).__name__} inactive>"


ctx: Final[TaskContext] = cast(TaskContext, _Proxy(current_context))
vars: Final[Vars] = cast(Vars, _Proxy(lambda: current_context().vars))
facts: Final[Mapping[str, Any]] = cast(Mapping[str, Any], _Proxy(lambda: current_context().facts))
