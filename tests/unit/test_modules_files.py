import os
import re
import stat

import pytest

from kitup._utils.file_helper import stat_path
from kitup.errors import TaskFailed
from kitup.modules import files
from kitup.registry import Bundle

from .helpers import FakeSudo, call_data, make_context, plain_output, privileged_context


def context(tmp_path, *, dry_run=False, diff=False):
    return make_context(
        tmp_path,
        dry_run=dry_run,
        diff=diff,
        output=plain_output(),
    )


def test_file_creates_file_and_is_idempotent(tmp_path):
    path = tmp_path / "example.conf"
    ctx = context(tmp_path)

    files.file(ctx, path, content="value = 1\n", mode=0o600)

    assert path.read_text() == "value = 1\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(ctx.changes) == 1
    assert ctx.changes[0].action == "files.file"

    files.file(ctx, path, content="value = 1\n", mode=0o600)
    assert len(ctx.changes) == 1
    assert len(ctx.calls) == 2
    assert ctx.calls[1].changed is False


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


@pytest.mark.parametrize(
    ("owner", "group", "message"),
    [
        ("kitup-missing-user", None, "unknown user"),
        (None, "kitup-missing-group", "unknown group"),
    ],
)
def test_dry_run_validates_metadata_identities(tmp_path, owner, group, message):
    ctx = context(tmp_path, dry_run=True)
    source = tmp_path / "source"
    source.write_text("data\n")
    existing_file = tmp_path / "existing"
    existing_file.write_text("data\n")
    existing_file.chmod(0o644)
    existing_dir = tmp_path / "existing-dir"
    existing_dir.mkdir()

    calls = [
        lambda: files.file(
            ctx,
            tmp_path / "new-file",
            content="data\n",
            owner=owner,
            group=group,
        ),
        lambda: files.file(
            ctx,
            existing_file,
            content="data\n",
            mode=0o600,
            owner=owner,
            group=group,
        ),
        lambda: files.copy(ctx, source, tmp_path / "new-copy", owner=owner, group=group),
        lambda: files.directory(ctx, tmp_path / "new-dir", owner=owner, group=group),
        lambda: files.directory(
            ctx,
            existing_dir,
            mode=0o700,
            owner=owner,
            group=group,
        ),
    ]

    for call in calls:
        with pytest.raises(TaskFailed, match=message):
            call()

    assert ctx.changes == []
    assert not (tmp_path / "new-file").exists()
    assert not (tmp_path / "new-copy").exists()
    assert not (tmp_path / "new-dir").exists()


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


def test_file_requires_parent_directory_also_in_dry_run(tmp_path):
    ctx = context(tmp_path, dry_run=True)

    with pytest.raises(TaskFailed, match="parent directory"):
        files.file(ctx, tmp_path / "missing" / "file", content="content")
    assert not (tmp_path / "missing").exists()


def test_copy_requires_parent_directory_also_in_dry_run(tmp_path):
    source = tmp_path / "source"
    source.write_text("data\n")
    ctx = context(tmp_path, dry_run=True)

    with pytest.raises(TaskFailed, match="parent directory"):
        files.copy(ctx, source, tmp_path / "missing" / "dest")
    with pytest.raises(TaskFailed, match="parent directory"):
        files.copy(ctx, source, str(tmp_path / "missing-dir") + "/")
    assert not (tmp_path / "missing").exists()
    assert not (tmp_path / "missing-dir").exists()


def test_file_refuses_directory_in_both_states(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="directory"):
        files.file(ctx, tmp_path, content="content")
    with pytest.raises(TaskFailed, match="directory"):
        files.file(ctx, tmp_path, state="absent")


def test_file_without_content_preserves_existing_content_and_mode(tmp_path):
    path = tmp_path / "existing"
    path.write_text("keep me\n")
    path.chmod(0o600)
    ctx = context(tmp_path)

    files.file(ctx, path)

    assert path.read_text() == "keep me\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ctx.changes == []
    assert len(ctx.calls) == 1
    assert ctx.calls[0].changed is False
    assert ctx.calls[0].summary == f"ensure file {path}"


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


def test_privileged_file_operations_use_worker(tmp_path):
    path = tmp_path / "root-owned.conf"
    directory = tmp_path / "root-owned-dir"
    ctx = privileged_context(tmp_path)

    try:
        files.file(ctx, path, content="one\n", mode=0o600, sudo=True)
        files.line(ctx, path, "two", sudo=True)
        files.directory(ctx, directory, mode=0o700, sudo=True)
        files.directory(ctx, directory, state="absent", sudo=True)
        files.file(ctx, path, state="absent", sudo=True)

        assert ctx.sudo.refresh_count > 0
        assert not path.exists()
        assert not directory.exists()
    finally:
        ctx.close()


def test_privileged_symlink_copy_and_metadata_use_worker(tmp_path):
    ctx = privileged_context(tmp_path)
    source = tmp_path / "source"
    source.write_text("data\n")
    dest = tmp_path / "dest"
    link = tmp_path / "link"

    try:
        files.copy(ctx, source, dest, sudo=True)
        files.symlink(ctx, dest, link, sudo=True)
        files.file(ctx, dest, mode=0o600, sudo=True)

        assert dest.read_text() == "data\n"
        assert link.is_symlink()
        assert link.resolve() == dest
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    finally:
        ctx.close()


class ReadFailureWorker:
    """Simulate a privileged worker whose read action fails."""

    def request(self, request):
        if request.action == "read":
            raise TaskFailed("privileged worker read failed: simulated")
        if request.action == "stat":
            return stat_path(request.params["path"]) or {"exists": False}
        return {}


def test_privileged_read_failure_does_not_rewrite_file(tmp_path):
    path = tmp_path / "example"
    path.write_text("keep me\n")
    ctx = make_context(tmp_path, sudo=FakeSudo())
    ctx.privileged_worker = ReadFailureWorker()  # type: ignore[assignment]

    with pytest.raises(TaskFailed, match="read failed"):
        files.line(ctx, path, "new line", sudo=True)

    assert path.read_text() == "keep me\n"


def test_unknown_owner_is_a_task_failure(tmp_path):
    path = tmp_path / "example"

    with pytest.raises(TaskFailed, match="unknown user"):
        files.file(context(tmp_path), path, content="value\n", owner="kitup-missing-user")

    assert not path.exists()


def test_unprivileged_file_operations_do_not_request_credentials(tmp_path):
    path = tmp_path / "user-owned.conf"
    sudo = FakeSudo()
    ctx = make_context(tmp_path, sudo=sudo)

    files.file(ctx, path, content="value\n")

    assert path.read_text() == "value\n"
    assert sudo.refresh_count == 0


