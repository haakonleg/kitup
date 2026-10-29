from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import git

from .helpers import FakeCommandRunner, completed, make_context

URL = "https://example.test/repo.git"
OLD_URL = "https://example.test/old.git"
OLD = "1" * 40
NEW = "2" * 40
SHA = "a" * 40

IS_REPO = ["git", "-C", "{dest}", "rev-parse", "--show-toplevel"]


@pytest.fixture(autouse=True)
def git_available(monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.git.shutil.which",
        lambda name: "git" if name == "git" else None,
    )


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


def is_repo(dest):
    return [part.format(dest=dest) for part in IS_REPO]


def toplevel(dest):
    """Script a successful repository detection for ``dest``."""
    return response(0, f"{dest}\n")


def test_clone_when_destination_is_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(128)]
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"

    git.repo(ctx, URL, dest)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "clone", URL, str(dest)],
    ]
    assert command.calls[1][1]["env"] == {"GIT_TERMINAL_PROMPT": "0"}
    assert command.calls[1][1]["sudo"] is False
    assert ctx.changes[0].data == {
        "repo": URL,
        "dest": str(dest),
        "version": "HEAD",
        "single_branch": False,
        "depth": None,
    }
    assert ctx.changes[0].summary == f"clone {URL} into {dest}"


def test_clone_with_version_single_branch_and_depth(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"

    git.repo(ctx, URL, dest, version="main", single_branch=True, depth=5)

    assert command.calls[1][0] == [
        "git",
        "clone",
        "--depth=5",
        "--single-branch",
        "--branch=main",
        URL,
        str(dest),
    ]


def test_clone_with_sha_checks_out_after_cloning(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"

    git.repo(ctx, URL, dest, version=SHA)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "clone", URL, str(dest)],
        ["git", "-C", str(dest), "checkout", "--force", SHA],
    ]


def test_clone_into_existing_empty_directory(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()

    git.repo(ctx, URL, dest)

    assert command.calls[1][0] == ["git", "clone", URL, str(dest)]


def test_empty_directory_inside_another_repo_is_cloned(tmp_path):
    # Being inside a work tree does not make ``dest`` a checkout of its own;
    # detecting the parent would rewrite the parent's origin URL.
    parent = tmp_path / "parent"
    dest = parent / "sub"
    dest.mkdir(parents=True)
    command = FakeCommandRunner()
    command.responses = [toplevel(str(parent)), response(0)]
    ctx = make_context(tmp_path, command=command)

    git.repo(ctx, URL, dest)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "clone", URL, str(dest)],
    ]
    assert [change.summary for change in ctx.changes] == [f"clone {URL} into {dest}"]


def test_existing_repo_up_to_date_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
        ["git", "-C", str(dest), "fetch", "origin"],
        ["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "origin/HEAD^{commit}"],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
    ]
    assert command.calls[2][1]["env"] == {"GIT_TERMINAL_PROMPT": "0"}
    assert ctx.changes == []


def test_existing_repo_behind_remote_is_updated(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{OLD}\n"),
        response(0, ""),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
        ["git", "-C", str(dest), "fetch", "origin"],
        ["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "origin/HEAD^{commit}"],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        ["git", "-C", str(dest), "status", "--porcelain", "--untracked-files=no"],
        ["git", "-C", str(dest), "checkout", "--force", "HEAD"],
        ["git", "-C", str(dest), "reset", "--hard", NEW],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
    ]
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data == {
        "before": OLD,
        "after": NEW,
        "version": "HEAD",
        "dest": str(dest),
    }
    assert ctx.changes[0].summary == f"update {dest} {OLD[:12]}..{NEW[:12]}"


def test_version_resolves_branch_before_tag(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest, version="main")

    assert command.calls[3][0] == [
        "git",
        "-C",
        str(dest),
        "rev-parse",
        "--verify",
        "--quiet",
        "origin/main^{commit}",
    ]
    assert ctx.changes == []


def test_version_falls_back_to_tag_or_commit(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(128),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest, version="v1.0")

    assert command.calls[3][0][-1] == "origin/v1.0^{commit}"
    assert command.calls[4][0][-1] == "v1.0^{commit}"
    assert ctx.changes == []


def test_unknown_version_fails(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(128),
        response(128),
    ]

    with pytest.raises(TaskFailed, match="version 'nope' not found"):
        git.repo(ctx, URL, dest, version="nope")


def test_dirty_worktree_without_force_fails(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{OLD}\n"),
        response(0, " M file.txt\n"),
    ]

    with pytest.raises(TaskFailed, match="local changes"):
        git.repo(ctx, URL, dest)

    assert len(command.calls) == 6
    assert ctx.changes == []


def test_untracked_files_do_not_block_update(tmp_path):
    # ``reset --hard`` keeps untracked files, so they must not fail the update.
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{OLD}\n"),
        response(0, ""),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest)

    assert command.calls[5][0] == [
        "git",
        "-C",
        str(dest),
        "status",
        "--porcelain",
        "--untracked-files=no",
    ]
    assert len(ctx.changes) == 1


