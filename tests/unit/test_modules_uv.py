import json
import shutil

import pytest

from kitup.errors import TaskFailed
from kitup.modules import uv

from .helpers import (
    FakeCommandRunner,
    argvs,
    call_data,
    completed,
    local_sudo_context,
    make_context,
    privileged_context,
    response,
)

UV = "/usr/bin/uv"


@pytest.fixture(autouse=True)
def uv_on_path(monkeypatch):
    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: UV if name == "uv" else None)


def write_pyproject(project, body: str = "[]") -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / ".venv").mkdir(exist_ok=True)  # a synced project has a project environment
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.14"\ndependencies = {body}\n',
        encoding="utf-8",
    )


def synced_probes() -> list:
    """Scripted pre-sync probes when the project is already converged."""
    return [response(0), response(0)]


def post_sync_ok() -> list:
    """Scripted post-sync checks, which always run both probes."""
    return [response(0), response(0)]


def make_receipt(
    tools_dir,
    name: str,
    specifier: str = "",
    *,
    extras: list[str] | None = None,
    marker: str | None = None,
    extra_requirements: list[str] | None = None,
) -> None:
    tool_dir = tools_dir / name
    tool_dir.mkdir(parents=True, exist_ok=True)
    fields = f'name = "{name}"'
    if specifier:
        fields += f', specifier = "{specifier}"'
    if extras:
        fields += ", extras = [" + ", ".join(f'"{extra}"' for extra in extras) + "]"
    if marker:
        fields += f", marker = {json.dumps(marker)}"
    requirements = [f"{{ {fields} }}"]
    requirements.extend(f'{{ name = "{extra}" }}' for extra in extra_requirements or [])
    (tool_dir / "uv-receipt.toml").write_text(
        f"[tool]\nrequirements = [\n    {',\n    '.join(requirements)},\n]\n", encoding="utf-8"
    )


def test_present_adds_a_missing_dependency_and_is_idempotent(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project)
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests>=2"], project=project)

    # an edit already forces a sync, so the pre-sync probes are skipped
    assert argvs(command) == [
        [UV, "sync", "--project", str(project)],
        [UV, "sync", "--project", str(project), "--check"],
        [UV, "lock", "--project", str(project), "--check"],
    ]
    assert 'dependencies = ["requests>=2"]' in pyproject.read_text()
    assert result.action == "uv.dependencies"
    assert result.changed is True
    assert result.summary == f"+requests>=2; sync {project}"
    assert call_data(result) == {
        "dependencies": ["requests>=2"],
        "project": str(project),
        "added": ["requests>=2"],
    }

    # once the project is in sync the same request is a no-op
    command = FakeCommandRunner()
    command.responses = synced_probes()
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests>=2"], project=project)

    assert argvs(command) == [
        [UV, "sync", "--project", str(project), "--check"],
        [UV, "lock", "--project", str(project), "--check"],
    ]
    assert result.changed is False
    assert result.summary == "ensure dependencies are present: requests>=2"
    assert call_data(result) == {
        "dependencies": ["requests>=2"],
        "project": str(project),
        "added": [],
    }


def test_present_reconciles_existing_specifiers(tmp_path):
    # a differing specifier is replaced verbatim
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["requests >= 2"]')
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests>=3"], project=project)

    assert 'dependencies = ["requests>=3"]' in pyproject.read_text()
    assert call_data(result)["added"] == ["requests>=3"]
    assert result.summary == f"+requests>=3; sync {project}"

    # the same normalized name with a different verbatim string is replaced too
    normalized = tmp_path / "normalized"
    write_pyproject(normalized, '["Charset_Normalizer>=3"]')
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["charset-normalizer"], project=normalized)

    assert result.changed is True
    assert 'dependencies = ["charset-normalizer"]' in (normalized / "pyproject.toml").read_text()


def test_present_preserves_non_registry_entries_and_reconciles_duplicates(tmp_path):
    # non-registry entries are preserved verbatim and never matched
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["flask", "requests @ https://example.com/requests.whl", "./local"]')
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests>=2"], project=project)

    text = pyproject.read_text()
    assert '"requests @ https://example.com/requests.whl"' in text  # not rewritten to a bare name
    assert '"./local"' in text  # not matched (and no crash parsing it)
    assert '"requests>=2"' in text
    assert '"flask"' in text
    assert call_data(result)["added"] == ["requests>=2"]

    # duplicate entries for the same name collapse into the wanted entry
    duplicate = tmp_path / "duplicate"
    duplicate_pyproject = duplicate / "pyproject.toml"
    write_pyproject(duplicate, '["flask", "flask>=2"]')
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    uv.dependencies(["flask>=3"], project=duplicate)

    assert 'dependencies = ["flask>=3"]' in duplicate_pyproject.read_text()