def test_file_absent_removes_file(tmp_path):
    path = tmp_path / "obsolete"
    path.write_text("old\n")
    ctx = context(tmp_path)

    files.file(ctx, path, state="absent")

    assert not path.exists()
    assert len(ctx.changes) == 1


def test_file_absent_removes_valid_and_broken_symlinks(tmp_path):
    target = tmp_path / "target"
    target.write_text("keep\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    broken = tmp_path / "broken"
    broken.symlink_to(tmp_path / "missing")
    ctx = context(tmp_path)

    files.file(ctx, link, state="absent")
    files.file(ctx, broken, state="absent")

    assert not link.is_symlink()
    assert not broken.is_symlink()
    assert target.read_text() == "keep\n"


def test_file_invalid_state_is_an_error(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid file state"):
        files.file(ctx, tmp_path / "file", state="unknown")


def test_invalid_arguments_are_rejected(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="file path must not be empty"):
        files.file(ctx, "")
    with pytest.raises(TaskFailed, match="owner must not be empty"):
        files.file(ctx, tmp_path / "file", content="x", owner="")
    with pytest.raises(TaskFailed, match="group must not be empty"):
        files.directory(ctx, tmp_path / "dir", group="  ")
    with pytest.raises(TaskFailed, match="copy source must not be empty"):
        files.copy(ctx, "", tmp_path / "dest")
    with pytest.raises(TaskFailed, match="copy destination must not be empty"):
        files.copy(ctx, tmp_path / "source", "")
    with pytest.raises(TaskFailed, match="directory path must not be empty"):
        files.directory(ctx, "")
    with pytest.raises(TaskFailed, match="symlink target must not be empty"):
        files.symlink(ctx, "", tmp_path / "link")

    for mode in (True, -1, 0o10000):
        with pytest.raises(TaskFailed, match="mode"):
            files.file(ctx, tmp_path / "file", content="x", mode=mode)
    for owner in (True, -1):
        with pytest.raises(TaskFailed, match="owner"):
            files.file(ctx, tmp_path / "file", content="x", owner=owner)
    for group in (True, -1):
        with pytest.raises(TaskFailed, match="group"):
            files.file(ctx, tmp_path / "file", content="x", group=group)

    assert ctx.changes == []
    assert not (tmp_path / "file").exists()


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
    assert ctx.calls[-1].summary == f"ensure line in {path}"


def test_line_appends_after_missing_final_newline(tmp_path):
    path = tmp_path / "lines"
    path.write_text("one\ntwo\nthree")
    ctx = context(tmp_path)

    files.line(ctx, path, "four")

    assert path.read_text() == "one\ntwo\nthree\nfour\n"

    count = len(ctx.changes)
    files.line(ctx, path, "four")
    assert len(ctx.changes) == count
    assert ctx.calls[-1].summary == f"ensure line in {path}"


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

    files.block(ctx, path, "managed", "two\n")
    assert ctx.calls[-1].summary == f"ensure managed block in {path}"

    files.block(ctx, path, "managed", "", state="absent")
    assert "managed" not in path.read_text()

    files.block(ctx, path, "managed", "", state="absent")
    assert ctx.calls[-1].summary == f"ensure managed block is absent from {path}"


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
    assert ctx.calls[-1].summary == f"ensure directory {path}"


def test_directory_refuses_file_in_both_states(tmp_path):
    path = tmp_path / "file"
    path.write_text("x")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="not a directory"):
        files.directory(ctx, path)
    with pytest.raises(TaskFailed, match="not a directory"):
        files.directory(ctx, path, state="absent")


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


def test_directory_invalid_state_is_an_error(tmp_path):
    with pytest.raises(TaskFailed, match="invalid directory state"):
        files.directory(context(tmp_path), tmp_path / "dir", state="unknown")


def test_symlink_replaces_wrong_target_file_and_is_idempotent(tmp_path):
    wrong = tmp_path / "wrong"
    wrong.symlink_to("old")
    file_link = tmp_path / "file-link"
    file_link.write_text("old\n")
    ctx = context(tmp_path)

    files.symlink(ctx, "new", wrong)
    files.symlink(ctx, "new", file_link)
    assert wrong.readlink().as_posix() == "new"
    assert file_link.is_symlink()
    assert file_link.readlink().as_posix() == "new"

    count = len(ctx.changes)
    files.symlink(ctx, "new", wrong)
    assert len(ctx.changes) == count


def test_symlink_refuses_directory(tmp_path):
    link = tmp_path / "directory"
    link.mkdir()

    with pytest.raises(TaskFailed, match="directory"):
        files.symlink(context(tmp_path), "new", link)


def test_copy_preserves_mode_and_is_idempotent(tmp_path):
    source = tmp_path / "source.sh"
    source.write_text("#!/bin/sh\n")
    source.chmod(0o755)
    dest = tmp_path / "dest.sh"
    ctx = context(tmp_path)

    files.copy(ctx, source, dest)

    assert dest.read_text() == source.read_text()
    assert stat.S_IMODE(dest.stat().st_mode) == 0o755
    assert call_data(ctx.calls[-1]) == {"path": str(dest), "src": str(source)}

    count = len(ctx.changes)
    files.copy(ctx, source, dest)
    assert len(ctx.changes) == count
    assert ctx.calls[-1].summary == f"ensure {dest} from {source}"


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


def test_copy_into_directory_with_trailing_slash(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    ctx = context(tmp_path)

    files.copy(ctx, source, f"{dest_dir}/")

    assert (dest_dir / source.name).read_text() == "value\n"


def test_copy_resolves_sources_relative_to_the_bundle(tmp_path):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "bare.conf").write_text("bare\n")
    (bundle_dir / "nested").mkdir()
    (bundle_dir / "nested" / "relative.conf").write_text("relative\n")

    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = context(tmp_path)
    ctx.begin_task(bundle.tasks[0])

    files.copy(ctx, "bare.conf", tmp_path / "bare.out")
    files.copy(ctx, "nested/relative.conf", tmp_path / "relative.out")
    files.copy(ctx, bundle_dir / "nested" / "relative.conf", tmp_path / "absolute.out")

    assert (tmp_path / "bare.out").read_text() == "bare\n"
    assert (tmp_path / "relative.out").read_text() == "relative\n"
    assert (tmp_path / "absolute.out").read_text() == "relative\n"


def test_move_moves_file_and_is_idempotent(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    source.chmod(0o600)
    dest = tmp_path / "dest.conf"
    ctx = context(tmp_path)

    files.move(ctx, source, dest)

    assert not source.exists()
    assert dest.read_text() == "value\n"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert len(ctx.changes) == 1
    assert ctx.changes[0].action == "files.move"
    assert call_data(ctx.changes[0]) == {"dest": str(dest), "src": str(source)}

    files.move(ctx, source, dest)

    assert len(ctx.changes) == 1
    assert ctx.calls[-1].summary == f"ensure {dest} is moved from {source}"


def test_move_replaces_destination_file(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("new\n")
    dest = tmp_path / "dest.conf"
    dest.write_text("old\n")
    ctx = context(tmp_path)

    files.move(ctx, source, dest)

    assert not source.exists()
    assert dest.read_text() == "new\n"
    assert len(ctx.changes) == 1


def test_move_into_directory_with_trailing_slash(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    ctx = context(tmp_path)

    files.move(ctx, source, f"{dest_dir}/")

    assert not source.exists()
    assert (dest_dir / source.name).read_text() == "value\n"


def test_move_sets_requested_mode(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    source.chmod(0o600)
    dest = tmp_path / "dest.conf"
    ctx = context(tmp_path)

    files.move(ctx, source, dest, mode=0o640)

    assert not source.exists()
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640


def test_move_reconciles_metadata_when_source_is_gone(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    dest = tmp_path / "dest.conf"
    files.move(context(tmp_path), source, dest)
    dest.chmod(0o600)

    ctx = context(tmp_path)
    files.move(ctx, source, dest, mode=0o640)

    assert stat.S_IMODE(dest.stat().st_mode) == 0o640
    assert len(ctx.changes) == 1
    assert ctx.changes[0].action == "files.move"
    assert call_data(ctx.changes[0]) == {"dest": str(dest), "src": str(source)}

    unchanged = context(tmp_path)
    files.move(unchanged, source, dest, mode=0o640)
    assert unchanged.changes == []


def test_move_dry_run_reports_without_moving(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    dest = tmp_path / "dest.conf"
    ctx = context(tmp_path, dry_run=True)

    files.move(ctx, source, dest)

    assert source.read_text() == "value\n"
    assert not dest.exists()
    assert len(ctx.changes) == 1


def test_move_missing_source_without_destination_fails(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="does not exist"):
        files.move(ctx, tmp_path / "missing.conf", tmp_path / "dest.conf")


def test_move_rejects_directories_and_symlinks(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    source_dir = tmp_path / "source-dir"
    source_dir.mkdir()
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source)
    dest_dir = tmp_path / "dest-dir"
    dest_dir.mkdir()
    dest_link = tmp_path / "dest-link"
    dest_link.symlink_to(tmp_path / "target")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="not a regular file"):
        files.move(ctx, source_dir, tmp_path / "dest.conf")
    with pytest.raises(TaskFailed, match="not a regular file"):
        files.move(ctx, source_link, tmp_path / "dest.conf")
    with pytest.raises(TaskFailed, match="destination is a directory"):
        files.move(ctx, source, dest_dir)
    with pytest.raises(TaskFailed, match="symlink"):
        files.move(ctx, source, dest_link)
    assert source.read_text() == "value\n"


def test_move_uses_privileged_worker(tmp_path):
    source = tmp_path / "source.conf"
    source.write_text("value\n")
    dest = tmp_path / "dest.conf"
    ctx = privileged_context(tmp_path)

    try:
        files.move(ctx, source, dest, sudo=True)
    finally:
        ctx.close()

    assert not source.exists()
    assert dest.read_text() == "value\n"


def test_ini_sets_option_and_is_idempotent(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[drinks]\nfav = tea\n")
    ctx = context(tmp_path)

    result = files.ini(ctx, path, section="drinks", option="fav", value="lemonade")

    assert path.read_text() == "[drinks]\nfav = lemonade\n"
    assert result.action == "files.ini"
    assert result.changed is True
    assert result.summary == f"set fav in [drinks] of {path}"
    assert call_data(result) == {
        "path": str(path),
        "section": "drinks",
        "option": "fav",
        "value": "lemonade",
        "ini_action": "replaced",
    }

    result = files.ini(ctx, path, section="drinks", option="fav", value="lemonade")
    assert result.changed is False
    assert result.summary == f"ensure fav is lemonade in [drinks] of {path}"
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(ctx.changes) == 1


def test_ini_result_fields_report_ini_action_and_arguments(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path)

    result = files.ini(ctx, path, section="s", option="b", value="2")
    assert call_data(result) == {
        "path": str(path),
        "section": "s",
        "option": "b",
        "value": "2",
        "ini_action": "added",
    }

    result = files.ini(ctx, path, section="s", option="a", value="9")
    assert call_data(result)["ini_action"] == "replaced"

    result = files.ini(ctx, path, section="s", option="a", state="absent")
    assert call_data(result) == {
        "path": str(path),
        "section": "s",
        "option": "a",
        "value": None,
        "ini_action": "removed",
    }

    result = files.ini(ctx, path, section="s", state="absent")
    assert call_data(result)["ini_action"] == "removed"

    result = files.ini(ctx, path, section="s", state="absent")
    assert call_data(result) == {
        "path": str(path),
        "section": "s",
        "option": None,
        "value": None,
        "ini_action": "unchanged",
    }

    result = files.ini(ctx, path, option="pre", value="1")
    assert call_data(result) == {
        "path": str(path),
        "section": None,
        "option": "pre",
        "value": "1",
        "ini_action": "added",
    }


def test_ini_edit_scenarios(tmp_path):
    # Spacing variants that already match are no-ops.
    path = tmp_path / "spacing.conf"
    path.write_text("[drinks]\nfav=lemonade\n")
    ctx = context(tmp_path)
    result = files.ini(ctx, path, section="drinks", option="fav", value="lemonade")
    assert path.read_text() == "[drinks]\nfav=lemonade\n"
    assert result.changed is False

    # Insertion at the end of a section body preserves the rest of the file.
    path = tmp_path / "insert.conf"
    path.write_text("# top\n[s]\na = 1\n\n# trailing\n[t]\nb = 2\n")
    files.ini(ctx, path, section="s", option="c", value="3")
    assert path.read_text() == "# top\n[s]\na = 1\nc = 3\n\n# trailing\n[t]\nb = 2\n"

    # Missing sections are appended; missing files are created with the default mode.
    path = tmp_path / "append.conf"
    path.write_text("[s]\na = 1\n")
    files.ini(ctx, path, section="t", option="b", value="2")
    assert path.read_text() == "[s]\na = 1\n[t]\nb = 2\n"

    fresh = tmp_path / "new.conf"
    files.ini(ctx, fresh, section="s", option="a", value="1")
    assert fresh.read_text() == "[s]\na = 1\n"
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o644

    # The preamble is edited when section is None.
    path = tmp_path / "preamble.conf"
    path.write_text("a = 1\n[s]\nb = 2\n")
    files.ini(ctx, path, option="c", value="3")
    assert path.read_text() == "a = 1\nc = 3\n[s]\nb = 2\n"
    result = files.ini(ctx, path, option="c", value="3")
    assert result.changed is False
    assert result.summary == f"ensure c is 3 in {path}"

    # Duplicate option lines are collapsed.
    path = tmp_path / "duplicates.conf"
    path.write_text("[s]\na = 1\nb = 2\na = 9\n")
    files.ini(ctx, path, section="s", option="a", value="3")
    assert path.read_text() == "[s]\na = 3\nb = 2\n"

    # Only the first of duplicate sections is edited.
    path = tmp_path / "sections.conf"
    path.write_text("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\n")
    files.ini(ctx, path, section="Peer", option="Endpoint", value="1.2.3.4:51820")
    assert path.read_text() == "[Peer]\nPublicKey = aaa\nEndpoint = 1.2.3.4:51820\n[Peer]\nPublicKey = bbb\n"
    files.ini(ctx, path, section="Peer", state="absent")
    assert path.read_text() == "[Peer]\nPublicKey = bbb\n"

    # Values may contain equals signs.
    path = tmp_path / "equals.conf"
    files.ini(ctx, path, section="s", option="filter", value="a=b=c")
    assert path.read_text() == "[s]\nfilter = a=b=c\n"
    result = files.ini(ctx, path, section="s", option="filter", value="a=b=c")
    assert result.changed is False

    # A no-op does not repair a missing final newline.
    path = tmp_path / "newline.conf"
    path.write_text("[s]\na = 1")
    result = files.ini(ctx, path, section="s", option="a", value="1")
    assert path.read_text() == "[s]\na = 1"
    assert result.changed is False


def test_ini_create_false_rejects_a_missing_file(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="does not exist"):
        files.ini(ctx, tmp_path / "missing.conf", section="s", option="a", value="1", create=False)

    assert not (tmp_path / "missing.conf").exists()


def test_ini_absent_scenarios(tmp_path):
    path = tmp_path / "absent.conf"
    path.write_text("a = 0\n[s]\na = 1\nb = 2\n[t]\na = 3\n")
    ctx = context(tmp_path)
    result = files.ini(ctx, path, section="s", option="a", state="absent")
    assert path.read_text() == "a = 0\n[s]\nb = 2\n[t]\na = 3\n"
    assert result.changed is True
    assert result.summary == f"remove a from [s] of {path}"

    result = files.ini(ctx, path, section="s", option="a", state="absent")
    assert result.changed is False
    assert result.summary == f"ensure a is absent from [s] of {path}"

    path = tmp_path / "section.conf"
    path.write_text("pre\n[s]\na = 1\n\n[t]\nb = 2\n")
    ctx = context(tmp_path)
    result = files.ini(ctx, path, section="s", state="absent")
    assert path.read_text() == "pre\n[t]\nb = 2\n"
    assert result.summary == f"remove section [s] from {path}"

    result = files.ini(ctx, path, section="s", state="absent")
    assert result.changed is False
    assert result.summary == f"ensure section [s] is absent from {path}"

    # No matching section is a no-op.
    path = tmp_path / "nomatch.conf"
    path.write_text("[Peer]\nPublicKey = aaa\n")
    ctx = context(tmp_path)
    result = files.ini(ctx, path, section="Peer", match={"PublicKey": "zzz"}, option="Endpoint", state="absent")
    assert result.changed is False
    assert path.read_text() == "[Peer]\nPublicKey = aaa\n"
    result = files.ini(ctx, path, section="Peer", match={"PublicKey": "zzz"}, state="absent")
    assert result.changed is False
    assert path.read_text() == "[Peer]\nPublicKey = aaa\n"


def test_ini_match_scenarios(tmp_path):
    path = tmp_path / "wg0.conf"
    path.write_text(
        "[Peer]\nPublicKey = aaa\nAllowedIPs = 10.128.0.11/32\n[Peer]\nPublicKey = bbb\nAllowedIPs = 10.128.0.12/32\n"
    )
    ctx = context(tmp_path)
    result = files.ini(
        ctx,
        path,
        section="Peer",
        match={"AllowedIPs": r"10\.128\.0\.12/32"},
        option="Endpoint",
        value="10.0.0.2:51820",
    )
    assert path.read_text() == (
        "[Peer]\nPublicKey = aaa\nAllowedIPs = 10.128.0.11/32\n"
        "[Peer]\nPublicKey = bbb\nAllowedIPs = 10.128.0.12/32\nEndpoint = 10.0.0.2:51820\n"
    )
    assert call_data(result)["ini_action"] == "added"

    # second run is a no-op
    result = files.ini(
        ctx,
        path,
        section="Peer",
        match={r"Allowed.*": r"10\.128\.0\.12/32"},
        option="Endpoint",
        value="10.0.0.2:51820",
    )
    assert result.changed is False
    assert len(ctx.changes) == 1

    # regex key selects the right peer
    path = tmp_path / "regex.conf"
    path.write_text("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\n")
    files.ini(ctx, path, section="Peer", match={r"Public.*": "bbb"}, option="Endpoint", value="1.2.3.4:51820")
    assert path.read_text() == "[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\nEndpoint = 1.2.3.4:51820\n"

    # first matching section wins
    path = tmp_path / "first.conf"
    path.write_text("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = aaa\n")
    files.ini(ctx, path, section="Peer", match={"PublicKey": "aaa"}, option="Endpoint", value="1.2.3.4:51820")
    assert path.read_text() == "[Peer]\nPublicKey = aaa\nEndpoint = 1.2.3.4:51820\n[Peer]\nPublicKey = aaa\n"

    # creating via the matched option is idempotent
    path = tmp_path / "create.conf"
    path.write_text("[Interface]\nPrivateKey = xxxx\n")
    for _ in range(2):
        files.ini(ctx, path, section="Peer", match={"PublicKey": "bbbb"}, option="PublicKey", value="bbbb")
    assert path.read_text() == "[Interface]\nPrivateKey = xxxx\n[Peer]\nPublicKey = bbbb\n"


def test_ini_match_failure_scenarios(tmp_path):
    path = tmp_path / "wg0.conf"
    path.write_text("[Peer]\nPublicKey = aaa\n")
    ctx = context(tmp_path)

    # the appended section would hold only PublicKey = bbbb, which cannot satisfy
    # a match on AllowedIPs, so the call fails instead of duplicating every run
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        files.ini(ctx, path, section="Peer", match={"AllowedIPs": "10\\."}, option="PublicKey", value="bbbb")
    assert path.read_text() == "[Peer]\nPublicKey = aaa\n"
    assert ctx.changes == []

    path = tmp_path / "escape.conf"
    ctx = context(tmp_path)
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        files.ini(ctx, path, section="Peer", match={"PublicKey": "abc+def"}, option="PublicKey", value="abc+def")
    assert not path.exists()

    for _ in range(2):
        files.ini(
            ctx,
            path,
            section="Peer",
            match={"PublicKey": re.escape("abc+def")},
            option="PublicKey",
            value="abc+def",
        )
    assert path.read_text() == "[Peer]\nPublicKey = abc+def\n"
    assert len(ctx.changes) == 1

    path = tmp_path / "rotate.conf"
    path.write_text("[Peer]\nPublicKey = old\n")
    ctx = context(tmp_path)
    files.ini(ctx, path, section="Peer", match={"PublicKey": "old"}, option="PublicKey", value="new")
    assert path.read_text() == "[Peer]\nPublicKey = new\n"
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        files.ini(ctx, path, section="Peer", match={"PublicKey": "old"}, option="PublicKey", value="new")
    assert path.read_text() == "[Peer]\nPublicKey = new\n"
    assert len(ctx.changes) == 1


def test_ini_invalid_arguments_are_rejected(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid ini state"):
        files.ini(ctx, path, section="s", option="a", value="1", state="unknown")
    with pytest.raises(TaskFailed, match="ini path must not be empty"):
        files.ini(ctx, "", section="s", option="a", value="1")
    with pytest.raises(TaskFailed, match="ini section must not contain"):
        files.ini(ctx, path, section="[s]", option="a", value="1")
    with pytest.raises(TaskFailed, match="ini option must not contain"):
        files.ini(ctx, path, section="s", option="a=b", value="1")
    with pytest.raises(TaskFailed, match="ini value must not contain"):
        files.ini(ctx, path, section="s", option="a", value="1\n2")
    with pytest.raises(TaskFailed, match="requires an option"):
        files.ini(ctx, path, section="s", value="1")
    with pytest.raises(TaskFailed, match="requires a value"):
        files.ini(ctx, path, section="s", option="a")
    with pytest.raises(TaskFailed, match="requires a section or an option"):
        files.ini(ctx, path, state="absent")
    with pytest.raises(TaskFailed, match="ini match requires a section"):
        files.ini(ctx, path, option="a", value="1", match={"b": "2"})
    with pytest.raises(TaskFailed, match="ini match must not be empty"):
        files.ini(ctx, path, section="s", option="a", value="1", match={})
    with pytest.raises(TaskFailed, match="invalid ini match regex"):
        files.ini(ctx, path, section="s", option="a", value="1", match={"a": "["})
    with pytest.raises(TaskFailed, match="invalid ini match regex"):
        files.ini(ctx, path, section="s", option="a", value="1", match={"[": "1"})
    with pytest.raises(TaskFailed, match="must be a boolean"):
        files.ini(ctx, path, section="s", option="a", value="1", create="yes")

    assert ctx.changes == []
    assert path.read_text() == "[s]\na = 1\n"


def test_ini_rejects_directories_symlinks_and_non_utf8_files(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="directory"):
        files.ini(ctx, tmp_path, section="s", option="a", value="1")

    target = tmp_path / "target"
    target.write_text("[s]\na = 1\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(TaskFailed, match="symlink"):
        files.ini(ctx, link, section="s", option="a", value="2")

    binary = tmp_path / "binary"
    binary.write_bytes(b"[s]\n\xff\xfe\n")
    with pytest.raises(TaskFailed, match="UTF-8"):
        files.ini(ctx, binary, section="s", option="a", value="2")

    assert target.read_text() == "[s]\na = 1\n"


def test_ini_dry_run_reports_without_writing_and_repairs_final_newline(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[s]\na = 1")
    ctx = context(tmp_path, dry_run=True)

    result = files.ini(ctx, path, section="s", option="b", value="2")

    assert path.read_text() == "[s]\na = 1"
    assert result.changed is True
    assert len(ctx.changes) == 1

    ctx = context(tmp_path)
    files.ini(ctx, path, section="s", option="b", value="2")
    assert path.read_text() == "[s]\na = 1\nb = 2\n"


def test_ini_noop_does_not_repair_a_missing_final_newline(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[s]\na = 1")
    ctx = context(tmp_path)

    result = files.ini(ctx, path, section="s", option="a", value="1")

    assert path.read_text() == "[s]\na = 1"
    assert result.changed is False


def test_ini_preserves_existing_file_mode(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[s]\na = 1\n")
    path.chmod(0o600)
    ctx = context(tmp_path)

    files.ini(ctx, path, section="s", option="a", value="2")

    assert path.read_text() == "[s]\na = 2\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_ini_value_may_contain_equals_signs(tmp_path):
    path = tmp_path / "app.conf"
    ctx = context(tmp_path)

    files.ini(ctx, path, section="s", option="filter", value="a=b=c")

    assert path.read_text() == "[s]\nfilter = a=b=c\n"
    result = files.ini(ctx, path, section="s", option="filter", value="a=b=c")
    assert result.changed is False


def test_ini_uses_privileged_worker(tmp_path):
    path = tmp_path / "root-owned.conf"
    ctx = privileged_context(tmp_path)

    try:
        result = files.ini(ctx, path, section="s", option="a", value="1", sudo=True)
        assert result.changed is True
        assert path.read_text() == "[s]\na = 1\n"

        result = files.ini(ctx, path, section="s", option="a", value="1", sudo=True)
        assert result.changed is False
        assert ctx.sudo.refresh_count > 0
    finally:
        ctx.close()


def test_ini_validates_arguments_before_touching_the_file(tmp_path):
    ctx = context(tmp_path)

    # argument errors win over filesystem errors (the path is a directory)
    with pytest.raises(TaskFailed, match="requires an option"):
        files.ini(ctx, tmp_path, state="present", value="1")
    with pytest.raises(TaskFailed, match="requires a section or an option"):
        files.ini(ctx, tmp_path, state="absent")
    # argument errors win over the create=False missing-file check
    with pytest.raises(TaskFailed, match="requires a value"):
        files.ini(ctx, tmp_path / "missing.conf", option="a", create=False)

    assert ctx.changes == []


@pytest.mark.parametrize("char", ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\0"])
def test_ini_rejects_line_breaks_and_null_bytes_in_arguments(tmp_path, char):
    path = tmp_path / "app.conf"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="must not contain"):
        files.ini(ctx, path, section=f"s{char}t", option="a", value="1")
    with pytest.raises(TaskFailed, match="must not contain"):
        files.ini(ctx, path, section="s", option=f"a{char}b", value="1")
    with pytest.raises(TaskFailed, match="must not contain"):
        files.ini(ctx, path, section="s", option="a", value=f"1{char}2")

    assert path.read_text() == "[s]\na = 1\n"
    assert ctx.changes == []


def test_ini_rejects_non_utf8_encodable_arguments(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="UTF-8 encodable"):
        files.ini(ctx, path, section="s", option="a", value="\ud800")
    with pytest.raises(TaskFailed, match="UTF-8 encodable"):
        files.ini(ctx, path, section="\ud800", option="a", value="1")

    assert path.read_text() == "[s]\na = 1\n"
    assert ctx.changes == []


def test_line_rejects_non_utf8_encodable_text(tmp_path):
    path = tmp_path / "lines"
    path.write_text("one\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="UTF-8 encodable"):
        files.line(ctx, path, "bad\ud800")

    assert path.read_text() == "one\n"


def test_ini_rejects_comment_prefixed_option_names(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[s]\n#foo = 1\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="comment character"):
        files.ini(ctx, path, section="s", option="#foo", value="2")
    with pytest.raises(TaskFailed, match="comment character"):
        files.ini(ctx, path, section="s", option=";foo", state="absent")

    assert path.read_text() == "[s]\n#foo = 1\n"
    assert ctx.changes == []


def test_ini_absent_on_a_missing_file_is_a_noop_and_creates_nothing(tmp_path):
    missing = tmp_path / "missing.conf"
    ctx = context(tmp_path)

    result = files.ini(ctx, missing, section="s", option="a", state="absent")
    assert result.changed is False
    result = files.ini(ctx, missing, section="s", state="absent")
    assert result.changed is False

    assert not missing.exists()
    assert ctx.changes == []


def test_ini_strips_a_utf8_bom_on_write_but_not_on_a_noop(tmp_path):
    path = tmp_path / "bom.conf"
    path.write_bytes(b"\xef\xbb\xbf[s]\na = 1\nb = 2\n")
    ctx = context(tmp_path)

    # a no-op run only reads, so the BOM is kept
    result = files.ini(ctx, path, section="s", option="a", value="1")
    assert result.changed is False
    assert path.read_bytes() == b"\xef\xbb\xbf[s]\na = 1\nb = 2\n"

    # the first real write parses the bom-prefixed header normally and drops the BOM
    result = files.ini(ctx, path, section="s", option="a", value="9")
    assert result.changed is True
    assert path.read_bytes() == b"[s]\na = 9\nb = 2\n"

    # idempotent afterwards
    result = files.ini(ctx, path, section="s", option="a", value="9")
    assert result.changed is False
    assert path.read_bytes() == b"[s]\na = 9\nb = 2\n"

    # a missing section is appended after the existing one
    files.ini(ctx, path, section="t", option="c", value="3")
    assert path.read_bytes() == b"[s]\na = 9\nb = 2\n[t]\nc = 3\n"


def test_ini_absent_removes_a_bom_prefixed_section_and_strips_the_bom(tmp_path):
    path = tmp_path / "bom.conf"
    path.write_bytes(b"\xef\xbb\xbf[s]\na = 1\n[t]\nb = 2\n")
    ctx = context(tmp_path)

    result = files.ini(ctx, path, section="s", state="absent")

    assert result.changed is True
    assert path.read_bytes() == b"[t]\nb = 2\n"

    result = files.ini(ctx, path, section="s", state="absent")
    assert result.changed is False


def test_toml_sets_key_and_is_idempotent(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[server]\nhost = "old"\nport = 8080\n')
    ctx = context(tmp_path)

    result = files.toml(ctx, path, table="server", key="host", value="example.com")

    assert path.read_text() == '[server]\nhost = "example.com"\nport = 8080\n'
    assert result.action == "files.toml"
    assert result.changed is True
    assert result.summary == f"set host in [server] of {path}"
    assert call_data(result) == {
        "path": str(path),
        "table": "server",
        "key": "host",
        "value": "example.com",
        "toml_action": "replaced",
    }

    result = files.toml(ctx, path, table="server", key="host", value="example.com")
    assert result.changed is False
    assert result.summary == f'ensure host is "example.com" in [server] of {path}'
    assert call_data(result)["toml_action"] == "unchanged"
    assert len(ctx.changes) == 1


def test_toml_result_fields_report_toml_action_and_arguments(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path)

    result = files.toml(ctx, path, table="s", key="b", value=2)
    assert call_data(result) == {
        "path": str(path),
        "table": "s",
        "key": "b",
        "value": 2,
        "toml_action": "added",
    }

    result = files.toml(ctx, path, table="s", key="a", value=9)
    assert call_data(result)["toml_action"] == "replaced"

    result = files.toml(ctx, path, table="s", key="a", state="absent")
    assert call_data(result) == {
        "path": str(path),
        "table": "s",
        "key": "a",
        "value": None,
        "toml_action": "removed",
    }

    result = files.toml(ctx, path, table="s", state="absent")
    assert call_data(result)["toml_action"] == "removed"

    result = files.toml(ctx, path, table="s", state="absent")
    assert call_data(result) == {
        "path": str(path),
        "table": "s",
        "key": None,
        "value": None,
        "toml_action": "unchanged",
    }

    result = files.toml(ctx, path, key="pre", value=True)
    assert call_data(result) == {
        "path": str(path),
        "table": None,
        "key": "pre",
        "value": True,
        "toml_action": "added",
    }


def test_toml_edit_scenarios(tmp_path):
    ctx = context(tmp_path)

    # Spacing and quote variants that already match are no-ops.
    path = tmp_path / "spacing.toml"
    path.write_text('[server]\nhost  =  "example.com"  # main\n')
    result = files.toml(ctx, path, table="server", key="host", value="example.com")
    assert path.read_text() == '[server]\nhost  =  "example.com"  # main\n'
    assert result.changed is False

    # Insertion goes to the end of the table body and preserves the rest.
    path = tmp_path / "insert.toml"
    path.write_text("# top\n[s]\na = 1\n\n# trailing\n[t]\nb = 2\n")
    files.toml(ctx, path, table="s", key="c", value=3)
    assert path.read_text() == "# top\n[s]\na = 1\nc = 3\n\n# trailing\n[t]\nb = 2\n"

    # Missing tables are appended; missing files are created with mode 0o644.
    path = tmp_path / "append.toml"
    path.write_text("[s]\na = 1\n")
    files.toml(ctx, path, table="t", key="b", value=2)
    assert path.read_text() == "[s]\na = 1\n[t]\nb = 2\n"

    fresh = tmp_path / "new.toml"
    files.toml(ctx, fresh, table="s", key="a", value=1)
    assert fresh.read_text() == "[s]\na = 1\n"
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o644

    # The top-level preamble is edited when table is None.
    path = tmp_path / "preamble.toml"
    path.write_text("a = 1\n[s]\nb = 2\n")
    files.toml(ctx, path, key="c", value=3)
    assert path.read_text() == "a = 1\nc = 3\n[s]\nb = 2\n"
    result = files.toml(ctx, path, key="c", value=3)
    assert result.changed is False
    assert result.summary == f"ensure c is 3 in {path}"

    # A dotted key resolves into an existing sub-table header.
    path = tmp_path / "dotted.toml"
    path.write_text("[tool.uv]\nnative-tls = true\n\n[tool.uv.pip]\nno-cache = false\n")
    files.toml(ctx, path, table="tool.uv", key="pip.no-cache", value=True)
    assert path.read_text() == "[tool.uv]\nnative-tls = true\n\n[tool.uv.pip]\nno-cache = true\n"

    # A multi-line array keeps its layout when changed.
    path = tmp_path / "array.toml"
    path.write_text('[project]\ndependencies = [\n    "a",\n]\n')
    files.toml(ctx, path, table="project", key="dependencies", value=["b", "c"])
    assert path.read_text() == '[project]\ndependencies = [\n    "b",\n    "c",\n]\n'

    # A no-op does not repair a missing final newline.
    path = tmp_path / "newline.toml"
    path.write_text("[s]\na = 1")
    result = files.toml(ctx, path, table="s", key="a", value=1)
    assert path.read_text() == "[s]\na = 1"
    assert result.changed is False


def test_toml_value_types(tmp_path):
    path = tmp_path / "typed.toml"
    ctx = context(tmp_path)

    files.toml(ctx, path, key="flag", value=True)
    files.toml(ctx, path, key="count", value=4)
    files.toml(ctx, path, key="ratio", value=1.5)
    files.toml(ctx, path, key="name", value="demo")
    files.toml(ctx, path, key="groups", value=["dev", "lint"])
    files.toml(ctx, path, key="empty", value=[])

    assert path.read_text() == (
        'flag = true\ncount = 4\nratio = 1.5\nname = "demo"\ngroups = ["dev", "lint"]\nempty = []\n'
    )

    # values compare by parsed type: true is not 1, and 1 is not 1.0
    assert files.toml(ctx, path, key="flag", value=True).changed is False
    assert files.toml(ctx, path, key="flag", value=1).changed is True
    assert path.read_text().splitlines()[0] == "flag = 1"
    assert files.toml(ctx, path, key="count", value=4.0).changed is True


def test_toml_absent_scenarios(tmp_path):
    path = tmp_path / "absent.toml"
    path.write_text("a = 0\n[s]\na = 1\nb = 2\n[t]\na = 3\n")
    ctx = context(tmp_path)

    result = files.toml(ctx, path, table="s", key="a", state="absent")
    assert path.read_text() == "a = 0\n[s]\nb = 2\n[t]\na = 3\n"
    assert result.changed is True
    assert result.summary == f"remove a from [s] of {path}"

    result = files.toml(ctx, path, table="s", key="a", state="absent")
    assert result.changed is False
    assert result.summary == f"ensure a is absent from [s] of {path}"

    # removing a table takes its span, comments included, and keeps the rest
    path = tmp_path / "table.toml"
    path.write_text("pre = 1\n[s]\na = 1\n# note\n\n[t]\nb = 2\n")
    result = files.toml(ctx, path, table="s", state="absent")
    assert path.read_text() == "pre = 1\n[t]\nb = 2\n"
    assert result.summary == f"remove table [s] from {path}"

    result = files.toml(ctx, path, table="s", state="absent")
    assert result.changed is False
    assert result.summary == f"ensure table [s] is absent from {path}"

    # removing the only table leaves an empty file
    path = tmp_path / "only.toml"
    path.write_text("[s]\na = 1\n")
    result = files.toml(ctx, path, table="s", state="absent")
    assert path.read_text() == ""
    assert result.changed is True


def test_toml_absent_removes_a_dotted_key_declared_table(tmp_path):
    path = tmp_path / "dotted-table.toml"
    path.write_text('tool.uv.native-tls = true\n[project]\nname = "demo"\n')
    ctx = context(tmp_path)

    result = files.toml(ctx, path, table="tool.uv", state="absent")

    assert path.read_text() == '[project]\nname = "demo"\n'
    assert result.changed is True
    assert result.summary == f"remove table [tool.uv] from {path}"


def test_toml_create_false_rejects_a_missing_file(tmp_path):
    ctx = context(tmp_path)
    missing = tmp_path / "missing.toml"

    with pytest.raises(TaskFailed, match="does not exist"):
        files.toml(ctx, missing, table="s", key="a", value=1, create=False)
    with pytest.raises(TaskFailed, match="does not exist"):
        files.toml(ctx, missing, table="s", key="a", state="absent", create=False)

    assert not missing.exists()


def test_toml_creates_a_missing_file_with_parents(tmp_path):
    path = tmp_path / "deep" / "nested" / "app.toml"
    ctx = context(tmp_path)

    result = files.toml(ctx, path, table="s", key="a", value=1)

    assert path.read_text() == "[s]\na = 1\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert stat.S_IMODE((tmp_path / "deep").stat().st_mode) == 0o755
    assert result.changed is True


def test_toml_absent_on_a_missing_file_is_a_noop_and_creates_nothing(tmp_path):
    missing = tmp_path / "missing.toml"
    ctx = context(tmp_path)

    result = files.toml(ctx, missing, table="s", key="a", state="absent")
    assert result.changed is False
    result = files.toml(ctx, missing, table="s", state="absent")
    assert result.changed is False

    assert not missing.exists()
    assert ctx.changes == []


def test_toml_dry_run_reports_without_writing_or_creating(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path, dry_run=True)

    result = files.toml(ctx, path, table="s", key="b", value=2)

    assert path.read_text() == "[s]\na = 1\n"
    assert result.changed is True
    assert len(ctx.changes) == 1

    missing = tmp_path / "deep" / "new.toml"
    result = files.toml(ctx, missing, table="s", key="a", value=1)
    assert result.changed is True
    assert not missing.exists()
    assert not (tmp_path / "deep").exists()


def test_toml_invalid_arguments_are_rejected(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid toml state"):
        files.toml(ctx, path, table="s", key="a", value=1, state="unknown")
    with pytest.raises(TaskFailed, match="toml path must not be empty"):
        files.toml(ctx, "", table="s", key="a", value=1)
    with pytest.raises(TaskFailed, match="toml table must use bare key parts"):
        files.toml(ctx, path, table="a b", key="a", value=1)
    with pytest.raises(TaskFailed, match="toml key must use bare key parts"):
        files.toml(ctx, path, table="s", key='"quoted"', value=1)
    with pytest.raises(TaskFailed, match="requires a key"):
        files.toml(ctx, path, table="s", value=1)
    with pytest.raises(TaskFailed, match="requires a value"):
        files.toml(ctx, path, table="s", key="a")
    with pytest.raises(TaskFailed, match="requires a key or a table"):
        files.toml(ctx, path, state="absent")
    with pytest.raises(TaskFailed, match="does not take a value"):
        files.toml(ctx, path, table="s", key="a", value=1, state="absent")
    with pytest.raises(TaskFailed, match="must be a boolean"):
        files.toml(ctx, path, table="s", key="a", value=1, create="yes")
    with pytest.raises(TaskFailed, match="unsupported toml value type"):
        files.toml(ctx, path, table="s", key="a", value={"a": 1})
    with pytest.raises(TaskFailed, match="toml array items must be strings"):
        files.toml(ctx, path, table="s", key="a", value=["a", 1])

    assert ctx.changes == []
    assert path.read_text() == "[s]\na = 1\n"


def test_toml_rejects_array_of_tables_targets(tmp_path):
    path = tmp_path / "aot.toml"
    path.write_text('[[tool.uv.index]]\nurl = "https://x"\n')
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="array-of-tables"):
        files.toml(ctx, path, table="tool.uv.index", key="url", value="https://y")
    with pytest.raises(TaskFailed, match="array-of-tables"):
        files.toml(ctx, path, table="tool.uv.index", key="url", state="absent")
    with pytest.raises(TaskFailed, match="array-of-tables"):
        files.toml(ctx, path, table="tool.uv.index", state="absent")

    assert path.read_text() == '[[tool.uv.index]]\nurl = "https://x"\n'
    assert ctx.changes == []


def test_toml_rejects_directories_symlinks_and_non_utf8_files(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="directory"):
        files.toml(ctx, tmp_path, table="s", key="a", value=1)

    target = tmp_path / "target.toml"
    target.write_text("[s]\na = 1\n")
    link = tmp_path / "link.toml"
    link.symlink_to(target)
    with pytest.raises(TaskFailed, match="symlink"):
        files.toml(ctx, link, table="s", key="a", value=2)

    binary = tmp_path / "binary.toml"
    binary.write_bytes(b"[s]\n\xff\xfe\n")
    with pytest.raises(TaskFailed, match="UTF-8"):
        files.toml(ctx, binary, table="s", key="a", value=2)

    assert target.read_text() == "[s]\na = 1\n"


def test_toml_preserves_existing_file_mode(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text("[s]\na = 1\n")
    path.chmod(0o600)
    ctx = context(tmp_path)

    files.toml(ctx, path, table="s", key="a", value=2)

    assert path.read_text() == "[s]\na = 2\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_toml_uses_privileged_worker(tmp_path):
    path = tmp_path / "root-owned.toml"
    ctx = privileged_context(tmp_path)

    try:
        result = files.toml(ctx, path, table="s", key="a", value=1, sudo=True)
        assert result.changed is True
        assert path.read_text() == "[s]\na = 1\n"

        result = files.toml(ctx, path, table="s", key="a", value=1, sudo=True)
        assert result.changed is False
        assert ctx.sudo.refresh_count > 0
    finally:
        ctx.close()


def test_toml_validates_arguments_before_touching_the_file(tmp_path):
    ctx = context(tmp_path)

    # argument errors win over filesystem errors (the path is a directory)
    with pytest.raises(TaskFailed, match="requires a value"):
        files.toml(ctx, tmp_path, table="s", key="a")
    with pytest.raises(TaskFailed, match="requires a key or a table"):
        files.toml(ctx, tmp_path, state="absent")
    # argument errors win over the create=False missing-file check
    with pytest.raises(TaskFailed, match="requires a value"):
        files.toml(ctx, tmp_path / "missing.toml", key="a", create=False)

    assert ctx.changes == []


@pytest.mark.parametrize("char", ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\0"])
def test_toml_rejects_line_breaks_and_null_bytes_in_arguments(tmp_path, char):
    path = tmp_path / "app.toml"
    path.write_text("[s]\na = 1\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="must not contain"):
        files.toml(ctx, path, table=f"s{char}t", key="a", value=1)
    with pytest.raises(TaskFailed, match="must not contain"):
        files.toml(ctx, path, table="s", key=f"a{char}b", value=1)

    assert path.read_text() == "[s]\na = 1\n"
    assert ctx.changes == []


def test_toml_strips_a_utf8_bom_on_write_but_not_on_a_noop(tmp_path):
    path = tmp_path / "bom.toml"
    path.write_bytes(b"\xef\xbb\xbf[s]\na = 1\nb = 2\n")
    ctx = context(tmp_path)

    # a no-op run only reads, so the BOM is kept
    result = files.toml(ctx, path, table="s", key="a", value=1)
    assert result.changed is False
    assert path.read_bytes() == b"\xef\xbb\xbf[s]\na = 1\nb = 2\n"

    # the first real write drops the BOM
    result = files.toml(ctx, path, table="s", key="a", value=9)
    assert result.changed is True
    assert path.read_bytes() == b"[s]\na = 9\nb = 2\n"

    result = files.toml(ctx, path, table="s", key="a", value=9)
    assert result.changed is False
    assert path.read_bytes() == b"[s]\na = 9\nb = 2\n"


def test_line_strips_a_utf8_bom_on_write_but_not_on_a_noop(tmp_path):
    path = tmp_path / "lines"
    path.write_bytes(b"\xef\xbb\xbfone\ntwo\n")
    ctx = context(tmp_path)

    result = files.line(ctx, path, "one")
    assert result.changed is False
    assert path.read_bytes() == b"\xef\xbb\xbfone\ntwo\n"

    files.line(ctx, path, "ONE", match=r"^one$")
    assert path.read_bytes() == b"ONE\ntwo\n"
