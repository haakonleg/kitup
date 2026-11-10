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


def test_probes_stay_unprivileged_without_sudo(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report([])]
    sudo = FakeSudo()
    ctx = make_context(tmp_path, command=command, sudo=sudo)

    result = pip.packages(ctx, ["requests"])

    assert argvs(command) == [
        ["/usr/bin/python3", "-m", "pip", "install", "--dry-run", "--quiet", "--report", "-", "requests"]
    ]
    assert command.calls[0][1]["sudo"] is False
    assert sudo.refresh_count == 0
    assert result.action == "pip.packages"
    assert result.changed is False
    assert result.summary == "ensure packages are present: requests"
    assert call_data(result) == {"added": [], "packages": ["requests"]}


def test_present_installs_what_the_report_lists(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["requests", "urllib3"]), response(0), report([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests"])

    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "install", "requests"]
    assert result.changed is True
    assert result.summary == "+requests +urllib3"
    assert call_data(result) == {"added": ["requests", "urllib3"], "packages": ["requests"]}


def test_present_version_mismatch_is_reinstalled(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["foo"]), response(0), report([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["foo==1.0"])

    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "install", "foo==1.0"]
    assert call_data(result)["added"] == ["foo"]


def test_present_dry_run_does_not_install(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["requests"])]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = pip.packages(ctx, ["requests"])

    assert len(command.calls) == 1
    assert result.changed is True
    assert result.summary == "+requests"


def test_present_dry_run_with_missing_venv_reports_creation_without_running(tmp_path):
    venv = tmp_path / "venv"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = pip.packages(ctx, ["requests"], venv=venv)

    assert command.calls == []
    assert result.changed is True
    assert result.summary == f"create venv {venv}; +requests"
    assert call_data(result) == {
        "added": ["requests"],
        "packages": ["requests"],
        "venv": str(venv),
    }


def make_venv(venv) -> None:
    """Create a minimal fake virtualenv layout on disk."""
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("", encoding="utf-8")


