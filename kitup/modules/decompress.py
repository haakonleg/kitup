"""Idempotent archive extraction and single-file decompression."""

from __future__ import annotations

import bz2
import fnmatch
import gzip
import lzma
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any

from .._utils import file_ops
from .._utils.file_helper import (
    apply_metadata,
    mkdir_path,
    replace_path,
    symlink_path,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import normalize_strings, require_mode, require_path, require_text

try:
    from compression import zstd
except ImportError:  # pragma: no cover - Python builds without zstd support
    zstd = None  # type: ignore[assignment]

_CHUNK_SIZE = 1024 * 1024
_COMPRESSION_SUFFIXES = (".zst", ".zstd", ".gz", ".bz2", ".xz")

_DECOMPRESSION_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    EOFError,
    lzma.LZMAError,
    zlib.error,
)
if zstd is not None:
    _DECOMPRESSION_ERRORS += (zstd.ZstdError,)

_ARCHIVE_ERRORS: tuple[type[BaseException], ...] = (
    tarfile.TarError,
    zipfile.BadZipFile,
    OSError,
    EOFError,
    RuntimeError,
    NotImplementedError,
    zlib.error,
)


@dataclass(frozen=True)
class _Member:
    """One selected archive member."""

    name: str
    kind: str
    size: int  # file size; unused for directories and symlinks
    mode: int | None
    link: str | None
    raw: Any


def archive(
    ctx: TaskContext,
    src: str | Path,
    dest: str | Path,
    *,
    creates: str | Path | None = None,
    strip_components: int = 0,
    include: str | Iterable[str] | None = None,
    exclude: str | Iterable[str] | None = None,
    mode: int | None = None,
) -> None:
    """Extract a tar or zip archive into ``dest``, replacing only differences.

    ``src`` resolves like :func:`kitup.modules.files.copy`: a bare filename is
    read from the bundle's ``files`` directory, other relative paths resolve
    against the bundle directory, and absolute paths are used as-is. The format
    is detected from the archive contents.

    ``creates`` is a path relative to ``dest`` (or an absolute path); when it
    exists the archive is not opened. ``strip_components`` removes leading path
    components before ``include`` and ``exclude`` patterns are matched.
    """
    require_path(src, "archive source")
    require_path(dest, "archive destination")
    require_mode(mode)
    strip = _require_strip_components(strip_components)
    includes = _require_patterns(include, "include")
    excludes = _require_patterns(exclude, "exclude")

    source = ctx.resolve_source(src)
    _require_source(source, "archive source")
    target = Path(dest)
    target_info = _lstat(target)
    if target_info is not None and not (
        stat.S_ISDIR(target_info.st_mode) and not stat.S_ISLNK(target_info.st_mode)
    ):
        raise TaskFailed(f"archive destination is not a directory: {target}")
    if target_info is None:
        file_ops.require_parent_local(target)
    if creates is not None:
        marker = Path(creates)
        if not marker.is_absolute():
            marker = target / marker
        if _lstat(marker) is not None:
            return

    opened = _open_archive(source)
    try:
        entries = _select_entries(opened, strip, includes, excludes)
        changed = _reconcile(
            ctx,
            opened,
            entries,
            target,
            target_info,
            mode=mode,
        )
    finally:
        opened.close()

    if not changed:
        return
    ctx.changed(
        {
            "path": str(target),
            "source": str(source),
            "action": "archive",
            "changed": len(changed),
        },
        f"extract {len(changed)} paths from {source} -> {target}",
    )


