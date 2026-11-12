import os
import stat

import pytest

from kitup._utils import file_actions
from kitup.errors import TaskFailed

from .helpers import make_context


def test_require_path_kind_allows_a_missing_path_by_default(tmp_path):
    file_actions.require_path_kind(tmp_path / "missing", None, policy=file_actions.PathPolicy.FILE)


def test_require_path_kind_rejects_a_missing_path_when_required(tmp_path):
    with pytest.raises(TaskFailed, match="does not exist"):
        file_actions.require_path_kind(
            tmp_path / "missing",
            None,
            policy=file_actions.PathPolicy.FILE,
            missing_ok=False,
        )


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        (file_actions.PathPolicy.FILE, "is a symlink"),
        (file_actions.PathPolicy.DIRECTORY, "is a symlink"),
        (file_actions.PathPolicy.REPLACEABLE, None),
        (file_actions.PathPolicy.NOT_DIRECTORY, None),
    ],
)
def test_require_path_kind_handles_symlinks(tmp_path, policy, message):
    target = tmp_path / "target"
    target.write_text("data\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    info = file_actions.stat_local(link)

    if message is None:
        file_actions.require_path_kind(link, info, policy=policy)
    else:
        with pytest.raises(TaskFailed, match=message):
            file_actions.require_path_kind(link, info, policy=policy)


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        (file_actions.PathPolicy.FILE, "is a directory"),
        (file_actions.PathPolicy.DIRECTORY, None),
        (file_actions.PathPolicy.REPLACEABLE, "is a directory"),
        (file_actions.PathPolicy.NOT_DIRECTORY, "is a directory"),
    ],
)
def test_require_path_kind_handles_directories(tmp_path, policy, message):
    path = tmp_path / "directory"
    path.mkdir()
    info = file_actions.stat_local(path)

    if message is None:
        file_actions.require_path_kind(path, info, policy=policy)
    else:
        with pytest.raises(TaskFailed, match=message):
            file_actions.require_path_kind(path, info, policy=policy)


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        (file_actions.PathPolicy.FILE, "is not a regular file"),
        (file_actions.PathPolicy.DIRECTORY, "is not a directory"),
        (file_actions.PathPolicy.REPLACEABLE, "is not a regular file"),
        (file_actions.PathPolicy.NOT_DIRECTORY, None),
    ],
)
def test_require_path_kind_handles_special_files(tmp_path, policy, message):
    path = tmp_path / "fifo"
    os.mkfifo(path)
    info = file_actions.stat_local(path)

    if message is None:
        file_actions.require_path_kind(path, info, policy=policy)
    else:
        with pytest.raises(TaskFailed, match=message):
            file_actions.require_path_kind(path, info, policy=policy)


@pytest.mark.parametrize(
    "policy",
    [
        file_actions.PathPolicy.FILE,
        file_actions.PathPolicy.REPLACEABLE,
        file_actions.PathPolicy.NOT_DIRECTORY,
    ],
)
def test_require_path_kind_accepts_regular_files(tmp_path, policy):
    path = tmp_path / "file"
    path.write_text("data\n")

    file_actions.require_path_kind(path, file_actions.stat_local(path), policy=policy)


def test_reconcile_metadata_changed_applies_a_different_mode(tmp_path):
    path = tmp_path / "file"
    path.write_text("data\n")
    path.chmod(0o600)
    ctx = make_context(tmp_path)

    changed = file_actions.reconcile_metadata_changed(
        ctx,
        path,
        file_actions.stat_local(path),
        mode=0o640,
        sudo=False,
    )

    assert changed
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_reconcile_metadata_changed_is_idempotent(tmp_path):
    path = tmp_path / "file"
    path.write_text("data\n")
    path.chmod(0o640)
    ctx = make_context(tmp_path)

    changed = file_actions.reconcile_metadata_changed(
        ctx,
        path,
        file_actions.stat_local(path),
        mode=0o640,
        sudo=False,
    )

    assert not changed


def test_reconcile_metadata_changed_treats_a_missing_path_as_unchanged(tmp_path):
    ctx = make_context(tmp_path)

    assert not file_actions.reconcile_metadata_changed(
        ctx,
        tmp_path / "missing",
        None,
        mode=0o640,
        sudo=False,
    )


def test_reconcile_metadata_changed_reports_dry_run_without_applying(tmp_path):
    path = tmp_path / "file"
    path.write_text("data\n")
    path.chmod(0o600)
    ctx = make_context(tmp_path, dry_run=True)

    changed = file_actions.reconcile_metadata_changed(
        ctx,
        path,
        file_actions.stat_local(path),
        mode=0o640,
        sudo=False,
    )

    assert changed
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reconcile_metadata_changed_ignores_a_missing_mode(tmp_path):
    path = tmp_path / "file"
    path.write_text("data\n")
    path.chmod(0o600)
    ctx = make_context(tmp_path)

    assert not file_actions.reconcile_metadata_changed(
        ctx,
        path,
        file_actions.stat_local(path),
        mode=None,
        sudo=False,
    )
