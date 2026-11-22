import bz2
import gzip
import io
import lzma
import os
import stat
import struct
import tarfile
import zipfile
from collections.abc import Callable
from compression import zstd

import pytest

from kitup.errors import TaskFailed
from kitup.modules import decompress
from kitup.registry import Bundle

from .helpers import make_context

TAR_COMPRESSIONS = ("", "gz", "bz2", "xz", "zst")


def _tar_entry(
    archive: tarfile.TarFile,
    name: str,
    data: bytes = b"",
    *,
    mode: int = 0o644,
    kind: str = "file",
    link: str | None = None,
) -> None:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.mtime = 1_700_000_000
    if kind == "dir":
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
    elif kind == "symlink":
        info.type = tarfile.SYMTYPE
        info.linkname = link or ""
        archive.addfile(info)
    else:
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))


def _write_tar(path, compression: str = ""):
    mode = f"w:{compression}" if compression else "w"
    with tarfile.open(path, mode) as archive:  # type: ignore[call-overload]
        _tar_entry(archive, "app", kind="dir", mode=0o755)
        _tar_entry(archive, "app/bin", kind="dir", mode=0o755)
        _tar_entry(archive, "app/bin/run", b"#!/bin/sh\necho hi\n", mode=0o755)
        _tar_entry(archive, "app/etc", kind="dir", mode=0o750)
        _tar_entry(archive, "app/etc/config.ini", b"key=value\n", mode=0o640)
        _tar_entry(archive, "app/link", kind="symlink", link="bin/run")
    return path


@pytest.fixture
def tar_archive(tmp_path):
    return _write_tar(tmp_path / "app.tar")


def _zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes = b"",
    *,
    mode: int = 0o644,
    kind: str = "file",
    link: str | None = None,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    if kind == "dir":
        info.external_attr = (stat.S_IFDIR | mode) << 16
        archive.writestr(info, b"")
    elif kind == "symlink":
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, (link or "").encode())
    else:
        info.external_attr = (stat.S_IFREG | mode) << 16
        archive.writestr(info, data)