def test_dirty_worktree_with_force_is_updated(tmp_path):
    # force=True skips the status probe entirely (short-circuit).
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{OLD}\n"),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest, force=True)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
        ["git", "-C", str(dest), "fetch", "origin"],
        ["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "origin/HEAD^{commit}"],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        ["git", "-C", str(dest), "checkout", "--force", "HEAD"],
        ["git", "-C", str(dest), "reset", "--hard", NEW],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
    ]
    assert len(ctx.changes) == 1


def test_update_false_with_same_url_does_not_contact_remote(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [toplevel(str(dest)), response(0, f"{URL}\n")]

    git.repo(ctx, URL, dest, update=False)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
    ]
    assert ctx.changes == []


def test_url_change_triggers_fetch_and_update_even_when_update_is_false(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{OLD_URL}\n"),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{OLD}\n"),
        response(0, ""),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest, update=False)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
        ["git", "-C", str(dest), "remote", "set-url", "origin", URL],
        ["git", "-C", str(dest), "fetch", "origin"],
        ["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "origin/HEAD^{commit}"],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        ["git", "-C", str(dest), "status", "--porcelain", "--untracked-files=no"],
        ["git", "-C", str(dest), "checkout", "--force", "HEAD"],
        ["git", "-C", str(dest), "reset", "--hard", NEW],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
    ]
    assert [change.summary for change in ctx.changes] == [
        f"set remote URL of {dest} to {URL}",
        f"update {dest} {OLD[:12]}..{NEW[:12]}",
    ]
    assert ctx.changes[0].data == {"repo": URL, "dest": str(dest), "previous_url": OLD_URL}


def test_url_change_without_drift_only_sets_the_url(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{OLD_URL}\n"),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest, update=False)

    assert command.calls[2][0] == ["git", "-C", str(dest), "remote", "set-url", "origin", URL]
    assert len(command.calls) == 6
    assert [change.summary for change in ctx.changes] == [f"set remote URL of {dest} to {URL}"]


def test_missing_origin_remote_is_added(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(128),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(ctx, URL, dest, update=False)

    assert command.calls[2][0] == ["git", "-C", str(dest), "remote", "add", "origin", URL]
    assert ctx.changes[0].data == {"repo": URL, "dest": str(dest)}


def test_dry_run_clone_records_without_running(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(128)]
    ctx = make_context(tmp_path, command=command, dry_run=True)
    dest = tmp_path / "repo"

    git.repo(ctx, URL, dest)

    assert [call[0] for call in command.calls] == [is_repo(str(dest))]
    assert [change.summary for change in ctx.changes] == [f"clone {URL} into {dest}"]


def test_dry_run_update_records_without_mutating(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command, dry_run=True)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{OLD}\n"),
        response(0, ""),
    ]

    git.repo(ctx, URL, dest)

    assert [call[0] for call in command.calls] == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
        ["git", "-C", str(dest), "fetch", "origin"],
        ["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "origin/HEAD^{commit}"],
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        ["git", "-C", str(dest), "status", "--porcelain", "--untracked-files=no"],
    ]
    assert [change.summary for change in ctx.changes] == [f"update {dest} {OLD[:12]}..{NEW[:12]}"]


def test_non_empty_non_repo_destination_fails(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "file.txt").write_text("data", encoding="utf-8")

    with pytest.raises(TaskFailed, match="not empty and not a git repository"):
        git.repo(ctx, URL, dest)

    assert len(command.calls) == 1


def test_file_destination_fails(tmp_path):
    ctx = make_context(tmp_path)
    dest = tmp_path / "file"
    dest.write_text("data", encoding="utf-8")

    with pytest.raises(TaskFailed, match="not a directory"):
        git.repo(ctx, URL, dest)


def test_invalid_arguments_are_rejected(tmp_path):
    ctx = make_context(tmp_path)
    dest = tmp_path / "repo"

    with pytest.raises(TaskFailed, match="git repository URL must not be empty"):
        git.repo(ctx, "  ", dest)
    with pytest.raises(TaskFailed, match="must not start with '-'"):
        git.repo(ctx, "-x", dest)
    with pytest.raises(TaskFailed, match="git destination path must not be empty"):
        git.repo(ctx, URL, "")
    with pytest.raises(TaskFailed, match="git version must not be empty"):
        git.repo(ctx, URL, dest, version="")
    with pytest.raises(TaskFailed, match="must not start with '-'"):
        git.repo(ctx, URL, dest, version="-main")
    with pytest.raises(TaskFailed, match="depth must be an integer"):
        git.repo(ctx, URL, dest, depth=True)
    with pytest.raises(TaskFailed, match="depth must be an integer"):
        git.repo(ctx, URL, dest, depth="3")
    with pytest.raises(TaskFailed, match="depth must be at least 1"):
        git.repo(ctx, URL, dest, depth=0)


def test_missing_git_binary_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup.modules.git.shutil.which", lambda name: None)
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="git is required but was not found"):
        git.repo(ctx, URL, tmp_path / "repo")

    assert command.calls == []