def file(
    ctx: TaskContext,
    src: str | Path,
    dest: str | Path,
    *,
    mode: int | None = None,
) -> None:
    """Decompress a single-file compression format into ``dest``.

    Supported formats are gzip, bzip2, xz, and zstd, detected from the file
    contents. ``dest`` may be a file path, an existing directory, or end with
    ``/``; directory destinations use the source filename without its
    compression suffix. Archive formats are rejected; use
    :func:`archive` for those.
    """
    require_path(src, "compressed source")
    require_path(dest, "decompressed destination")
    require_mode(mode)

    source = ctx.resolve_source(src)
    _require_source(source, "compressed source")
    _reject_archive(source)
    try:
        with _decompressed_stream(source):
            pass
    except TaskFailed:
        raise
    except _DECOMPRESSION_ERRORS as exc:
        raise TaskFailed(f"could not decompress {source}: {exc}") from exc
    target = _single_destination(source, dest)
    info = _lstat(target)
    if info is not None:
        if stat.S_ISLNK(info.st_mode):
            raise TaskFailed(f"refusing to replace symlink destination: {target}")
        if stat.S_ISDIR(info.st_mode):
            raise TaskFailed(f"decompressed destination is a directory: {target}")
        if not stat.S_ISREG(info.st_mode):
            raise TaskFailed(f"decompressed destination is not a regular file: {target}")
    else:
        file_ops.require_parent_local(target)

    needs_write = info is None
    if info is not None:
        try:
            with _decompressed_stream(source) as stream:
                needs_write = not _stream_matches(target, stream)
        except TaskFailed:
            raise
        except _DECOMPRESSION_ERRORS as exc:
            raise TaskFailed(f"could not decompress {source}: {exc}") from exc

    if not needs_write:
        if _reconcile_metadata(target, info, mode=mode, dry_run=ctx.dry_run):
            _record_file_change(ctx, target, source)
        return

    if ctx.dry_run:
        _record_file_change(ctx, target, source)
        return

    try:
        temporary = _decompress_to_temp(source, target.parent)
    except TaskFailed:
        raise
    except _DECOMPRESSION_ERRORS as exc:
        raise TaskFailed(f"could not decompress {source}: {exc}") from exc
    try:
        replace_path(temporary, target, mode=mode, default_mode=0o644)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not write {target}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    _record_file_change(ctx, target, source)


def _record_file_change(ctx: TaskContext, target: Path, source: Path) -> None:
    ctx.changed(
        {"path": str(target), "source": str(source), "action": "decompress"},
        f"decompress {source} -> {target}",
    )


def _reconcile(
    ctx: TaskContext,
    archive: tarfile.TarFile | zipfile.ZipFile,
    entries: dict[str, _Member],
    target: Path,
    target_info: os.stat_result | None,
    *,
    mode: int | None,
) -> list[str]:
    changed: list[str] = []
    dry_run = ctx.dry_run
    if target_info is None:
        if not dry_run:
            _mkdir(target, 0o755)
        changed.append(".")

    ordered = sorted(entries.values(), key=lambda item: (item.name.count("/"), item.name))
    for member in ordered:
        parts = member.name.split("/")
        _ensure_parents(target, parts[:-1], changed, dry_run=dry_run)
        path = target.joinpath(*parts)
        if member.kind == "dir":
            _ensure_directory(path, member, changed, dry_run=dry_run)
        elif member.kind == "symlink":
            _ensure_symlink(path, member, changed, dry_run=dry_run)
        else:
            _ensure_file(
                ctx,
                archive,
                member,
                path,
                changed,
                dry_run=dry_run,
                mode=mode,
            )
    return changed


def _ensure_parents(
    target: Path,
    parts: list[str],
    changed: list[str],
    *,
    dry_run: bool,
) -> None:
    current = target
    for index, part in enumerate(parts, start=1):
        current = current / part
        info = _lstat(current)
        if info is None:
            if not dry_run:
                _mkdir(current, 0o755)
            changed.append("/".join(parts[:index]))
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TaskFailed(f"refusing to traverse non-directory: {current}")


def _ensure_directory(
    path: Path,
    member: _Member,
    changed: list[str],
    *,
    dry_run: bool,
) -> None:
    info = _lstat(path)
    if info is None:
        if not dry_run:
            _mkdir(path, member.mode or 0o755)
        changed.append(member.name)
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TaskFailed(f"refusing to replace non-directory with directory: {path}")