def _write_zip(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        _zip_entry(archive, "app/", kind="dir", mode=0o755)
        _zip_entry(archive, "app/bin/", kind="dir", mode=0o755)
        _zip_entry(archive, "app/bin/run", b"#!/bin/sh\necho hi\n", mode=0o755)
        _zip_entry(archive, "app/etc/config.ini", b"key=value\n", mode=0o640)
        _zip_entry(archive, "app/link", kind="symlink", link="bin/run")
    return path


@pytest.mark.parametrize("compression", TAR_COMPRESSIONS)
def test_archive_extracts_and_is_idempotent(tmp_path, compression):
    suffix = f".tar.{compression}" if compression else ".tar"
    tar_archive = _write_tar(tmp_path / f"app{suffix}", compression)
    dest = tmp_path / "dest"
    ctx = make_context(tmp_path)

    decompress.archive(tar_archive, dest)

    assert (dest / "app/bin/run").read_bytes() == b"#!/bin/sh\necho hi\n"
    assert stat.S_IMODE((dest / "app/bin/run").stat().st_mode) == 0o755
    assert stat.S_IMODE((dest / "app/etc").stat().st_mode) == 0o750
    assert stat.S_IMODE((dest / "app/etc/config.ini").stat().st_mode) == 0o640
    assert (dest / "app/link").is_symlink()
    assert os.readlink(dest / "app/link") == "bin/run"
    assert len(ctx.changes) == 1
    call = ctx.changes[0]
    assert call.action == "decompress.archive"
    assert call.src == str(tar_archive)
    assert call.dest == str(dest)
    assert call.files == ["app/bin/run", "app/etc/config.ini", "app/link"]
    assert call.size == (dest / "app/bin/run").stat().st_size + (dest / "app/etc/config.ini").stat().st_size

    second = make_context(tmp_path)
    decompress.archive(tar_archive, dest)

    assert second.changes == []
    assert second.calls[0].files == call.files
    assert second.calls[0].size == call.size


def test_archive_applies_directory_modes_for_late_directory_entries(tmp_path):
    archive = tmp_path / "late-dirs.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, "app/etc/config.ini", b"key=value\n", mode=0o640)
        _tar_entry(tar, "app/etc", kind="dir", mode=0o750)
        _tar_entry(tar, "app/bin/run", b"#!/bin/sh\n", mode=0o755)
        _tar_entry(tar, "app/bin", kind="dir", mode=0o700)
    dest = tmp_path / "dest"

    ctx = make_context(tmp_path)
    decompress.archive(archive, dest)

    assert (dest / "app/etc/config.ini").read_bytes() == b"key=value\n"
    assert (dest / "app/bin/run").read_bytes() == b"#!/bin/sh\n"
    assert stat.S_IMODE((dest / "app/etc").stat().st_mode) == 0o750
    assert stat.S_IMODE((dest / "app/bin").stat().st_mode) == 0o700
    assert len(ctx.changes) == 1

    second = make_context(tmp_path)
    decompress.archive(archive, dest)

    assert second.changes == []


class _CountingStream:
    """Wrap a decompressed archive stream and count backward seeks."""

    def __init__(self, stream, raw):
        self._stream = stream
        self._raw = raw
        self.rewinds = 0

    def read(self, size=-1):
        return self._stream.read(size)

    def seek(self, offset, whence=0):
        before = self._stream.tell()
        result = self._stream.seek(offset, whence)
        if result < before:
            self.rewinds += 1
        return result

    def tell(self):
        return self._stream.tell()

    def close(self):
        self._stream.close()
        self._raw.close()


def _corrupt_zip_payload(path):
    data = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", data, 26)
    offset = 30 + name_length + extra_length
    data[offset] ^= 0xFF
    path.write_bytes(data)


def test_archive_handles_zip_formats_and_corruption(tmp_path):
    archive = _write_zip(tmp_path / "app.zip")
    dest = tmp_path / "dest"
    make_context(tmp_path)

    # Extract modes, symlinks, and idempotency.
    decompress.archive(archive, dest)

    assert (dest / "app/bin/run").read_bytes() == b"#!/bin/sh\necho hi\n"
    assert stat.S_IMODE((dest / "app/bin/run").stat().st_mode) == 0o755
    assert stat.S_IMODE((dest / "app/etc/config.ini").stat().st_mode) == 0o640
    assert (dest / "app/link").is_symlink()
    assert os.readlink(dest / "app/link") == "bin/run"

    second = make_context(tmp_path)
    decompress.archive(archive, dest)

    assert second.changes == []

    # Entries without file type bits are treated as regular files.
    plain_zip = tmp_path / "permissions.zip"
    with zipfile.ZipFile(plain_zip, "w", zipfile.ZIP_DEFLATED) as zip_archive:
        zip_archive.writestr("file.txt", b"payload\n")
    plain_dest = tmp_path / "plain-dest"

    decompress.archive(plain_zip, plain_dest)

    assert (plain_dest / "file.txt").read_bytes() == b"payload\n"

    # A corrupt payload is reported as an extraction failure.
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(corrupt, "w", zipfile.ZIP_DEFLATED) as zip_archive:
        zip_archive.writestr("file.txt", b"payload\n" * 100)
    _corrupt_zip_payload(corrupt)

    with pytest.raises(TaskFailed, match="could not extract"):
        decompress.archive(corrupt, tmp_path / "corrupt-dest")


def test_archive_rewrites_changed_content(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    make_context(tmp_path)
    decompress.archive(tar_archive, dest)
    target = dest / "app/etc/config.ini"
    target.write_bytes(b"corrupt\n")

    ctx = make_context(tmp_path)
    decompress.archive(tar_archive, dest)

    assert target.read_bytes() == b"key=value\n"
    assert len(ctx.changes) == 1
    assert ctx.changes[0].files == ["app/bin/run", "app/etc/config.ini", "app/link"]


def test_archive_preserves_or_overrides_existing_modes(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    make_context(tmp_path)
    decompress.archive(tar_archive, dest)
    target = dest / "app/etc/config.ini"
    target.chmod(0o600)

    unchanged = make_context(tmp_path)
    decompress.archive(tar_archive, dest)

    assert unchanged.changes == []
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    ctx = make_context(tmp_path)
    decompress.archive(tar_archive, dest, mode=0o640)

    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert len(ctx.changes) == 1


def test_archive_accepts_octal_string_mode(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    make_context(tmp_path)

    decompress.archive(tar_archive, dest, mode="0640")

    target = dest / "app/etc/config.ini"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_archive_strips_and_filters(tmp_path):
    archive = tmp_path / "pkg.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, "pkg-1.0/bin/tool", b"tool\n")
        _tar_entry(tar, "pkg-1.0/share/doc.txt", b"doc\n")
        _tar_entry(tar, "pkg-1.0/share/keep.txt", b"keep\n")
    dest = tmp_path / "dest"

    make_context(tmp_path)
    decompress.archive(
        archive,
        dest,
        strip_components=1,
        include=["bin/*", "share/*"],
        exclude=["share/doc.txt"],
    )

    assert (dest / "bin/tool").read_bytes() == b"tool\n"
    assert (dest / "share/keep.txt").read_bytes() == b"keep\n"
    assert not (dest / "share/doc.txt").exists()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("filters", "message"),
    [
        ({"include": "missing/*"}, "include=['missing/*']"),
        ({"exclude": "*"}, "exclude=['*']"),
    ],
)
def test_archive_rejects_filters_selecting_no_entries(tmp_path, tar_archive, dry_run, filters, message):
    dest = tmp_path / "dest"

    with pytest.raises(TaskFailed, match="no archive entries selected") as failure:
        make_context(tmp_path, dry_run=dry_run)
        decompress.archive(tar_archive, dest, **filters)

    assert message in str(failure.value)
    assert not dest.exists()


def test_archive_creates_guard_skips_extraction(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "marker").write_text("ready\n", encoding="utf-8")

    ctx = make_context(tmp_path)
    decompress.archive(tar_archive, dest, creates="marker")

    assert ctx.changes == []
    assert not (dest / "app").exists()
    assert ctx.calls[0].files == []
    assert ctx.calls[0].size == (dest / "marker").stat().st_size


def test_archive_dry_run_reports_without_writing(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    ctx = make_context(tmp_path, dry_run=True)

    decompress.archive(tar_archive, dest)

    assert not dest.exists()
    assert len(ctx.changes) == 1
    assert ctx.changes[0].files == ["app/bin/run", "app/etc/config.ini", "app/link"]
    assert ctx.changes[0].size == 0


def test_archive_dry_run_defers_missing_parent_check(tmp_path, tar_archive):
    dest = tmp_path / "missing" / "dest"
    ctx = make_context(tmp_path, dry_run=True)

    decompress.archive(tar_archive, dest)

    assert not (tmp_path / "missing").exists()
    assert len(ctx.changes) == 1
    assert ctx.changes[0].changed


@pytest.mark.parametrize("name", ["../evil", "/absolute", "a/../../evil"])
def test_archive_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, name, b"evil\n")
    dest = tmp_path / "dest"

    with pytest.raises(TaskFailed, match="unsafe"):
        make_context(tmp_path)
        decompress.archive(archive, dest)

    assert not (tmp_path.parent / "evil").exists()


def test_archive_refuses_to_follow_symlinks_inside_dest(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "escape").symlink_to(outside, target_is_directory=True)
    archive = tmp_path / "escape.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, "escape/pwned.txt", b"pwned\n")

    with pytest.raises(TaskFailed, match="symlink"):
        make_context(tmp_path)
        decompress.archive(archive, dest)

    assert not (outside / "pwned.txt").exists()


def test_archive_rejects_conflicting_entries(tmp_path):
    archive = tmp_path / "conflict.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, "a", b"file\n")
        _tar_entry(tar, "a/b", b"nested\n")

    with pytest.raises(TaskFailed, match="conflicting archive entries"):
        make_context(tmp_path)
        decompress.archive(archive, tmp_path / "dest")


def test_archive_rejects_invalid_destinations(tmp_path, tar_archive):
    make_context(tmp_path)
    file_dest = tmp_path / "file"
    file_dest.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(TaskFailed, match="not a directory"):
        decompress.archive(tar_archive, file_dest)

    link_dest = tmp_path / "link"
    link_dest.symlink_to(tmp_path)
    with pytest.raises(TaskFailed, match="symlink"):
        decompress.archive(tar_archive, link_dest)

    with pytest.raises(TaskFailed, match="parent directory does not exist"):
        decompress.archive(tar_archive, tmp_path / "missing" / "dest")


def test_archive_rejects_unsupported_format(tmp_path):
    source = tmp_path / "plain.txt"
    source.write_text("not an archive\n", encoding="utf-8")

    with pytest.raises(TaskFailed, match="unsupported archive format"):
        make_context(tmp_path)
        decompress.archive(source, tmp_path / "dest")


def test_archive_validates_dest_before_creates_guard(tmp_path):
    source = tmp_path / "app.tar"
    with tarfile.open(source, "w") as tar:
        _tar_entry(tar, "file.txt", b"payload\n")
    marker = tmp_path / "marker"
    marker.write_text("ready\n", encoding="utf-8")
    file_dest = tmp_path / "file"
    file_dest.write_text("not a directory\n", encoding="utf-8")
    link_dest = tmp_path / "link"
    link_dest.symlink_to(tmp_path)
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="not a directory"):
        decompress.archive(source, file_dest, creates=marker)
    with pytest.raises(TaskFailed, match="symlink"):
        decompress.archive(source, link_dest, creates=marker)


def test_archive_validates_arguments(tmp_path, tar_archive):
    make_context(tmp_path)
    dest = tmp_path / "dest"

    with pytest.raises(TaskFailed, match="strip_components"):
        decompress.archive(tar_archive, dest, strip_components=-1)
    with pytest.raises(TaskFailed, match="strip_components"):
        decompress.archive(tar_archive, dest, strip_components=True)
    with pytest.raises(TaskFailed, match="include patterns must be strings"):
        decompress.archive(tar_archive, dest, include=[1])  # type: ignore[list-item]
    with pytest.raises(TaskFailed, match="include pattern must not be empty"):
        decompress.archive(tar_archive, dest, include=[" "])
    with pytest.raises(TaskFailed, match="mode"):
        decompress.archive(tar_archive, dest, mode=True)  # type: ignore[arg-type]

    with pytest.raises(TaskFailed, match="does not exist"):
        decompress.archive(tmp_path / "missing.tar", dest)
    with pytest.raises(TaskFailed, match="directory"):
        decompress.archive(tmp_path, dest)


def test_file_reports_corruption_as_task_failure(tmp_path):
    # A corrupt compressed payload fails during decompression.
    source = tmp_path / "corrupt.txt.gz"
    data = bytearray(gzip.compress(b"payload\n" * 100))
    data[12] ^= 0xFF
    source.write_bytes(data)

    with pytest.raises(TaskFailed, match="could not decompress"):
        make_context(tmp_path)
        decompress.file(source, tmp_path / "out.txt")

    # A corrupt CRC is reported as a source error, not a destination read error.
    payload = b"payload\n" * 100
    crc_source = tmp_path / "crc.txt.gz"
    data = bytearray(gzip.compress(payload))
    data[-8] ^= 0xFF
    crc_source.write_bytes(data)
    dest = tmp_path / "out.txt"
    dest.write_bytes(payload)

    with pytest.raises(TaskFailed, match="could not decompress") as failure:
        make_context(tmp_path)
        decompress.file(crc_source, dest)

    assert "could not read" not in str(failure.value)


def test_file_reconciles_content_and_staging(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"old\n")

    ctx = make_context(tmp_path)
    decompress.file(source, dest)

    assert dest.read_bytes() == b"payload\n"
    assert len(ctx.changes) == 1
    assert list(tmp_path.glob(".kitup-decompress-*")) == []

    # A matching destination discards the staged copy without recording a change.
    ctx = make_context(tmp_path)
    decompress.file(source, dest)

    assert ctx.changes == []
    assert list(tmp_path.glob(".kitup-decompress-*")) == []


def test_file_preserves_or_overrides_modes(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))

    # Existing modes are preserved when no explicit mode is requested.
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"payload\n")
    dest.chmod(0o600)

    unchanged = make_context(tmp_path)
    decompress.file(source, dest)

    assert unchanged.changes == []
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600

    # An explicit mode overrides it.
    ctx = make_context(tmp_path)
    decompress.file(source, dest, mode=0o640)

    assert stat.S_IMODE(dest.stat().st_mode) == 0o640
    assert len(ctx.changes) == 1

    # Octal string modes are accepted for new destinations.
    octal_dest = tmp_path / "octal.txt"
    make_context(tmp_path)
    decompress.file(source, octal_dest, mode="0640")

    assert stat.S_IMODE(octal_dest.stat().st_mode) == 0o640


