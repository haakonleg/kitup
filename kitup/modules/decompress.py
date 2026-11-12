"""Idempotent archive extraction and single-file decompression."""

import bz2
import fnmatch
import gzip
import lzma
import os
import shutil
import stat
import tarfile
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any, Final, Literal

from .._utils import file_actions
from .._utils.file_actions import PathPolicy, require_path_kind
from .._utils.file_primitives import (
    CHUNK_SIZE,
    TempFileError,
    apply_metadata,
    compare_paths,
    mkdir_path,
    replace_path,
    stage_path,
    symlink_path,
)
from .._utils.paths import expand_path
from .._utils.validation import normalize_strings, require_mode, require_non_negative_int, require_path, require_text
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult

try:
    from compression import zstd
except ImportError:  # pragma: no cover - Python builds without zstd support
    zstd = None  # type: ignore[assignment]

type _Archive = tarfile.TarFile | zipfile.ZipFile
type _MemberKind = Literal["dir", "symlink", "file"]

_COMPRESSION_SUFFIXES: Final = (".zst", ".zstd", ".gz", ".bz2", ".xz")

_DECOMPRESSION_ERRORS: Final[tuple[type[BaseException], ...]] = (
    OSError,
    EOFError,
    lzma.LZMAError,
    zlib.error,
    *((zstd.ZstdError,) if zstd is not None else ()),
)

_ARCHIVE_ERRORS: Final[tuple[type[BaseException], ...]] = (
    tarfile.TarError,
    zipfile.BadZipFile,
    OSError,
    EOFError,
    RuntimeError,
    NotImplementedError,
    zlib.error,
)


@dataclass(frozen=True, slots=True)
class _Member:
    """One selected archive member."""

    name: str
    kind: _MemberKind
    raw: Any
    size: int = 0  # file size; unused for directories and symlinks
    mode: int | None = None
    link: str | None = None


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
) -> CallResult:
    """Extract a tar or zip archive into ``dest``, replacing only differences.

    ``src`` resolves like :func:`kitup.modules.files.copy`. Absolute paths are
    used as-is. The format is detected from the archive contents.

    ``creates`` is a path relative to ``dest`` (or an absolute path); when it
    exists the archive is not opened. ``strip_components`` removes leading path
    components before ``include`` and ``exclude`` patterns are matched.
    """
    require_path(src, "archive source")
    require_path(dest, "archive destination")
    require_mode(mode)
    require_non_negative_int(strip_components, "strip_components")
    includes = _require_patterns(include, "include")
    excludes = _require_patterns(exclude, "exclude")

    source = ctx.resolve_source(src)
    _require_source(source, "archive source")
    target = expand_path(dest, error=TaskFailed)
    target_info = file_actions.stat_local(target)
    require_path_kind(target, target_info, policy=PathPolicy.DIRECTORY, label="archive destination")
    if target_info is None:
        file_actions.require_parent_local(target)
    if creates is not None:
        marker = expand_path(creates, error=TaskFailed)
        if not marker.is_absolute():
            marker = target / marker
        if file_actions.stat_local(marker) is not None:
            return ctx.changed(
                _archive_result(
                    source,
                    target,
                    changes=0,
                    files=[],
                    size=_directory_size(target),
                )
            )

    with closing(_open_archive(source)) as opened:
        entries = _select_entries(opened, strip_components, includes, excludes)
        files = _archive_files(entries)
        changed = _reconcile(
            ctx,
            opened,
            entries,
            target,
            target_info,
            mode=mode,
        )

    return ctx.changed(
        _archive_result(
            source,
            target,
            changes=changed,
            files=files,
            size=_directory_size(target),
        )
    )


def file(
    ctx: TaskContext,
    src: str | Path,
    dest: str | Path,
    *,
    mode: int | None = None,
) -> CallResult:
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
    target, info = _single_destination(source, dest)
    require_path_kind(target, info, policy=PathPolicy.FILE, label="decompressed destination")
    if info is None:
        file_actions.require_parent_local(target)

    if ctx.dry_run:
        if info is None:
            # Validate the compression format without decompressing.
            with _translate_decompression_errors(source), _decompressed_stream(source):
                pass
            changed = True
        else:
            with _translate_decompression_errors(source), _decompressed_stream(source) as stream:
                matches = _stream_matches(target, stream)
            changed = not matches or file_actions.reconcile_metadata_changed(ctx, target, info, mode=mode, sudo=False)
    else:
        # Decompress once into a staged file, then compare it with the target so
        # a rewrite never decompresses the source a second time.
        with (
            _translate_decompression_errors(source),
            _staged(
                target.parent,
                lambda: _decompressed_stream(source),
            ) as temporary,
        ):
            if info is not None and _files_match(target, temporary):
                changed = file_actions.reconcile_metadata_changed(ctx, target, info, mode=mode, sudo=False)
            else:
                _install_temp(temporary, target, mode=mode, default_mode=0o644)
                changed = True
    return ctx.changed(_file_change(target, source, changed=changed))