def _ensure_file(
    ctx: TaskContext,
    archive: tarfile.TarFile | zipfile.ZipFile,
    member: _Member,
    path: Path,
    changed: list[str],
    *,
    dry_run: bool,
    mode: int | None,
) -> None:
    info = _lstat(path)
    if info is not None and stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        raise TaskFailed(f"refusing to replace directory with file: {path}")

    if _member_matches(archive, member, path, info):
        if _reconcile_metadata(path, info, mode=mode, dry_run=dry_run):
            changed.append(member.name)
        return

    changed.append(member.name)
    if dry_run:
        return

    try:
        temporary = _materialize(archive, member, path.parent)
    except TaskFailed:
        raise
    except _ARCHIVE_ERRORS as exc:
        raise TaskFailed(f"could not extract {member.name!r}: {exc}") from exc
    try:
        replace_path(
            temporary,
            path,
            mode=mode,
            default_mode=member.mode or 0o644,
        )
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not write {path}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_symlink(
    path: Path,
    member: _Member,
    changed: list[str],
    *,
    dry_run: bool,
) -> None:
    link = member.link
    if link is None:
        raise TaskFailed(f"missing symlink target in archive: {member.name!r}")
    info = _lstat(path)
    if info is not None:
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            raise TaskFailed(f"refusing to replace directory with symlink: {path}")
        if stat.S_ISLNK(info.st_mode):
            try:
                if os.readlink(path) == link:
                    return
            except OSError as exc:
                raise TaskFailed(f"could not read symlink {path}: {exc}") from exc
    changed.append(member.name)
    if dry_run:
        return
    try:
        symlink_path(link, path)
    except OSError as exc:
        raise TaskFailed(f"could not create symlink {path}: {exc}") from exc


def _member_matches(
    archive: tarfile.TarFile | zipfile.ZipFile,
    member: _Member,
    path: Path,
    info: os.stat_result | None,
) -> bool:
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False
    if info.st_size != member.size:
        return False
    try:
        with _open_member(archive, member) as stream:
            return _stream_matches(path, stream)
    except TaskFailed:
        raise
    except _ARCHIVE_ERRORS as exc:
        raise TaskFailed(f"could not read archive member {member.name!r}: {exc}") from exc


def _stream_matches(path: Path, stream: Any) -> bool:
    try:
        existing = path.open("rb")
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc
    with existing:
        while True:
            try:
                current = existing.read(_CHUNK_SIZE)
            except OSError as exc:
                raise TaskFailed(f"could not read {path}: {exc}") from exc
            # Stream errors belong to the source and are reported by callers.
            incoming = stream.read(_CHUNK_SIZE)
            if current != incoming:
                return False
            if not current:
                return True


@contextmanager
def _open_member(
    archive: tarfile.TarFile | zipfile.ZipFile,
    member: _Member,
) -> Iterator[Any]:
    if isinstance(archive, tarfile.TarFile):
        try:
            stream = archive.extractfile(member.raw)
        except KeyError as exc:
            raise TaskFailed(f"could not read archive member {member.name!r}: {exc}") from exc
        if stream is None:
            raise TaskFailed(f"could not read archive member {member.name!r}")
        try:
            yield stream
        finally:
            stream.close()
        return
    with archive.open(member.raw) as stream:
        yield stream