def test_file_uses_directory_destinations(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    directory = tmp_path / "out"
    directory.mkdir()

    make_context(tmp_path)
    decompress.file(source, directory)

    assert (directory / "data.txt").read_bytes() == b"payload\n"

    trailing = tmp_path / "trailing"
    trailing.mkdir()
    make_context(tmp_path)
    decompress.file(source, f"{trailing}/")

    assert (trailing / "data.txt").read_bytes() == b"payload\n"


def test_file_dry_run_reports_without_writing(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    dest = tmp_path / "out.txt"
    ctx = make_context(tmp_path, dry_run=True)

    decompress.file(source, dest)

    assert not dest.exists()
    assert len(ctx.changes) == 1

    # A missing parent is not created, and is not an error, in dry-run mode.
    nested = tmp_path / "missing" / "out.txt"
    ctx = make_context(tmp_path, dry_run=True)

    decompress.file(source, nested)

    assert not (tmp_path / "missing").exists()
    assert len(ctx.changes) == 1
    assert ctx.changes[0].changed


def test_file_rejects_invalid_sources_and_destinations(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    make_context(tmp_path)

    # Archives must use decompress.archive.
    archive = _write_tar(tmp_path / "app.tar.gz", "gz")
    with pytest.raises(TaskFailed, match=r"use decompress\.archive"):
        decompress.file(archive, tmp_path / "out")

    # Unsupported and missing sources.
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"plain\n")
    with pytest.raises(TaskFailed, match="unsupported compression format"):
        decompress.file(plain, tmp_path / "out")
    with pytest.raises(TaskFailed, match="does not exist"):
        decompress.file(tmp_path / "missing.gz", tmp_path / "out")

    # Symlink destinations are refused without touching their target.
    target = tmp_path / "target"
    target.write_bytes(b"keep\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(TaskFailed, match="symlink"):
        decompress.file(source, link)
    assert target.read_bytes() == b"keep\n"

    # Missing destination parents are rejected outside dry-run mode.
    with pytest.raises(TaskFailed, match="parent directory does not exist"):
        decompress.file(source, tmp_path / "missing" / "out.txt")


def test_file_resolves_bundle_relative_sources(tmp_path):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "data.txt.gz").write_bytes(gzip.compress(b"payload\n"))
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda: None)
    ctx = make_context(tmp_path)
    ctx.begin_task(bundle.tasks[0])
    dest = tmp_path / "out.txt"

    result = decompress.file("data.txt.gz", dest)

    assert dest.read_bytes() == b"payload\n"
    assert result.summary == f"decompress data.txt.gz -> {dest}"

    result = decompress.file("data.txt.gz", dest)

    assert result.summary == f"ensure {dest} from data.txt.gz"


COMPRESSIONS: list[tuple[str, Callable[[bytes], bytes]]] = [
    (".gz", gzip.compress),
    (".bz2", bz2.compress),
    (".xz", lzma.compress),
    (".zst", zstd.compress),
]


def test_file_decompresses_and_is_idempotent(tmp_path):
    for suffix, compress in COMPRESSIONS:
        case = tmp_path / suffix.removeprefix(".")
        case.mkdir()
        source = case / f"data.txt{suffix}"
        source.write_bytes(compress(b"payload\n"))
        dest = case / "out.txt"
        ctx = make_context(tmp_path)

        decompress.file(source, dest)

        assert dest.read_bytes() == b"payload\n"
        assert stat.S_IMODE(dest.stat().st_mode) == 0o644
        assert len(ctx.changes) == 1
        assert ctx.changes[0].action == "decompress.file"
        assert ctx.changes[0].dest == str(dest)
        assert ctx.changes[0].src == str(source)

        second = make_context(tmp_path)
        decompress.file(source, dest)

        assert second.changes == []
        assert second.calls[0].summary == f"ensure {dest} from {source}"