def test_diff_dependencies_applies_the_last_duplicate_wanted_entry():
    wanted = [uv._try_parse_requirement("requests>=2"), uv._try_parse_requirement("requests>=3")]

    desired, added, removed = uv._diff_dependencies(["requests>=1"], wanted, state="present")

    assert desired == ["requests>=3"]
    assert added == ["requests>=2", "requests>=3"]
    assert removed == []


def test_present_dry_run_reports_without_writing_or_syncing(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project)
    command = FakeCommandRunner()
    make_context(tmp_path, dry_run=True, command=command)

    result = uv.dependencies(["requests"], project=project)

    assert command.calls == []
    assert result.changed is True
    assert result.summary == f"+requests; sync {project}"
    assert pyproject.read_text().endswith("dependencies = []\n")


def test_latest_upgrades_bare_names_and_is_idempotent(tmp_path):
    # only bare names are passed to --upgrade-package
    project = tmp_path / "proj"
    write_pyproject(project, '["flask>=1"]')
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests", "flask>=1"], project=project, state="latest")

    assert argvs(command)[0] == [
        UV,
        "sync",
        "--project",
        str(project),
        "--upgrade-package",
        "requests",
    ]
    assert result.changed is True
    assert result.summary == f"+requests; sync {project}"

    # an already up-to-date project is a no-op
    synced = tmp_path / "synced"
    write_pyproject(synced, '["requests"]')
    command = FakeCommandRunner()
    command.responses = synced_probes()
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=synced, state="latest")

    assert result.changed is False
    assert result.summary == "ensure dependencies are up to date: requests"


def test_absent_reconciliation_scenarios(tmp_path):
    # installed entries are removed by name and the project is synced
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["requests>=2", "flask"]')
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests==2.32"], project=project, state="absent")

    assert 'dependencies = ["flask"]' in pyproject.read_text()
    assert result.changed is True
    assert result.summary == f"-requests>=2; sync {project}"
    assert call_data(result) == {
        "dependencies": ["requests==2.32"],
        "project": str(project),
        "removed": ["requests>=2"],
    }

    # nothing matching is a no-op
    clean = tmp_path / "clean"
    write_pyproject(clean, '["flask"]')
    command = FakeCommandRunner()
    command.responses = synced_probes()
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=clean, state="absent")

    assert result.changed is False
    assert result.summary == "ensure dependencies are absent: requests"
    assert call_data(result) == {
        "dependencies": ["requests"],
        "project": str(clean),
        "removed": [],
    }

    # an undefined dependency group needs no uv probe and is a no-op
    undefined = tmp_path / "undefined"
    write_pyproject(undefined)
    undefined_pyproject = undefined / "pyproject.toml"
    undefined_pyproject.write_text(
        undefined_pyproject.read_text() + "\n[dependency-groups]\nother = []\n", encoding="utf-8"
    )
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    result = uv.dependencies(["ruff"], project=undefined, group="lint", state="absent")

    assert command.calls == []  # no uv probe would succeed with an undefined --group
    assert result.changed is False
    assert result.summary == "ensure dependencies are absent: ruff"


def test_missing_project_scenarios(tmp_path):
    # absent against a missing project is a no-op without commands
    command = FakeCommandRunner()
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=tmp_path / "missing", state="absent")

    assert command.calls == []
    assert result.changed is False
    assert result.summary == "ensure dependencies are absent: requests"

    # present initializes a bare project, then adds and syncs
    project = tmp_path / "proj"

    def init(argv):
        write_pyproject(project)
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [init, response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=project)

    assert argvs(command)[0] == [UV, "init", "--bare", str(project)]
    assert result.changed is True
    assert result.summary == f"init project {project}; +requests; sync {project}"

    # dry run reports the same work without creating anything
    dry_project = tmp_path / "dry"
    command = FakeCommandRunner()
    make_context(tmp_path, dry_run=True, command=command)

    result = uv.dependencies(["requests"], project=dry_project)

    assert command.calls == []
    assert result.changed is True
    assert result.summary == f"init project {dry_project}; +requests; sync {dry_project}"
    assert not dry_project.exists()


def test_project_path_and_pyproject_symlink_scenarios(tmp_path):
    # a project path that is a file is rejected
    file = tmp_path / "file"
    file.write_text("", encoding="utf-8")
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="not a directory"):
        uv.dependencies(["requests"], project=file)

    # a symlinked project directory is followed, but a symlinked pyproject is refused
    real = tmp_path / "real"
    write_pyproject(real, '["requests"]')
    link = tmp_path / "link"
    link.symlink_to(real)
    command = FakeCommandRunner()
    command.responses = synced_probes()
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=link)

    assert result.changed is False  # the symlinked directory is a valid project

    (real / "pyproject.toml").unlink()
    (real / "pyproject.toml").symlink_to(tmp_path / "elsewhere.toml")
    with pytest.raises(TaskFailed, match="symlink"):
        uv.dependencies(["requests"], project=real)


