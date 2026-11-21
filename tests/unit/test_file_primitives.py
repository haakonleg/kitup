import errno
import grp
import hashlib
import os
import pwd
import stat
from pathlib import Path

import pytest

from kitup._utils.file_primitives import (
    TempFileError,
    apply_metadata,
    atomic_write_path,
    compare_paths,
    copy_path,
    digest_path,
    hasher_for,
    mkdir_path,
    move_path,
    read_regular_path,
    remove_path,
    replace_path,
    resolve_group,
    resolve_owner,
    stage_path,
    stat_path,
    symlink_path,
    write_path,
    write_stream_path,
)


def test_write_stat_read_mkdir_metadata_symlink_and_remove(tmp_path):
    path = tmp_path / "file"

    write_path(path, b"data\n", mode=0o600)
    info = stat_path(path)

    assert info is not None
    assert info["is_file"]
    assert not info["is_dir"]
    assert not info["is_symlink"]
    assert info["mode"] == 0o600
    assert info["size"] == 5
    assert info["mtime_ns"] > 0
    assert read_regular_path(path) == b"data\n"

    remove_path(path)
    assert stat_path(path) is None

    directory = tmp_path / "dir"
    mkdir_path(directory, mode=0o700)
    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    apply_metadata(directory, 0o750, None, None)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o750
    remove_path(directory)
    assert not directory.exists()

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


def test_stage_path_writes_cleans_up_and_reports_errors(tmp_path):
    def writer(stream):
        stream.write(b"staged\n")

    temporary = Path(stage_path(tmp_path, writer, prefix=".test-"))

    assert temporary.parent == tmp_path
    assert temporary.name.startswith(".test-")
    assert temporary.name.endswith(".tmp")
    assert temporary.read_bytes() == b"staged\n"
    temporary.unlink()

    def failing_writer(stream):
        stream.write(b"partial")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        stage_path(tmp_path, failing_writer, prefix=".test-")
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(TempFileError, match="could not create a temporary file"):
        stage_path(tmp_path / "missing", lambda stream: None)

    def disk_full(stream):
        raise OSError(errno.ENOSPC, "no space")

    with pytest.raises(OSError, match="no space") as failure:
        stage_path(tmp_path, disk_full, prefix=".test-")

    assert not isinstance(failure.value, TempFileError)
    assert list(tmp_path.iterdir()) == []