def _write_temp(
    directory: Path,
    write: Callable[[IO[bytes]], None],
) -> Path:
    """Write a temporary file beside its destination and return its path."""
    try:
        handle, name = tempfile.mkstemp(
            dir=directory,
            prefix=".kitup-decompress-",
            suffix=".tmp",
        )
    except OSError as exc:
        raise TaskFailed(f"could not create a temporary file in {directory}: {exc}") from exc
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as sink:
            write(sink)
            sink.flush()
            os.fsync(sink.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _materialize(
    archive: tarfile.TarFile | zipfile.ZipFile,
    member: _Member,
    directory: Path,
) -> Path:
    def write(sink: IO[bytes]) -> None:
        with _open_member(archive, member) as source:
            shutil.copyfileobj(source, sink, _CHUNK_SIZE)

    return _write_temp(directory, write)


def _reconcile_metadata(
    path: Path,
    info: os.stat_result | None,
    *,
    mode: int | None,
    dry_run: bool,
) -> bool:
    if info is None or mode is None or stat.S_IMODE(info.st_mode) == mode:
        return False
    if not dry_run:
        try:
            apply_metadata(path, mode, None, None, preserve=True)
        except OSError as exc:
            raise TaskFailed(f"could not update metadata for {path}: {exc}") from exc
    return True


def _open_archive(path: Path) -> tarfile.TarFile | zipfile.ZipFile:
    try:
        if zipfile.is_zipfile(path):
            return zipfile.ZipFile(path)
        if tarfile.is_tarfile(path):
            return tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise TaskFailed(f"could not open archive {path}: {exc}") from exc
    raise TaskFailed(f"unsupported archive format: {path}")


def _select_entries(
    archive: tarfile.TarFile | zipfile.ZipFile,
    strip_components: int,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> dict[str, _Member]:
    selected: dict[str, _Member] = {}
    try:
        for member in _iter_members(archive):
            name = _normalize_name(member.name)
            if name is None:
                continue
            name = _strip_name(name, strip_components)
            if name is None:
                continue
            if include and not _matches(name, include):
                continue
            if _matches(name, exclude):
                continue
            selected[name] = replace(member, name=name)
    except TaskFailed:
        raise
    except _ARCHIVE_ERRORS as exc:
        raise TaskFailed(f"could not read archive: {exc}") from exc
    _require_no_conflicts(selected)
    return selected


def _iter_members(
    archive: tarfile.TarFile | zipfile.ZipFile,
) -> Iterator[_Member]:
    if isinstance(archive, tarfile.TarFile):
        for info in archive.getmembers():
            if info.isdir():
                yield _Member(
                    info.name,
                    "dir",
                    0,
                    _archive_mode(info.mode, 0o755),
                    None,
                    info,
                )
            elif info.issym():
                yield _Member(
                    info.name,
                    "symlink",
                    0,
                    None,
                    _require_link(info.linkname, info.name),
                    info,
                )
            elif info.isreg():
                yield _Member(
                    info.name,
                    "file",
                    info.size,
                    _archive_mode(info.mode, 0o644),
                    None,
                    info,
                )
            else:
                raise TaskFailed(f"unsupported archive member type for {info.name!r}")
        return

    for zip_info in archive.infolist():
        raw_mode = (zip_info.external_attr >> 16) & 0xFFFF
        if zip_info.is_dir() or stat.S_ISDIR(raw_mode):
            yield _Member(
                zip_info.filename,
                "dir",
                0,
                _archive_mode(raw_mode, 0o755),
                None,
                zip_info,
            )
        elif stat.S_ISLNK(raw_mode):
            link = archive.read(zip_info).decode("utf-8", "surrogateescape")
            yield _Member(
                zip_info.filename,
                "symlink",
                0,
                None,
                _require_link(link, zip_info.filename),
                zip_info,
            )
        elif (raw_mode & 0o170000) in (0, stat.S_IFREG):
            yield _Member(
                zip_info.filename,
                "file",
                zip_info.file_size,
                _archive_mode(raw_mode, 0o644),
                None,
                zip_info,
            )
        else:
            raise TaskFailed(f"unsupported archive member type for {zip_info.filename!r}")


def _normalize_name(name: str) -> str | None:
    if "\0" in name:
        raise TaskFailed(f"unsafe archive member path: {name!r}")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise TaskFailed(f"unsafe absolute archive member path: {name!r}")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise TaskFailed(f"unsafe archive member path: {name!r}")
    if not parts:
        return None
    return "/".join(parts)


def _strip_name(name: str, count: int) -> str | None:
    parts = name.split("/")
    if len(parts) <= count:
        return None
    return "/".join(parts[count:])


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _require_no_conflicts(entries: dict[str, _Member]) -> None:
    for name in entries:
        parts = name.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            ancestor = entries.get(parent)
            if ancestor is not None and ancestor.kind != "dir":
                raise TaskFailed(
                    f"conflicting archive entries: {parent!r} is not a directory "
                    f"but {name!r} is nested inside it"
                )


def _require_link(link: str, member_name: str) -> str:
    if not isinstance(link, str) or not link or "\0" in link:
        raise TaskFailed(f"invalid symlink target for archive member {member_name!r}")
    return link


def _archive_mode(value: int | None, default: int) -> int:
    if value is None:
        return default
    resolved = value & 0o777
    return resolved or default


def _require_strip_components(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TaskFailed("strip_components must be a non-negative integer")
    return value


def _require_patterns(
    value: str | Iterable[str] | None,
    label: str,
) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        patterns = normalize_strings(value)
    except TypeError as exc:
        raise TaskFailed(f"{label} must be a string or an iterable of strings") from exc
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise TaskFailed(f"{label} patterns must be strings")
        require_text(pattern, f"{label} pattern")
    return tuple(patterns)


def _require_source(path: Path, label: str) -> None:
    info = _lstat(path)
    if info is None:
        raise TaskFailed(f"{label} does not exist: {path}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TaskFailed(f"{label} is not a regular file: {path}")


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TaskFailed(f"could not stat {path}: {exc}") from exc


def _mkdir(path: Path, mode: int) -> None:
    try:
        mkdir_path(path, mode=mode)
    except OSError as exc:
        raise TaskFailed(f"could not create directory {path}: {exc}") from exc


def _single_destination(source: Path, dest: str | Path) -> Path:
    target = Path(dest)
    if isinstance(dest, str) and dest.endswith("/"):
        return target / _decompressed_name(source)
    info = _lstat(target)
    if info is not None and stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        return target / _decompressed_name(source)
    return target


def _decompressed_name(source: Path) -> str:
    name = source.name
    for suffix in _COMPRESSION_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    raise TaskFailed(
        f"cannot derive a decompressed filename from {name!r}; pass a destination file path"
    )


def _reject_archive(path: Path) -> None:
    try:
        is_archive = zipfile.is_zipfile(path) or tarfile.is_tarfile(path)
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc
    if is_archive:
        raise TaskFailed(
            f"source is an archive, not a single compressed file: {path}; use decompress.archive()"
        )


@contextmanager
def _decompressed_stream(path: Path) -> Iterator[Any]:
    try:
        raw = path.open("rb")
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc
    try:
        try:
            head = raw.read(8)
            raw.seek(0)
        except OSError as exc:
            raise TaskFailed(f"could not read {path}: {exc}") from exc
        if head.startswith(b"\x1f\x8b"):
            yield gzip.GzipFile(fileobj=raw, mode="rb")
        elif head.startswith(b"BZh"):
            yield bz2.BZ2File(raw, mode="rb")
        elif head.startswith(b"\xfd7zXZ\x00"):
            yield lzma.LZMAFile(raw, mode="rb")  # noqa: SIM115 - closed with raw
        elif zstd is not None and head.startswith(b"\x28\xb5\x2f\xfd"):
            yield zstd.ZstdFile(raw, mode="rb")
        else:
            raise TaskFailed(f"unsupported compression format: {path}")
    finally:
        raw.close()


def _decompress_to_temp(source: Path, directory: Path) -> Path:
    def write(sink: IO[bytes]) -> None:
        with _decompressed_stream(source) as stream:
            shutil.copyfileobj(stream, sink, _CHUNK_SIZE)

    return _write_temp(directory, write)
