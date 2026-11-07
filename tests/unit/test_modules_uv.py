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
    config_context,
    make_context,
    privileged_context,
    response,
)

UV = "/usr/bin/uv"


@pytest.fixture(autouse=True)
def uv_on_path(monkeypatch):
    monkeypatch.setattr("kitup.modules.uv.shutil.which", lambda name: UV if name == "uv" else None)


def write_pyproject(project, body: str = "[]") -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / ".venv").mkdir(exist_ok=True)  # a synced project has a project environment
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.14"\ndependencies = {body}\n',
        encoding="utf-8",
    )


def stale_probes() -> list:
    """Scripted pre-sync probes when the project is not converged.

    The lock probe is skipped because the sync probe already failed.
    """
    return [response(1)]


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


def test_present_adds_a_missing_dependency_and_syncs(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project)
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests>=2"], project=project)

    assert argvs(command) == [
        [UV, "sync", "--project", str(project), "--check"],
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


def test_present_is_idempotent_when_in_sync(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project, '["requests>=2"]')
    command = FakeCommandRunner()
    command.responses = synced_probes()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests>=2"], project=project)

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


def test_present_replaces_a_differing_specifier_verbatim(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["requests >= 2"]')
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests>=3"], project=project)

    assert 'dependencies = ["requests>=3"]' in pyproject.read_text()
    assert call_data(result)["added"] == ["requests>=3"]
    assert result.summary == f"+requests>=3; sync {project}"


def test_present_matches_normalized_names(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["Charset_Normalizer>=3"]')
    command = FakeCommandRunner()
    command.responses = synced_probes()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["charset-normalizer"], project=project)

    # same normalized name, different verbatim string: replaced
    assert result.changed is True
    assert 'dependencies = ["charset-normalizer"]' in pyproject.read_text()


def test_present_keeps_the_multi_line_layout_of_the_table(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '[\n    "flask",\n]')
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    uv.dependencies(ctx, ["requests"], project=project)

    assert pyproject.read_text().endswith('dependencies = [\n    "flask",\n    "requests",\n]\n')


def test_present_dry_run_reports_without_writing_or_syncing(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project)
    command = FakeCommandRunner()
    command.responses = stale_probes()
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project)

    assert argvs(command) == [[UV, "sync", "--project", str(project), "--check"]]
    assert result.changed is True
    assert result.summary == f"+requests; sync {project}"
    assert pyproject.read_text().endswith("dependencies = []\n")


def test_latest_upgrades_only_bare_names(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project, '["flask>=1"]')
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests", "flask>=1"], project=project, state="latest")

    assert argvs(command)[1] == [
        UV,
        "sync",
        "--project",
        str(project),
        "--upgrade-package",
        "requests",
    ]
    assert result.changed is True
    assert result.summary == f"+requests; sync {project}"


def test_latest_is_idempotent_when_in_sync(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project, '["requests"]')
    command = FakeCommandRunner()
    command.responses = synced_probes()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project, state="latest")

    assert result.changed is False
    assert result.summary == "ensure dependencies are up to date: requests"


def test_absent_removes_entries_by_name_and_syncs(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["requests>=2", "flask"]')
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests==2.32"], project=project, state="absent")

    assert 'dependencies = ["flask"]' in pyproject.read_text()
    assert result.changed is True
    assert result.summary == f"-requests>=2; sync {project}"
    assert call_data(result) == {
        "dependencies": ["requests==2.32"],
        "project": str(project),
        "removed": ["requests>=2"],
    }


def test_absent_is_idempotent_when_nothing_matches(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project, '["flask"]')
    command = FakeCommandRunner()
    command.responses = synced_probes()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project, state="absent")

    assert result.changed is False
    assert result.summary == "ensure dependencies are absent: requests"
    assert call_data(result) == {
        "dependencies": ["requests"],
        "project": str(project),
        "removed": [],
    }