def _archive_result(
    source: Path,
    target: Path,
    *,
    changes: int,
    files: list[str],
    size: int,
) -> CallResult:
    summary = f"extract {changes} paths from {source} -> {target}" if changes else f"ensure {target} from {source}"
    return CallResult(
        action="decompress.archive",
        changed=bool(changes),
        summary=summary,
        src=str(source),
        dest=str(target),
        files=files,
        size=size,
    )


def _file_change(target: Path, source: Path, *, changed: bool) -> CallResult:
    summary = f"decompress {source} -> {target}" if changed else f"ensure {target} from {source}"
    return CallResult(
        action="decompress.file",
        changed=changed,
        summary=summary,
        dest=str(target),
        src=str(source),
    )


def _reconcile(
    ctx: TaskContext,
    archive: _Archive,
    entries: dict[str, _Member],
    target: Path,
    target_info: file_actions.FileInfo | None,
    *,
    mode: int | None,
) -> int:
    changed = 0
    dry_run = ctx.dry_run
    if target_info is None:
        if not dry_run:
            _mkdir(target, 0o755)
        changed += 1
    # Process members in archive order: compressed tar streams decompress
    # sequentially, so revisiting an earlier member restarts from the beginning.
    # Missing parents are created on demand instead of sorting by depth.
    known: set[Path] = set()
    created: set[Path] = set()
    for member in entries.values():
        parts = member.name.split("/")
        changed += _ensure_parents(target, parts[:-1], known=known, created=created, dry_run=dry_run)
        path = target.joinpath(*parts)
        if member.kind == "dir":
            changed += _ensure_directory(path, member, known=known, created=created, dry_run=dry_run)
        elif member.kind == "symlink":
            changed += _ensure_symlink(path, member, dry_run=dry_run)
        else:
            changed += _ensure_file(ctx, archive, member, path, mode=mode)
    return changed


def _ensure_parents(
    target: Path,
    parts: list[str],
    *,
    known: set[Path],
    created: set[Path],
    dry_run: bool,
) -> int:
    count = 0
    current = target
    for part in parts:
        current = current / part
        if current in known:
            continue
        info = file_actions.stat_local(current)
        if info is None:
            if not dry_run:
                _mkdir(current, 0o755)
            known.add(current)
            created.add(current)
            count += 1
            continue
        require_path_kind(current, info, policy=PathPolicy.DIRECTORY)
        known.add(current)
    return count


def _ensure_directory(
    path: Path,
    member: _Member,
    *,
    known: set[Path],
    created: set[Path],
    dry_run: bool,
) -> bool:
    if path in created:
        # Created as a parent earlier in this run; apply the archive mode now.
        if not dry_run and member.mode is not None:
            _apply_mode(path, member.mode)
        return False
    if path in known:
        return False
    info = file_actions.stat_local(path)
    if info is None:
        if not dry_run:
            _mkdir(path, member.mode or 0o755)
        known.add(path)
        created.add(path)
        return True
    require_path_kind(path, info, policy=PathPolicy.DIRECTORY)
    known.add(path)
    return False


def _ensure_file(
    ctx: TaskContext,
    archive: _Archive,
    member: _Member,
    path: Path,
    *,
    mode: int | None,
) -> bool:
    info = file_actions.stat_local(path)
    require_path_kind(path, info, policy=PathPolicy.NOT_DIRECTORY)

    if ctx.dry_run:
        if _member_matches(archive, member, path, info):
            return file_actions.reconcile_metadata_changed(ctx, path, info, mode=mode, sudo=False)
        return True

    # Stage the member once, then compare the staged copy with the target. This
    # avoids reopening the member, which would rewind a compressed archive
    # stream and decompress it from the start a second time.
    try:
        with _staged(path.parent, lambda: _open_member(archive, member)) as temporary:
            if _same_contents(path, info, temporary, member):
                return file_actions.reconcile_metadata_changed(ctx, path, info, mode=mode, sudo=False)
            _install_temp(temporary, path, mode=mode, default_mode=member.mode or 0o644)
            return True
    except TaskFailed:
        raise
    except _ARCHIVE_ERRORS as exc:
        raise TaskFailed(f"could not extract {member.name!r}: {exc}") from exc