def test_dependency_configuration_errors(tmp_path):
    # a pyproject without a [project] table is rejected
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".venv").mkdir()
    (project / "pyproject.toml").write_text("[dependency-groups]\nlint = []\n", encoding="utf-8")
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match=r"no \[project\] table"):
        uv.dependencies(["requests"], project=project)

    # dotted group names are rejected
    valid = tmp_path / "valid"
    write_pyproject(valid)
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="must be a single key, not dotted"):
        uv.dependencies(["ruff"], project=valid, group="a.b")

    # dependencies must be an array of strings
    non_array = tmp_path / "non-array"
    write_pyproject(non_array, '"requests"')
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="not an array of strings"):
        uv.dependencies(["requests"], project=non_array)


def test_group_edits_dependency_groups_and_passes_the_group_flag(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project)
    text = pyproject.read_text() + "\n[dependency-groups]\nlint = []\n"
    pyproject.write_text(text, encoding="utf-8")
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["ruff"], project=project, group="lint")

    assert argvs(command)[0] == [UV, "sync", "--project", str(project), "--group", "lint"]
    assert argvs(command)[1] == [UV, "sync", "--project", str(project), "--group", "lint", "--check"]
    assert 'lint = ["ruff"]' in pyproject.read_text()
    assert result.summary == f"+ruff; sync {project}"


def test_python_and_extra_args_are_forwarded(tmp_path):
    # --python is passed to both the sync and its post-check
    project = tmp_path / "proj"
    write_pyproject(project)
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    uv.dependencies(["requests"], project=project, python="3.13")

    assert argvs(command)[0] == [UV, "sync", "--project", str(project), "--python", "3.13"]
    assert argvs(command)[1] == [UV, "sync", "--project", str(project), "--python", "3.13", "--check"]

    # --python is passed to uv init when the project is missing
    init_project = tmp_path / "init"

    def init(argv):
        write_pyproject(init_project)
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [init, response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    uv.dependencies(["requests"], project=init_project, python="3.13")

    assert argvs(command)[0] == [UV, "init", "--bare", "--python", "3.13", str(init_project)]

    # extra args reach the sync, its check, and the lock check
    extra_project = tmp_path / "extra"
    write_pyproject(extra_project)
    command = FakeCommandRunner()
    command.responses = [response(0), *post_sync_ok()]
    make_context(tmp_path, command=command)

    uv.dependencies(["requests"], project=extra_project, extra_args=["--no-index", "--find-links=/srv/repo"])

    sync_run = [UV, "sync", "--project", str(extra_project), "--no-index", "--find-links=/srv/repo"]
    assert argvs(command) == [
        sync_run,  # the sync itself
        [*sync_run, "--check"],  # post-sync check
        [UV, "lock", "--project", str(extra_project), "--no-index", "--find-links=/srv/repo", "--check"],
    ]


def test_sync_still_out_of_sync_after_sync_fails(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    command = FakeCommandRunner()
    # the sync succeeds, then the lock post-check fails
    command.responses = [response(0), response(0), response(1)]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="not synchronized after uv sync"):
        uv.dependencies(["requests"], project=project)


def test_latest_probe_is_upgrade_aware_so_an_available_upgrade_is_detected(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project, '["requests"]')
    command = FakeCommandRunner()
    # the upgrade-aware pre-probe fails (a newer version is available), the sync and post-checks pass
    command.responses = [response(1), response(0), response(0), response(0)]
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=project, state="latest")

    assert argvs(command) == [
        [UV, "sync", "--project", str(project), "--upgrade-package", "requests", "--check"],
        [UV, "sync", "--project", str(project), "--upgrade-package", "requests"],
        [UV, "sync", "--project", str(project), "--check"],
        [UV, "lock", "--project", str(project), "--check"],
    ]
    assert result.changed is True
    assert result.summary == f"sync {project}"