def test_absent_with_a_missing_project_is_a_no_change_without_commands(tmp_path):
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=tmp_path / "missing", state="absent")

    assert command.calls == []
    assert result.changed is False
    assert result.summary == "ensure dependencies are absent: requests"


def test_missing_project_is_initialized_with_uv_init_bare(tmp_path):
    project = tmp_path / "proj"

    def init(argv):
        write_pyproject(project)
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [init, *stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project)

    assert argvs(command)[0] == [UV, "init", "--bare", str(project)]
    assert result.changed is True
    assert result.summary == f"init project {project}; +requests; sync {project}"


def test_missing_project_dry_run_reports_without_running_anything(tmp_path):
    project = tmp_path / "proj"
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project)

    assert command.calls == []
    assert result.changed is True
    assert result.summary == f"init project {project}; +requests; sync {project}"
    assert not project.exists()


def test_project_path_that_is_not_a_directory_is_rejected(tmp_path):
    file = tmp_path / "file"
    file.write_text("", encoding="utf-8")
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="not a directory"):
        uv.dependencies(ctx, ["requests"], project=file)


def test_symlinked_project_directory_is_followed_but_a_symlinked_pyproject_is_refused(tmp_path):
    real = tmp_path / "real"
    write_pyproject(real, '["requests"]')
    link = tmp_path / "link"
    link.symlink_to(real)
    command = FakeCommandRunner()
    command.responses = synced_probes()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=link)

    assert result.changed is False  # the symlinked directory is a valid project

    (real / "pyproject.toml").unlink()
    (real / "pyproject.toml").symlink_to(tmp_path / "elsewhere.toml")
    with pytest.raises(TaskFailed, match="refusing to edit symlink"):
        uv.dependencies(ctx, ["requests"], project=real)


def test_pyproject_without_a_project_table_is_rejected_for_present(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".venv").mkdir()
    (project / "pyproject.toml").write_text("[dependency-groups]\nlint = []\n", encoding="utf-8")
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match=r"no \[project\] table"):
        uv.dependencies(ctx, ["requests"], project=project)


def test_absent_with_an_undefined_group_is_a_no_change_without_commands(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "\n[dependency-groups]\nother = []\n", encoding="utf-8")
    command = FakeCommandRunner()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["ruff"], project=project, group="lint", state="absent")

    assert command.calls == []  # no uv probe would succeed with an undefined --group
    assert result.changed is False
    assert result.summary == "ensure dependencies are absent: ruff"


def test_dotted_group_names_are_rejected(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="must be a single key, not dotted"):
        uv.dependencies(ctx, ["ruff"], project=project, group="a.b")


def test_non_registry_entries_are_preserved_verbatim(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["flask", "requests @ https://example.com/requests.whl", "./local"]')
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests>=2"], project=project)

    text = pyproject.read_text()
    assert '"requests @ https://example.com/requests.whl"' in text  # not rewritten to a bare name
    assert '"./local"' in text  # not matched (and no crash parsing it)
    assert '"requests>=2"' in text
    assert '"flask"' in text
    assert call_data(result)["added"] == ["requests>=2"]


def test_duplicate_entries_for_a_name_are_all_reconciled(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project, '["flask", "flask>=2"]')
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    uv.dependencies(ctx, ["flask>=3"], project=project)

    assert 'dependencies = ["flask>=3"]' in pyproject.read_text()


def test_group_edits_dependency_groups_and_passes_the_group_flag(tmp_path):
    project = tmp_path / "proj"
    pyproject = project / "pyproject.toml"
    write_pyproject(project)
    text = pyproject.read_text() + "\n[dependency-groups]\nlint = []\n"
    pyproject.write_text(text, encoding="utf-8")
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["ruff"], project=project, group="lint")

    assert argvs(command)[0] == [UV, "sync", "--project", str(project), "--group", "lint", "--check"]
    assert argvs(command)[1] == [UV, "sync", "--project", str(project), "--group", "lint"]
    assert 'lint = ["ruff"]' in pyproject.read_text()
    assert result.summary == f"+ruff; sync {project}"