def _same_contents(
    path: Path,
    info: file_actions.FileInfo | None,
    temporary: Path,
    member: _Member,
) -> bool:
    """Return whether the target already holds the member's contents."""
    if not _same_size_regular(info, member.size):
        return False
    return _files_match(path, temporary)


def _same_size_regular(info: file_actions.FileInfo | None, size: int) -> bool:
    """Return whether ``info`` describes a regular file of ``size`` bytes."""
    return info is not None and not info.is_symlink and info.is_file and info.size == size


def _ensure_symlink(
    path: Path,
    member: _Member,
    *,
    dry_run: bool,
) -> bool:
    link = member.link
    if link is None:
        raise TaskFailed(f"missing symlink target in archive: {member.name!r}")
    info = file_actions.stat_local(path)
    require_path_kind(path, info, policy=PathPolicy.NOT_DIRECTORY)
    if info is not None and info.is_symlink and info.link == link:
        return False
    if dry_run:
        return True
    try:
        symlink_path(link, path)
    except OSError as exc:
        raise TaskFailed(f"could not create symlink {path}: {exc}") from exc
    return True


def _member_matches(
    archive: _Archive,
    member: _Member,
    path: Path,
    info: file_actions.FileInfo | None,
) -> bool:
    if not _same_size_regular(info, member.size):
        return False
    try:
        with _open_member(archive, member) as stream:
            return _stream_matches(path, stream)
    except TaskFailed:
        raise
    except _ARCHIVE_ERRORS as exc:
        raise TaskFailed(f"could not read archive member {member.name!r}: {exc}") from exc


def _files_match(path: Path, other: Path) -> bool:
    """Return whether two regular files have identical contents."""
    try:
        return compare_paths(path, other)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not compare {path} and {other}: {exc}") from exc


def _stream_matches(path: Path, stream: Any) -> bool:
    try:
        existing = path.open("rb")
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc
    with existing:
        while True:
            try:
                current = existing.read(CHUNK_SIZE)
            except OSError as exc:
                raise TaskFailed(f"could not read {path}: {exc}") from exc
            # Stream errors belong to the source and are reported by callers.
            incoming = stream.read(CHUNK_SIZE)
            if current != incoming:
                return False
            if not current:
                return True


