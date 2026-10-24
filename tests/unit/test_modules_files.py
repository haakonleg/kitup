from __future__ import annotations

import os
import stat
from io import StringIO

import pytest

from kitup.commands import CommandRunner
from kitup.errors import TaskFailed
from kitup.modules import files
from kitup.output import Output
from kitup.registry import Task

from .helpers import PassthroughSudo, make_context


def context(tmp_path, *, dry_run=False, diff=False):
    return make_context(
        tmp_path,
        dry_run=dry_run,
        diff=diff,
        output=Output(StringIO(), no_color=True),
    )


def privileged_context(tmp_path):
    sudo = PassthroughSudo()
    ctx = make_context(
        tmp_path,
        command=CommandRunner(sudo),
        sudo=sudo,
        output=Output(StringIO(), no_color=True),
    )
    ctx.begin_task(Task("test", "test", lambda ctx: None, needs_sudo=True))
    return ctx


def test_file_creates_file_and_is_idempotent(tmp_path):
    path = tmp_path / "example.conf"
    ctx = context(tmp_path)

    files.file(ctx, path, content="value = 1\n", mode=0o600)

    assert path.read_text() == "value = 1\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(ctx.changes) == 1

    files.file(ctx, path, content="value = 1\n", mode=0o600)
    assert len(ctx.changes) == 1


def test_file_updates_metadata_without_rewriting(tmp_path):
    path = tmp_path / "example.conf"
    path.write_text("value = 1\n")
    path.chmod(0o644)
    ctx = context(tmp_path)

    files.file(ctx, path, content="value = 1\n", mode=0o600)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(ctx.changes) == 1


def test_file_preserves_ownership_and_extended_attributes(tmp_path):
    path = tmp_path / "metadata"
    path.write_text("old\n")
    try:
        os.setxattr(path, "user.kitup-test", b"value")
    except OSError:
        pytest.skip("user xattrs are not supported")
    uid = path.stat().st_uid
    ctx = context(tmp_path)

    files.file(ctx, path, content="new\n")

    assert path.stat().st_uid == uid
    assert os.getxattr(path, "user.kitup-test") == b"value"


def test_file_dry_run_reports_without_writing(tmp_path):
    path = tmp_path / "example.conf"
    ctx = context(tmp_path, dry_run=True)

    files.file(ctx, path, content="value = 1\n")

    assert not path.exists()
    assert len(ctx.changes) == 1


def test_file_prints_diff(tmp_path):
    path = tmp_path / "example.conf"
    path.write_text("old\n")
    ctx = context(tmp_path, diff=True)

    files.file(ctx, path, content="new\n")

    output = ctx.output.stream.getvalue()
    assert "---" in output
    assert "-old" in output
    assert "+new" in output