def test_python_is_passed_as_the_python_flag(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    uv.dependencies(ctx, ["requests"], project=project, python="3.13")

    assert argvs(command)[0] == [UV, "sync", "--project", str(project), "--python", "3.13", "--check"]
    assert argvs(command)[1] == [UV, "sync", "--project", str(project), "--python", "3.13"]


def test_python_is_passed_to_uv_init(tmp_path):
    project = tmp_path / "proj"

    def init(argv):
        write_pyproject(project)
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [init, *stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    uv.dependencies(ctx, ["requests"], project=project, python="3.13")

    assert argvs(command)[0] == [UV, "init", "--bare", "--python", "3.13", str(project)]


def test_extra_args_are_passed_to_the_probes_and_sync(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    command = FakeCommandRunner()
    command.responses = [*stale_probes(), response(0), *post_sync_ok()]
    ctx = make_context(tmp_path, command=command)

    uv.dependencies(ctx, ["requests"], project=project, extra_args=["--no-index", "--find-links=/srv/repo"])

    sync_run = [UV, "sync", "--project", str(project), "--no-index", "--find-links=/srv/repo"]
    assert argvs(command) == [
        [*sync_run, "--check"],  # pre-sync probe
        sync_run,  # the sync itself
        [*sync_run, "--check"],  # post-sync check
        [UV, "lock", "--project", str(project), "--no-index", "--find-links=/srv/repo", "--check"],
    ]


def test_sync_still_out_of_sync_after_sync_fails(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    command = FakeCommandRunner()
    # pre-sync probe fails, the sync succeeds, then the lock post-check fails
    command.responses = [response(1), response(0), response(0), response(1)]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="not synchronized after uv sync"):
        uv.dependencies(ctx, ["requests"], project=project)


def test_latest_probe_is_upgrade_aware_so_an_available_upgrade_is_detected(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project, '["requests"]')
    command = FakeCommandRunner()
    # the upgrade-aware pre-probe fails (a newer version is available), the sync and post-checks pass
    command.responses = [response(1), response(0), response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project, state="latest")

    assert argvs(command) == [
        [UV, "sync", "--project", str(project), "--upgrade-package", "requests", "--check"],
        [UV, "sync", "--project", str(project), "--upgrade-package", "requests"],
        [UV, "sync", "--project", str(project), "--check"],
        [UV, "lock", "--project", str(project), "--check"],
    ]
    assert result.changed is True
    assert result.summary == f"sync {project}"


def test_latest_pre_probe_stays_plain_after_a_sync(tmp_path):
    # the post-sync checks are plain --check probes, without --upgrade-package
    project = tmp_path / "proj"
    write_pyproject(project, '["requests"]')
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    uv.dependencies(ctx, ["requests"], project=project, state="latest")

    assert "--upgrade-package" not in argvs(command)[2]
    assert "--upgrade-package" not in argvs(command)[3]


def test_converged_requires_the_project_environment(tmp_path):
    # uv sync --check exits 0 when only the .venv creation is pending, so the
    # environment's existence is checked too
    project = tmp_path / "proj"
    write_pyproject(project, '["requests"]')
    shutil.rmtree(project / ".venv")
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(0)]  # sync run + both post-checks
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project)

    assert argvs(command) == [
        [UV, "sync", "--project", str(project)],
        [UV, "sync", "--project", str(project), "--check"],
        [UV, "lock", "--project", str(project), "--check"],
    ]
    assert result.changed is True
    assert result.summary == f"sync {project}"


def test_project_environment_honors_uv_project_environment(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    write_pyproject(project, '["requests"]')
    shutil.rmtree(project / ".venv")
    env = tmp_path / "custom-env"
    env.mkdir()
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(env))
    command = FakeCommandRunner()
    command.responses = synced_probes()
    ctx = make_context(tmp_path, command=command)

    result = uv.dependencies(ctx, ["requests"], project=project)

    assert argvs(command) == [
        [UV, "sync", "--project", str(project), "--check"],
        [UV, "lock", "--project", str(project), "--check"],
    ]
    assert result.changed is False


def test_dependencies_rejects_non_array_tables(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project, '"requests"')
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="not an array of strings"):
        uv.dependencies(ctx, ["requests"], project=project)