def test_project_environment_scenarios(tmp_path, monkeypatch):
    # uv sync --check exits 0 when only the .venv creation is pending, so the
    # environment's existence is checked too
    project = tmp_path / "proj"
    write_pyproject(project, '["requests"]')
    shutil.rmtree(project / ".venv")
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(0)]  # sync run + both post-checks
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=project)

    assert argvs(command) == [
        [UV, "sync", "--project", str(project)],
        [UV, "sync", "--project", str(project), "--check"],
        [UV, "lock", "--project", str(project), "--check"],
    ]
    assert result.changed is True
    assert result.summary == f"sync {project}"

    # UV_PROJECT_ENVIRONMENT points at an existing environment and avoids the sync
    custom = tmp_path / "custom"
    write_pyproject(custom, '["requests"]')
    shutil.rmtree(custom / ".venv")
    env = tmp_path / "custom-env"
    env.mkdir()
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(env))
    command = FakeCommandRunner()
    command.responses = synced_probes()
    make_context(tmp_path, command=command)

    result = uv.dependencies(["requests"], project=custom)

    assert argvs(command) == [
        [UV, "sync", "--project", str(custom), "--check"],
        [UV, "lock", "--project", str(custom), "--check"],
    ]
    assert result.changed is False


def test_dependencies_validates_arguments(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="dependency names must not be empty"):
        uv.dependencies([], project=project)
    with pytest.raises(TaskFailed, match="dependency name must not be empty"):
        uv.dependencies(["ok", "  "], project=project)
    with pytest.raises(TaskFailed, match="invalid uv state"):
        uv.dependencies(["ok"], project=project, state="forcereinstall")
    with pytest.raises(TaskFailed, match="project must not be empty"):
        uv.dependencies(["ok"], project=" ")
    with pytest.raises(TaskFailed, match="dependency group"):
        uv.dependencies(["ok"], project=project, group="bad group")
    with pytest.raises(TaskFailed, match="python must not be empty"):
        uv.dependencies(["ok"], project=project, python="")
    with pytest.raises(TaskFailed, match="extra arg must not be empty"):
        uv.dependencies(["ok"], project=project, extra_args=[""])
    for bad in (
        "git+https://github.com/org/repo.git",
        "https://example.com/pkg.whl",
        "./pkg.whl",
        "foo @ https://x/y.whl",
    ):
        with pytest.raises(TaskFailed, match="registry requirements only"):
            uv.dependencies([bad], project=project)
        with pytest.raises(TaskFailed, match="supports package names only"):
            uv.dependencies([bad], project=project, state="absent")
    # uv tool specifier syntax is not valid in project dependency tables
    for bad in ("ruff@latest", "ruff@0.17.4"):
        with pytest.raises(TaskFailed, match="'@' specifiers are uv tool syntax"):
            uv.dependencies([bad], project=project)
    for bad in ("requests>>2", "pkg[", "req;", "two words", "req >= "):
        with pytest.raises(TaskFailed, match="invalid"):
            uv.dependencies([bad], project=project)


def test_missing_uv_and_tool_dir_probe_failures(tmp_path, monkeypatch):
    # a missing uv is reported for both dependency and tool operations
    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: None)
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="uv is required but was not found"):
        uv.dependencies(["requests"], project=tmp_path)
    with pytest.raises(TaskFailed, match="uv is required but was not found"):
        uv.tools(["ruff"])

    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: UV if name == "uv" else None)

    # a failing tool dir probe distinguishes a broken uv from an unreadable tools dir
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="boom"), response(1, stderr="boom")]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="uv could not run"):
        uv.tools(["ruff"])
    assert argvs(command)[1] == [UV, "--version"]

    command = FakeCommandRunner()
    command.responses = [response(1, stderr="boom"), response(0, stdout="uv 0.9.0")]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not determine the uv tools directory"):
        uv.tools(["ruff"])


def test_tools_install_scenarios(tmp_path):
    # @ specifiers are documented uv tool syntax and install unchanged
    tools_dir = tmp_path / "tools"

    def install(argv):
        make_receipt(tools_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    make_context(tmp_path, command=command)

    uv.tools(["ruff@latest"])

    assert argvs(command)[1] == [UV, "tool", "install", "ruff@latest"]

    # a missing tool is installed and reported
    missing_dir = tmp_path / "missing-tools"

    def install_missing(argv):
        make_receipt(missing_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(missing_dir)), install_missing]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"])

    assert argvs(command) == [[UV, "tool", "dir"], [UV, "tool", "install", "ruff"]]
    assert result.action == "uv.tools"
    assert result.changed is True
    assert result.summary == "+ruff"
    assert call_data(result) == {"tools": ["ruff"], "added": ["ruff"]}

    # python and extra args are forwarded to the install
    extra_dir = tmp_path / "extra-tools"

    def install_extra(argv):
        make_receipt(extra_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(extra_dir)), install_extra]
    make_context(tmp_path, command=command)

    uv.tools(["ruff"], python="3.13", extra_args=["--no-index", "--find-links=/srv/repo"])

    assert argvs(command)[1] == [
        UV,
        "tool",
        "install",
        "--python",
        "3.13",
        "--no-index",
        "--find-links=/srv/repo",
        "ruff",
    ]