@contextmanager
def _open_member(
    archive: _Archive,
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


@contextmanager
def _staged(
    directory: Path,
    open_stream: Callable[[], AbstractContextManager[Any]],
) -> Iterator[Path]:
    """Stage a stream beside its destination, removing it on exit."""

    def write(sink: IO[bytes]) -> None:
        with open_stream() as source:
            shutil.copyfileobj(source, sink, CHUNK_SIZE)

    try:
        temporary = Path(stage_path(directory, write, prefix=".kitup-decompress-"))
    except TempFileError as exc:
        raise TaskFailed(str(exc)) from exc
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def _install_temp(temporary: Path, path: Path, *, mode: int | None, default_mode: int) -> None:
    """Replace ``path`` with a staged temporary file."""
    try:
        replace_path(temporary, path, mode=mode, default_mode=default_mode)
    except ValueError as exc:
        raise TaskFailed(str(exc)) from exc
    except OSError as exc:
        raise TaskFailed(f"could not write {path}: {exc}") from exc


def _apply_mode(path: Path, mode: int) -> None:
    try:
        apply_metadata(path, mode, None, None, preserve=True)
    except OSError as exc:
        raise TaskFailed(f"could not update metadata for {path}: {exc}") from exc


def _archive_kind(path: Path) -> Literal["zip", "tar"] | None:
    """Return the archive format detected from ``path``, if any."""
    try:
        if zipfile.is_zipfile(path):
            return "zip"
        if tarfile.is_tarfile(path):
            return "tar"
    except OSError as exc:
        raise TaskFailed(f"could not read {path}: {exc}") from exc
    return None


def _open_archive(path: Path) -> _Archive:
    kind = _archive_kind(path)
    try:
        if kind == "zip":
            return zipfile.ZipFile(path)
        if kind == "tar":
            return tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise TaskFailed(f"could not open archive {path}: {exc}") from exc
    raise TaskFailed(f"unsupported archive format: {path}")


def _select_entries(
    archive: _Archive,
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


def _archive_files(entries: dict[str, _Member]) -> list[str]:
    """Return selected file and symlink entries relative to the destination."""
    return sorted(name for name, member in entries.items() if member.kind != "dir")


def _directory_size(path: Path) -> int:
    """Return the total size of regular files under ``path`` in bytes."""
    info = file_actions.stat_local(path)
    if info is None or not info.is_dir:
        return 0
    total = 0
    try:
        for root, _directories, names in os.walk(path, followlinks=False):
            for name in names:
                file_info = file_actions.stat_local(Path(root) / name)
                if file_info is not None and file_info.is_file:
                    total += file_info.size
    except OSError as exc:
        raise TaskFailed(f"could not measure {path}: {exc}") from exc
    return total


def _iter_members(
    archive: _Archive,
) -> Iterator[_Member]:
    if isinstance(archive, tarfile.TarFile):
        for info in archive.getmembers():
            if info.isdir():
                yield _Member(info.name, "dir", info, mode=_archive_mode(info.mode, 0o755))
            elif info.issym():
                yield _Member(info.name, "symlink", info, link=_require_link(info.linkname, info.name))
            elif info.isreg():
                yield _Member(
                    info.name,
                    "file",
                    info,
                    size=info.size,
                    mode=_archive_mode(info.mode, 0o644),
                )
            else:
                raise TaskFailed(f"unsupported archive member type for {info.name!r}")
        return

    for zip_info in archive.infolist():
        raw_mode = (zip_info.external_attr >> 16) & 0xFFFF
        if zip_info.is_dir() or stat.S_ISDIR(raw_mode):
            yield _Member(zip_info.filename, "dir", zip_info, mode=_archive_mode(raw_mode, 0o755))
        elif stat.S_ISLNK(raw_mode):
            link = archive.read(zip_info).decode("utf-8", "surrogateescape")
            yield _Member(
                zip_info.filename,
                "symlink",
                zip_info,
                link=_require_link(link, zip_info.filename),
            )
        elif (raw_mode & 0o170000) in (0, stat.S_IFREG):
            yield _Member(
                zip_info.filename,
                "file",
                zip_info,
                size=zip_info.file_size,
                mode=_archive_mode(raw_mode, 0o644),
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
                    f"conflicting archive entries: {parent!r} is not a directory but {name!r} is nested inside it"
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
    require_path_kind(path, file_actions.stat_local(path), policy=PathPolicy.FILE, label=label, missing_ok=False)


def _mkdir(path: Path, mode: int) -> None:
    try:
        mkdir_path(path, mode=mode)
    except OSError as exc:
        raise TaskFailed(f"could not create directory {path}: {exc}") from exc


def _single_destination(source: Path, dest: str | Path) -> tuple[Path, file_actions.FileInfo | None]:
    """Resolve the destination file and stat it once."""
    target = expand_path(dest, error=TaskFailed)
    if isinstance(dest, str) and dest.endswith("/"):
        target = target / _decompressed_name(source)
        return target, file_actions.stat_local(target)
    info = file_actions.stat_local(target)
    if info is not None and info.is_dir:
        target = target / _decompressed_name(source)
        return target, file_actions.stat_local(target)
    return target, info


def _decompressed_name(source: Path) -> str:
    name = source.name
    for suffix in _COMPRESSION_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    raise TaskFailed(f"cannot derive a decompressed filename from {name!r}; pass a destination file path")


def _reject_archive(path: Path) -> None:
    if _archive_kind(path) is not None:
        raise TaskFailed(f"source is an archive, not a single compressed file: {path}; use decompress.archive()")


@contextmanager
def _translate_decompression_errors(source: Path) -> Iterator[None]:
    """Translate source stream failures into ``TaskFailed`` for ``source``."""
    try:
        yield
    except TaskFailed:
        raise
    except _DECOMPRESSION_ERRORS as exc:
        raise TaskFailed(f"could not decompress {source}: {exc}") from exc


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