def test_dependencies_validates_arguments(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="dependency names must not be empty"):
        uv.dependencies(ctx, [], project=project)
    with pytest.raises(TaskFailed, match="dependency name must not be empty"):
        uv.dependencies(ctx, ["ok", "  "], project=project)
    with pytest.raises(TaskFailed, match="invalid uv state"):
        uv.dependencies(ctx, ["ok"], project=project, state="forcereinstall")
    with pytest.raises(TaskFailed, match="project must not be empty"):
        uv.dependencies(ctx, ["ok"], project=" ")
    with pytest.raises(TaskFailed, match="dependency group"):
        uv.dependencies(ctx, ["ok"], project=project, group="bad group")
    with pytest.raises(TaskFailed, match="python must not be empty"):
        uv.dependencies(ctx, ["ok"], project=project, python="")
    with pytest.raises(TaskFailed, match="extra arg must not be empty"):
        uv.dependencies(ctx, ["ok"], project=project, extra_args=[""])
    for bad in (
        "git+https://github.com/org/repo.git",
        "https://example.com/pkg.whl",
        "./pkg.whl",
        "foo @ https://x/y.whl",
    ):
        with pytest.raises(TaskFailed, match="registry requirements only"):
            uv.dependencies(ctx, [bad], project=project)
        with pytest.raises(TaskFailed, match="supports package names only"):
            uv.dependencies(ctx, [bad], project=project, state="absent")
    # uv tool specifier syntax is not valid in project dependency tables
    for bad in ("ruff@latest", "ruff@0.17.4"):
        with pytest.raises(TaskFailed, match="'@' specifiers are uv tool syntax"):
            uv.dependencies(ctx, [bad], project=project)
    for bad in ("requests>>2", "pkg[", "req;", "two words", "req >= "):
        with pytest.raises(TaskFailed, match="invalid"):
            uv.dependencies(ctx, [bad], project=project)


def test_tools_accepts_at_specifiers_but_dependencies_does_not(tmp_path):
    tools_dir = tmp_path / "tools"

    def install(argv):
        make_receipt(tools_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    ctx = make_context(tmp_path, command=command)

    # "pkg@latest" is a documented uv tool specifier; the tool is not installed, so it installs
    uv.tools(ctx, ["ruff@latest"])

    assert argvs(command)[1] == [UV, "tool", "install", "ruff@latest"]


def test_missing_uv_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup.modules.uv.shutil.which", lambda name: None)
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="uv is required but was not found"):
        uv.dependencies(ctx, ["requests"], project=tmp_path)
    with pytest.raises(TaskFailed, match="uv is required but was not found"):
        uv.tools(ctx, ["ruff"])