def test_read_regular_path_rejects_symlinks_directories_and_permission_errors(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_bytes(b"data\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()

    assert read_regular_path(target) == b"data\n"
    with pytest.raises(ValueError, match="regular file"):
        read_regular_path(link)
    with pytest.raises(ValueError, match="regular file"):
        read_regular_path(directory)

    source = tmp_path / "source"
    source.write_bytes(b"data\n")
    destination = tmp_path / "destination"
    real_open = os.open

    def deny(path, flags, *args, **kwargs):
        if os.fspath(path) == str(source):
            raise PermissionError(13, "Permission denied", str(source))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny)

    with pytest.raises(PermissionError):
        read_regular_path(source)
    with pytest.raises(PermissionError):
        copy_path(source, destination)


def test_compare_paths_compares_content(tmp_path):
    left = tmp_path / "left"
    left.write_bytes(b"data\n")
    same = tmp_path / "same"
    same.write_bytes(b"data\n")
    different = tmp_path / "different"
    different.write_bytes(b"other\n")

    assert compare_paths(left, same) is True
    assert compare_paths(left, different) is False


def test_copy_path_copies_content_mode_replaces_symlinks_and_rejects_directories(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"data\n")
    source.chmod(0o640)
    destination = tmp_path / "destination"

    copy_path(source, destination)

    assert destination.read_bytes() == b"data\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640

    source.write_bytes(b"new\n")
    target = tmp_path / "target"
    target.write_bytes(b"old\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source)

    copy_path(source, link)

    assert not link.is_symlink()
    assert link.read_bytes() == b"new\n"
    assert target.read_bytes() == b"old\n"
    with pytest.raises(IsADirectoryError):
        copy_path(source, directory)
    with pytest.raises(ValueError, match="regular file"):
        copy_path(source_link, tmp_path / "destination")


def test_write_stream_path_copies_a_stream(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"data\n")
    destination = tmp_path / "destination"

    with source.open("rb") as stream:
        write_stream_path(destination, stream)

    assert destination.read_bytes() == b"data\n"


def test_move_path_moves_content_and_rejects_invalid_arguments(tmp_path):
    source = tmp_path / "source"
    source.write_text("data\n")
    source.chmod(0o600)
    destination = tmp_path / "destination"

    move_path(source, destination)

    assert not source.exists()
    assert destination.read_text() == "data\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600

    source = tmp_path / "source"
    source.write_text("data\n")
    directory = tmp_path / "directory"
    directory.mkdir()
    link = tmp_path / "link"
    link.symlink_to(source)

    with pytest.raises(ValueError, match="regular file"):
        move_path(tmp_path, tmp_path / "destination")
    with pytest.raises(ValueError, match="regular file"):
        move_path(link, tmp_path / "destination")
    with pytest.raises(IsADirectoryError):
        move_path(source, directory)
    with pytest.raises(ValueError, match="symlink"):
        move_path(source, link)


def test_replace_path_preserves_metadata_replaces_symlinks_and_validates(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")
    destination = tmp_path / "destination"
    destination.write_text("old\n")
    destination.chmod(0o600)

    replace_path(source, destination)

    assert destination.read_text() == "new\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not source.exists()

    source = tmp_path / "source"
    source.write_text("new\n")
    replace_path(source, tmp_path / "new-destination", default_mode=0o640)
    assert stat.S_IMODE((tmp_path / "new-destination").stat().st_mode) == 0o640

    source = tmp_path / "source"
    source.write_text("new\n")
    target = tmp_path / "target"
    target.write_text("keep\n")
    link = tmp_path / "link"
    link.symlink_to(target)

    replace_path(source, link)

    assert not link.is_symlink()
    assert link.read_text() == "new\n"
    assert target.read_text() == "keep\n"

    source = tmp_path / "source"
    source.write_text("new\n")
    with pytest.raises(ValueError, match="regular file"):
        replace_path(tmp_path, tmp_path / "destination")
    with pytest.raises(IsADirectoryError):
        replace_path(source, tmp_path)


def test_hasher_and_digest_path_support_and_reject_algorithms(tmp_path):
    assert hasher_for("sha256").hexdigest() == hashlib.sha256().hexdigest()
    with pytest.raises(ValueError, match="unsupported checksum algorithm"):
        hasher_for("not-a-real-algorithm")
    # Variable-length digests have no fixed size, so they cannot be verified.
    with pytest.raises(ValueError, match="unsupported checksum algorithm"):
        hasher_for("shake_128")

    path = tmp_path / "file"
    path.write_bytes(b"data\n")

    assert digest_path(path, "sha256") == hashlib.sha256(b"data\n").hexdigest()
    with pytest.raises(ValueError, match="unsupported checksum algorithm"):
        digest_path(path, "not-a-real-algorithm")


def test_resolve_owner_and_group_do_not_cache_failures(monkeypatch):
    user_calls = []

    class UserEntry:
        pw_uid = 1234

    def fake_getpwnam(name):
        user_calls.append(name)
        if len(user_calls) == 1:
            raise KeyError(name)
        return UserEntry()

    monkeypatch.setattr(pwd, "getpwnam", fake_getpwnam)
    resolve_owner.cache_clear()
    try:
        with pytest.raises(ValueError, match="unknown user: alice"):
            resolve_owner("alice")
        assert resolve_owner("alice") == 1234
        assert user_calls == ["alice", "alice"]
    finally:
        resolve_owner.cache_clear()

    group_calls = []

    class GroupEntry:
        gr_gid = 4321

    def fake_getgrnam(name):
        group_calls.append(name)
        if len(group_calls) == 1:
            raise KeyError(name)
        return GroupEntry()

    monkeypatch.setattr(grp, "getgrnam", fake_getgrnam)
    resolve_group.cache_clear()
    try:
        with pytest.raises(ValueError, match="unknown group: staff"):
            resolve_group("staff")
        assert resolve_group("staff") == 4321
        assert group_calls == ["staff", "staff"]
    finally:
        resolve_group.cache_clear()
