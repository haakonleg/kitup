"""Path helpers: ``~`` expansion and the read-only overlay view of bundle files."""

import errno
import os
from collections.abc import Generator, Sequence
from pathlib import Path, PurePath
from typing import Any, Final, Never, Self, cast, override

from ..errors import KitupError, TaskFailed

_READ_ONLY: Final = "merged files view is read-only"


def expand_path(value: str | Path, *, error: type[KitupError], resolve: bool = False) -> Path:
    """Expand a path, optionally resolving it, and raise ``error`` on failure."""
    try:
        path = Path(value).expanduser()
        return path.resolve() if resolve else path
    except (RuntimeError, OSError) as exc:
        raise error(f"cannot resolve path {str(value)!r}: {exc}") from exc


def _relative_to_prefix(nominal: str, prefix: PurePath) -> PurePath | None:
    """Return ``nominal`` below ``prefix``, or ``None`` when it escapes it."""
    try:
        return PurePath(nominal).relative_to(prefix)
    except ValueError:
        return None


class OverlayPath(Path):
    """Read-only path that merges a bundle's ``files`` directory with its overlay.

    Instances are produced by :attr:`kitup.context.TaskContext.files`. The path
    string is a synthetic prefix that never exists on disk, so handing one to a
    raw ``os`` call fails loudly instead of reading an unmerged file. Use
    :func:`real_path` to reach the concrete file behind an entry. Iterate with
    ``iterdir()``; ``glob()``, ``rglob()``, and ``walk()`` are not supported.
    """

    _roots: tuple[Path, ...]
    _prefix: PurePath
    _rel: PurePath

    @override
    def __init__(self, *args: str | os.PathLike[str]) -> None:
        super().__init__(*args)
        self._roots = ()
        self._prefix = PurePath()
        self._rel = PurePath()

    @classmethod
    def merged(cls, roots: Sequence[Path], *, prefix: str) -> Self:
        """Return the merged view of ``roots``, highest priority first."""
        path = cls(prefix)
        path._roots = tuple(roots)
        path._prefix = PurePath(prefix)
        return path

    @override
    def with_segments(self, *pathsegments: str | os.PathLike[str]) -> Self:
        path = super().with_segments(*pathsegments)
        rel = _relative_to_prefix(str(path), self._prefix)
        if rel is None:
            # The join escaped the synthetic prefix, so it is a plain path.
            return cast(Self, Path(str(path)))
        path._roots = self._roots
        path._prefix = self._prefix
        path._rel = rel
        return path

    def _backing(self) -> Path | None:
        """Return the winning real path for this entry, or ``None`` when absent."""
        for root in self._roots:
            candidate = root / self._rel
            if os.path.lexists(candidate):
                return candidate
        return None

    def _require_backing(self) -> Path:
        backing = self._backing()
        if backing is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(self))
        return backing

    def _real(self) -> Path:
        """Return the concrete path, preferring the highest-priority root."""
        backing = self._backing()
        if backing is not None:
            return backing
        if self._roots:
            return self._roots[0] / self._rel
        return Path(str(self))

    @override
    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        return self._require_backing().stat(follow_symlinks=follow_symlinks)

    @override
    def lstat(self) -> os.stat_result:
        return self._require_backing().lstat()

    @override
    def exists(self, *, follow_symlinks: bool = True) -> bool:
        backing = self._backing()
        return backing is not None and backing.exists(follow_symlinks=follow_symlinks)

    @override
    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        backing = self._backing()
        return backing is not None and backing.is_dir(follow_symlinks=follow_symlinks)

    @override
    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        backing = self._backing()
        return backing is not None and backing.is_file(follow_symlinks=follow_symlinks)

    @override
    def is_symlink(self) -> bool:
        backing = self._backing()
        return backing is not None and backing.is_symlink()

    @override
    def readlink(self) -> Self:
        target = self._require_backing().readlink()
        return self.with_segments(target)

    @override
    def open(self, *args: Any, **kwargs: Any) -> Any:
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        if any(flag in mode for flag in "wax+"):
            raise TaskFailed(_READ_ONLY)
        return self._require_backing().open(*args, **kwargs)

    @override
    def iterdir(self) -> Generator[Self]:
        backing = self._require_backing()
        if not backing.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(self))
        names: dict[str, None] = {}
        for root in self._roots:
            try:
                with os.scandir(root / self._rel) as entries:
                    names.update(dict.fromkeys(entry.name for entry in entries))
            except FileNotFoundError, NotADirectoryError:
                continue
        return (self.with_segments(self, name) for name in names)

    @override
    def mkdir(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def touch(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def write_text(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def write_bytes(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def unlink(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def rmdir(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def rename(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def replace(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def chmod(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def lchmod(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def symlink_to(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)

    @override
    def hardlink_to(self, *args: Any, **kwargs: Any) -> Never:
        raise TaskFailed(_READ_ONLY)


def real_path(path: str | os.PathLike[str]) -> Path:
    """Return the concrete filesystem path that backs ``path``.

    ``OverlayPath`` entries resolve to the winning entry in their merged roots;
    every other input becomes a plain ``Path``.
    """
    if isinstance(path, OverlayPath):
        return path._real()
    if isinstance(path, Path):
        return path
    return Path(path)