def test_tools_install_missing_tool(tmp_path):
    tools_dir = tmp_path / "tools"

    def install(argv):
        make_receipt(tools_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"])

    assert argvs(command) == [[UV, "tool", "dir"], [UV, "tool", "install", "ruff"]]
    assert result.action == "uv.tools"
    assert result.changed is True
    assert result.summary == "+ruff"
    assert call_data(result) == {"tools": ["ruff"], "added": ["ruff"]}


def test_tools_present_is_idempotent_when_the_receipt_matches(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"])

    assert argvs(command) == [[UV, "tool", "dir"]]
    assert result.changed is False
    assert result.summary == "ensure tools are present: ruff"
    assert call_data(result) == {"tools": ["ruff"], "added": []}


def test_tools_present_reinstalls_a_differing_specifier(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0")

    def install(argv):
        make_receipt(tools_dir, "ruff", specifier="==2.0")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff==2.0"])

    assert argvs(command)[1] == [UV, "tool", "install", "ruff==2.0"]
    assert result.changed is True
    assert call_data(result)["added"] == ["ruff==2.0"]


def test_tools_present_leaves_a_satisfied_bare_name_alone(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0")
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"])

    assert argvs(command) == [[UV, "tool", "dir"]]
    assert result.changed is False


def test_tools_latest_upgrades_an_installed_bare_tool(tmp_path):
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
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"], state="latest")

    assert argvs(command) == [
        [UV, "tool", "dir"],
        [UV, "tool", "list"],
        [UV, "tool", "upgrade", "ruff"],
        [UV, "tool", "list"],
    ]
    assert result.changed is True
    assert result.summary == "+ruff"
    assert call_data(result)["added"] == ["ruff"]


def test_tools_latest_reinstalls_a_pinned_tool_at_latest(tmp_path):
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
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"], state="latest")

    # a pinned tool is refreshed with @latest, which uv tool upgrade refuses to move
    assert argvs(command)[2] == [UV, "tool", "install", "ruff@latest"]
    assert result.changed is True
    assert call_data(result)["added"] == ["ruff"]


def test_tools_latest_is_idempotent_when_the_version_does_not_change(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(tools_dir)),
        response(0, stdout="ruff v2.0\n- ruff\n"),
        response(0),
        response(0, stdout="ruff v2.0\n- ruff\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"], state="latest")

    assert argvs(command)[2] == [UV, "tool", "upgrade", "ruff"]
    assert result.changed is False
    assert result.summary == "ensure tools are up to date: ruff"


def test_tools_absent_uninstalls_and_checks_the_receipts(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")

    def uninstall(argv):
        shutil.rmtree(tools_dir / "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), uninstall]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"], state="absent")

    assert argvs(command) == [[UV, "tool", "dir"], [UV, "tool", "uninstall", "ruff"]]
    assert result.changed is True
    assert result.summary == "-ruff"
    assert call_data(result) == {"tools": ["ruff"], "removed": ["ruff"]}


def test_tools_absent_is_idempotent_when_nothing_is_installed(tmp_path):
    tools_dir = tmp_path / "tools"
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"], state="absent")

    assert argvs(command) == [[UV, "tool", "dir"]]
    assert result.changed is False
    assert result.summary == "ensure tools are absent: ruff"
    assert call_data(result) == {"tools": ["ruff"], "removed": []}


def test_tools_dry_run_reports_without_installing(tmp_path):
    tools_dir = tmp_path / "tools"
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    result = uv.tools(ctx, ["ruff"])

    assert argvs(command) == [[UV, "tool", "dir"]]
    assert result.changed is True
    assert result.summary == "+ruff"
    assert not (tools_dir / "ruff").exists()


def test_tools_passes_python_and_extra_args_to_install(tmp_path):
    tools_dir = tmp_path / "tools"

    def install(argv):
        make_receipt(tools_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    ctx = make_context(tmp_path, command=command)

    uv.tools(ctx, ["ruff"], python="3.13", extra_args=["--no-index", "--find-links=/srv/repo"])

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


def test_tools_still_missing_after_install_fails(tmp_path):
    tools_dir = tmp_path / "tools"
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), response(0)]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still not installed after install: ruff"):
        uv.tools(ctx, ["ruff"])


def test_tools_reads_the_tool_requirement_from_multi_requirement_receipts(tmp_path):
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
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"])

    assert result.changed is False  # installed with the same pin: no reinstall
    assert result.summary == "ensure tools are present: ruff"


def test_tools_skips_a_malformed_receipt_with_a_note_and_treats_the_tool_as_missing(tmp_path):
    tools_dir = tmp_path / "tools"
    tool_dir = tools_dir / "ruff"
    tool_dir.mkdir(parents=True)
    (tool_dir / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")  # no requirements

    def install(argv):
        make_receipt(tools_dir, "ruff")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"])

    assert any("skipping uv tool receipt" in note for note in ctx.notes)
    assert argvs(command)[1] == [UV, "tool", "install", "ruff"]
    assert result.changed is True


def test_tools_present_is_idempotent_for_at_specifiers_and_spacing(tmp_path):
    # the receipt normalizes "@1.0" to specifier "==1.0"
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0")
    for wanted in ("ruff@1.0", "ruff==1.0", "ruff == 1.0"):
        command = FakeCommandRunner()
        command.responses = [response(0, stdout=str(tools_dir))]
        ctx = make_context(tmp_path, command=command)

        result = uv.tools(ctx, [wanted])

        assert result.changed is False
        assert argvs(command) == [[UV, "tool", "dir"]]


def test_tools_present_is_idempotent_when_extras_and_marker_match(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0", extras=["socks"], marker='python_version >= "3.9"')
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ['ruff[socks]==1.0; python_version >= "3.9"'])

    assert result.changed is False
    assert argvs(command) == [[UV, "tool", "dir"]]


def test_tools_present_with_at_latest_is_a_no_change_when_the_tool_is_installed(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff")  # unpinned receipt
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff@latest"])

    assert result.changed is False
    assert argvs(command) == [[UV, "tool", "dir"]]


def test_tools_present_reinstalls_when_extras_or_marker_differ(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0", extras=["socks"])

    def install(argv):
        make_receipt(tools_dir, "ruff", specifier="==1.0")
        return completed(argv, 0)

    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir)), install]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff==1.0"])  # extras dropped: reinstall

    assert argvs(command)[1] == [UV, "tool", "install", "ruff==1.0"]
    assert result.changed is True


def test_tools_latest_uses_upgrade_for_a_range_pin_not_only_exact_pins(tmp_path):
    # only an exact "==" pin needs the @latest reinstall; a range is moved by uv tool upgrade
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier=">=1.0")
    command = FakeCommandRunner()
    command.responses = [
        response(0, stdout=str(tools_dir)),
        response(0, stdout="ruff v1.0\n- ruff\n"),
        response(0),
        response(0, stdout="ruff v2.0\n- ruff\n"),
    ]
    ctx = make_context(tmp_path, command=command)

    result = uv.tools(ctx, ["ruff"], state="latest")

    assert argvs(command)[2] == [UV, "tool", "upgrade", "ruff"]
    assert result.changed is True


def test_tools_reinstall_with_a_different_specifier_is_rejected_when_with_requirements_exist(tmp_path):
    tools_dir = tmp_path / "tools"
    make_receipt(tools_dir, "ruff", specifier="==1.0", extra_requirements=["idna"])
    command = FakeCommandRunner()
    command.responses = [response(0, stdout=str(tools_dir))]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="refusing to reinstall"):
        uv.tools(ctx, ["ruff==2.0"])


