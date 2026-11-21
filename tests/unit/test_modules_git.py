import pytest

from kitup.errors import TaskFailed
from kitup.modules import git

from .helpers import FakeCommandRunner, argvs, call_data, make_context, response

URL = "https://example.test/repo.git"
OLD_URL = "https://example.test/old.git"
OLD = "1" * 40
NEW = "2" * 40
SHA = "a" * 40

IS_REPO = ["git", "-C", "{dest}", "rev-parse", "--show-toplevel"]


@pytest.fixture(autouse=True)
def git_available(monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "git" if name == "git" else None,
    )


def is_repo(dest):
    return [part.format(dest=dest) for part in IS_REPO]


def toplevel(dest):
    """Script a successful repository detection for ``dest``."""
    return response(0, f"{dest}\n")


def test_clone_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(128)]
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"

    git.repo(URL, dest)

    assert argvs(command) == [
        is_repo(str(dest)),
        ["git", "clone", URL, str(dest)],
    ]
    assert command.calls[1][1]["env"] == {"GIT_TERMINAL_PROMPT": "0"}
    assert command.calls[1][1]["sudo"] is False
    assert call_data(ctx.changes[0]) == {
        "repo": URL,
        "dest": str(dest),
        "version": "HEAD",
    }
    assert ctx.changes[0].action == "git.repo"
    assert ctx.changes[0].summary == f"clone {URL} into {dest}"

    command = FakeCommandRunner()
    command.responses = [response(1)]
    make_context(tmp_path, command=command)
    dest = tmp_path / "repo-options"

    git.repo(URL, dest, version="main", single_branch=True, depth=5)

    assert command.calls[1][0] == [
        "git",
        "clone",
        "--depth=5",
        "--single-branch",
        "--branch=main",
        URL,
        str(dest),
    ]

    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    make_context(tmp_path, command=command)
    dest = tmp_path / "repo-sha"

    git.repo(URL, dest, version=SHA)

    assert argvs(command) == [
        is_repo(str(dest)),
        ["git", "clone", URL, str(dest)],
        ["git", "-C", str(dest), "checkout", "--force", SHA],
    ]

    command = FakeCommandRunner()
    command.responses = [response(1)]
    make_context(tmp_path, command=command)
    empty = tmp_path / "repo-empty"
    empty.mkdir()

    git.repo(URL, empty)

    assert command.calls[1][0] == ["git", "clone", URL, str(empty)]

    # Being inside a work tree does not make ``dest`` a checkout of its own;
    # detecting the parent would rewrite the parent's origin URL.
    parent = tmp_path / "parent"
    nested = parent / "sub"
    nested.mkdir(parents=True)
    command = FakeCommandRunner()
    command.responses = [toplevel(str(parent)), response(0)]
    ctx = make_context(tmp_path, command=command)

    git.repo(URL, nested)

    assert argvs(command) == [
        is_repo(str(nested)),
        ["git", "clone", URL, str(nested)],
    ]
    assert [change.summary for change in ctx.changes] == [f"clone {URL} into {nested}"]


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

    git.repo(URL, dest)

    assert argvs(command) == [
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

    git.repo(URL, dest)

    assert argvs(command) == [
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
    assert call_data(ctx.changes[0]) == {
        "repo": URL,
        "dest": str(dest),
        "before": OLD,
        "after": NEW,
        "version": "HEAD",
    }
    assert ctx.changes[0].summary == f"update {dest} {OLD[:12]}..{NEW[:12]}"


def test_version_resolution_prefers_branches_then_tags(tmp_path):
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

    git.repo(URL, dest, version="main")

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

    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "tagged"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{URL}\n"),
        response(0),
        response(128),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(URL, dest, version="v1.0")

    assert command.calls[3][0][-1] == "origin/v1.0^{commit}"
    assert command.calls[4][0][-1] == "v1.0^{commit}"
    assert ctx.changes == []


def test_unknown_version_fails(tmp_path):
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)
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
        git.repo(URL, dest, version="nope")


def test_dirty_worktree_handling_and_force(tmp_path):
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
        git.repo(URL, dest)

    assert len(command.calls) == 6
    assert ctx.changes == []

    # ``reset --hard`` keeps untracked files, so they must not fail the update.
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "untracked"
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

    git.repo(URL, dest)

    assert command.calls[5][0] == [
        "git",
        "-C",
        str(dest),
        "status",
        "--porcelain",
        "--untracked-files=no",
    ]
    assert len(ctx.changes) == 1

    # force=True skips the status probe entirely (short-circuit).
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    forced = tmp_path / "forced"
    forced.mkdir()
    command.responses = [
        toplevel(str(forced)),
        response(0, f"{URL}\n"),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{OLD}\n"),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
    ]

    git.repo(URL, forced, force=True)

    assert argvs(command) == [
        is_repo(str(forced)),
        ["git", "-C", str(forced), "remote", "get-url", "origin"],
        ["git", "-C", str(forced), "fetch", "origin"],
        ["git", "-C", str(forced), "rev-parse", "--verify", "--quiet", "origin/HEAD^{commit}"],
        ["git", "-C", str(forced), "rev-parse", "HEAD"],
        ["git", "-C", str(forced), "checkout", "--force", "HEAD"],
        ["git", "-C", str(forced), "reset", "--hard", NEW],
        ["git", "-C", str(forced), "rev-parse", "HEAD"],
    ]
    assert len(ctx.changes) == 1


def test_remote_url_changes_update_fetch_and_reconcile_urls(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [toplevel(str(dest)), response(0, f"{URL}\n")]

    git.repo(URL, dest, update=False)

    assert argvs(command) == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
    ]
    assert ctx.changes == []

    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    changed = tmp_path / "changed"
    changed.mkdir()
    command.responses = [
        toplevel(str(changed)),
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

    git.repo(URL, changed, update=False)

    assert argvs(command) == [
        is_repo(str(changed)),
        ["git", "-C", str(changed), "remote", "get-url", "origin"],
        ["git", "-C", str(changed), "remote", "set-url", "origin", URL],
        ["git", "-C", str(changed), "fetch", "origin"],
        ["git", "-C", str(changed), "rev-parse", "--verify", "--quiet", "origin/HEAD^{commit}"],
        ["git", "-C", str(changed), "rev-parse", "HEAD"],
        ["git", "-C", str(changed), "status", "--porcelain", "--untracked-files=no"],
        ["git", "-C", str(changed), "checkout", "--force", "HEAD"],
        ["git", "-C", str(changed), "reset", "--hard", NEW],
        ["git", "-C", str(changed), "rev-parse", "HEAD"],
    ]
    assert [change.summary for change in ctx.changes] == [
        f"set remote URL of {changed} to {URL}; update {changed} {OLD[:12]}..{NEW[:12]}",
    ]
    assert call_data(ctx.changes[0]) == {
        "repo": URL,
        "dest": str(changed),
        "version": "HEAD",
        "previous_url": OLD_URL,
        "before": OLD,
        "after": NEW,
    }

    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    stable = tmp_path / "stable"
    stable.mkdir()
    command.responses = [
        toplevel(str(stable)),
        response(0, f"{OLD_URL}\n"),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(URL, stable, update=False)

    assert command.calls[2][0] == ["git", "-C", str(stable), "remote", "set-url", "origin", URL]
    assert len(command.calls) == 6
    assert [change.summary for change in ctx.changes] == [f"set remote URL of {stable} to {URL}"]

    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)
    missing = tmp_path / "missing-origin"
    missing.mkdir()
    command.responses = [
        toplevel(str(missing)),
        response(128),
        response(0),
        response(0),
        response(0, f"{NEW}\n"),
        response(0, f"{NEW}\n"),
    ]

    git.repo(URL, missing, update=False)

    assert command.calls[2][0] == ["git", "-C", str(missing), "remote", "add", "origin", URL]
    assert call_data(ctx.changes[0]) == {
        "repo": URL,
        "dest": str(missing),
        "version": "HEAD",
        "previous_url": None,
        "before": NEW,
        "after": NEW,
    }


def test_dry_run_url_change_does_not_fetch_the_old_remote(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command, dry_run=True)
    dest = tmp_path / "repo"
    dest.mkdir()
    command.responses = [
        toplevel(str(dest)),
        response(0, f"{OLD_URL}\n"),
    ]

    git.repo(URL, dest)

    assert argvs(command) == [
        is_repo(str(dest)),
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
    ]
    assert [change.summary for change in ctx.changes] == [f"set remote URL of {dest} to {URL}"]
    assert call_data(ctx.changes[0]) == {
        "repo": URL,
        "dest": str(dest),
        "version": "HEAD",
        "previous_url": OLD_URL,
    }


def test_dry_run_clone_records_without_running(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(128)]
    ctx = make_context(tmp_path, command=command, dry_run=True)
    dest = tmp_path / "repo"

    git.repo(URL, dest)

    assert argvs(command) == [is_repo(str(dest))]
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

    git.repo(URL, dest)

    assert argvs(command) == [
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
    make_context(tmp_path, command=command)
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "file.txt").write_text("data", encoding="utf-8")

    with pytest.raises(TaskFailed, match="not empty and not a git repository"):
        git.repo(URL, dest)

    assert len(command.calls) == 1


def test_file_destination_fails(tmp_path):
    make_context(tmp_path)
    dest = tmp_path / "file"
    dest.write_text("data", encoding="utf-8")

    with pytest.raises(TaskFailed, match="not a directory"):
        git.repo(URL, dest)


def test_invalid_arguments_are_rejected(tmp_path):
    make_context(tmp_path)
    dest = tmp_path / "repo"

    with pytest.raises(TaskFailed, match="git repository URL must not be empty"):
        git.repo("  ", dest)
    with pytest.raises(TaskFailed, match="must not start with '-'"):
        git.repo("-x", dest)
    with pytest.raises(TaskFailed, match="git destination path must not be empty"):
        git.repo(URL, "")
    with pytest.raises(TaskFailed, match="git version must not be empty"):
        git.repo(URL, dest, version="")
    with pytest.raises(TaskFailed, match="must not start with '-'"):
        git.repo(URL, dest, version="-main")
    with pytest.raises(TaskFailed, match="depth must be an integer"):
        git.repo(URL, dest, depth=True)
    with pytest.raises(TaskFailed, match="depth must be an integer"):
        git.repo(URL, dest, depth="3")
    with pytest.raises(TaskFailed, match="depth must be at least 1"):
        git.repo(URL, dest, depth=0)
    for keyword in ("single_branch", "update", "force"):
        with pytest.raises(TaskFailed, match=f"git {keyword} must be a boolean"):
            git.repo(URL, dest, **{keyword: "yes"})


def test_missing_git_binary_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: None)
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="git is required but was not found"):
        git.repo(URL, tmp_path / "repo")

    assert command.calls == []
