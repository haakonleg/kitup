import json
import stat

import pytest

from kitup.errors import TaskFailed
from kitup.modules import pip

from .helpers import (
    FakeCommandRunner,
    FakeSudo,
    argvs,
    call_data,
    completed,
    local_sudo_context,
    make_context,
    response,
)


@pytest.fixture(autouse=True)
def system_python(monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.pip.shutil.which",
        lambda name: {"python3": "/usr/bin/python3", "python": "/usr/bin/python"}.get(name),
    )


def report(names: list[str]):
    """Build a scripted ``pip install --dry-run --report`` response (stable format)."""
    return response(0, json.dumps({"version": "1", "install": [{"metadata": {"name": name}} for name in names]}))


def listing(names: list[str]):
    """Build a scripted ``pip list --format=json`` response."""
    return response(0, json.dumps([{"name": name, "version": "1.0"} for name in names]))


def make_venv(venv) -> None:
    """Create a minimal fake virtualenv layout on disk."""
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("", encoding="utf-8")


def test_probes_stay_unprivileged_without_sudo(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report([])]
    sudo = FakeSudo()
    make_context(tmp_path, command=command, sudo=sudo)

    result = pip.packages(["requests"])

    assert argvs(command) == [
        ["/usr/bin/python3", "-m", "pip", "install", "--dry-run", "--quiet", "--report", "-", "requests"]
    ]
    assert command.calls[0][1]["sudo"] is False
    assert sudo.refresh_count == 0
    assert result.action == "pip.packages"
    assert result.changed is False
    assert result.summary == "ensure packages are present: requests"
    assert call_data(result) == {"added": [], "packages": ["requests"]}