def test_failing_tool_dir_probe_distinguishes_missing_uv(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="boom"), response(1, stderr="boom")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="uv could not run"):
        uv.tools(ctx, ["ruff"])
    assert argvs(command)[1] == [UV, "--version"]

    command = FakeCommandRunner()
    command.responses = [response(1, stderr="boom"), response(0, stdout="uv 0.9.0")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not determine the uv tools directory"):
        uv.tools(ctx, ["ruff"])


def test_tools_validates_arguments(tmp_path):
    ctx = make_context(tmp_path, command=FakeCommandRunner())

    with pytest.raises(TaskFailed, match="tool names must not be empty"):
        uv.tools(ctx, [])
    with pytest.raises(TaskFailed, match="tool name must not be empty"):
        uv.tools(ctx, ["ok", ""])
    with pytest.raises(TaskFailed, match="invalid uv state"):
        uv.tools(ctx, ["ok"], state="reinstall")
    with pytest.raises(TaskFailed, match="python must not be empty"):
        uv.tools(ctx, ["ok"], python="")
    with pytest.raises(TaskFailed, match="extra arg must not be empty"):
        uv.tools(ctx, ["ok"], extra_args="")
    with pytest.raises(TaskFailed, match="registry requirements only"):
        uv.tools(ctx, ["git+https://example.com/x.git"])
    with pytest.raises(TaskFailed, match="supports package names only"):
        uv.tools(ctx, ["https://example.com/x.whl"], state="absent")


def test_config_sets_an_option_in_a_uv_toml_and_is_idempotent(tmp_path):
    path = tmp_path / "uv.toml"
    path.write_text('index-url = "old"\nkeep = 1\n', encoding="utf-8")
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "index-url", "https://pypi.example.com/simple", path=path)

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

    result = uv.config(ctx, "index-url", "https://pypi.example.com/simple", path=path)
    assert result.changed is False
    assert result.summary == f'ensure index-url is "https://pypi.example.com/simple" in {path}'
    assert call_data(result)["toml_action"] == "unchanged"
    assert len(ctx.changes) == 1