def test_present_creates_missing_venv(tmp_path):
    venv = tmp_path / "venv"

    def create_venv(argv):
        make_venv(venv)
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [create_venv, report(["requests"]), response(0), report([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests"], venv=venv)

    assert argvs(command)[0] == ["/usr/bin/python3", "-m", "venv", str(venv)]
    assert argvs(command)[1][:3] == [str(venv / "bin" / "python"), "-m", "pip"]
    assert argvs(command)[2] == [str(venv / "bin" / "python"), "-m", "pip", "install", "requests"]
    assert result.summary == f"create venv {venv}; +requests"
    assert call_data(result)["venv"] == str(venv)


def test_present_uses_existing_venv_pip(tmp_path):
    venv = tmp_path / "venv"
    make_venv(venv)
    command = FakeCommandRunner()
    command.responses = [report([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests"], venv=venv)

    assert argvs(command) == [
        [str(venv / "bin" / "python"), "-m", "pip", "install", "--dry-run", "--quiet", "--report", "-", "requests"]
    ]
    assert result.changed is False


def test_absent_removes_only_installed_packages(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["requests", "charset-normalizer"]), response(0), listing([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests", "urllib3"], state="absent")

    assert argvs(command)[0] == ["/usr/bin/python3", "-m", "pip", "list", "--local", "--format=json"]
    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "uninstall", "-y", "requests"]
    assert result.changed is True
    assert result.summary == "-requests"
    assert call_data(result) == {"removed": ["requests"], "packages": ["requests", "urllib3"]}


def test_absent_checks_installed_packages_once_for_all_targets(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["requests", "urllib3"]), response(0), listing([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests", "urllib3"], state="absent")

    assert argvs(command) == [
        ["/usr/bin/python3", "-m", "pip", "list", "--local", "--format=json"],
        ["/usr/bin/python3", "-m", "pip", "uninstall", "-y", "requests", "urllib3"],
        ["/usr/bin/python3", "-m", "pip", "list", "--local", "--format=json"],
    ]


def test_absent_is_idempotent_when_nothing_is_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["pip"])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests"], state="absent")

    assert len(command.calls) == 1
    assert result.changed is False
    assert result.summary == "ensure packages are absent: requests"
    assert call_data(result) == {"removed": [], "packages": ["requests"]}


def test_absent_dry_run_does_not_uninstall(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["requests"])]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = pip.packages(ctx, ["requests"], state="absent")

    assert len(command.calls) == 1
    assert result.changed is True
    assert result.summary == "-requests"


def test_absent_with_missing_venv_is_a_no_change(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests"], state="absent", venv=tmp_path / "venv")

    assert command.calls == []
    assert result.changed is False
    assert result.summary == "ensure packages are absent: requests"


def test_absent_strips_version_specifiers(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["requests"]), response(0), listing([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests==2.32.0"], state="absent")

    assert argvs(command)[1] == ["/usr/bin/python3", "-m", "pip", "uninstall", "-y", "requests"]
    assert call_data(result)["removed"] == ["requests"]


def test_absent_passes_extra_args_to_uninstall(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["requests"]), response(0), listing([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests"], state="absent", extra_args=["--break-system-packages"])

    assert argvs(command)[1] == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "uninstall",
        "-y",
        "--break-system-packages",
        "requests",
    ]


def test_absent_rejects_url_and_path_names(tmp_path):
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    for bad in (
        "git+https://github.com/org/repo.git",
        "https://example.com/pkg.whl",
        "./pkg.whl",
        "foo @ https://x/y.whl",
    ):
        with pytest.raises(TaskFailed, match="supports package names only"):
            pip.packages(ctx, [bad], state="absent")


def test_venv_path_that_is_not_a_virtualenv_is_rejected(tmp_path):
    not_a_venv = tmp_path / "not-a-venv"
    (not_a_venv / "bin").mkdir(parents=True)
    (not_a_venv / "bin" / "python").write_text("", encoding="utf-8")
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="not a virtualenv"):
        pip.packages(ctx, ["requests"], venv=not_a_venv)
    with pytest.raises(TaskFailed, match="not a virtualenv"):
        pip.packages(ctx, ["requests"], state="absent", venv=not_a_venv)


def test_absent_matches_normalized_names(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["charset_normalizer"]), response(0), listing([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["charset-normalizer"], state="absent")

    assert argvs(command)[1][-1] == "charset-normalizer"
    assert call_data(result)["removed"] == ["charset-normalizer"]


def test_install_still_pending_after_install_fails(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report(["requests"])]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still pending after install: requests"):
        pip.packages(ctx, ["requests"])


def test_uninstall_still_installed_after_removal_fails(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["requests"]), response(0), listing(["requests"])]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still installed after removal: requests"):
        pip.packages(ctx, ["requests"], state="absent")


def test_unresolvable_requirements_fail(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, "", "ERROR: No matching distribution found")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not resolve pip requirements"):
        pip.packages(ctx, ["nonexistent-package-xyz"])


def test_missing_pip_is_reported_as_missing_not_too_old(tmp_path):
    # the report probe fails and pip --version fails too: pip is absent entirely
    command = FakeCommandRunner(returncode=1, stderr="No module named pip")
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match=r"pip could not run via .* is pip installed"):
        pip.packages(ctx, ["requests"])
    assert argvs(command)[1][3:] == ["--version"]


def test_repeatable_extra_args_are_not_deduplicated(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests"], extra_args=["-v", "-v", "--no-index"])

    assert argvs(command)[0][-4:-1] == ["-v", "-v", "--no-index"]  # probe
    assert argvs(command)[1][-4:-1] == ["-v", "-v", "--no-index"]  # install


def test_install_report_with_unexpected_shape_fails_loudly(tmp_path):
    payloads = [
        json.dumps(["not", "a", "dict"]),  # report is not a JSON object
        json.dumps({"version": "1", "install": None}),  # install is null
        json.dumps({"version": "1", "install": {"a": 1}}),  # install is not a list
        json.dumps({"version": "1", "install": [{"metadata": {}}]}),  # malformed entry
    ]
    for payload in payloads:
        command = FakeCommandRunner()
        command.responses = [response(0, payload)]
        ctx = make_context(tmp_path, command=command)
        with pytest.raises(TaskFailed, match="pip install report"):
            pip.packages(ctx, ["requests"])


def test_install_report_requires_the_stable_version(tmp_path):
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
        ctx = make_context(tmp_path, command=command)
        with pytest.raises(TaskFailed, match="unsupported pip install report version"):
            pip.packages(ctx, ["requests"])

    command = FakeCommandRunner()
    command.responses = [response(0, json.dumps({"version": "1", "install": []}))]
    ctx = make_context(tmp_path, command=command)
    result = pip.packages(ctx, ["requests"])
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
        ctx = make_context(tmp_path, command=command)
        with pytest.raises(TaskFailed, match="pip list output"):
            pip.packages(ctx, ["requests"], state="absent")


def test_latest_state_passes_upgrade_flag(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests"], state="latest")

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


def test_latest_state_is_idempotent_when_nothing_to_upgrade(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report([])]
    ctx = make_context(tmp_path, command=command)

    result = pip.packages(ctx, ["requests"], state="latest")

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


def test_present_state_does_not_pass_upgrade_flag(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests"], state="present")

    assert "--upgrade" not in argvs(command)[0]


def test_extra_args_are_passed_to_probe_and_install(tmp_path):
    command = FakeCommandRunner()
    command.responses = [report(["requests"]), response(0), report([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests"], extra_args=["--no-index", "--find-links=/srv/repo"])

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
    ctx = make_context(tmp_path, command=command, sudo=sudo)

    pip.packages(ctx, ["requests"], sudo=True)

    # probes use the same privilege as the changes, so they see the environment
    # the privileged install/uninstall actually acts on (no user-site shadowing)
    assert [call[1]["sudo"] for call in command.calls] == [True, True, True]


def test_absent_probes_request_sudo_when_asked(tmp_path):
    command = FakeCommandRunner()
    command.responses = [listing(["requests"]), response(0), listing([])]
    sudo = FakeSudo()
    ctx = make_context(tmp_path, command=command, sudo=sudo)

    pip.packages(ctx, ["requests"], state="absent", sudo=True)

    assert [call[1]["sudo"] for call in command.calls] == [True, True, True]
    assert argvs(command)[0][3:] == ["list", "--local", "--format=json"]


def test_python_parameter_selects_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.pip.shutil.which",
        lambda name: {"python3.13": "/usr/bin/python3.13"}.get(name),
    )
    command = FakeCommandRunner()
    command.responses = [report([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests"], python="python3.13")

    assert argvs(command)[0][0] == "/usr/bin/python3.13"


def test_python_parameter_accepts_a_path(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup.modules.pip.shutil.which", lambda name: None)
    custom = tmp_path / "python-custom"
    custom.write_text("", encoding="utf-8")
    command = FakeCommandRunner()
    command.responses = [report([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests"], python=str(custom))

    assert argvs(command)[0][0] == str(custom)


def test_python_discovery_falls_back_to_python(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.pip.shutil.which",
        lambda name: "/usr/bin/python" if name == "python" else None,
    )
    command = FakeCommandRunner()
    command.responses = [report([])]
    ctx = make_context(tmp_path, command=command)

    pip.packages(ctx, ["requests"])

    assert argvs(command)[0][0] == "/usr/bin/python"


def test_missing_python_interpreter_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup.modules.pip.shutil.which", lambda name: None)
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="no Python interpreter found"):
        pip.packages(ctx, ["requests"])
    with pytest.raises(TaskFailed, match=r"Python interpreter not found: python3\.13"):
        pip.packages(ctx, ["requests"], python="python3.13")


def test_packages_validates_arguments(tmp_path):
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="package names must not be empty"):
        pip.packages(ctx, [])
    with pytest.raises(TaskFailed, match="package name must not be empty"):
        pip.packages(ctx, ["ok", "  "])
    with pytest.raises(TaskFailed, match="invalid pip state"):
        pip.packages(ctx, ["ok"], state="forcereinstall")
    with pytest.raises(TaskFailed, match="sudo must be a boolean"):
        pip.packages(ctx, ["ok"], sudo=1)
    with pytest.raises(TaskFailed, match="extra arg must not be empty"):
        pip.packages(ctx, ["ok"], extra_args=[""])
    with pytest.raises(TaskFailed, match="venv must not be empty"):
        pip.packages(ctx, ["ok"], venv=" ")


def test_config_sets_option_and_is_idempotent(tmp_path):
    path = tmp_path / "pip.conf"
    path.write_text("[global]\ntimeout = 30\n")
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "no-cache-dir", value=True, path=path)

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

    result = pip.config(ctx, "no-cache-dir", value=True, path=path)
    assert result.changed is False
    assert result.summary == f"ensure no-cache-dir is true in [global] of {path}"
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(ctx.changes) == 1


def test_config_writes_multi_line_value_in_pip_syntax_and_is_idempotent(tmp_path):
    path = tmp_path / "pip.conf"
    ctx = local_sudo_context(tmp_path)
    mirrors = ["https://mirror1.example.com", "https://mirror2.example.com"]

    result = pip.config(ctx, "find-links", mirrors, path=path)

    assert (
        path.read_text() == "[global]\nfind-links =\n    https://mirror1.example.com\n    https://mirror2.example.com\n"
    )
    assert call_data(result)["value"] == mirrors
    assert call_data(result)["ini_action"] == "added"

    result = pip.config(ctx, "find-links", mirrors, path=path)
    assert result.changed is False
    assert call_data(result)["ini_action"] == "unchanged"
    assert len(ctx.changes) == 1


def test_config_replaces_multi_line_value_with_single_line(tmp_path):
    path = tmp_path / "pip.conf"
    path.write_text("[global]\nfind-links =\n    https://a\n    https://b\n")
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "find-links", "https://c", path=path)

    assert path.read_text() == "[global]\nfind-links = https://c\n"
    assert call_data(result) == {
        "path": str(path),
        "section": "global",
        "option": "find-links",
        "value": "https://c",
        "ini_action": "replaced",
    }


def test_config_absent_removes_option_and_continuation_lines(tmp_path):
    path = tmp_path / "pip.conf"
    path.write_text("[global]\ntimeout = 30\nfind-links =\n    https://a\n    https://b\n")
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "find-links", state="absent", path=path)

    assert path.read_text() == "[global]\ntimeout = 30\n"
    assert result.changed is True
    assert result.summary == f"remove find-links from [global] of {path}"
    assert call_data(result) == {
        "path": str(path),
        "section": "global",
        "option": "find-links",
        "value": None,
        "ini_action": "removed",
    }

    result = pip.config(ctx, "find-links", state="absent", path=path)
    assert result.changed is False
    assert result.summary == f"ensure find-links is absent from [global] of {path}"


def test_config_creates_a_missing_file_with_parents(tmp_path):
    path = tmp_path / "deep" / "pip" / "pip.conf"
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "timeout", 60, path=path)

    assert path.read_text() == "[global]\ntimeout = 60\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert call_data(result)["ini_action"] == "added"


def test_config_matches_and_writes_canonical_option_names(tmp_path):
    path = tmp_path / "pip.conf"
    path.write_text("[global]\nFind-Links = old\nkeep = 1\n")
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "find_links", "https://new", path=path)

    # the non-canonical line is replaced by a single canonical one, not duplicated
    assert path.read_text() == "[global]\nfind-links = https://new\nkeep = 1\n"
    assert result["option"] == "find-links"
    assert call_data(result)["ini_action"] == "replaced"

    # setting the same value again is unchanged, matching the canonical line
    result = pip.config(ctx, "find-links", "https://new", path=path)
    assert result.changed is False


def test_config_absent_removes_non_canonical_option_lines(tmp_path):
    path = tmp_path / "pip.conf"
    path.write_text("[global]\nfind_links: /a\nkeep = 1\n")
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "find-links", state="absent", path=path)

    assert path.read_text() == "[global]\nkeep = 1\n"
    assert result.changed is True


def test_config_edits_per_command_section(tmp_path):
    path = tmp_path / "pip.conf"
    ctx = local_sudo_context(tmp_path)

    pip.config(ctx, "no-deps", value=True, section="install", path=path)

    assert path.read_text() == "[install]\nno-deps = true\n"


def test_config_normalizes_typed_values(tmp_path):
    path = tmp_path / "pip.conf"
    ctx = local_sudo_context(tmp_path)

    pip.config(ctx, "no-cache-dir", value=False, path=path)
    pip.config(ctx, "timeout", 42, path=path)
    pip.config(ctx, "index-url", " https://pypi.example.com/simple ", path=path)

    assert path.read_text() == (
        "[global]\nno-cache-dir = false\ntimeout = 42\nindex-url = https://pypi.example.com/simple\n"
    )


def test_config_dry_run_reports_without_writing(tmp_path):
    path = tmp_path / "pip.conf"
    ctx = local_sudo_context(tmp_path, dry_run=True)

    result = pip.config(ctx, "timeout", 30, path=path)

    assert result.changed is True
    assert not path.exists()


def test_config_dry_run_does_not_create_parent_directories(tmp_path):
    deep = tmp_path / "deep" / "pip" / "pip.conf"
    ctx = local_sudo_context(tmp_path, dry_run=True)

    result = pip.config(ctx, "timeout", 30, path=deep)

    assert result.changed is True
    assert not (tmp_path / "deep").exists()


def test_config_absent_on_missing_file_does_not_create_parents(tmp_path):
    deep = tmp_path / "deep" / "pip" / "pip.conf"
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "timeout", state="absent", path=deep)

    assert result.changed is False
    assert not (tmp_path / "deep").exists()


def test_config_scope_user_uses_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ctx = make_context(tmp_path)

    result = pip.config(ctx, "timeout", 30, scope="user")

    target = tmp_path / "xdg" / "pip" / "pip.conf"
    assert target.read_text() == "[global]\ntimeout = 30\n"
    assert result["path"] == str(target)


def test_config_scope_user_defaults_to_dot_config(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    ctx = make_context(tmp_path)

    result = pip.config(ctx, "timeout", 30, scope="user")

    target = tmp_path / ".config" / "pip" / "pip.conf"
    assert target.read_text() == "[global]\ntimeout = 30\n"
    assert result["path"] == str(target)


def test_config_scope_global_defaults_to_etc_pip_conf(tmp_path, monkeypatch):
    target = tmp_path / "pip.conf"
    monkeypatch.setattr(pip, "_GLOBAL_CONFIG_PATH", target)
    ctx = local_sudo_context(tmp_path)

    result = pip.config(ctx, "index-url", "https://pypi.example.com/simple", scope="global")

    assert target.read_text() == "[global]\nindex-url = https://pypi.example.com/simple\n"
    assert result["path"] == str(target)


def test_config_scope_user_rejected_as_root(tmp_path):
    ctx = local_sudo_context(tmp_path)  # is_root=True

    with pytest.raises(TaskFailed, match="user-scoped pip config cannot run as root"):
        pip.config(ctx, "timeout", 30, scope="user")


def test_config_scope_user_rejects_sudo(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    ctx = make_context(tmp_path)  # non-root

    # a privileged write would leave a root-owned file in the user's home
    with pytest.raises(TaskFailed, match="user-scoped pip config cannot use sudo"):
        pip.config(ctx, "timeout", 30, scope="user", sudo=True)
    with pytest.raises(TaskFailed, match="user-scoped pip config cannot use sudo"):
        pip.config(ctx, "timeout", state="absent", scope="user", sudo=True)
    assert not (tmp_path / ".config").exists()


def test_config_scope_site_uses_the_venv(tmp_path):
    ctx = local_sudo_context(tmp_path)

    with pytest.raises(TaskFailed, match="site scope requires a venv"):
        pip.config(ctx, "timeout", 30, scope="site")

    venv = tmp_path / "venv"
    with pytest.raises(TaskFailed, match="site scope requires an existing virtualenv"):
        pip.config(ctx, "timeout", 30, scope="site", venv=venv)

    # a directory with bin/python but no pyvenv.cfg is not a virtualenv
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    with pytest.raises(TaskFailed, match="site scope requires an existing virtualenv"):
        pip.config(ctx, "timeout", 30, scope="site", venv=venv)

    make_venv(venv)
    result = pip.config(ctx, "timeout", 30, scope="site", venv=venv)

    assert result["path"] == str(venv / "pip.conf")
    assert (venv / "pip.conf").read_text() == "[global]\ntimeout = 30\n"


def test_config_validates_arguments_before_touching_the_file(tmp_path):
    missing = tmp_path / "missing.conf"
    ctx = local_sudo_context(tmp_path)

    with pytest.raises(TaskFailed, match="config state 'present' requires a value"):
        pip.config(ctx, "timeout", path=missing)
    with pytest.raises(TaskFailed, match="config state 'absent' does not take a value"):
        pip.config(ctx, "timeout", "30", state="absent", path=missing)
    with pytest.raises(TaskFailed, match="invalid config state"):
        pip.config(ctx, "timeout", "30", state="maybe", path=missing)
    with pytest.raises(TaskFailed, match="invalid pip config scope"):
        pip.config(ctx, "timeout", "30", scope="system", path=missing)
    with pytest.raises(TaskFailed, match="ini section"):
        pip.config(ctx, "timeout", "30", section="[bad]", path=missing)
    with pytest.raises(TaskFailed, match="ini option"):
        pip.config(ctx, "time=out", "30", path=missing)
    with pytest.raises(TaskFailed, match="config sudo must be a boolean"):
        pip.config(ctx, "timeout", "30", path=missing, sudo="yes")
    with pytest.raises(TaskFailed, match="config path must not be empty"):
        pip.config(ctx, "timeout", "30", path="")
    with pytest.raises(TaskFailed, match="ini value must not contain"):
        pip.config(ctx, "timeout", "a\nb", path=missing)
    with pytest.raises(TaskFailed, match="unsupported config value type"):
        pip.config(ctx, "timeout", 1.5, path=missing)
    with pytest.raises(TaskFailed, match="ini value list items must be strings"):
        pip.config(ctx, "find-links", ["a", 1], path=missing)
    with pytest.raises(TaskFailed, match="ini value list must not be empty"):
        pip.config(ctx, "find-links", [], path=missing)
    with pytest.raises(TaskFailed, match="must be a sequence"):
        pip.config(ctx, "find-links", {"a", "b"}, path=missing)
    assert not missing.exists()


def test_config_validates_before_creating_parent_directories(tmp_path):
    deep = tmp_path / "deep" / "pip" / "pip.conf"
    ctx = local_sudo_context(tmp_path)

    with pytest.raises(TaskFailed, match="config state 'present' requires a value"):
        pip.config(ctx, "timeout", path=deep)

    assert not (tmp_path / "deep").exists()