def test_tools_present_is_idempotent_for_matching_receipts(tmp_path):
    # an exactly matching receipt is a no-op
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"])

    assert argvs(command) == [[UV, "tool", "dir"]]
    assert result.changed is False
    assert result.summary == "ensure tools are present: ruff"
    assert call_data(result) == {"tools": ["ruff"], "added": []}

    # the receipt normalizes "@1.0" to specifier "==1.0"; spacing is tolerated
    spaced_dir = tmp_path / "spaced-tools"
    make_receipt(spaced_dir, "ruff", specifier="==1.0")
    for wanted in ("ruff@1.0", "ruff==1.0", "ruff == 1.0"):
        command = FakeCommandRunner()
        command.responses = [response(0, stdout=str(spaced_dir))]
        make_context(tmp_path, command=command)

        result = uv.tools([wanted])

        assert result.changed is False
        assert argvs(command) == [[UV, "tool", "dir"]]

    # extras and markers from the receipt are part of the match
    extras_dir = tmp_path / "extras-tools"
    make_receipt(extras_dir, "ruff", specifier="==1.0", extras=["socks"], marker='python_version >= "3.9"')
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(extras_dir))]
    make_context(tmp_path, command=command)

    result = uv.tools(['ruff[socks]==1.0; python_version >= "3.9"'])

    assert result.changed is False
    assert argvs(command) == [[UV, "tool", "dir"]]


def test_tools_present_reinstalls_differing_receipts(tmp_path):
    # a different specifier triggers a reinstall
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0")

    def install(argv):
        make_receipt(tools_dir, "ruff", specifier="==2.0")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff==2.0"])

    assert argvs(command)[1] == [UV, "tool", "install", "ruff==2.0"]
    assert result.changed is True
    assert call_data(result)["added"] == ["ruff==2.0"]

    # dropped extras also differ from the receipt and trigger a reinstall
    extras_dir = tmp_path / "extras-tools"
    make_receipt(extras_dir, "ruff", specifier="==1.0", extras=["socks"])

    def install_no_extras(argv):
        make_receipt(extras_dir, "ruff", specifier="==1.0")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(extras_dir)), install_no_extras]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff==1.0"])  # extras dropped: reinstall

    assert argvs(command)[1] == [UV, "tool", "install", "ruff==1.0"]
    assert result.changed is True


def test_tools_latest_upgrade_scenarios(tmp_path):
    # a bare installed tool is upgraded
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")

    def upgrade(argv):
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(tools_dir)),
        response(0, stdout="ruff v1.0\n- ruff\n"),
        upgrade,
        response(0, stdout="ruff v2.0\n- ruff\n"),
    ]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"], state="latest")

    assert argvs(command) == [
        [UV, "tool", "dir"],
        [UV, "tool", "list"],
        [UV, "tool", "upgrade", "ruff"],
        [UV, "tool", "list"],
    ]
    assert result.changed is True
    assert result.summary == "+ruff"
    assert call_data(result)["added"] == ["ruff"]

    # all targets are upgraded in one command
    multi_dir = tmp_path / "multi-tools"
    make_receipt(multi_dir, "ruff")
    make_receipt(multi_dir, "httpie")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(multi_dir)),
        response(0, stdout="ruff v1.0\nhttpie v1.0\n- ruff\n- httpie\n"),
        response(0),
        response(0, stdout="ruff v2.0\nhttpie v2.0\n- ruff\n- httpie\n"),
    ]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff", "httpie"], state="latest")

    assert argvs(command) == [
        [UV, "tool", "dir"],
        [UV, "tool", "list"],
        [UV, "tool", "upgrade", "ruff", "httpie"],
        [UV, "tool", "list"],
    ]
    assert result.changed is True
    assert result.summary == "+ruff +httpie"

    # python and extra args are forwarded to the upgrade
    args_dir = tmp_path / "args-tools"
    make_receipt(args_dir, "ruff")
    make_receipt(args_dir, "httpie")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(args_dir)),
        response(0, stdout="ruff v1.0\nhttpie v1.0\n"),
        response(0),
        response(0, stdout="ruff v2.0\nhttpie v2.0\n"),
    ]
    make_context(tmp_path, command=command)

    uv.tools(
        ["ruff", "httpie"],
        state="latest",
        python="3.13",
        extra_args=["--no-index", "--find-links=/srv/repo"],
    )

    assert argvs(command)[2] == [
        UV,
        "tool",
        "upgrade",
        "--python",
        "3.13",
        "--no-index",
        "--find-links=/srv/repo",
        "ruff",
        "httpie",
    ]

    # an upgrade that does not change the version is a no-op
    unchanged_dir = tmp_path / "unchanged-tools"
    make_receipt(unchanged_dir, "ruff")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(unchanged_dir)),
        response(0, stdout="ruff v2.0\n- ruff\n"),
        response(0),
        response(0, stdout="ruff v2.0\n- ruff\n"),
    ]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"], state="latest")

    assert argvs(command)[2] == [UV, "tool", "upgrade", "ruff"]
    assert result.changed is False
    assert result.summary == "ensure tools are up to date: ruff"