def test_config_creates_a_missing_file_with_parents(tmp_path):
    path = tmp_path / "deep" / "uv" / "uv.toml"
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "concurrent-downloads", 4, path=path)

    assert path.read_text() == "concurrent-downloads = 4\n"
    assert call_data(result)["toml_action"] == "added"


def test_config_normalizes_typed_values(tmp_path):
    path = tmp_path / "uv.toml"
    ctx = make_context(tmp_path)

    uv.config(ctx, "native-tls", value=True, path=path)
    uv.config(ctx, "concurrent-downloads", 4, path=path)
    uv.config(ctx, "required-version", "0.9", path=path)
    uv.config(ctx, "default-groups", ["dev", "lint"], path=path)

    assert path.read_text() == (
        'native-tls = true\nconcurrent-downloads = 4\nrequired-version = "0.9"\ndefault-groups = ["dev", "lint"]\n'
    )


def test_config_absent_removes_the_key_and_is_idempotent(tmp_path):
    path = tmp_path / "uv.toml"
    path.write_text('index-url = "https://x"\nkeep = 1\n', encoding="utf-8")
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "index-url", state="absent", path=path)

    assert path.read_text() == "keep = 1\n"
    assert result.changed is True
    assert result.summary == f"remove index-url from {path}"
    assert call_data(result)["value"] is None
    assert call_data(result)["toml_action"] == "removed"

    result = uv.config(ctx, "index-url", state="absent", path=path)
    assert result.changed is False
    assert result.summary == f"ensure index-url is absent from {path}"


def test_config_dry_run_reports_without_writing(tmp_path):
    path = tmp_path / "uv.toml"
    ctx = make_context(tmp_path, dry_run=True)

    result = uv.config(ctx, "native-tls", value=True, path=path)

    assert result.changed is True
    assert not path.exists()


def test_config_a_pyproject_path_uses_the_tool_uv_table(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "demo"\n', encoding="utf-8")
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "python-downloads", "manual", path=path)

    assert path.read_text() == '[project]\nname = "demo"\n[tool.uv]\npython-downloads = "manual"\n'
    assert result["table"] == "tool.uv"
    assert result.summary == f"set python-downloads in [tool.uv] of {path}"


def test_config_scope_project_edits_the_project_pyproject(tmp_path):
    project = tmp_path / "proj"
    write_pyproject(project)
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "native-tls", value=True, scope="project", project=project)

    assert result["path"] == str(project / "pyproject.toml")
    assert result["table"] == "tool.uv"
    assert "[tool.uv]\nnative-tls = true\n" in (project / "pyproject.toml").read_text()


def test_config_scope_user_uses_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "native-tls", value=True, scope="user")

    target = tmp_path / "xdg" / "uv" / "uv.toml"
    assert target.read_text() == "native-tls = true\n"
    assert result["path"] == str(target)