def test_present_install_scenarios(tmp_path):
    # everything the report lists is installed, including transitive dependencies
    command = FakeCommandRunner()
    command.responses = [report(["requests", "urllib3"]), response(0), report([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests"])

    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "install", "requests"]
    assert result.changed is True
    assert result.summary == "+requests +urllib3"
    assert call_data(result) == {"added": ["requests", "urllib3"], "packages": ["requests"]}

    # all requested packages are installed in one command
    command = FakeCommandRunner()
    command.responses = [report(["requests", "urllib3"]), response(0), report([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests", "urllib3"])

    assert argvs(command)[1] == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "install",
        "requests",
        "urllib3",
    ]
    assert result.changed is True
    assert result.summary == "+requests +urllib3"
    assert call_data(result) == {"added": ["requests", "urllib3"], "packages": ["requests", "urllib3"]}

    # a version mismatch is reinstalled
    command = FakeCommandRunner()
    command.responses = [report(["foo"]), response(0), report([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["foo==1.0"])

    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "install", "foo==1.0"]
    assert call_data(result)["added"] == ["foo"]

    # dry run reports the pending install without installing
    command = FakeCommandRunner()
    command.responses = [report(["requests"])]
    make_context(tmp_path, dry_run=True, command=command)

    result = pip.packages(["requests"])

    assert len(command.calls) == 1
    assert result.changed is True
    assert result.summary == "+requests"


def test_present_venv_scenarios(tmp_path):
    # a missing venv in dry-run reports creation without running anything
    venv = tmp_path / "dry-venv"
    command = FakeCommandRunner()
    make_context(tmp_path, dry_run=True, command=command)

    result = pip.packages(["requests"], venv=venv)

    assert command.calls == []
    assert result.changed is True
    assert result.summary == f"create venv {venv}; +requests"
    assert call_data(result) == {
        "added": ["requests"],
        "packages": ["requests"],
        "venv": str(venv),
    }

    # a missing venv is created before the probes run
    created = tmp_path / "created-venv"

    def create_venv(argv):
        make_venv(created)
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [create_venv, report(["requests"]), response(0), report([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests"], venv=created)

    assert argvs(command)[0] == ["/usr/bin/python3", "-m", "venv", str(created)]
    assert argvs(command)[1][:3] == [str(created / "bin" / "python"), "-m", "pip"]
    assert argvs(command)[2] == [str(created / "bin" / "python"), "-m", "pip", "install", "requests"]
    assert result.summary == f"create venv {created}; +requests"
    assert call_data(result)["venv"] == str(created)

    # an existing venv uses its own pip
    existing = tmp_path / "existing-venv"
    make_venv(existing)
    command = FakeCommandRunner()
    command.responses = [report([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests"], venv=existing)

    assert argvs(command) == [
        [str(existing / "bin" / "python"), "-m", "pip", "install", "--dry-run", "--quiet", "--report", "-", "requests"]
    ]
    assert result.changed is False

    # a directory with bin/python but no pyvenv.cfg is not a virtualenv
    not_a_venv = tmp_path / "not-a-venv"
    (not_a_venv / "bin").mkdir(parents=True)
    (not_a_venv / "bin" / "python").write_text("", encoding="utf-8")
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="not a virtualenv"):
        pip.packages(["requests"], venv=not_a_venv)
    with pytest.raises(TaskFailed, match="not a virtualenv"):
        pip.packages(["requests"], state="absent", venv=not_a_venv)


def test_absent_reconciliation_scenarios(tmp_path):
    # only installed packages are removed
    command = FakeCommandRunner()
    command.responses = [listing(["requests", "charset-normalizer"]), response(0), listing([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests", "urllib3"], state="absent")

    assert argvs(command)[0] == ["/usr/bin/python3", "-m", "pip", "list", "--local", "--format=json"]
    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "uninstall", "-y", "requests"]
    assert result.changed is True
    assert result.summary == "-requests"
    assert call_data(result) == {"removed": ["requests"], "packages": ["requests", "urllib3"]}

    # nothing installed is a no-op
    command = FakeCommandRunner()
    command.responses = [listing(["pip"])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests"], state="absent")

    assert len(command.calls) == 1
    assert result.changed is False
    assert result.summary == "ensure packages are absent: requests"
    assert call_data(result) == {"removed": [], "packages": ["requests"]}

    # dry run reports the removal without uninstalling
    command = FakeCommandRunner()
    command.responses = [listing(["requests"])]
    make_context(tmp_path, dry_run=True, command=command)

    result = pip.packages(["requests"], state="absent")

    assert len(command.calls) == 1
    assert result.changed is True
    assert result.summary == "-requests"

    # absent against a missing venv is a no-op
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    result = pip.packages(["requests"], state="absent", venv=tmp_path / "venv")

    assert command.calls == []
    assert result.changed is False
    assert result.summary == "ensure packages are absent: requests"

    # version specifiers are stripped for the uninstall
    command = FakeCommandRunner()
    command.responses = [listing(["requests"]), response(0), listing([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests==2.32.0"], state="absent")

    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "uninstall", "-y", "requests"]
    assert call_data(result)["removed"] == ["requests"]

    # installed names are matched after normalization
    command = FakeCommandRunner()
    command.responses = [listing(["charset_normalizer"]), response(0), listing([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["charset-normalizer"], state="absent")

    assert argvs(command)[1][-1] == "charset-normalizer"
    assert call_data(result)["removed"] == ["charset-normalizer"]


def test_post_operation_failures(tmp_path):
    # an install that leaves the package pending fails
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report(["requests"])]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still pending after install: requests"):
        pip.packages(["requests"])

    # a removal that leaves the package installed fails
    command = FakeCommandRunner()
    command.responses = [listing(["requests"]), response(0), listing(["requests"])]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still installed after removal: requests"):
        pip.packages(["requests"], state="absent")

    # an unresolvable requirement is reported
    command = FakeCommandRunner()
    command.responses = [response(1, "", "ERROR: No matching distribution found")]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not resolve pip requirements"):
        pip.packages(["nonexistent-package-xyz"])

    # the report probe fails and pip --version fails too: pip is absent entirely
    command = FakeCommandRunner(returncode=1, stderr="No module named pip")
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match=r"pip could not run via .* is pip installed"):
        pip.packages(["requests"])
    assert argvs(command)[1][3:] == ["--version"]


def test_install_report_shape_scenarios(tmp_path):
    # unexpected report shapes fail loudly
    payloads = [
        json.dumps(["not", "a", "dict"]),  # report is not a JSON object
        json.dumps({"version": "1", "install": None}),  # install is null
        json.dumps({"version": "1", "install": {"a": 1}}),  # install is not a list
        json.dumps({"version": "1", "install": [{"metadata": {}}]}),  # malformed entry
    ]
    for payload in payloads:
        command = FakeCommandRunner()
        command.responses = [response(0, payload)]
        make_context(tmp_path, command=command)
        with pytest.raises(TaskFailed, match="pip install report"):
            pip.packages(["requests"])

    # only the stable report format (version "1", pip >= 23.0) is supported;
    # pip 22.2's experimental "0" and a missing version are rejected
    old_versions = [
        {"version": "0", "install": []},
        {"version": "2", "install": []},
        {"install": []},
    ]
    for payload_dict in old_versions:
        command = FakeCommandRunner()
        command.responses = [response(0, json.dumps(payload_dict))]
        make_context(tmp_path, command=command)
        with pytest.raises(TaskFailed, match="unsupported pip install report version"):
            pip.packages(["requests"])

    command = FakeCommandRunner()
    command.responses = [response(0, json.dumps({"version": "1", "install": []}))]
    make_context(tmp_path, command=command)
    result = pip.packages(["requests"])
    assert result.changed is False


def test_installed_names_with_unexpected_shape_fails_loudly(tmp_path):
    payloads = [
        json.dumps({"not": "a list"}),  # not a JSON array
        json.dumps([{"version": "1.0"}]),  # entry without a name
        json.dumps([42]),  # entry is not an object
    ]
    for payload in payloads:
        command = FakeCommandRunner()
        command.responses = [response(0, payload)]
        make_context(tmp_path, command=command)
        with pytest.raises(TaskFailed, match="pip list output"):
            pip.packages(["requests"], state="absent")


def test_latest_scenarios(tmp_path):
    # latest passes --upgrade to the probe and the install
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests"], state="latest")

    assert argvs(command)[0] == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--quiet",
        "--report",
        "-",
        "--upgrade",
        "requests",
    ]
    assert argvs(command)[1] == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "requests",
    ]
    assert result.summary == "+requests"

    # nothing to upgrade is a no-op
    command = FakeCommandRunner()
    command.responses = [report([])]
    make_context(tmp_path, command=command)

    result = pip.packages(["requests"], state="latest")

    assert argvs(command) == [
        [
            "/usr/bin/python3",
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--quiet",
            "--report",
            "-",
            "--upgrade",
            "requests",
        ]
    ]
    assert result.changed is False
    assert result.summary == "ensure packages are up to date: requests"


def test_extra_args_scenarios(tmp_path):
    # repeatable extra args are not deduplicated
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report([])]
    make_context(tmp_path, command=command)

    pip.packages(["requests"], extra_args=["-v", "-v", "--no-index"])

    assert argvs(command)[0][-4:-1] == ["-v", "-v", "--no-index"]  # probe
    assert argvs(command)[1][-4:-1] == ["-v", "-v", "--no-index"]  # install

    # extra args are passed to the probe and the install
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report([])]
    make_context(tmp_path, command=command)

    pip.packages(["requests"], extra_args=["--no-index", "--find-links=/srv/repo"])

    assert argvs(command)[0] == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--quiet",
        "--report",
        "-",
        "--no-index",
        "--find-links=/srv/repo",
        "requests",
    ]
    assert argvs(command)[1] == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links=/srv/repo",
        "requests",
    ]


def test_commands_and_probes_request_sudo_when_asked(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report([])]
    sudo = FakeSudo()
    make_context(tmp_path, command=command, sudo=sudo)

    pip.packages(["requests"], sudo=True)

    # probes use the same privilege as the changes, so they see the environment
    # the privileged install/uninstall actually acts on (no user-site shadowing)
    assert [call[1]["sudo"] for call in command.calls] == [True, True, True]


def test_python_interpreter_scenarios(tmp_path, monkeypatch):
    # the requested interpreter is used for all pip commands
    monkeypatch.setattr(
        "kitup.modules.pip.shutil.which",
        lambda name: {"python3.13": "/usr/bin/python3.13"}.get(name),
    )
    command = FakeCommandRunner()
    command.responses = [report([])]
    make_context(tmp_path, command=command)

    pip.packages(["requests"], python="python3.13")

    assert argvs(command)[0][0] == "/usr/bin/python3.13"

    # a missing interpreter is reported clearly
    monkeypatch.setattr("kitup.modules.pip.shutil.which", lambda name: None)
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="no Python interpreter found"):
        pip.packages(["requests"])
    with pytest.raises(TaskFailed, match=r"Python interpreter not found: python3\.13"):
        pip.packages(["requests"], python="python3.13")


def test_packages_validates_arguments(tmp_path):
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="package names must not be empty"):
        pip.packages([])
    with pytest.raises(TaskFailed, match="package name must not be empty"):
        pip.packages(["ok", "  "])
    with pytest.raises(TaskFailed, match="invalid pip state"):
        pip.packages(["ok"], state="forcereinstall")
    with pytest.raises(TaskFailed, match="sudo must be a boolean"):
        pip.packages(["ok"], sudo=1)
    with pytest.raises(TaskFailed, match="extra arg must not be empty"):
        pip.packages(["ok"], extra_args=[""])
    with pytest.raises(TaskFailed, match="venv must not be empty"):
        pip.packages(["ok"], venv=" ")
    for bad in (
        "git+https://github.com/org/repo.git",
        "https://example.com/pkg.whl",
        "./pkg.whl",
        "foo @ https://x/y.whl",
    ):
        with pytest.raises(TaskFailed, match="supports package names only"):
            pip.packages([bad], state="absent")


def test_config_set_and_absent_ini_scenarios(tmp_path):
    # set an option and confirm idempotency
    path = tmp_path / "pip.conf"
    path.write_text("[global]\ntimeout = 30\n")
    ctx = local_sudo_context(tmp_path)

    result = pip.config("no-cache-dir", value=True, path=path)

    assert path.read_text() == "[global]\ntimeout = 30\nno-cache-dir = true\n"
    assert result.action == "pip.config"
    assert result.changed is True
    assert result.summary == f"set no-cache-dir in [global] of {path}"
    assert call_data(result) == {
        "path": str(path),
        "section": "global",
        "option": "no-cache-dir",
        "value": "true",
        "ini_action": "added",
    }

    result = pip.config("no-cache-dir", value=True, path=path)
    assert result.changed is False
    assert result.summary == f"ensure no-cache-dir is true in [global] of {path}"
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(ctx.changes) == 1

    # list values are written in pip's multi-line syntax and are idempotent
    multiline = tmp_path / "multiline.conf"
    multiline_ctx = local_sudo_context(tmp_path)
    mirrors = ["https://mirror1.example.com", "https://mirror2.example.com"]

    result = pip.config("find-links", mirrors, path=multiline)

    assert (
        multiline.read_text()
        == "[global]\nfind-links =\n    https://mirror1.example.com\n    https://mirror2.example.com\n"
    )
    assert call_data(result)["value"] == mirrors
    assert call_data(result)["ini_action"] == "added"

    result = pip.config("find-links", mirrors, path=multiline)
    assert result.changed is False
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(multiline_ctx.changes) == 1

    # a multi-line value is replaced by a single-line value
    replace = tmp_path / "replace.conf"
    replace.write_text("[global]\nfind-links =\n    https://a\n    https://b\n")
    local_sudo_context(tmp_path)

    result = pip.config("find-links", "https://c", path=replace)

    assert replace.read_text() == "[global]\nfind-links = https://c\n"
    assert call_data(result) == {
        "path": str(replace),
        "section": "global",
        "option": "find-links",
        "value": "https://c",
        "ini_action": "replaced",
    }

    # absent removes the option and its continuation lines
    absent = tmp_path / "absent.conf"
    absent.write_text("[global]\ntimeout = 30\nfind-links =\n    https://a\n    https://b\n")
    local_sudo_context(tmp_path)

    result = pip.config("find-links", state="absent", path=absent)

    assert absent.read_text() == "[global]\ntimeout = 30\n"
    assert result.changed is True
    assert result.summary == f"remove find-links from [global] of {absent}"
    assert call_data(result) == {
        "path": str(absent),
        "section": "global",
        "option": "find-links",
        "value": None,
        "ini_action": "removed",
    }

    result = pip.config("find-links", state="absent", path=absent)
    assert result.changed is False
    assert result.summary == f"ensure find-links is absent from [global] of {absent}"

    # non-canonical option lines are replaced by a single canonical one
    canonical = tmp_path / "canonical.conf"
    canonical.write_text("[global]\nFind-Links = old\nkeep = 1\n")
    local_sudo_context(tmp_path)

    result = pip.config("find_links", "https://new", path=canonical)

    assert canonical.read_text() == "[global]\nfind-links = https://new\nkeep = 1\n"
    assert result["option"] == "find-links"
    assert call_data(result)["ini_action"] == "replaced"

    # setting the same value again is unchanged, matching the canonical line
    result = pip.config("find-links", "https://new", path=canonical)
    assert result.changed is False

    # absent removes non-canonical option lines too
    noncanonical = tmp_path / "noncanonical.conf"
    noncanonical.write_text("[global]\nfind_links: /a\nkeep = 1\n")
    local_sudo_context(tmp_path)

    result = pip.config("find-links", state="absent", path=noncanonical)

    assert noncanonical.read_text() == "[global]\nkeep = 1\n"
    assert result.changed is True


def test_config_creates_missing_file_and_dry_run(tmp_path):
    # a missing file is created with parents and the default mode
    path = tmp_path / "deep" / "pip" / "pip.conf"
    local_sudo_context(tmp_path)

    result = pip.config("timeout", 60, path=path)

    assert path.read_text() == "[global]\ntimeout = 60\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert call_data(result)["ini_action"] == "added"

    # dry run reports the change without writing
    dry = tmp_path / "dry.conf"
    local_sudo_context(tmp_path, dry_run=True)

    result = pip.config("timeout", 30, path=dry)

    assert result.changed is True
    assert not dry.exists()


def test_config_normalizes_typed_values(tmp_path):
    path = tmp_path / "pip.conf"
    local_sudo_context(tmp_path)

    pip.config("no-cache-dir", value=False, path=path)
    pip.config("timeout", 42, path=path)
    pip.config("index-url", " https://pypi.example.com/simple ", path=path)

    assert path.read_text() == (
        "[global]\nno-cache-dir = false\ntimeout = 42\nindex-url = https://pypi.example.com/simple\n"
    )


def test_config_scope_paths(tmp_path, monkeypatch):
    # user scope honors XDG_CONFIG_HOME
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    make_context(tmp_path)

    result = pip.config("timeout", 30, scope="user")

    user_target = tmp_path / "xdg" / "pip" / "pip.conf"
    assert user_target.read_text() == "[global]\ntimeout = 30\n"
    assert result["path"] == str(user_target)

    # global scope defaults to the system-wide pip.conf
    global_target = tmp_path / "global-pip.conf"
    monkeypatch.setattr(pip, "_GLOBAL_CONFIG_PATH", global_target)
    local_sudo_context(tmp_path)

    result = pip.config("index-url", "https://pypi.example.com/simple", scope="global")

    assert global_target.read_text() == "[global]\nindex-url = https://pypi.example.com/simple\n"
    assert result["path"] == str(global_target)

    # site scope requires a real virtualenv
    local_sudo_context(tmp_path)

    with pytest.raises(TaskFailed, match="site scope requires a venv"):
        pip.config("timeout", 30, scope="site")

    venv = tmp_path / "venv"
    with pytest.raises(TaskFailed, match="site scope requires an existing virtualenv"):
        pip.config("timeout", 30, scope="site", venv=venv)

    # a directory with bin/python but no pyvenv.cfg is not a virtualenv
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    with pytest.raises(TaskFailed, match="site scope requires an existing virtualenv"):
        pip.config("timeout", 30, scope="site", venv=venv)

    make_venv(venv)
    result = pip.config("timeout", 30, scope="site", venv=venv)

    assert result["path"] == str(venv / "pip.conf")
    assert (venv / "pip.conf").read_text() == "[global]\ntimeout = 30\n"


def test_config_user_scope_guards(tmp_path, monkeypatch):
    # user scope cannot run as root
    local_sudo_context(tmp_path)  # is_root=True

    with pytest.raises(TaskFailed, match="user-scoped pip config cannot run as root"):
        pip.config("timeout", 30, scope="user")

    # a privileged write would leave a root-owned file in the user's home
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    make_context(tmp_path)  # non-root

    with pytest.raises(TaskFailed, match="user-scoped pip config cannot use sudo"):
        pip.config("timeout", 30, scope="user", sudo=True)
    with pytest.raises(TaskFailed, match="user-scoped pip config cannot use sudo"):
        pip.config("timeout", state="absent", scope="user", sudo=True)
    assert not (tmp_path / ".config").exists()


def test_config_validates_arguments_before_touching_the_file(tmp_path):
    missing = tmp_path / "missing.conf"
    local_sudo_context(tmp_path)

    with pytest.raises(TaskFailed, match="config state 'present' requires a value"):
        pip.config("timeout", path=missing)
    with pytest.raises(TaskFailed, match="config state 'absent' does not take a value"):
        pip.config("timeout", "30", state="absent", path=missing)
    with pytest.raises(TaskFailed, match="invalid config state"):
        pip.config("timeout", "30", state="maybe", path=missing)
    with pytest.raises(TaskFailed, match="invalid pip config scope"):
        pip.config("timeout", "30", scope="system", path=missing)
    with pytest.raises(TaskFailed, match="ini section"):
        pip.config("timeout", "30", section="[bad]", path=missing)
    with pytest.raises(TaskFailed, match="ini option"):
        pip.config("time=out", "30", path=missing)
    with pytest.raises(TaskFailed, match="config sudo must be a boolean"):
        pip.config("timeout", "30", path=missing, sudo="yes")
    with pytest.raises(TaskFailed, match="config path must not be empty"):
        pip.config("timeout", "30", path="")
    with pytest.raises(TaskFailed, match="ini value must not contain"):
        pip.config("timeout", "a\nb", path=missing)
    with pytest.raises(TaskFailed, match="unsupported config value type"):
        pip.config("timeout", 1.5, path=missing)
    with pytest.raises(TaskFailed, match="ini value list items must be strings"):
        pip.config("find-links", ["a", 1], path=missing)
    with pytest.raises(TaskFailed, match="ini value list must not be empty"):
        pip.config("find-links", [], path=missing)
    with pytest.raises(TaskFailed, match="must be a sequence"):
        pip.config("find-links", {"a", "b"}, path=missing)
    assert not missing.exists()

    # validation happens before parent directories are created
    deep = tmp_path / "deep" / "pip" / "pip.conf"

    with pytest.raises(TaskFailed, match="config state 'present' requires a value"):
        pip.config("timeout", path=deep)

    assert not (tmp_path / "deep").exists()
