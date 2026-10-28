from __future__ import annotations

import hashlib
import os
import stat

import pytest

from kitup._utils.file_helper import (
    apply_metadata,
    atomic_write_path,
    copy_path,
    digest_path,
    mkdir_path,
    read_path,
    remove_path,
    stat_path,
    symlink_path,
    write_path,
)


def test_write_stat_read_remove(tmp_path):
    path = tmp_path / "file"

    write_path(path, b"data\n", mode=0o600)
    info = stat_path(path)

    assert info is not None
    assert info["is_file"]
    assert not info["is_dir"]
    assert not info["is_symlink"]
    assert info["mode"] == 0o600
    assert read_path(path) == b"data\n"

    remove_path(path)
    assert stat_path(path) is None


def test_mkdir_metadata_and_remove(tmp_path):
    path = tmp_path / "dir"

    mkdir_path(path, mode=0o700)
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700

    apply_metadata(path, 0o750, None, None)
    assert stat.S_IMODE(path.stat().st_mode) == 0o750

    remove_path(path)
    assert not path.exists()


def test_symlink_replace(tmp_path):
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "link"

    symlink_path(str(target), link)
    info = stat_path(link)

    assert info is not None
    assert info["is_symlink"]
    assert info["link"] == str(target)

    symlink_path("other", link)
    assert os.readlink(link) == "other"


def test_remove_force(tmp_path):
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "file").write_text("x")

    remove_path(root, force=True)

    assert not root.exists()


def test_atomic_write_path_uses_callback_and_preserves_metadata(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"old\n")
    path.chmod(0o600)

    def writer(stream):
        stream.write(b"new\n")

    atomic_write_path(path, writer)

    assert path.read_bytes() == b"new\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_copy_path_preserves_existing_metadata(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")
    destination = tmp_path / "destination"
    destination.write_text("old\n")
    destination.chmod(0o600)

    copy_path(source, destination)

    assert destination.read_text() == "new\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert source.exists()


def test_copy_path_defaults_new_file_mode_to_source_mode(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")
    source.chmod(0o755)
    destination = tmp_path / "destination"

    copy_path(source, destination)

    assert destination.read_text() == "new\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755


def test_copy_path_rejects_invalid_sources_and_destinations(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")

    with pytest.raises(ValueError, match="regular file"):
        copy_path(tmp_path, tmp_path / "destination")
    with pytest.raises(IsADirectoryError):
        copy_path(source, tmp_path)


def test_digest_path_streams_file_content(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"data\n")

    assert digest_path(path, "sha256") == hashlib.sha256(b"data\n").hexdigest()
    with pytest.raises(ValueError, match="unsupported checksum algorithm"):
        digest_path(path, "not-a-real-algorithm")