def test_config_scope_user_defaults_to_dot_config(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "native-tls", value=True, scope="user")

    target = tmp_path / ".config" / "uv" / "uv.toml"
    assert target.read_text() == "native-tls = true\n"
    assert result["path"] == str(target)


def test_config_scope_system_defaults_to_sudo(tmp_path, monkeypatch):
    target = tmp_path / "uv.toml"
    monkeypatch.setattr(uv, "_SYSTEM_CONFIG_PATH", target)
    ctx = privileged_context(tmp_path)

    try:
        result = uv.config(ctx, "index-url", "https://pypi.example.com/simple", scope="system")
        assert result["path"] == str(target)
        assert target.read_text() == 'index-url = "https://pypi.example.com/simple"\n'
        assert ctx.sudo.refresh_count > 0
    finally:
        ctx.close()


def test_config_scope_user_rejected_as_root(tmp_path):
    ctx = config_context(tmp_path)  # is_root=True

    with pytest.raises(TaskFailed, match="user-scoped uv config cannot run as root"):
        uv.config(ctx, "native-tls", value=True, scope="user")


def test_config_scope_user_rejects_sudo(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    ctx = make_context(tmp_path)  # non-root

    with pytest.raises(TaskFailed, match="user-scoped uv config cannot use sudo"):
        uv.config(ctx, "native-tls", value=True, scope="user", sudo=True)
    with pytest.raises(TaskFailed, match="user-scoped uv config cannot use sudo"):
        uv.config(ctx, "native-tls", state="absent", scope="user", sudo=True)
    assert not (tmp_path / ".config").exists()


def test_config_validates_arguments_before_touching_the_file(tmp_path):
    missing = tmp_path / "missing.toml"
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="config state 'present' requires a value"):
        uv.config(ctx, "native-tls", path=missing)
    with pytest.raises(TaskFailed, match="config state 'absent' does not take a value"):
        uv.config(ctx, "native-tls", value=True, state="absent", path=missing)
    with pytest.raises(TaskFailed, match="invalid config state"):
        uv.config(ctx, "native-tls", value=True, state="maybe", path=missing)
    with pytest.raises(TaskFailed, match="invalid uv config scope"):
        uv.config(ctx, "native-tls", value=True, scope="global", path=missing)
    with pytest.raises(TaskFailed, match="project scope requires a project directory"):
        uv.config(ctx, "native-tls", value=True, scope="project")
    with pytest.raises(TaskFailed, match="uv config option"):
        uv.config(ctx, "bad key", value=True, path=missing)
    with pytest.raises(TaskFailed, match="uv config option"):
        uv.config(ctx, '"quoted"', value=True, path=missing)
    with pytest.raises(TaskFailed, match="config sudo must be a boolean"):
        uv.config(ctx, "native-tls", value=True, path=missing, sudo="yes")
    with pytest.raises(TaskFailed, match="config path must not be empty"):
        uv.config(ctx, "native-tls", value=True, path="")
    with pytest.raises(TaskFailed, match="project must not be empty"):
        uv.config(ctx, "native-tls", value=True, scope="project", project=" ")
    with pytest.raises(TaskFailed, match="unsupported toml value type"):
        uv.config(ctx, "native-tls", value={"a": 1}, path=missing)
    with pytest.raises(TaskFailed, match="must be a sequence"):
        uv.config(ctx, "default-groups", value={"a", "b"}, path=missing)
    with pytest.raises(TaskFailed, match="toml array items must be strings"):
        uv.config(ctx, "default-groups", value=["a", 1], path=missing)
    with pytest.raises(TaskFailed, match="toml value must not be empty"):
        uv.config(ctx, "native-tls", value="  ", path=missing)
    assert not missing.exists()


def test_config_writes_an_empty_array(tmp_path):
    path = tmp_path / "uv.toml"
    ctx = make_context(tmp_path)

    result = uv.config(ctx, "default-groups", [], path=path)

    assert path.read_text() == "default-groups = []\n"
    assert result.changed is True
