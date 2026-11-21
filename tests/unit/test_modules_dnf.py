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


def test_present_install_scenarios(tmp_path):
    # read-only probes never request sudo, even when a change follows
    command = FakeCommandRunner()
    sudo = FakeSudo()
    command.responses = [response(0, "git\nvim\n")]
    ctx = make_context(tmp_path, command=command, sudo=sudo)

    dnf.packages(["git", "vim"])

    assert command.calls[0][1]["sudo"] is False
    assert sudo.refresh_count == 0
    assert argvs(command) == [["rpm", "-q", "--queryformat", "%{NAME}\n", "git", "vim"]]
    assert ctx.changes == []

    # only the missing packages are installed
    command = FakeCommandRunner()
    command.responses = [
        response(1, "git\npackage vim is not installed\n"),
        response(0),
        response(0, "vim\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(["git", "vim"])

    assert command.calls[1][0] == [
        "dnf5",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "vim",
    ]
    assert call_data(ctx.changes[0]) == {"added": ["vim"], "groups": [], "packages": ["vim"]}
    assert ctx.changes[0].action == "dnf.packages"
    assert ctx.changes[0].summary == "+vim"

    # dry run reports the pending install without running dnf
    command = FakeCommandRunner()
    command.responses = [response(1, "package git is not installed\n")]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    dnf.packages(["git"])

    assert argvs(command) == [["rpm", "-q", "--queryformat", "%{NAME}\n", "git"]]
    assert ctx.changes[0].summary == "+git"

    # excludes are forwarded to dnf
    command = FakeCommandRunner()
    command.responses = [
        response(1, "package git is not installed\n"),
        response(0),
        response(0, "git\n"),
    ]
    make_context(tmp_path, command=command)

    dnf.packages(["git"], exclude=["foo", "bar"])

    assert command.calls[1][0] == [
        "dnf5",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "--exclude=foo",
        "--exclude=bar",
        "git",
    ]


def test_group_install_scenarios(tmp_path):
    # a missing group is installed with the group subcommand
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n base\n"),
        response(0),
        response(0, "Installed groups:\n base\n desktop\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(["desktop"], group=True)

    assert command.calls[1][0] == [
        "dnf5",
        "group",
        "install",
        "-y",
        "--setopt=install_weak_deps=False",
        "desktop",
    ]
    assert call_data(ctx.changes[0]) == {"added": ["desktop"], "groups": ["desktop"], "packages": []}

    # an @-prefixed group and a plain package install together in one transaction
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n base\n"),
        response(1, "package ripgrep is not installed\n"),
        response(0),
        response(0, "Installed groups:\n base\n multimedia\n"),
        response(0, "ripgrep\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(["@multimedia", "ripgrep"])

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

    # hidden groups still count as installed
    command = FakeCommandRunner()
    command.responses = [response(0, "ID Name Installed\nmultimedia Multimedia yes\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(["@multimedia"])

    assert argvs(command) == [["dnf5", "group", "list", "--installed", "--hidden"]]
    assert ctx.changes == []


def test_failure_scenarios(tmp_path):
    # a failed package query is reported
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="rpmdb: cannot open database\n")]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not query installed packages"):
        dnf.packages(["git"])

    # an install that leaves the package missing fails
    command = FakeCommandRunner()
    command.responses = [
        response(1, "package git is not installed\n"),
        response(0),
        response(1, "package git is not installed\n"),
    ]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing"):
        dnf.packages(["git"])

    # a swap that leaves the package missing fails
    command = FakeCommandRunner()
    command.responses = [
        response(1, "package ffmpeg is not installed\n"),
        response(0),
        response(1, "package ffmpeg is not installed\n"),
    ]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing after swap"):
        dnf.swap("ffmpeg-free", "ffmpeg")


def test_remove_scenarios(tmp_path):
    # @-prefixed groups are removed with a plain dnf remove
    command = FakeCommandRunner()
    command.responses = [
        response(0, "Installed groups:\n multimedia\n"),
        response(0),
        response(0, "Installed groups:\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(["@multimedia"], state="absent")
    assert command.calls[1][0] == ["dnf5", "remove", "-y", "@multimedia"]
    assert call_data(ctx.changes[0]) == {"removed": ["multimedia"], "groups": ["multimedia"], "packages": []}

    # group=True uses the group subcommand instead
    command.responses = [
        response(0, "Installed groups:\n multimedia\n"),
        response(0),
        response(0, "Installed groups:\n"),
    ]
    ctx = make_context(tmp_path, command=command)
    dnf.packages(["multimedia"], group=True, state="absent")
    assert command.calls[4][0] == ["dnf5", "group", "remove", "-y", "multimedia"]
    assert call_data(ctx.changes[0]) == {"removed": ["multimedia"], "groups": ["multimedia"], "packages": []}

    # installed packages are removed
    command = FakeCommandRunner()
    command.responses = [
        response(0, "git\n"),
        response(0),
        response(1, "package git is not installed\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    dnf.packages(["git"], state="absent")

    assert command.calls[1][0] == ["dnf5", "remove", "-y", "git"]
    assert ctx.changes[0].summary == "-git"


def test_swap_replaces_package_and_is_idempotent(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "ffmpeg\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.swap("ffmpeg-free", "ffmpeg")
    assert argvs(command) == [["rpm", "-q", "--queryformat", "%{NAME}\n", "ffmpeg"]]
    assert ctx.changes == []

    command.responses = [
        response(1, "package ffmpeg is not installed\n"),
        response(0),
        response(0, "ffmpeg\n"),
    ]
    ctx = make_context(tmp_path, command=command)
    dnf.swap("ffmpeg-free", "ffmpeg", allowerasing=True)
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


def test_invalid_arguments_are_errors(tmp_path):
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="only supported"):
        dnf.packages(["git"], state="absent", exclude=["foo"])
    with pytest.raises(TaskFailed, match="invalid package state"):
        dnf.packages(["git"], state="unknown")
    with pytest.raises(TaskFailed, match="invalid package state"):
        dnf.packages([""], state="unknown")
    with pytest.raises(TaskFailed, match="invalid COPR state"):
        dnf.copr("owner/project", state="invalid")
    with pytest.raises(TaskFailed, match="invalid repository state"):
        dnf.repository("https://example.test/tool.repo", state="invalid")


def test_autoremove_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "")]
    ctx = make_context(tmp_path, command=command)

    dnf.autoremove()
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
    dnf.autoremove()
    assert command.calls[2][0] == ["dnf5", "autoremove", "-y"]
    assert command.calls[2][1]["sudo"] is True
    assert call_data(ctx.changes[0]) == {"removed": ["libfoo", "libbar"]}
    assert ctx.changes[0].summary == "-libfoo -libbar"


def test_copr_scenarios(tmp_path):
    # an already enabled COPR is a no-op
    command = FakeCommandRunner()
    command.responses = [response(0, "owner/project\n")]
    ctx = make_context(tmp_path, command=command)

    dnf.copr("owner/project")
    assert argvs(command) == [["dnf5", "copr", "list"]]
    assert ctx.changes == []

    # a missing COPR is enabled
    command = FakeCommandRunner()
    command.responses = [response(0, ""), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.copr("owner/project")
    assert command.calls[1][0] == [
        "dnf5",
        "copr",
        "enable",
        "-y",
        "owner/project",
    ]
    assert call_data(ctx.changes[0]) == {"repository": "owner/project"}

    # an enabled COPR is removed
    command = FakeCommandRunner()
    command.responses = [response(0, "owner/project\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.copr("owner/project", state="absent")
    assert command.calls[1][0] == ["dnf5", "copr", "remove", "owner/project"]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].summary == "remove COPR owner/project"

    # removing an absent COPR is a no-op
    command = FakeCommandRunner()
    command.responses = [response(0, "")]
    ctx = make_context(tmp_path, command=command)

    dnf.copr("owner/project", state="absent")
    assert argvs(command) == [["dnf5", "copr", "list"]]
    assert ctx.changes == []


def test_rpm_scenarios(tmp_path):
    source = "https://example.test/tool-release-2-1.noarch.rpm"

    # a missing release package is installed
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    make_context(tmp_path, command=command)

    result = dnf.rpm(source)
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

    # an already installed release package is a no-op
    command = FakeCommandRunner()
    command.responses = [response(0, "tool-release\n"), response(0, "tool-release\n")]
    ctx = make_context(tmp_path, command=command)

    result = dnf.rpm(source)
    assert argvs(command) == [
        [
            "dnf5",
            "repoquery",
            "--queryformat",
            "%{name}\n",
            source,
        ],
        ["rpm", "-q", "--queryformat", "%{NAME}\n", "tool-release"],
    ]
    assert result.changed is False
    assert call_data(result) == {"installed": []}
    assert ctx.changes == []

    # dry run reports the install without running dnf
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    make_context(tmp_path, dry_run=True, command=command)

    result = dnf.rpm(source)
    assert argvs(command) == [
        [
            "dnf5",
            "repoquery",
            "--queryformat",
            "%{name}\n",
            source,
        ],
        ["rpm", "-q", "--queryformat", "%{NAME}\n", "tool-release"],
    ]
    assert result.changed is True
    assert call_data(result) == {"installed": ["tool-release"]}

    # a local RPM path is passed through verbatim
    local = tmp_path / "tool-release-2-1.noarch.rpm"
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    make_context(tmp_path, command=command)

    result = dnf.rpm(local)

    assert command.calls[2][0] == ["dnf5", "install", "-y", str(local)]
    assert call_data(result) == {"installed": ["tool-release"]}

    # multiple URL and local sources install in one transaction
    url = "https://example.test/tool-release-2-1.noarch.rpm"
    extra = tmp_path / "extra-release-1-1.noarch.rpm"
    command = FakeCommandRunner()
    command.responses = [
        response(0, "tool-release\nextra-release\n"),
        response(1, "package tool-release is not installed\npackage extra-release is not installed\n"),
        response(0),
    ]
    make_context(tmp_path, command=command)

    result = dnf.rpm([url, extra])

    assert command.calls[2][0] == ["dnf5", "install", "-y", url, str(extra)]
    assert call_data(result) == {"installed": ["tool-release", "extra-release"]}
    assert result.summary == "install tool-release-2-1.noarch.rpm, extra-release-1-1.noarch.rpm"


def test_repository_scenarios(tmp_path):
    # required arguments are validated
    make_context(tmp_path)
    with pytest.raises(TaskFailed, match="source is required"):
        dnf.repository()
    with pytest.raises(TaskFailed, match="name is required"):
        dnf.repository("https://example.test/tool.repo", state="absent")

    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()

    # a missing repository file is added, then an existing one is a no-op
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.repository("https://example.test/tool.repo")
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
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, command=command)
    dnf.repository("https://example.test/tool.repo")
    assert argvs(command) == [["dnf5", "--dump-main-config"]]
    assert ctx.changes == []

    # an existing repository file is removed, then a missing one is a no-op
    repo_file = reposdir / "tool.repo"
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    dnf.repository(state="absent", name="tool.repo")
    assert command.calls[1][0] == ["rm", "-f", str(repo_file)]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].summary == "remove repository tool.repo"

    repo_file.unlink()
    command = FakeCommandRunner()
    command.responses = [response(0, f"reposdir = {reposdir}\n")]
    ctx = make_context(tmp_path, command=command)
    dnf.repository(state="absent", name="tool.repo")
    assert argvs(command) == [["dnf5", "--dump-main-config"]]
    assert ctx.changes == []


def test_config_set_absent_and_dry_run(tmp_path):
    # set an option and confirm idempotency
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\ninstallonly_limit = 3\n")
    ctx = local_sudo_context(tmp_path)

    result = dnf.config("best", value=True, path=path)

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

    result = dnf.config("best", value=True, path=path)
    assert result.changed is False
    assert result.summary == f"ensure best is True in [main] of {path}"
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(ctx.changes) == 1

    # remove an option and confirm idempotency
    absent_path = tmp_path / "absent.conf"
    absent_path.write_text("[main]\nbest = True\nkeep = 1\n")
    absent_ctx = local_sudo_context(tmp_path)

    result = dnf.config("best", state="absent", path=absent_path)

    assert absent_path.read_text() == "[main]\nkeep = 1\n"
    assert result.changed is True
    assert result.summary == f"remove best from [main] of {absent_path}"
    assert call_data(result) == {
        "path": str(absent_path),
        "section": "main",
        "option": "best",
        "value": None,
        "ini_action": "removed",
    }

    result = dnf.config("best", state="absent", path=absent_path)
    assert result.changed is False
    assert result.summary == f"ensure best is absent from [main] of {absent_path}"
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(absent_ctx.changes) == 1

    # dry run reports the change without writing
    dry_path = tmp_path / "dry.conf"
    dry_path.write_text("[main]\nbest = False\n")
    dry_ctx = local_sudo_context(tmp_path, dry_run=True)

    result = dnf.config("best", value=True, path=dry_path)

    assert result.changed is True
    assert result.summary == f"set best in [main] of {dry_path}"
    assert dry_path.read_text() == "[main]\nbest = False\n"
    assert len(dry_ctx.changes) == 1


def test_config_creates_missing_file_and_normalizes_values(tmp_path):
    # a missing file is created with the default mode
    path = tmp_path / "new.conf"
    local_sudo_context(tmp_path)

    result = dnf.config("gpgcheck", value=True, path=path)

    assert path.read_text() == "[main]\ngpgcheck = True\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert call_data(result)["ini_action"] == "added"

    # typed values are normalized before writing
    typed = tmp_path / "typed.conf"
    typed.write_text("[main]\n")
    local_sudo_context(tmp_path)

    dnf.config("installonly_limit", 5, path=typed)
    dnf.config("excludepkgs", ["foo", "bar", "foo"], path=typed)
    dnf.config("best", value=False, path=typed)
    dnf.config("releasever", "42", path=typed)

    assert typed.read_text() == (
        "[main]\ninstallonly_limit = 5\nexcludepkgs = foo, bar\nbest = False\nreleasever = 42\n"
    )


def test_config_validates_arguments_before_touching_the_file(tmp_path):
    ctx = local_sudo_context(tmp_path)
    missing = tmp_path / "missing.conf"

    with pytest.raises(TaskFailed, match="requires a value"):
        dnf.config("best", path=missing)
    with pytest.raises(TaskFailed, match="state 'absent' does not take a value"):
        dnf.config("best", "1", path=missing, state="absent")
    with pytest.raises(TaskFailed, match="invalid config state"):
        dnf.config("best", "1", path=missing, state="maybe")
    with pytest.raises(TaskFailed, match="must be a boolean"):
        dnf.config("best", "1", path=missing, sudo="yes")
    with pytest.raises(TaskFailed, match="ini option must not be empty"):
        dnf.config("  ", "1", path=missing)
    with pytest.raises(TaskFailed, match="config path must not be empty"):
        dnf.config("best", "1", path="")
    with pytest.raises(TaskFailed, match="control characters"):
        dnf.config("best", "a\nb", path=missing)
    with pytest.raises(TaskFailed, match="unsupported config value type"):
        dnf.config("best", 1.5, path=missing)
    with pytest.raises(TaskFailed, match="must be strings"):
        dnf.config("best", ["a", 1], path=missing)
    with pytest.raises(TaskFailed, match="config value must not be empty"):
        dnf.config("best", [], path=missing)
    with pytest.raises(TaskFailed, match="must be a sequence"):
        dnf.config("best", {"a", "b"}, path=missing)

    assert not missing.exists()
    assert ctx.changes == []


def test_config_defaults_to_etc_dnf_conf(tmp_path, monkeypatch):
    target = tmp_path / "dnf.conf"
    monkeypatch.setattr(dnf, "_DEFAULT_CONFIG_PATH", target)
    local_sudo_context(tmp_path)

    result = dnf.config("best", value=True)

    assert target.read_text() == "[main]\nbest = True\n"
    assert result["path"] == str(target)
    assert result["section"] == "main"


def test_config_uses_privileged_worker_when_sudo(tmp_path):
    path = tmp_path / "dnf.conf"
    path.write_text("[main]\nbest = False\n")
    ctx = privileged_context(tmp_path)

    try:
        result = dnf.config("best", value=True, path=path)
        assert result.changed is True
        assert path.read_text() == "[main]\nbest = True\n"

        result = dnf.config("best", value=True, path=path)
        assert result.changed is False
        assert ctx.sudo.refresh_count > 0
    finally:
        ctx.close()


def test_config_rejects_directories_symlinks_and_non_utf8_files(tmp_path):
    local_sudo_context(tmp_path)

    with pytest.raises(TaskFailed, match="directory"):
        dnf.config("best", "1", path=tmp_path)

    target = tmp_path / "target.conf"
    target.write_text("[main]\nbest = False\n")
    link = tmp_path / "link.conf"
    link.symlink_to(target)
    with pytest.raises(TaskFailed, match="symlink"):
        dnf.config("best", "1", path=link)

    binary = tmp_path / "binary.conf"
    binary.write_bytes(b"[main]\n\xff\xfe\n")
    with pytest.raises(TaskFailed, match="UTF-8"):
        dnf.config("best", "1", path=binary)

    assert target.read_text() == "[main]\nbest = False\n"
