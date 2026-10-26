from __future__ import annotations

import os
import stat

from kitup._utils.file_helper import (
    apply_metadata,
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


