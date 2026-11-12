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


def test_write_stat_read_remove(tmp_path):
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


def test_stage_path_writes_a_temporary_file(tmp_path):
    def writer(stream):
        stream.write(b"staged\n")

    temporary = Path(stage_path(tmp_path, writer, prefix=".test-"))

    assert temporary.parent == tmp_path
    assert temporary.name.startswith(".test-")
    assert temporary.name.endswith(".tmp")
    assert temporary.read_bytes() == b"staged\n"


def test_stage_path_removes_the_file_when_the_writer_fails(tmp_path):
    def writer(stream):
        stream.write(b"partial")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        stage_path(tmp_path, writer, prefix=".test-")

    assert list(tmp_path.iterdir()) == []


def test_stage_path_reports_a_missing_directory(tmp_path):
    with pytest.raises(TempFileError, match="could not create a temporary file"):
        stage_path(tmp_path / "missing", lambda stream: None)


def test_stage_path_propagates_write_errors(tmp_path):
    def writer(stream):
        raise OSError(errno.ENOSPC, "no space")

    with pytest.raises(OSError, match="no space") as failure:
        stage_path(tmp_path, writer, prefix=".test-")

    assert not isinstance(failure.value, TempFileError)
    assert list(tmp_path.iterdir()) == []


def test_writes_do_not_call_fsync(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("fsync must not be called")

    monkeypatch.setattr(os, "fsync", fail)

    write_path(tmp_path / "file", b"data\n")
    assert (tmp_path / "file").read_bytes() == b"data\n"


def test_read_regular_path_rejects_symlinks_and_directories(tmp_path):
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


def test_regular_file_errors_propagate_permission_failures(tmp_path, monkeypatch):
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


def test_read_regular_path_propagates_missing_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_regular_path(tmp_path / "missing")


def test_compare_paths_compares_content(tmp_path):
    left = tmp_path / "left"
    left.write_bytes(b"data\n")
    same = tmp_path / "same"
    same.write_bytes(b"data\n")
    different = tmp_path / "different"
    different.write_bytes(b"other\n")

    assert compare_paths(left, same) is True
    assert compare_paths(left, different) is False


def test_copy_path_copies_content_and_mode(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"data\n")
    source.chmod(0o640)
    destination = tmp_path / "destination"

    copy_path(source, destination)

    assert destination.read_bytes() == b"data\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640


def test_copy_path_replaces_symlinks_and_rejects_directories(tmp_path):
    source = tmp_path / "source"
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


def test_move_path_moves_a_file_and_removes_the_source(tmp_path):
    source = tmp_path / "source"
    source.write_text("data\n")
    source.chmod(0o600)
    destination = tmp_path / "destination"

    move_path(source, destination)

    assert not source.exists()
    assert destination.read_text() == "data\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_move_path_rejects_invalid_sources_and_destinations(tmp_path):
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


def test_replace_path_moves_content_and_preserves_metadata(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")
    destination = tmp_path / "destination"
    destination.write_text("old\n")
    destination.chmod(0o600)

    replace_path(source, destination)

    assert destination.read_text() == "new\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not source.exists()


def test_replace_path_uses_default_mode_for_new_destinations(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")

    replace_path(source, tmp_path / "destination", default_mode=0o640)

    assert stat.S_IMODE((tmp_path / "destination").stat().st_mode) == 0o640


def test_replace_path_replaces_symlinks_without_following(tmp_path):
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


def test_replace_path_rejects_invalid_sources_and_destinations(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")

    with pytest.raises(ValueError, match="regular file"):
        replace_path(tmp_path, tmp_path / "destination")
    with pytest.raises(IsADirectoryError):
        replace_path(source, tmp_path)


def test_digest_path_streams_file_content(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"data\n")

    assert digest_path(path, "sha256") == hashlib.sha256(b"data\n").hexdigest()
    with pytest.raises(ValueError, match="unsupported checksum algorithm"):
        digest_path(path, "not-a-real-algorithm")


def test_resolve_owner_caches_successful_lookups(monkeypatch):
    calls = []

    class Entry:
        pw_uid = 1234

    def fake_getpwnam(name):
        calls.append(name)
        return Entry()

    monkeypatch.setattr(pwd, "getpwnam", fake_getpwnam)
    resolve_owner.cache_clear()
    try:
        assert resolve_owner("alice") == 1234
        assert resolve_owner("alice") == 1234
        assert calls == ["alice"]
    finally:
        resolve_owner.cache_clear()


def test_resolve_owner_does_not_cache_failures(monkeypatch):
    calls = []

    class Entry:
        pw_uid = 1234

    def fake_getpwnam(name):
        calls.append(name)
        if len(calls) == 1:
            raise KeyError(name)
        return Entry()

    monkeypatch.setattr(pwd, "getpwnam", fake_getpwnam)
    resolve_owner.cache_clear()
    try:
        with pytest.raises(ValueError, match="unknown user: alice"):
            resolve_owner("alice")
        assert resolve_owner("alice") == 1234
        assert calls == ["alice", "alice"]
    finally:
        resolve_owner.cache_clear()


def test_resolve_group_caches_successful_lookups(monkeypatch):
    calls = []

    class Entry:
        gr_gid = 4321

    def fake_getgrnam(name):
        calls.append(name)
        return Entry()

    monkeypatch.setattr(grp, "getgrnam", fake_getgrnam)
    resolve_group.cache_clear()
    try:
        assert resolve_group("staff") == 4321
        assert resolve_group("staff") == 4321
        assert calls == ["staff"]
    finally:
        resolve_group.cache_clear()


def test_resolve_group_does_not_cache_failures(monkeypatch):
    calls = []

    class Entry:
        gr_gid = 4321

    def fake_getgrnam(name):
        calls.append(name)
        if len(calls) == 1:
            raise KeyError(name)
        return Entry()

    monkeypatch.setattr(grp, "getgrnam", fake_getgrnam)
    resolve_group.cache_clear()
    try:
        with pytest.raises(ValueError, match="unknown group: staff"):
            resolve_group("staff")
        assert resolve_group("staff") == 4321
        assert calls == ["staff", "staff"]
    finally:
        resolve_group.cache_clear()