def test_tools_latest_pin_scenarios(tmp_path):
    # a pinned tool is refreshed with @latest, which uv tool upgrade refuses to move
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0")

    def install(argv):
        make_receipt(tools_dir, "ruff")  # unpinned after the @latest reinstall
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(tools_dir)),
        response(0, stdout="ruff v1.0\n- ruff\n"),
        install,
        response(0, stdout="ruff v2.0\n- ruff\n"),
    ]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"], state="latest")

    assert argvs(command)[2] == [UV, "tool", "install", "ruff@latest"]
    assert result.changed is True
    assert call_data(result)["added"] == ["ruff"]

    # a range pin, unlike an exact pin, is moved by uv tool upgrade
    range_dir = tmp_path / "range-tools"
    make_receipt(range_dir, "ruff", specifier=">=1.0")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(range_dir)),
        response(0, stdout="ruff v1.0\n- ruff\n"),
        response(0),
        response(0, stdout="ruff v2.0\n- ruff\n"),
    ]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"], state="latest")

    assert argvs(command)[2] == [UV, "tool", "upgrade", "ruff"]
    assert result.changed is True


def test_tools_dry_run_scenarios(tmp_path):
    # an outdated tool is reported from the --outdated probe
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(tools_dir)),
        response(0, stdout="ruff v1.0 [latest: 2.0]\n- ruff\n"),
    ]
    make_context(tmp_path, dry_run=True, command=command)

    result = uv.tools(["ruff"], state="latest")

    assert argvs(command) == [
        [UV, "tool", "dir"],
        [UV, "tool", "list", "--outdated"],
    ]
    assert result.changed is True
    assert result.summary == "+ruff"
    assert call_data(result) == {"tools": ["ruff"], "added": ["ruff"]}

    # a failed outdated probe falls back to treating the tool as updatable
    fallback_dir = tmp_path / "fallback-tools"
    make_receipt(fallback_dir, "ruff")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(fallback_dir)),
        response(1, stderr="network unavailable"),
    ]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = uv.tools(["ruff"], state="latest")

    assert result.changed is True
    assert result.summary == "+ruff"
    assert any("could not check for outdated uv tools" in note for note in ctx.notes)

    # a present dry run reports without installing
    empty_dir = tmp_path / "empty-tools"
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(empty_dir))]
    make_context(tmp_path, dry_run=True, command=command)

    result = uv.tools(["ruff"])

    assert argvs(command) == [[UV, "tool", "dir"]]
    assert result.changed is True
    assert result.summary == "+ruff"
    assert not (empty_dir / "ruff").exists()


def test_tools_absent_scenarios(tmp_path):
    # an installed tool is uninstalled and its receipt removed
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")

    def uninstall(argv):
        shutil.rmtree(tools_dir / "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), uninstall]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"], state="absent")

    assert argvs(command) == [[UV, "tool", "dir"], [UV, "tool", "uninstall", "ruff"]]
    assert result.changed is True
    assert result.summary == "-ruff"
    assert call_data(result) == {"tools": ["ruff"], "removed": ["ruff"]}

    # removing a tool that is not installed is a no-op
    empty_dir = tmp_path / "empty-tools"
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(empty_dir))]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"], state="absent")

    assert argvs(command) == [[UV, "tool", "dir"]]
    assert result.changed is False
    assert result.summary == "ensure tools are absent: ruff"
    assert call_data(result) == {"tools": ["ruff"], "removed": []}


