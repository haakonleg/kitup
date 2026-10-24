from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import dnf

from .helpers import FakeCommandRunner, completed, make_context


@pytest.fixture(autouse=True)
def dnf5(monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.dnf.shutil.which",
        lambda name: "dnf5" if name == "dnf5" else None,
    )


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


def test_ensure_present_is_a_noop_when_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["git", "vim"])

    assert [call[0] for call in command.calls] == [
        ["rpm", "-q", "git"],
        ["rpm", "-q", "vim"],
    ]
    assert ctx.changes == []


def test_ensure_present_installs_only_missing_packages(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["git", "vim"])

    assert command.calls[2][0] == [
        "dnf5",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "git",
    ]
    assert ctx.changes[0].data == {"added": ["git"], "group": False}
    assert ctx.changes[0].summary == "+git"


def test_ensure_present_dry_run_does_not_install(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.packages(ctx, ["git"])

    assert [call[0] for call in command.calls] == [["rpm", "-q", "git"]]
    assert ctx.changes[0].summary == "+git"


def test_ensure_group_installs_missing_group(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n base\n"),
        response(0),
        response(0, "Installed groups:\n base\n desktop\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["desktop"], group=True)

    assert command.calls[1][0] == ["dnf5", "group", "install", "-y", "desktop"]
    assert ctx.changes[0].data == {"added": ["desktop"], "group": True}


def test_group_substring_is_not_treated_as_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "base-x\n"),
        response(0),
        response(0, "base-x\nbase\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["base"], group=True)

    assert command.calls[1][0] == ["dnf5", "group", "install", "-y", "base"]


def test_remove_removes_installed_packages(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(1)]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["git"], state="absent")

    assert command.calls[1][0] == ["dnf5", "remove", "-y", "git"]
    assert ctx.changes[0].summary == "-git"


def test_install_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(1)]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing"):
        dnf.packages(ctx, ["git"])


def test_invalid_state_is_an_error(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid package state"):
        dnf.packages(ctx, ["git"], state="unknown")


def test_packages_accepts_a_single_string(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, "git")

    assert [call[0] for call in command.calls] == [["rpm", "-q", "git"]]
    assert ctx.changes == []


def test_packages_rejects_empty_names(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="must not be empty"):
        dnf.packages(ctx, [])

    with pytest.raises(TaskFailed, match="must not be empty"):
        dnf.packages(ctx, "")

    with pytest.raises(TaskFailed, match="must not be empty"):
        dnf.packages(ctx, ["git", "  "])


def test_autoremove_is_a_noop_when_nothing_is_unneeded(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "")]
    ctx = make_context(tmp_path, command=command)

    dnf.autoremove(ctx)

    assert [call[0] for call in command.calls] == [
        [
            "dnf5",
            "repoquery",
            "--unneeded",
            "--quiet",
            "--queryformat",
            "%{name}\n",
        ]
    ]
    assert ctx.changes == []


def test_autoremove_removes_unneeded_packages(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "libfoo\n libbar \n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.autoremove(ctx)

    assert command.calls[1][0] == ["dnf5", "autoremove", "-y"]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].data == {"removed": ["libfoo", "libbar"]}
    assert ctx.changes[0].summary == "-libfoo -libbar"


def test_autoremove_dry_run_does_not_run(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "libfoo\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.autoremove(ctx)

    assert len(command.calls) == 1
    assert ctx.changes[0].summary == "-libfoo"


def test_autoremove_query_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not list unneeded"):
        dnf.autoremove(ctx)


def test_copr_is_a_noop_when_listed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "owner/project\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.copr(ctx, "owner/project")

    assert [call[0] for call in command.calls] == [["dnf5", "copr", "list"]]
    assert ctx.changes == []


def test_copr_enables_missing_project(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, ""), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.copr(ctx, "owner/project")

    assert command.calls[1][0] == [
        "dnf5",
        "copr",
        "enable",
        "-y",
        "owner/project",
    ]
    assert ctx.changes[0].data["type"] == "copr"


def test_copr_near_miss_is_not_treated_as_listed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "owner/project-extra\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.copr(ctx, "owner/project")

    assert command.calls[1][0] == [
        "dnf5",
        "copr",
        "enable",
        "-y",
        "owner/project",
    ]


def test_copr_list_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="copr unavailable\n")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not list COPR"):
        dnf.copr(ctx, "owner/project")


def test_copr_absent_removes_listed_project(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "owner/project\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.copr(ctx, "owner/project", state="absent")

    assert command.calls[1][0] == ["dnf5", "copr", "remove", "owner/project"]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].summary == "remove COPR owner/project"


def test_copr_absent_is_a_noop_when_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "")]
    ctx = make_context(tmp_path, command=command)

    dnf.copr(ctx, "owner/project", state="absent")

    assert [call[0] for call in command.calls] == [["dnf5", "copr", "list"]]
    assert ctx.changes == []


