import os
import stat

import pytest

from kitup._utils import file_actions
from kitup.errors import TaskFailed

from .helpers import make_context


def test_require_path_kind_applies_each_policy(tmp_path):
    file_actions.require_path_kind(tmp_path / "missing", None, policy=file_actions.PathPolicy.FILE)
    with pytest.raises(TaskFailed, match="does not exist"):
        file_actions.require_path_kind(
            tmp_path / "missing", None, policy=file_actions.PathPolicy.FILE, missing_ok=False
        )

    target = tmp_path / "target"
    target.write_text("data\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    regular = tmp_path / "regular"
    regular.write_text("data\n")

    link_info = file_actions.stat_local(link)
    for policy, message in [
        (file_actions.PathPolicy.FILE, "is a symlink"),
        (file_actions.PathPolicy.DIRECTORY, "is a symlink"),
        (file_actions.PathPolicy.REPLACEABLE, None),
        (file_actions.PathPolicy.NOT_DIRECTORY, None),
    ]:
        if message is None:
            file_actions.require_path_kind(link, link_info, policy=policy)
        else:
            with pytest.raises(TaskFailed, match=message):
                file_actions.require_path_kind(link, link_info, policy=policy)

    directory_info = file_actions.stat_local(directory)
    for policy, message in [
        (file_actions.PathPolicy.FILE, "is a directory"),
        (file_actions.PathPolicy.DIRECTORY, None),
        (file_actions.PathPolicy.REPLACEABLE, "is a directory"),
        (file_actions.PathPolicy.NOT_DIRECTORY, "is a directory"),
    ]:
        if message is None:
            file_actions.require_path_kind(directory, directory_info, policy=policy)
        else:
            with pytest.raises(TaskFailed, match=message):
                file_actions.require_path_kind(directory, directory_info, policy=policy)

    special_info = file_actions.stat_local(fifo)
    for policy, message in [
        (file_actions.PathPolicy.FILE, "is not a regular file"),
        (file_actions.PathPolicy.DIRECTORY, "is not a directory"),
        (file_actions.PathPolicy.REPLACEABLE, "is not a regular file"),
        (file_actions.PathPolicy.NOT_DIRECTORY, None),
    ]:
        if message is None:
            file_actions.require_path_kind(fifo, special_info, policy=policy)
        else:
            with pytest.raises(TaskFailed, match=message):
                file_actions.require_path_kind(fifo, special_info, policy=policy)

    regular_info = file_actions.stat_local(regular)
    for policy in (
        file_actions.PathPolicy.FILE,
        file_actions.PathPolicy.REPLACEABLE,
        file_actions.PathPolicy.NOT_DIRECTORY,
    ):
        file_actions.require_path_kind(regular, regular_info, policy=policy)


def test_reconcile_metadata_changed_applies_modes_and_reports_dry_run(tmp_path):
    path = tmp_path / "file"
    path.write_text("data\n")
    path.chmod(0o600)
    ctx = make_context(tmp_path)
    info = file_actions.stat_local(path)

    assert file_actions.reconcile_metadata_changed(ctx, path, info, mode=0o640, sudo=False)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    updated = file_actions.stat_local(path)
    assert not file_actions.reconcile_metadata_changed(ctx, path, updated, mode=0o640, sudo=False)
    assert not file_actions.reconcile_metadata_changed(ctx, path, updated, mode=None, sudo=False)
    assert not file_actions.reconcile_metadata_changed(ctx, tmp_path / "missing", None, mode=0o640, sudo=False)

    path.chmod(0o600)
    dry_run = make_context(tmp_path, dry_run=True)
    assert file_actions.reconcile_metadata_changed(dry_run, path, file_actions.stat_local(path), mode=0o640, sudo=False)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