def test_tools_receipt_scenarios(tmp_path):
    # `uv tool install --with idna ruff` writes both packages into one receipt;
    # the tool env dir name identifies the tool package
    tools_dir = tmp_path / "tools"
    tool_dir = tools_dir / "ruff"
    tool_dir.mkdir(parents=True)
    (tool_dir / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [\n    { name = "ruff", specifier = "==1.0" },\n    { name = "idna" },\n]\n',
        encoding="utf-8",
    )
    (tools_dir / ".lock").write_text("", encoding="utf-8")  # lock files are ignored
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    make_context(tmp_path, command=command)

    result = uv.tools(["ruff"])

    assert result.changed is False  # installed with the same pin: no reinstall
    assert result.summary == "ensure tools are present: ruff"

    # a malformed receipt is skipped with a note and the tool is treated as missing
    malformed_dir = tmp_path / "malformed-tools"
    malformed_tool = malformed_dir / "ruff"
    malformed_tool.mkdir(parents=True)
    (malformed_tool / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")  # no requirements

    def install(argv):
        make_receipt(malformed_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(malformed_dir)), install]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(["ruff"])

    assert any("skipping uv tool receipt" in note for note in ctx.notes)
    assert argvs(command)[1] == [UV, "tool", "install", "ruff"]
    assert result.changed is True


def test_tools_still_missing_after_install_fails(tmp_path):
    tools_dir = tmp_path / "tools"
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), response(0)]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still not installed after install: ruff"):
        uv.tools(["ruff"])


def test_tools_reinstall_with_a_different_specifier_is_rejected_when_with_requirements_exist(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0", extra_requirements=["idna"])
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="refusing to reinstall"):
        uv.tools(["ruff==2.0"])


def test_tools_validates_arguments(tmp_path):
    make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="tool names must not be empty"):
        uv.tools([])
    with pytest.raises(TaskFailed, match="tool name must not be empty"):
        uv.tools(["ok", ""])
    with pytest.raises(TaskFailed, match="invalid uv state"):
        uv.tools(["ok"], state="reinstall")
    with pytest.raises(TaskFailed, match="python must not be empty"):
        uv.tools(["ok"], python="")
    with pytest.raises(TaskFailed, match="extra arg must not be empty"):
        uv.tools(["ok"], extra_args="")
    with pytest.raises(TaskFailed, match="registry requirements only"):
        uv.tools(["git+https://example.com/x.git"])
    with pytest.raises(TaskFailed, match="supports package names only"):
        uv.tools(["https://example.com/x.whl"], state="absent")


def test_config_set_and_absent_are_idempotent(tmp_path):
    # setting an option replaces the existing line
    path = tmp_path / "uv.toml"
    path.write_text('index-url = "old"\nkeep = 1\n', encoding="utf-8")
    ctx = make_context(tmp_path)

    result = uv.config("index-url", "https://pypi.example.com/simple", path=path)

    assert path.read_text() == 'index-url = "https://pypi.example.com/simple"\nkeep = 1\n'
    assert result.action == "uv.config"
    assert result.changed is True
    assert result.summary == f"set index-url in {path}"
    assert call_data(result) == {
        "path": str(path),
        "table": None,
        "key": "index-url",
        "value": "https://pypi.example.com/simple",
        "toml_action": "replaced",
    }

    result = uv.config("index-url", "https://pypi.example.com/simple", path=path)
    assert result.changed is False
    assert result.summary == f'ensure index-url is "https://pypi.example.com/simple" in {path}'
    assert call_data(result)["toml_action"] == "unchanged"
    assert len(ctx.changes) == 1

    # removing an option is idempotent too
    absent_path = tmp_path / "absent.toml"
    absent_path.write_text('index-url = "https://x"\nkeep = 1\n', encoding="utf-8")
    absent_ctx = make_context(tmp_path)

    result = uv.config("index-url", state="absent", path=absent_path)

    assert absent_path.read_text() == "keep = 1\n"
    assert result.changed is True
    assert result.summary == f"remove index-url from {absent_path}"
    assert call_data(result)["value"] is None
    assert call_data(result)["toml_action"] == "removed"

    result = uv.config("index-url", state="absent", path=absent_path)
    assert result.changed is False
    assert result.summary == f"ensure index-url is absent from {absent_path}"
    assert len(absent_ctx.changes) == 1


def test_config_creates_missing_file_and_normalizes_values(tmp_path):
    # a missing file is created with parents
    path = tmp_path / "deep" / "uv" / "uv.toml"
    make_context(tmp_path)

    result = uv.config("concurrent-downloads", 4, path=path)

    assert path.read_text() == "concurrent-downloads = 4\n"
    assert call_data(result)["toml_action"] == "added"

    # typed values are serialized with TOML types
    typed = tmp_path / "typed.toml"
    make_context(tmp_path)

    uv.config("native-tls", value=True, path=typed)
    uv.config("concurrent-downloads", 4, path=typed)
    uv.config("required-version", "0.9", path=typed)
    uv.config("default-groups", ["dev", "lint"], path=typed)

    assert typed.read_text() == (
        'native-tls = true\nconcurrent-downloads = 4\nrequired-version = "0.9"\ndefault-groups = ["dev", "lint"]\n'
    )