def test_invalid_copr_state_is_an_error(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid COPR state"):
        dnf.copr(ctx, "owner/project", state="invalid")


def test_copr_rejects_empty_project(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="COPR project must not be empty"):
        dnf.copr(ctx, "  ")


def test_rpm_release_installs_source(tmp_path):
    source = "https://example.test/tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    dnf.rpm(ctx, source)

    assert command.calls[0][0] == ["dnf5", "install", "-y", source]
    assert command.calls[0][1]["sudo"] is True
    assert ctx.changes[0].data == {"source": source}


def test_rpm_release_dry_run_does_not_install(tmp_path):
    source = "https://example.test/tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.rpm(ctx, source)

    assert command.calls == []
    assert len(ctx.changes) == 1


def test_rpm_release_accepts_local_path(tmp_path):
    source = tmp_path / "tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    dnf.rpm(ctx, source)

    assert command.calls[0][0] == ["dnf5", "install", "-y", str(source)]
    assert ctx.changes[0].data == {"source": str(source)}


def test_invalid_repository_state_is_an_error(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid repository state"):
        dnf.repository(ctx, "https://example.test/tool.repo", state="invalid")


def test_repository_present_requires_source(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="source is required"):
        dnf.repository(ctx)


def test_repository_absent_requires_name(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="name is required"):
        dnf.repository(ctx, "https://example.test/tool.repo", state="absent")


def test_repository_rejects_empty_source(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="repository source must not be empty"):
        dnf.repository(ctx, "  ")


def test_repository_rejects_empty_name(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="repository name must not be empty"):
        dnf.repository(ctx, state="absent", name="  ")


def test_rpm_rejects_empty_source(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="RPM source must not be empty"):
        dnf.rpm(ctx, "  ")


def test_repository_present_adds_repo_file(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(ctx, "https://example.test/tool.repo")

    assert command.calls[0][0] == ["dnf5", "--dump-main-config"]
    assert command.calls[1][0] == [
        "dnf5",
        "config-manager",
        "addrepo",
        "--from-repofile=https://example.test/tool.repo",
        "--save-filename=tool.repo",
    ]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].data == {
        "repository": "https://example.test/tool.repo",
        "path": str(reposdir / "tool.repo"),
    }


def test_repository_present_is_a_noop_when_file_exists(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    (reposdir / "tool.repo").write_text("[tool]\n", encoding="utf-8")
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(ctx, "https://example.test/tool.repo")

    assert [call[0] for call in command.calls] == [["dnf5", "--dump-main-config"]]
    assert ctx.changes == []


def test_repository_absent_removes_repo_file(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    (reposdir / "tool.repo").write_text("[tool]\n", encoding="utf-8")
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(ctx, state="absent", name="tool.repo")

    assert command.calls[1][0] == ["rm", "-f", str(reposdir / "tool.repo")]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].summary == "remove repository tool.repo"


def test_repository_absent_is_a_noop_when_file_is_missing(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(ctx, state="absent", name="tool.repo")

    assert [call[0] for call in command.calls] == [["dnf5", "--dump-main-config"]]
    assert ctx.changes == []


def test_repository_present_dry_run_does_not_add(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.repository(ctx, "https://example.test/tool.repo")

    assert [call[0] for call in command.calls] == [["dnf5", "--dump-main-config"]]
    assert ctx.changes[0].summary == "add repository tool.repo"


def test_repository_absent_dry_run_does_not_remove(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    (reposdir / "tool.repo").write_text("[tool]\n", encoding="utf-8")
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.repository(ctx, state="absent", name="tool.repo")

    assert [call[0] for call in command.calls] == [["dnf5", "--dump-main-config"]]
    assert ctx.changes[0].summary == "remove repository tool.repo"


def test_repository_uses_custom_name(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(ctx, "https://example.test/tool.repo", name="vendor")

    assert command.calls[1][0][-1] == "--save-filename=vendor.repo"
    assert ctx.changes[0].data["path"] == str(reposdir / "vendor.repo")


def test_missing_dnf5_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr("kitup.modules.dnf.shutil.which", lambda name: None)
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="dnf5 is required"):
        dnf.rpm(ctx, "tool.rpm")