def test_file_replaces_symlink_without_following_it(tmp_path):
    target = tmp_path / "target"
    target.write_text("target content\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    ctx = context(tmp_path)

    files.file(ctx, link, content="managed content\n")

    assert not link.is_symlink()
    assert link.read_text() == "managed content\n"
    assert target.read_text() == "target content\n"


def test_file_requires_parent_directory(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="parent directory"):
        files.file(ctx, tmp_path / "missing" / "file", content="content")


def test_file_dry_run_still_validates_parent_directory(tmp_path):
    ctx = context(tmp_path, dry_run=True)

    with pytest.raises(TaskFailed, match="parent directory"):
        files.file(ctx, tmp_path / "missing" / "file", content="content")

    assert ctx.changes == []


def test_file_refuses_directory(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="directory"):
        files.file(ctx, tmp_path, content="content")


def test_file_creates_empty_file_without_content(tmp_path):
    path = tmp_path / "empty"
    ctx = context(tmp_path)

    files.file(ctx, path)

    assert path.read_text() == ""
    assert len(ctx.changes) == 1


def test_file_allows_empty_content(tmp_path):
    path = tmp_path / "empty-content"
    ctx = context(tmp_path)

    files.file(ctx, path, content="")

    assert path.read_text() == ""
    assert len(ctx.changes) == 1


def test_line_allows_empty_line(tmp_path):
    path = tmp_path / "lines"
    path.write_text("one\n")
    ctx = context(tmp_path)

    files.line(ctx, path, "")

    assert path.read_text() == "one\n\n"


def test_file_without_content_preserves_existing_content(tmp_path):
    path = tmp_path / "existing"
    path.write_text("keep me\n")
    ctx = context(tmp_path)

    files.file(ctx, path)

    assert path.read_text() == "keep me\n"
    assert ctx.changes == []


def test_file_preserves_existing_mode_by_default(tmp_path):
    path = tmp_path / "secret"
    path.write_text("secret\n")
    path.chmod(0o600)
    ctx = context(tmp_path)

    files.file(ctx, path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ctx.changes == []


def test_file_new_file_uses_default_mode(tmp_path):
    path = tmp_path / "new"
    ctx = context(tmp_path)

    files.file(ctx, path, content="x\n")

    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_file_without_content_refuses_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("data\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="symlink"):
        files.file(ctx, link)

    assert link.is_symlink()
    assert target.read_text() == "data\n"


def test_privileged_file_operations_use_helper(tmp_path):
    path = tmp_path / "root-owned.conf"
    directory = tmp_path / "root-owned-dir"
    ctx = privileged_context(tmp_path)

    files.file(ctx, path, content="one\n", mode=0o600)
    files.line(ctx, path, "two")
    files.directory(ctx, directory, mode=0o700)
    files.directory(ctx, directory, state="absent")
    files.file(ctx, path, state="absent")

    assert not path.exists()
    assert not directory.exists()


def test_file_absent_removes_file(tmp_path):
    path = tmp_path / "obsolete"
    path.write_text("old\n")
    ctx = context(tmp_path)

    files.file(ctx, path, state="absent")

    assert not path.exists()
    assert len(ctx.changes) == 1


def test_file_absent_removes_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("keep\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    ctx = context(tmp_path)

    files.file(ctx, link, state="absent")

    assert not link.is_symlink()
    assert target.read_text() == "keep\n"


def test_file_absent_removes_broken_symlink(tmp_path):
    link = tmp_path / "broken"
    link.symlink_to(tmp_path / "missing")
    ctx = context(tmp_path)

    files.file(ctx, link, state="absent")

    assert not link.is_symlink()


def test_file_absent_is_a_noop_when_missing(tmp_path):
    path = tmp_path / "missing"
    ctx = context(tmp_path)

    files.file(ctx, path, state="absent")

    assert ctx.changes == []


def test_file_invalid_state_is_an_error(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid file state"):
        files.file(ctx, tmp_path / "file", state="unknown")


def test_file_rejects_empty_path(tmp_path):
    with pytest.raises(TaskFailed, match="file path must not be empty"):
        files.file(context(tmp_path), "")


def test_file_rejects_empty_owner(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="owner must not be empty"):
        files.file(ctx, tmp_path / "file", content="x", owner="")

    assert ctx.changes == []


def test_directory_rejects_empty_group(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="group must not be empty"):
        files.directory(ctx, tmp_path / "dir", group="  ")

    assert ctx.changes == []


def test_copy_rejects_empty_paths(tmp_path):
    with pytest.raises(TaskFailed, match="copy source must not be empty"):
        files.copy(context(tmp_path), "", tmp_path / "dest")

    with pytest.raises(TaskFailed, match="copy destination must not be empty"):
        files.copy(context(tmp_path), tmp_path / "source", "")


def test_line_rejects_empty_match(tmp_path):
    path = tmp_path / "lines"
    path.write_text("value\n")

    with pytest.raises(TaskFailed, match="line match must not be empty"):
        files.line(context(tmp_path), path, "value", match="")


def test_block_rejects_empty_marker(tmp_path):
    path = tmp_path / "block"
    path.write_text("")

    with pytest.raises(TaskFailed, match="block marker must not be empty"):
        files.block(context(tmp_path), path, "", "content")


def test_directory_rejects_empty_path(tmp_path):
    with pytest.raises(TaskFailed, match="directory path must not be empty"):
        files.directory(context(tmp_path), "")


def test_symlink_rejects_empty_target(tmp_path):
    with pytest.raises(TaskFailed, match="symlink target must not be empty"):
        files.symlink(context(tmp_path), "", tmp_path / "link")


def test_line_add_replace_and_remove(tmp_path):
    path = tmp_path / "lines"
    path.write_text("one\ntwo\nthree\n")
    ctx = context(tmp_path)

    files.line(ctx, path, "four")
    assert path.read_text().endswith("four\n")

    files.line(ctx, path, "TWO", match=r"^two$")
    assert "TWO\n" in path.read_text()

    files.line(ctx, path, "four", state="absent")
    assert "four" not in path.read_text()

    count = len(ctx.changes)
    files.line(ctx, path, "TWO", match=r"^TWO$")
    assert len(ctx.changes) == count


def test_line_appends_after_missing_final_newline(tmp_path):
    path = tmp_path / "lines"
    path.write_text("one\ntwo\nthree")
    ctx = context(tmp_path)

    files.line(ctx, path, "four")

    assert path.read_text() == "one\ntwo\nthree\nfour\n"

    count = len(ctx.changes)
    files.line(ctx, path, "four")
    assert len(ctx.changes) == count


def test_line_rejects_non_utf8_file(tmp_path):
    path = tmp_path / "binary"
    path.write_bytes(b"\xff\xfe\n")

    with pytest.raises(TaskFailed, match="not a UTF-8 text file"):
        files.line(context(tmp_path), path, "value")


def test_line_rejects_broken_symlink(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "missing")

    with pytest.raises(TaskFailed, match="symlink"):
        files.line(context(tmp_path), link, "value")

    assert link.is_symlink()


def test_file_refuses_fifo(tmp_path):
    path = tmp_path / "fifo"
    os.mkfifo(path)

    with pytest.raises(TaskFailed, match="not a regular file"):
        files.file(context(tmp_path), path)


def test_block_add_update_and_remove(tmp_path):
    path = tmp_path / "block"
    path.write_text("before\n")
    ctx = context(tmp_path)

    files.block(ctx, path, "managed", "one\n")
    assert "# BEGIN managed\none\n# END managed\n" in path.read_text()

    files.block(ctx, path, "managed", "two\n")
    assert "one" not in path.read_text()
    assert "two\n" in path.read_text()

    files.block(ctx, path, "managed", "", state="absent")
    assert "managed" not in path.read_text()


def test_block_missing_end_marker_is_an_error(tmp_path):
    path = tmp_path / "block"
    path.write_text("# BEGIN managed\n")

    with pytest.raises(TaskFailed, match="malformed"):
        files.block(context(tmp_path), path, "managed", "content")


def test_directory_creates_and_is_idempotent(tmp_path):
    path = tmp_path / "newdir"
    ctx = context(tmp_path)

    files.directory(ctx, path, mode=0o700)
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert len(ctx.changes) == 1

    files.directory(ctx, path, mode=0o700)
    assert len(ctx.changes) == 1


def test_directory_refuses_file(tmp_path):
    path = tmp_path / "file"
    path.write_text("x")

    with pytest.raises(TaskFailed, match="not a directory"):
        files.directory(context(tmp_path), path)


def test_directory_preserves_existing_mode_by_default(tmp_path):
    path = tmp_path / "private"
    path.mkdir()
    path.chmod(0o700)
    ctx = context(tmp_path)

    files.directory(ctx, path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert ctx.changes == []


def test_directory_new_directory_uses_default_mode(tmp_path):
    path = tmp_path / "newdir"
    ctx = context(tmp_path)

    files.directory(ctx, path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_directory_absent_removes_empty_directory(tmp_path):
    path = tmp_path / "olddir"
    path.mkdir()
    ctx = context(tmp_path)

    files.directory(ctx, path, state="absent")

    assert not path.exists()
    assert len(ctx.changes) == 1


def test_directory_absent_is_a_noop_when_missing(tmp_path):
    ctx = context(tmp_path)

    files.directory(ctx, tmp_path / "missing", state="absent")

    assert ctx.changes == []


def test_directory_absent_refuses_non_empty_directory(tmp_path):
    path = tmp_path / "full"
    path.mkdir()
    (path / "file").write_text("keep\n")

    with pytest.raises(TaskFailed, match="not empty"):
        files.directory(context(tmp_path), path, state="absent")

    assert (path / "file").exists()


def test_directory_absent_force_removes_non_empty_directory(tmp_path):
    path = tmp_path / "full"
    (path / "nested").mkdir(parents=True)
    (path / "nested" / "file").write_text("remove\n")
    ctx = context(tmp_path)

    files.directory(ctx, path, state="absent", force=True)

    assert not path.exists()
    assert len(ctx.changes) == 1


def test_directory_absent_refuses_file(tmp_path):
    path = tmp_path / "file"
    path.write_text("x")

    with pytest.raises(TaskFailed, match="not a directory"):
        files.directory(context(tmp_path), path, state="absent")


def test_directory_invalid_state_is_an_error(tmp_path):
    with pytest.raises(TaskFailed, match="invalid directory state"):
        files.directory(context(tmp_path), tmp_path / "dir", state="unknown")


def test_symlink_replaces_wrong_target(tmp_path):
    link = tmp_path / "link"
    link.symlink_to("old")
    ctx = context(tmp_path)

    files.symlink(ctx, "new", link)
    assert link.readlink().as_posix() == "new"

    count = len(ctx.changes)
    files.symlink(ctx, "new", link)
    assert len(ctx.changes) == count


def test_symlink_replaces_regular_file(tmp_path):
    link = tmp_path / "link"
    link.write_text("old\n")
    ctx = context(tmp_path)

    files.symlink(ctx, "new", link)

    assert link.is_symlink()
    assert link.readlink().as_posix() == "new"


def test_symlink_refuses_directory(tmp_path):
    link = tmp_path / "directory"
    link.mkdir()

    with pytest.raises(TaskFailed, match="directory"):
        files.symlink(context(tmp_path), "new", link)


def test_file_absent_refuses_directory(tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()

    with pytest.raises(TaskFailed, match="directory"):
        files.file(context(tmp_path), directory, state="absent")

    assert directory.exists()


def test_copy_preserves_mode_and_is_idempotent(tmp_path):
    source = tmp_path / "source.sh"
    source.write_text("#!/bin/sh\n")
    source.chmod(0o755)
    dest = tmp_path / "dest.sh"
    ctx = context(tmp_path)

    files.copy(ctx, source, dest)

    assert dest.read_text() == source.read_text()
    assert stat.S_IMODE(dest.stat().st_mode) == 0o755

    count = len(ctx.changes)
    files.copy(ctx, source, dest)
    assert len(ctx.changes) == count


def test_copy_replaces_destination_symlink(tmp_path):
    source = tmp_path / "source"
    source.write_text("new\n")
    target = tmp_path / "target"
    target.write_text("old\n")
    dest = tmp_path / "dest"
    dest.symlink_to(target)
    ctx = context(tmp_path)

    files.copy(ctx, source, dest)

    assert not dest.is_symlink()
    assert dest.read_text() == "new\n"
    assert target.read_text() == "old\n"

