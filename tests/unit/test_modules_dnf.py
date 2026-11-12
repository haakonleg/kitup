import stat

import pytest

from kitup.errors import TaskFailed
from kitup.modules import dnf

from .helpers import (
    FakeCommandRunner,
    FakeSudo,
    argvs,
    call_data,
    local_sudo_context,
    make_context,
    privileged_context,
    response,
)


@pytest.fixture(autouse=True)
def dnf5(monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "dnf5" if name == "dnf5" else None,
    )


def test_read_only_probes_do_not_request_sudo(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    ctx = make_context(tmp_path, command=command, sudo=sudo)

    dnf.packages(ctx, ["git"])

    assert command.calls[0][1]["sudo"] is False
    assert sudo.refresh_count == 0


def test_ensure_present_installs_only_missing_packages(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "git\nvim\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["git", "vim"])
    assert argvs(command) == [["rpm", "-q", "--queryformat", "%{NAME}\n", "git", "vim"]]
    assert ctx.changes == []

    command.responses = [
        response(1, "git\npackage vim is not installed\n"),
        response(0),
        response(0, "vim\n"),
    ]
    ctx = make_context(tmp_path, command=command)
    dnf.packages(ctx, ["git", "vim"])
    assert command.calls[2][0] == [
        "dnf5",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "vim",
    ]
    assert call_data(ctx.changes[0]) == {"added": ["vim"], "groups": [], "packages": ["vim"]}
    assert ctx.changes[0].action == "dnf.packages"
    assert ctx.changes[0].summary == "+vim"


def test_package_checks_use_a_single_rpm_query(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(1, "git\npackage vim is not installed\n"),
        response(0),
        response(0, "vim\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["git", "vim"])

    assert argvs(command) == [
        ["rpm", "-q", "--queryformat", "%{NAME}\n", "git", "vim"],
        ["dnf5", "install", "-y", "--setopt=install_weak_deps=False", "vim"],
        ["rpm", "-q", "--queryformat", "%{NAME}\n", "vim"],
    ]


def test_missing_packages_are_parsed_from_stderr(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="package git is not installed\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.packages(ctx, ["git"])

    assert ctx.changes[0].summary == "+git"


def test_package_query_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="rpmdb: cannot open database\n")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not query installed packages"):
        dnf.packages(ctx, ["git"])


def test_ensure_present_dry_run_does_not_install(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "package git is not installed\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.packages(ctx, ["git"])

    assert argvs(command) == [["rpm", "-q", "--queryformat", "%{NAME}\n", "git"]]
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
    assert call_data(ctx.changes[0]) == {"added": ["desktop"], "groups": ["desktop"], "packages": []}


def test_at_prefixed_group_and_package_install_together(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n base\n"),
        response(1, "package ripgrep is not installed\n"),
        response(0),
        response(0, "Installed groups:\n base\n multimedia\n"),
        response(0, "ripgrep\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["@multimedia", "ripgrep"])

    assert command.calls[1][0] == ["rpm", "-q", "--queryformat", "%{NAME}\n", "ripgrep"]
    assert command.calls[2][0] == [
        "dnf5",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "@multimedia",
        "ripgrep",
    ]
    assert command.calls[2][1]["sudo"] is True
    assert call_data(ctx.changes[0]) == {
        "added": ["@multimedia", "ripgrep"],
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
    command.responses = [
        response(1, "package git is not installed\n"),
        response(0),
        response(0, "git\n"),
    ]
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


def test_group_removal_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n multimedia\n"),
        response(0),
        response(0, "Installed groups:\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["@multimedia"], state="absent")
    assert command.calls[1][0] == ["dnf5", "remove", "-y", "@multimedia"]
    assert call_data(ctx.changes[0]) == {"removed": ["multimedia"], "groups": ["multimedia"], "packages": []}

    command.responses = [
        response(0, "Installed groups:\n multimedia\n"),
        response(0),
        response(0, "Installed groups:\n"),
    ]
    ctx = make_context(tmp_path, command=command)
    dnf.packages(ctx, ["multimedia"], group=True, state="absent")
    assert command.calls[4][0] == ["dnf5", "group", "remove", "-y", "multimedia"]
    assert call_data(ctx.changes[0]) == {"removed": ["multimedia"], "groups": ["multimedia"], "packages": []}


def test_exclude_is_rejected_for_absent(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="only supported"):
        dnf.packages(ctx, ["git"], state="absent", exclude=["foo"])


def test_hidden_group_is_detected_as_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "ID Name Installed\nmultimedia Multimedia yes\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["@multimedia"])

    assert argvs(command) == [["dnf5", "group", "list", "--installed", "--hidden"]]
    assert ctx.changes == []


def test_remove_removes_installed_packages(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "git\n"),
        response(0),
        response(1, "package git is not installed\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(ctx, ["git"], state="absent")

    assert command.calls[1][0] == ["dnf5", "remove", "-y", "git"]
    assert ctx.changes[0].summary == "-git"


def test_install_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(1, "package git is not installed\n"),
        response(0),
        response(1, "package git is not installed\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing"):
        dnf.packages(ctx, ["git"])


def test_swap_replaces_package_and_is_idempotent(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "ffmpeg\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.swap(ctx, "ffmpeg-free", "ffmpeg")
    assert argvs(command) == [["rpm", "-q", "--queryformat", "%{NAME}\n", "ffmpeg"]]
    assert ctx.changes == []

    command.responses = [
        response(1, "package ffmpeg is not installed\n"),
        response(0),
        response(0, "ffmpeg\n"),
    ]
    ctx = make_context(tmp_path, command=command)
    dnf.swap(ctx, "ffmpeg-free", "ffmpeg", allowerasing=True)
    assert command.calls[2][0] == [
        "dnf5",
        "swap",
        "-y",
        "ffmpeg-free",
        "ffmpeg",
        "--allowerasing",
    ]
    assert command.calls[2][1]["sudo"] is True
    assert command.calls[3][0] == ["rpm", "-q", "--queryformat", "%{NAME}\n", "ffmpeg"]
    assert call_data(ctx.changes[0]) == {"removed": "ffmpeg-free", "installed": "ffmpeg"}
    assert ctx.changes[0].summary == "swap ffmpeg-free -> ffmpeg"


def test_swap_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(1, "package ffmpeg is not installed\n"),
        response(0),
        response(1, "package ffmpeg is not installed\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing after swap"):
        dnf.swap(ctx, "ffmpeg-free", "ffmpeg")


def test_invalid_states_are_errors(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid package state"):
        dnf.packages(ctx, ["git"], state="unknown")
    with pytest.raises(TaskFailed, match="invalid package state"):
        dnf.packages(ctx, [""], state="unknown")
    with pytest.raises(TaskFailed, match="invalid COPR state"):
        dnf.copr(ctx, "owner/project", state="invalid")
    with pytest.raises(TaskFailed, match="invalid repository state"):
        dnf.repository(ctx, "https://example.test/tool.repo", state="invalid")


def test_autoremove_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "")]
    ctx = make_context(tmp_path, command=command)

    dnf.autoremove(ctx)
    assert argvs(command) == [
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
    assert ctx.calls[0].summary == "ensure no unneeded packages"

    command.responses = [response(0, "libfoo\n libbar \n"), response(0)]
    ctx = make_context(tmp_path, command=command)
    dnf.autoremove(ctx)
    assert command.calls[2][0] == ["dnf5", "autoremove", "-y"]
    assert command.calls[2][1]["sudo"] is True
    assert call_data(ctx.changes[0]) == {"removed": ["libfoo", "libbar"]}
    assert ctx.changes[0].summary == "-libfoo -libbar"


def test_copr_enable_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "owner/project\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.copr(ctx, "owner/project")
    assert argvs(command) == [["dnf5", "copr", "list"]]
    assert ctx.changes == []

    command.responses = [response(0, ""), response(0)]
    ctx = make_context(tmp_path, command=command)
    dnf.copr(ctx, "owner/project")
    assert command.calls[2][0] == [
        "dnf5",
        "copr",
        "enable",
        "-y",
        "owner/project",
    ]
    assert call_data(ctx.changes[0]) == {"repository": "owner/project"}


def test_copr_absent_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "owner/project\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.copr(ctx, "owner/project", state="absent")
    assert command.calls[1][0] == ["dnf5", "copr", "remove", "owner/project"]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].summary == "remove COPR owner/project"

    command.responses = [response(0, "")]
    ctx = make_context(tmp_path, command=command)
    dnf.copr(ctx, "owner/project", state="absent")
    assert command.calls[2][0] == ["dnf5", "copr", "list"]
    assert ctx.changes == []


def test_rpm_release_scenarios(tmp_path):
    source = "https://example.test/tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    result = dnf.rpm(ctx, source)
    assert command.calls[0][0] == [
        "dnf5",
        "repoquery",
        "--queryformat",
        "%{name}\n",
        source,
    ]
    assert command.calls[1][0] == ["rpm", "-q", "--queryformat", "%{NAME}\n", "tool-release"]
    assert command.calls[2][0] == ["dnf5", "install", "-y", source]
    assert command.calls[2][1]["sudo"] is True
    assert call_data(result) == {"installed": ["tool-release"]}

    command.responses = [response(0, "tool-release\n"), response(0, "tool-release\n")]
    ctx = make_context(tmp_path, command=command)
    result = dnf.rpm(ctx, source)
    assert command.calls[3][0] == [
        "dnf5",
        "repoquery",
        "--queryformat",
        "%{name}\n",
        source,
    ]
    assert result.changed is False
    assert call_data(result) == {"installed": []}
    assert ctx.changes == []

    command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    ctx = make_context(tmp_path, dry_run=True, command=command)
    result = dnf.rpm(ctx, source)
    assert command.calls[6][0] == ["rpm", "-q", "--queryformat", "%{NAME}\n", "tool-release"]
    assert result.changed is True
    assert call_data(result) == {"installed": ["tool-release"]}


def test_rpm_release_does_not_use_bundle_file_discovery(tmp_path):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "tool-release.rpm").write_text("rpm")
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.rpm(ctx, "tool-release.rpm")

    assert command.calls[2][0] == ["dnf5", "install", "-y", "tool-release.rpm"]


def test_rpm_release_accepts_local_path(tmp_path):
    source = tmp_path / "tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    result = dnf.rpm(ctx, source)

    assert command.calls[2][0] == ["dnf5", "install", "-y", str(source)]
    assert call_data(result) == {"installed": ["tool-release"]}


def test_rpm_release_accepts_multiple_sources(tmp_path):
    url = "https://example.test/tool-release-2-1.noarch.rpm"
    local = tmp_path / "extra-release-1-1.noarch.rpm"
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\nextra-release\n"),
        response(1, "package tool-release is not installed\npackage extra-release is not installed\n"),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    result = dnf.rpm(ctx, [url, local])

    assert command.calls[2][0] == ["dnf5", "install", "-y", url, str(local)]
    assert call_data(result) == {"installed": ["tool-release", "extra-release"]}
    assert result.summary == ("install tool-release-2-1.noarch.rpm, extra-release-1-1.noarch.rpm")


def test_repository_requires_arguments(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="source is required"):
        dnf.repository(ctx)
    with pytest.raises(TaskFailed, match="name is required"):
        dnf.repository(ctx, "https://example.test/tool.repo", state="absent")


def test_repository_present_scenarios(tmp_path):
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
    assert call_data(ctx.changes[0]) == {
        "repository": "https://example.test/tool.repo",
        "path": str(reposdir / "tool.repo"),
    }

    (reposdir / "tool.repo").write_text("[tool]\n", encoding="utf-8")
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, command=command)
    dnf.repository(ctx, "https://example.test/tool.repo")
    assert command.calls[2][0] == ["dnf5", "--dump-main-config"]
    assert ctx.changes == []


def test_repository_does_not_use_bundle_file_discovery(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "tool.repo").write_text("[tool]\n")
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(ctx, "tool.repo")

    assert command.calls[1][0] == [
        "dnf5",
        "config-manager",
        "addrepo",
        "--from-repofile=tool.repo",
        "--save-filename=tool.repo",
    ]


def test_repository_absent_scenarios(tmp_path):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    repo_file = reposdir / "tool.repo"
    repo_file.write_text("[tool]\n", encoding="utf-8")
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(ctx, state="absent", name="tool.repo")
    assert command.calls[1][0] == ["rm", "-f", str(repo_file)]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].summary == "remove repository tool.repo"

    repo_file.unlink()
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, command=command)
    dnf.repository(ctx, state="absent", name="tool.repo")
    assert command.calls[2][0] == ["dnf5", "--dump-main-config"]
    assert ctx.changes == []


def test_config_sets_option_and_is_idempotent(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\ninstallonly_limit = 3\n")
    ctx = local_sudo_context(tmp_path)

    result = dnf.config(ctx, "best", value=True, path=path)

    assert path.read_text() == "[main]\ninstallonly_limit = 3\nbest = True\n"
    assert result.action == "dnf.config"
    assert result.changed is True
    assert result.summary == f"set best in [main] of {path}"
    assert call_data(result) == {
        "path": str(path),
        "section": "main",
        "option": "best",
        "value": "True",
        "ini_action": "added",
    }

    result = dnf.config(ctx, "best", value=True, path=path)
    assert result.changed is False
    assert result.summary == f"ensure best is True in [main] of {path}"
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(ctx.changes) == 1


def test_config_replaces_existing_option_value(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\nbest = 1\n")
    ctx = local_sudo_context(tmp_path)

    result = dnf.config(ctx, "best", value=True, path=path)

    assert path.read_text() == "[main]\nbest = True\n"
    assert result.changed is True
    assert call_data(result)["ini_action"] == "replaced"

    result = dnf.config(ctx, "best", value=True, path=path)
    assert result.changed is False


def test_config_absent_removes_option_and_is_idempotent(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\nbest = True\nkeep = 1\n")
    ctx = local_sudo_context(tmp_path)

    result = dnf.config(ctx, "best", state="absent", path=path)

    assert path.read_text() == "[main]\nkeep = 1\n"
    assert result.changed is True
    assert result.summary == f"remove best from [main] of {path}"
    assert call_data(result) == {
        "path": str(path),
        "section": "main",
        "option": "best",
        "value": None,
        "ini_action": "removed",
    }

    result = dnf.config(ctx, "best", state="absent", path=path)
    assert result.changed is False
    assert result.summary == f"ensure best is absent from [main] of {path}"
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(ctx.changes) == 1


def test_config_creates_a_missing_file(tmp_path):
    path = tmp_path / "dnf.conf"
    ctx = local_sudo_context(tmp_path)

    result = dnf.config(ctx, "gpgcheck", value=True, path=path)

    assert path.read_text() == "[main]\ngpgcheck = True\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert call_data(result)["ini_action"] == "added"


def test_config_edits_a_custom_section(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\na = 1\n[other]\nb = 2\n")
    ctx = local_sudo_context(tmp_path)

    result = dnf.config(ctx, "c", "3", section="other", path=path)

    assert path.read_text() == "[main]\na = 1\n[other]\nb = 2\nc = 3\n"
    assert result.summary == f"set c in [other] of {path}"


def test_config_normalizes_typed_values(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\n")
    ctx = local_sudo_context(tmp_path)

    dnf.config(ctx, "installonly_limit", 5, path=path)
    dnf.config(ctx, "excludepkgs", ["foo", "bar", "foo"], path=path)
    dnf.config(ctx, "best", value=False, path=path)
    dnf.config(ctx, "releasever", "42", path=path)

    assert path.read_text() == (
        "[main]\ninstallonly_limit = 5\nexcludepkgs = foo, bar\nbest = False\nreleasever = 42\n"
    )


def test_config_dry_run_reports_without_writing(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\nbest = False\n")
    ctx = local_sudo_context(tmp_path, dry_run=True)

    result = dnf.config(ctx, "best", value=True, path=path)

    assert result.changed is True
    assert result.summary == f"set best in [main] of {path}"
    assert path.read_text() == "[main]\nbest = False\n"
    assert len(ctx.changes) == 1


def test_config_validates_arguments_before_touching_the_file(tmp_path):
    ctx = local_sudo_context(tmp_path)
    missing = tmp_path / "missing.conf"

    with pytest.raises(TaskFailed, match="requires a value"):
        dnf.config(ctx, "best", path=missing)
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        dnf.config(ctx, "best", "1", path=missing, state="absent")
    with pytest.raises(TaskFailed, match="invalid config state"):
        dnf.config(ctx, "best", "1", path=missing, state="maybe")
    with pytest.raises(TaskFailed, match="must be a boolean"):
        dnf.config(ctx, "best", "1", path=missing, sudo="yes")
    with pytest.raises(TaskFailed, match="ini option must not be empty"):
        dnf.config(ctx, "  ", "1", path=missing)
    with pytest.raises(TaskFailed, match="config path must not be empty"):
        dnf.config(ctx, "best", "1", path="")
    with pytest.raises(TaskFailed, match="control characters"):
        dnf.config(ctx, "best", "a\nb", path=missing)
    with pytest.raises(TaskFailed, match="unsupported config value type"):
        dnf.config(ctx, "best", 1.5, path=missing)
    with pytest.raises(TaskFailed, match="must be strings"):
        dnf.config(ctx, "best", ["a", 1], path=missing)
    with pytest.raises(TaskFailed, match="config value must not be empty"):
        dnf.config(ctx, "best", [], path=missing)
    with pytest.raises(TaskFailed, match="must be a sequence"):
        dnf.config(ctx, "best", {"a", "b"}, path=missing)

    assert not missing.exists()
    assert ctx.changes == []


def test_config_defaults_to_etc_dnf_conf(tmp_path, monkeypatch):
    target = tmp_path / "dnf.conf"
    monkeypatch.setattr(dnf, "_DEFAULT_CONFIG_PATH", target)
    ctx = local_sudo_context(tmp_path)

    result = dnf.config(ctx, "best", value=True)

    assert target.read_text() == "[main]\nbest = True\n"
    assert result["path"] == str(target)
    assert result["section"] == "main"


def test_config_uses_privileged_worker_when_sudo(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\nbest = False\n")
    ctx = privileged_context(tmp_path)

    try:
        result = dnf.config(ctx, "best", value=True, path=path)
        assert result.changed is True
        assert path.read_text() == "[main]\nbest = True\n"

        result = dnf.config(ctx, "best", value=True, path=path)
        assert result.changed is False
        assert ctx.sudo.refresh_count > 0
    finally:
        ctx.close()


def test_config_user_scoped_file_without_sudo(tmp_path):
    path = tmp_path / "user-dnf.conf"
    ctx = make_context(tmp_path)

    result = dnf.config(ctx, "keepcache", value=True, path=path, sudo=False)

    assert result.changed is True
    assert path.read_text() == "[main]\nkeepcache = True\n"
    assert ctx.sudo.refresh_count == 0


def test_config_rejects_directories_symlinks_and_non_utf8_files(tmp_path):
    ctx = local_sudo_context(tmp_path)

    with pytest.raises(TaskFailed, match="directory"):
        dnf.config(ctx, "best", "1", path=tmp_path)

    target = tmp_path / "target.conf"
    target.write_text("[main]\nbest = False\n")
    link = tmp_path / "link.conf"
    link.symlink_to(target)
    with pytest.raises(TaskFailed, match="symlink"):
        dnf.config(ctx, "best", "1", path=link)

    binary = tmp_path / "binary.conf"
    binary.write_bytes(b"[main]\n\xff\xfe\n")
    with pytest.raises(TaskFailed, match="UTF-8"):
        dnf.config(ctx, "best", "1", path=binary)

    assert target.read_text() == "[main]\nbest = False\n"
