"""Path expansion helpers that keep failures inside the kitup error contract."""

from pathlib import Path

from ..errors import KitupError


def expand_path(value: str | Path, *, error: type[KitupError], resolve: bool = False) -> Path:
    """Expand a path, optionally resolving it, and raise ``error`` on failure."""
    try:
        path = Path(value).expanduser()
        return path.resolve() if resolve else path
    except (RuntimeError, OSError) as exc:
        raise error(f"cannot resolve path {str(value)!r}: {exc}") from exc
