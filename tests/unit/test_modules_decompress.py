from __future__ import annotations

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


@pytest.fixture(params=TAR_COMPRESSIONS)
def tar_archive(tmp_path, request):
    suffix = f".tar.{request.param}" if request.param else ".tar"
    return _write_tar(tmp_path / f"app{suffix}", request.param)


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


def test_archive_extracts_and_is_idempotent(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    ctx = make_context(tmp_path)

    decompress.archive(ctx, tar_archive, dest)

    assert (dest / "app/bin/run").read_bytes() == b"#!/bin/sh\necho hi\n"
    assert stat.S_IMODE((dest / "app/bin/run").stat().st_mode) == 0o755
    assert stat.S_IMODE((dest / "app/etc").stat().st_mode) == 0o750
    assert stat.S_IMODE((dest / "app/etc/config.ini").stat().st_mode) == 0o640
    assert (dest / "app/link").is_symlink()
    assert os.readlink(dest / "app/link") == "bin/run"
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data["action"] == "archive"

    second = make_context(tmp_path)
    decompress.archive(second, tar_archive, dest)

    assert second.changes == []


def test_archive_extracts_zip(tmp_path):
    archive = _write_zip(tmp_path / "app.zip")
    dest = tmp_path / "dest"
    ctx = make_context(tmp_path)

    decompress.archive(ctx, archive, dest)

    assert (dest / "app/bin/run").read_bytes() == b"#!/bin/sh\necho hi\n"
    assert stat.S_IMODE((dest / "app/bin/run").stat().st_mode) == 0o755
    assert stat.S_IMODE((dest / "app/etc/config.ini").stat().st_mode) == 0o640
    assert (dest / "app/link").is_symlink()
    assert os.readlink(dest / "app/link") == "bin/run"

    second = make_context(tmp_path)
    decompress.archive(second, archive, dest)

    assert second.changes == []


def _corrupt_zip_payload(path):
    data = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", data, 26)
    offset = 30 + name_length + extra_length
    data[offset] ^= 0xFF
    path.write_bytes(data)


def test_archive_reports_corrupt_zip_as_task_failure(tmp_path):
    archive = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_archive:
        zip_archive.writestr("file.txt", b"payload\n" * 100)
    _corrupt_zip_payload(archive)

    with pytest.raises(TaskFailed, match="could not extract"):
        decompress.archive(make_context(tmp_path), archive, tmp_path / "dest")


def test_archive_extracts_zip_entries_without_file_type_bits(tmp_path):
    archive = tmp_path / "permissions.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_archive:
        zip_archive.writestr("file.txt", b"payload\n")
    dest = tmp_path / "dest"

    decompress.archive(make_context(tmp_path), archive, dest)

    assert (dest / "file.txt").read_bytes() == b"payload\n"


def test_archive_rewrites_changed_content(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    decompress.archive(make_context(tmp_path), tar_archive, dest)
    target = dest / "app/etc/config.ini"
    target.write_bytes(b"corrupt\n")

    ctx = make_context(tmp_path)
    decompress.archive(ctx, tar_archive, dest)

    assert target.read_bytes() == b"key=value\n"
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data["changed"] == 1


def test_archive_preserves_or_overrides_existing_modes(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    decompress.archive(make_context(tmp_path), tar_archive, dest)
    target = dest / "app/etc/config.ini"
    target.chmod(0o600)

    unchanged = make_context(tmp_path)
    decompress.archive(unchanged, tar_archive, dest)

    assert unchanged.changes == []
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    ctx = make_context(tmp_path)
    decompress.archive(ctx, tar_archive, dest, mode=0o640)

    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert len(ctx.changes) == 1


def test_archive_strips_and_filters(tmp_path):
    archive = tmp_path / "pkg.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, "pkg-1.0/bin/tool", b"tool\n")
        _tar_entry(tar, "pkg-1.0/share/doc.txt", b"doc\n")
        _tar_entry(tar, "pkg-1.0/share/keep.txt", b"keep\n")
    dest = tmp_path / "dest"

    decompress.archive(
        make_context(tmp_path),
        archive,
        dest,
        strip_components=1,
        include=["bin/*", "share/*"],
        exclude=["share/doc.txt"],
    )

    assert (dest / "bin/tool").read_bytes() == b"tool\n"
    assert (dest / "share/keep.txt").read_bytes() == b"keep\n"
    assert not (dest / "share/doc.txt").exists()


def test_archive_creates_guard_skips_extraction(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "marker").write_text("ready\n", encoding="utf-8")

    ctx = make_context(tmp_path)
    decompress.archive(ctx, tar_archive, dest, creates="marker")

    assert ctx.changes == []
    assert not (dest / "app").exists()


def test_archive_dry_run_reports_without_writing(tmp_path, tar_archive):
    dest = tmp_path / "dest"
    ctx = make_context(tmp_path, dry_run=True)

    decompress.archive(ctx, tar_archive, dest)

    assert not dest.exists()
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data["changed"] > 0


@pytest.mark.parametrize("name", ["../evil", "/absolute", "a/../../evil"])
def test_archive_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, name, b"evil\n")
    dest = tmp_path / "dest"

    with pytest.raises(TaskFailed, match="unsafe"):
        decompress.archive(make_context(tmp_path), archive, dest)

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

    with pytest.raises(TaskFailed, match="non-directory"):
        decompress.archive(make_context(tmp_path), archive, dest)

    assert not (outside / "pwned.txt").exists()


def test_archive_rejects_conflicting_entries(tmp_path):
    archive = tmp_path / "conflict.tar"
    with tarfile.open(archive, "w") as tar:
        _tar_entry(tar, "a", b"file\n")
        _tar_entry(tar, "a/b", b"nested\n")

    with pytest.raises(TaskFailed, match="conflicting archive entries"):
        decompress.archive(make_context(tmp_path), archive, tmp_path / "dest")


def test_archive_rejects_invalid_destinations(tmp_path, tar_archive):
    ctx = make_context(tmp_path)
    file_dest = tmp_path / "file"
    file_dest.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(TaskFailed, match="not a directory"):
        decompress.archive(ctx, tar_archive, file_dest)

    link_dest = tmp_path / "link"
    link_dest.symlink_to(tmp_path)
    with pytest.raises(TaskFailed, match="not a directory"):
        decompress.archive(ctx, tar_archive, link_dest)

    with pytest.raises(TaskFailed, match="parent directory does not exist"):
        decompress.archive(ctx, tar_archive, tmp_path / "missing" / "dest")


def test_archive_rejects_unsupported_format(tmp_path):
    source = tmp_path / "plain.txt"
    source.write_text("not an archive\n", encoding="utf-8")

    with pytest.raises(TaskFailed, match="unsupported archive format"):
        decompress.archive(make_context(tmp_path), source, tmp_path / "dest")


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
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="not a directory"):
        decompress.archive(ctx, source, file_dest, creates=marker)
    with pytest.raises(TaskFailed, match="not a directory"):
        decompress.archive(ctx, source, link_dest, creates=marker)


def test_archive_validates_arguments(tmp_path, tar_archive):
    ctx = make_context(tmp_path)
    dest = tmp_path / "dest"

    with pytest.raises(TaskFailed, match="strip_components"):
        decompress.archive(ctx, tar_archive, dest, strip_components=-1)
    with pytest.raises(TaskFailed, match="strip_components"):
        decompress.archive(ctx, tar_archive, dest, strip_components=True)
    with pytest.raises(TaskFailed, match="include patterns must be strings"):
        decompress.archive(ctx, tar_archive, dest, include=[1])  # type: ignore[list-item]
    with pytest.raises(TaskFailed, match="include pattern must not be empty"):
        decompress.archive(ctx, tar_archive, dest, include=[" "])
    with pytest.raises(TaskFailed, match="mode"):
        decompress.archive(ctx, tar_archive, dest, mode=True)  # type: ignore[arg-type]

    with pytest.raises(TaskFailed, match="does not exist"):
        decompress.archive(ctx, tmp_path / "missing.tar", dest)
    with pytest.raises(TaskFailed, match="not a regular file"):
        decompress.archive(ctx, tmp_path, dest)


def test_file_reports_corrupt_gzip_as_task_failure(tmp_path):
    source = tmp_path / "data.txt.gz"
    data = bytearray(gzip.compress(b"payload\n" * 100))
    data[12] ^= 0xFF
    source.write_bytes(data)

    with pytest.raises(TaskFailed, match="could not decompress"):
        decompress.file(make_context(tmp_path), source, tmp_path / "out.txt")


def test_file_reports_decompression_errors_as_source_errors(tmp_path):
    payload = b"payload\n" * 100
    source = tmp_path / "data.txt.gz"
    data = bytearray(gzip.compress(payload))
    data[-8] ^= 0xFF  # corrupt the CRC
    source.write_bytes(data)
    dest = tmp_path / "out.txt"
    dest.write_bytes(payload)

    with pytest.raises(TaskFailed, match="could not decompress") as failure:
        decompress.file(make_context(tmp_path), source, dest)

    assert "could not read" not in str(failure.value)


def test_file_rewrites_changed_content(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"old\n")

    ctx = make_context(tmp_path)
    decompress.file(ctx, source, dest)

    assert dest.read_bytes() == b"payload\n"
    assert len(ctx.changes) == 1


def test_file_preserves_or_overrides_existing_modes(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"payload\n")
    dest.chmod(0o600)

    unchanged = make_context(tmp_path)
    decompress.file(unchanged, source, dest)

    assert unchanged.changes == []
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600

    ctx = make_context(tmp_path)
    decompress.file(ctx, source, dest, mode=0o640)

    assert stat.S_IMODE(dest.stat().st_mode) == 0o640
    assert len(ctx.changes) == 1


def test_file_uses_directory_destinations(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    directory = tmp_path / "out"
    directory.mkdir()

    decompress.file(make_context(tmp_path), source, directory)

    assert (directory / "data.txt").read_bytes() == b"payload\n"

    trailing = tmp_path / "trailing"
    trailing.mkdir()
    decompress.file(make_context(tmp_path), source, f"{trailing}/")

    assert (trailing / "data.txt").read_bytes() == b"payload\n"


def test_file_dry_run_reports_without_writing(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    dest = tmp_path / "out.txt"
    ctx = make_context(tmp_path, dry_run=True)

    decompress.file(ctx, source, dest)

    assert not dest.exists()
    assert len(ctx.changes) == 1


def test_file_rejects_archives(tmp_path):
    archive = _write_tar(tmp_path / "app.tar.gz", "gz")

    with pytest.raises(TaskFailed, match="use decompress.archive"):
        decompress.file(make_context(tmp_path), archive, tmp_path / "out")


def test_file_rejects_unsupported_and_invalid_sources(tmp_path):
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"plain\n")

    with pytest.raises(TaskFailed, match="unsupported compression format"):
        decompress.file(make_context(tmp_path), plain, tmp_path / "out")
    with pytest.raises(TaskFailed, match="does not exist"):
        decompress.file(make_context(tmp_path), tmp_path / "missing.gz", tmp_path / "out")


def test_file_dry_run_still_validates_compression(tmp_path):
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"plain\n")

    with pytest.raises(TaskFailed, match="unsupported compression format"):
        decompress.file(make_context(tmp_path, dry_run=True), plain, tmp_path / "out")


def test_file_rejects_symlink_and_invalid_destinations(tmp_path):
    source = tmp_path / "data.txt.gz"
    source.write_bytes(gzip.compress(b"payload\n"))
    target = tmp_path / "target"
    target.write_bytes(b"keep\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="symlink"):
        decompress.file(ctx, source, link)
    assert target.read_bytes() == b"keep\n"

    with pytest.raises(TaskFailed, match="parent directory does not exist"):
        decompress.file(ctx, source, tmp_path / "missing" / "out.txt")


def test_file_resolves_bundle_relative_sources(tmp_path):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "data.txt.gz").write_bytes(gzip.compress(b"payload\n"))
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = make_context(tmp_path)
    ctx.begin_task(bundle.tasks[0])
    dest = tmp_path / "out.txt"

    decompress.file(ctx, "data.txt.gz", dest)

    assert dest.read_bytes() == b"payload\n"


COMPRESSIONS: list[tuple[str, Callable[[bytes], bytes]]] = [
    (".gz", gzip.compress),
    (".bz2", bz2.compress),
    (".xz", lzma.compress),
    (".zst", zstd.compress),
]


@pytest.mark.parametrize(("suffix", "compress"), COMPRESSIONS)
def test_file_decompresses_and_is_idempotent(tmp_path, suffix, compress):
    source = tmp_path / f"data.txt{suffix}"
    source.write_bytes(compress(b"payload\n"))
    dest = tmp_path / "out.txt"
    ctx = make_context(tmp_path)

    decompress.file(ctx, source, dest)

    assert dest.read_bytes() == b"payload\n"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data["action"] == "decompress"

    second = make_context(tmp_path)
    decompress.file(second, source, dest)

    assert second.changes == []
