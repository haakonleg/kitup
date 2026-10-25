from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import dnf
from kitup.registry import Bundle

from .helpers import FakeCommandRunner, FakeSudo, completed, make_context


@pytest.fixture(autouse=True)
def dnf5(monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.dnf.shutil.which",
        lambda name: "dnf5" if name == "dnf5" else None,
    )


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


def bundle_context(tmp_path, bundle_dir, command):
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = make_context(tmp_path, command=command)
    ctx.begin_task(bundle.tasks[0])
    return ctx


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


def test_read_only_probes_do_not_request_sudo(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    ctx = make_context(tmp_path, command=command, sudo=sudo)

    dnf.packages(ctx, ["git"])

    assert command.calls[0][1]["sudo"] is False
    assert sudo.refresh_count == 0


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

    assert command.calls[1][0] == [
        "dnf5",
        "group",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "desktop",
    ]
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

    assert command.calls[1][0] == [
        "dnf5",
        "group",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "base",
    ]


def test_at_prefixed_group_and_package_install_together(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n base\n"),
        response(1),
        response(0),
        response(0, "Installed groups:\n base\n multimedia\n"),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["@multimedia", "ripgrep"])

    assert command.calls[1][0] == ["rpm", "-q", "ripgrep"]
    assert command.calls[2][0] == [
        "dnf5",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "@multimedia",
        "ripgrep",
    ]
    assert command.calls[2][1]["sudo"] is True
    assert ctx.changes[0].data == {
        "added": ["@multimedia", "ripgrep"],
        "group": False,
        "groups": ["multimedia"],
        "packages": ["ripgrep"],
    }
    assert ctx.changes[0].summary == "+@multimedia +ripgrep"


def test_group_install_passes_weak_deps_and_excludes(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n base\n"),
        response(0),
        response(0, "Installed groups:\n base\n multimedia\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(
        ctx,
        ["multimedia"],
        group=True,
        exclude=["PackageKit-gstreamer-plugin"],
    )

    assert command.calls[1][0] == [
        "dnf5",
        "group",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "--exclude=PackageKit-gstreamer-plugin",
        "multimedia",
    ]


def test_package_install_passes_excludes(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["git"], exclude=["foo", "bar"])

    assert command.calls[1][0] == [
        "dnf5",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "--exclude=foo",
        "--exclude=bar",
        "git",
    ]


def test_at_prefixed_group_removal_uses_remove(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n multimedia\n"),
        response(0),
        response(0, "Installed groups:\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["@multimedia"], state="absent")

    assert command.calls[1][0] == ["dnf5", "remove", "-y", "@multimedia"]
    assert ctx.changes[0].data == {"removed": ["multimedia"], "group": True}


def test_group_true_removal_uses_group_remove(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n multimedia\n"),
        response(0),
        response(0, "Installed groups:\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["multimedia"], group=True, state="absent")

    assert command.calls[1][0] == ["dnf5", "group", "remove", "-y", "multimedia"]
    assert ctx.changes[0].data == {"removed": ["multimedia"], "group": True}


def test_exclude_is_rejected_for_absent(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="only supported"):
        dnf.packages(ctx, ["git"], state="absent", exclude=["foo"])


def test_hidden_group_is_detected_as_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "ID Name Installed\nmultimedia Multimedia yes\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["@multimedia"])

    assert [call[0] for call in command.calls] == [
        ["dnf5", "group", "list", "--installed", "--hidden"]
    ]
    assert ctx.changes == []


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


def test_swap_is_a_noop_when_replacement_is_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.swap(ctx, "ffmpeg-free", "ffmpeg")

    assert [call[0] for call in command.calls] == [["rpm", "-q", "ffmpeg"]]
    assert ctx.changes == []


def test_swap_replaces_package_and_verifies_result(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.swap(ctx, "ffmpeg-free", "ffmpeg", allowerasing=True)

    assert command.calls[1][0] == [
        "dnf5",
        "swap",
        "-y",
        "ffmpeg-free",
        "ffmpeg",
        "--allowerasing",
    ]
    assert command.calls[1][1]["sudo"] is True
    assert command.calls[2][0] == ["rpm", "-q", "ffmpeg"]
    assert ctx.changes[0].data == {"removed": "ffmpeg-free", "installed": "ffmpeg"}
    assert ctx.changes[0].summary == "swap ffmpeg-free -> ffmpeg"


def test_swap_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(1)]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing after swap"):
        dnf.swap(ctx, "ffmpeg-free", "ffmpeg")


def test_swap_rejects_empty_names(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="package to remove must not be empty"):
        dnf.swap(ctx, " ", "ffmpeg")
    with pytest.raises(TaskFailed, match="package to install must not be empty"):
        dnf.swap(ctx, "ffmpeg-free", " ")


def test_invalid_states_are_errors(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid package state"):
        dnf.packages(ctx, ["git"], state="unknown")
    with pytest.raises(TaskFailed, match="invalid COPR state"):
        dnf.copr(ctx, "owner/project", state="invalid")
    with pytest.raises(TaskFailed, match="invalid repository state"):
        dnf.repository(ctx, "https://example.test/tool.repo", state="invalid")


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


def test_rpm_release_installs_source(tmp_path):
    source = "https://example.test/tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    dnf.rpm(ctx, source)

    assert command.calls[0][0] == ["dnf5", "install", "-y", source]
    assert command.calls[0][1]["sudo"] is True
    assert ctx.changes[0].data == {"sources": [source]}


def test_rpm_release_resolves_bundle_relative_source(tmp_path):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "tool-release.rpm").write_text("rpm")
    command = FakeCommandRunner()
    ctx = bundle_context(tmp_path, bundle_dir, command)

    dnf.rpm(ctx, "tool-release.rpm")

    assert command.calls[0][0] == [
        "dnf5",
        "install",
        "-y",
        str(bundle_dir / "files" / "tool-release.rpm"),
    ]


def test_rpm_release_accepts_local_path(tmp_path):
    source = tmp_path / "tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    dnf.rpm(ctx, source)

    assert command.calls[0][0] == ["dnf5", "install", "-y", str(source)]
    assert ctx.changes[0].data == {"sources": [str(source)]}


def test_rpm_release_accepts_multiple_sources(tmp_path):
    url = "https://example.test/tool-release-2-1.noarch.rpm"
    local = tmp_path / "extra-release-1-1.noarch.rpm"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    dnf.rpm(ctx, [url, local])

    assert command.calls[0][0] == ["dnf5", "install", "-y", url, str(local)]
    assert ctx.changes[0].data == {"sources": [url, str(local)]}
    assert ctx.changes[0].summary == (
        "install tool-release-2-1.noarch.rpm, extra-release-1-1.noarch.rpm"
    )


def test_repository_present_requires_source(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="source is required"):
        dnf.repository(ctx)


def test_repository_absent_requires_name(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="name is required"):
        dnf.repository(ctx, "https://example.test/tool.repo", state="absent")


def test_empty_inputs_are_rejected(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="must not be empty"):
        dnf.packages(ctx, [])
    with pytest.raises(TaskFailed, match="must not be empty"):
        dnf.packages(ctx, "")
    with pytest.raises(TaskFailed, match="must not be empty"):
        dnf.packages(ctx, ["git", "  "])
    with pytest.raises(TaskFailed, match="group name must not be empty"):
        dnf.packages(ctx, ["@"])
    with pytest.raises(TaskFailed, match="excluded package must not be empty"):
        dnf.packages(ctx, ["git"], exclude=["  "])
    with pytest.raises(TaskFailed, match="COPR project must not be empty"):
        dnf.copr(ctx, "  ")
    with pytest.raises(TaskFailed, match="repository source must not be empty"):
        dnf.repository(ctx, "  ")
    with pytest.raises(TaskFailed, match="repository name must not be empty"):
        dnf.repository(ctx, state="absent", name="  ")
    with pytest.raises(TaskFailed, match="RPM source must not be empty"):
        dnf.rpm(ctx, "  ")
    with pytest.raises(TaskFailed, match="RPM sources must not be empty"):
        dnf.rpm(ctx, [])


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


def test_repository_resolves_bundle_relative_source(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "tool.repo").write_text("[tool]\n")
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = bundle_context(tmp_path, bundle_dir, command)

    dnf.repository(ctx, "tool.repo")

    source = bundle_dir / "files" / "tool.repo"
    assert command.calls[1][0] == [
        "dnf5",
        "config-manager",
        "addrepo",
        f"--from-repofile={source}",
        "--save-filename=tool.repo",
    ]


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
        dnf.rpm(ctx, tmp_path / "tool.rpm")