def test_config_dry_run_reports_without_writing(tmp_path):
    path = tmp_path / "uv.toml"
    make_context(tmp_path, dry_run=True)

    result = uv.config("native-tls", value=True, path=path)

    assert result.changed is True
    assert not path.exists()


def test_config_pyproject_path_and_project_scope(tmp_path):
    # a pyproject path writes into [tool.uv]
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "demo"\n', encoding="utf-8")
    make_context(tmp_path)

    result = uv.config("python-downloads", "manual", path=path)

    assert path.read_text() == '[project]\nname = "demo"\n[tool.uv]\npython-downloads = "manual"\n'
    assert result["table"] == "tool.uv"
    assert result.summary == f"set python-downloads in [tool.uv] of {path}"

    # project scope edits the project's pyproject.toml
    project = tmp_path / "proj"
    write_pyproject(project)
    make_context(tmp_path)

    result = uv.config("native-tls", value=True, scope="project", project=project)

    assert result["path"] == str(project / "pyproject.toml")
    assert result["table"] == "tool.uv"
    assert "[tool.uv]\nnative-tls = true\n" in (project / "pyproject.toml").read_text()


def test_config_user_scope_and_guards(tmp_path, monkeypatch):
    # user scope honors XDG_CONFIG_HOME
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    make_context(tmp_path)

    result = uv.config("native-tls", value=True, scope="user")

    target = tmp_path / "xdg" / "uv" / "uv.toml"
    assert target.read_text() == "native-tls = true\n"
    assert result["path"] == str(target)

    # user scope cannot run as root
    local_sudo_context(tmp_path)  # is_root=True

    with pytest.raises(TaskFailed, match="user-scoped uv config cannot run as root"):
        uv.config("native-tls", value=True, scope="user")

    # user scope cannot use sudo
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    make_context(tmp_path)  # non-root

    with pytest.raises(TaskFailed, match="user-scoped uv config cannot use sudo"):
        uv.config("native-tls", value=True, scope="user", sudo=True)
    with pytest.raises(TaskFailed, match="user-scoped uv config cannot use sudo"):
        uv.config("native-tls", state="absent", scope="user", sudo=True)
    assert not (tmp_path / ".config").exists()


def test_config_scope_system_defaults_to_sudo(tmp_path, monkeypatch):
    target = tmp_path / "uv.toml"
    monkeypatch.setattr(uv, "_SYSTEM_CONFIG_PATH", target)
    ctx = privileged_context(tmp_path)

    try:
        result = uv.config("index-url", "https://pypi.example.com/simple", scope="system")
        assert result["path"] == str(target)
        assert target.read_text() == 'index-url = "https://pypi.example.com/simple"\n'
        assert ctx.sudo.refresh_count > 0
    finally:
        ctx.close()


def test_config_validates_arguments_before_touching_the_file(tmp_path):
    missing = tmp_path / "missing.toml"
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="config state 'present' requires a value"):
        uv.config("native-tls", path=missing)
    with pytest.raises(TaskFailed, match="config state 'absent' does not take a value"):
        uv.config("native-tls", value=True, state="absent", path=missing)
    with pytest.raises(TaskFailed, match="invalid config state"):
        uv.config("native-tls", value=True, state="maybe", path=missing)
    with pytest.raises(TaskFailed, match="invalid uv config scope"):
        uv.config("native-tls", value=True, scope="global", path=missing)
    with pytest.raises(TaskFailed, match="project scope requires a project directory"):
        uv.config("native-tls", value=True, scope="project")
    with pytest.raises(TaskFailed, match="uv config option"):
        uv.config("bad key", value=True, path=missing)
    with pytest.raises(TaskFailed, match="uv config option"):
        uv.config('"quoted"', value=True, path=missing)
    with pytest.raises(TaskFailed, match="config sudo must be a boolean"):
        uv.config("native-tls", value=True, path=missing, sudo="yes")
    with pytest.raises(TaskFailed, match="config path must not be empty"):
        uv.config("native-tls", value=True, path="")
    with pytest.raises(TaskFailed, match="project must not be empty"):
        uv.config("native-tls", value=True, scope="project", project=" ")
    with pytest.raises(TaskFailed, match="unsupported toml value type"):
        uv.config("native-tls", value={"a": 1}, path=missing)
    with pytest.raises(TaskFailed, match="must be a sequence"):
        uv.config("default-groups", value={"a", "b"}, path=missing)
    with pytest.raises(TaskFailed, match="toml array items must be strings"):
        uv.config("default-groups", value=["a", 1], path=missing)
    with pytest.raises(TaskFailed, match="toml value must not be empty"):
        uv.config("native-tls", value="  ", path=missing)
    assert not missing.exists()
