import tomllib
from pathlib import Path

import pytest

import kitup
from kitup import cli
from kitup.errors import PrivilegeError, TaskFailed

from ..helpers import make_project, write


@pytest.fixture(autouse=True)
def fake_facts(monkeypatch):
    monkeypatch.setattr(
        cli,
        "collect_facts",
        lambda: {
            "hostname": "host",
            "distro_id": "testos",
            "version_id": "1",
            "os_release": {
                "NAME": "Test Linux",
                "ID": "testos",
                "VERSION_ID": "1",
                "VARIANT": "Workstation Edition",
            },
        },
    )


def task_source(name, *, tags="", body="ctx.changed(summary='ran')"):
    decorator = f"@task(tags={{{tags!r}}})" if tags else "@task"
    return f"from kitup import ctx, task\n\n{decorator}\ndef {name}():\n    {body}\n"


def test_apply_uses_discovered_vars(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="VALUE = 'base'\n",
        var_files={"work": "VALUE = 'work'\n"},
        bundles={
            "base": """
            from kitup import ctx, task

            @task
            def show():
                ctx.changed(summary=f"value={ctx.vars.VALUE}")
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    assert code == 0
    assert "value=work" in capsys.readouterr().out


def test_project_dir_defaults_to_cwd_and_accepts_relative_paths(tmp_path, monkeypatch, capsys):
    make_project(tmp_path / "machine", bundles={"base": task_source("run")})
    monkeypatch.chdir(tmp_path)

    code = cli.main(["list", "machine", "--no-color"])
    assert code == 0
    assert "bundle: base" in capsys.readouterr().out

    monkeypatch.chdir(tmp_path / "machine")
    code = cli.main(["list", "--no-color"])
    assert code == 0
    assert "bundle: base" in capsys.readouterr().out


def test_list_does_not_load_var_files_but_check_does(tmp_path, capsys):
    make_project(tmp_path, vars="1 / 0\n", bundles={"base": task_source("run")})

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "bundle: base" in out
    assert "vars: base.py" in out

    code = cli.main(["check", str(tmp_path), "--no-color"])

    assert code == 2
    assert "division by zero" in capsys.readouterr().err

    bundle_vars = make_project(
        tmp_path / "bundle-vars",
        bundles={"base": {"tasks.py": task_source("run"), "vars/base.py": "1 / 0\n"}},
    )

    assert cli.main(["list", str(bundle_vars), "--no-color"]) == 0
    capsys.readouterr()

    assert cli.main(["check", str(bundle_vars), "--no-color"]) == 2
    assert "division by zero" in capsys.readouterr().err

    assert cli.main(["apply", str(bundle_vars), "-n", "--no-color"]) == 2
    assert "division by zero" in capsys.readouterr().err


def test_list_and_check_do_not_collect_facts(tmp_path, monkeypatch):
    make_project(tmp_path, bundles={"base": task_source("run")})

    def fail():
        raise AssertionError("collect_facts called")

    monkeypatch.setattr(cli, "collect_facts", fail)

    assert cli.main(["list", str(tmp_path), "--no-color"]) == 0
    assert cli.main(["check", str(tmp_path), "--no-color"]) == 0


def test_commands_without_bundles(tmp_path, capsys):
    code = cli.main(["apply", str(tmp_path)])

    assert code == 2
    error = capsys.readouterr().err
    assert "no bundles found" in error
    assert "kitup init" in error

    code = cli.main(["check", str(tmp_path)])

    assert code == 2
    assert "no bundles found" in capsys.readouterr().err

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"project: {tmp_path.name}" in out
    assert "bundle:" not in out


def test_check_prints_the_plan_and_warns_about_empty_bundles(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={"base": {"__init__.py": "from kitup import bundle\n\nbundle()\n"}},
    )

    code = cli.main(["check", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "bundle: base" in out
    assert "warning: bundle 'base' has no tasks" in out


def test_check_never_executes_tasks(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import ctx, task

            @task
            def explode():
                raise RuntimeError("task ran")
            """
        },
    )

    code = cli.main(["check", str(tmp_path), "--no-color"])

    assert code == 0
    assert "task ran" not in capsys.readouterr().out


def test_apply_honors_dry_run(tmp_path, capsys):
    marker = tmp_path / "marker.txt"
    make_project(
        tmp_path,
        bundles={
            "base": f"""
            from pathlib import Path

            from kitup import ctx, task

            @task
            def write_marker():
                if not ctx.dry_run:
                    Path({str(marker)!r}).write_text("applied")
                ctx.changed(summary="write marker")
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    assert code == 0
    assert not marker.exists()

    code = cli.main(["apply", str(tmp_path), "--no-color"])

    assert code == 0
    assert marker.read_text(encoding="utf-8") == "applied"


def test_apply_tag_filters(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import ctx, task

            @task(tags={'packages'})
            def packages():
                ctx.changed(summary="packages")

            @task(tags={'config'})
            def config():
                ctx.changed(summary="config")

            @task(tags={'docs'})
            def docs():
                ctx.changed(summary="docs")
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color", "-t", "packages,config"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Packages" in out
    assert "Config" in out
    assert "Docs" not in out

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color", "-x", "packages"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Packages" not in out
    assert "Config" in out
    assert "Docs" in out


def test_bundle_filters_support_include_exclude_comma_and_repeated_values(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": task_source("base_task", body="ctx.changed(summary='base')"),
            "desktop": task_source("desktop_task", body="ctx.changed(summary='desktop')"),
            "gaming": task_source("gaming_task", body="ctx.changed(summary='gaming')"),
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color", "-b", "desktop"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Desktop task" in out
    assert "Base task" not in out

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color", "-B", "desktop"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Base task" in out
    assert "Gaming task" in out
    assert "Desktop task" not in out

    code = cli.main(["list", str(tmp_path), "--no-color", "-b", "base,gaming"])

    out = capsys.readouterr().out
    assert code == 0
    assert "bundle: base" in out
    assert "bundle: gaming" in out
    assert "bundle: desktop" not in out

    code = cli.main(["list", str(tmp_path), "--no-color", "-B", "base", "-B", "gaming"])

    out = capsys.readouterr().out
    assert code == 0
    assert "bundle: desktop" in out
    assert "bundle: base" not in out
    assert "bundle: gaming" not in out


def test_unknown_bundle_filter_lists_available_ids(tmp_path, capsys):
    make_project(tmp_path, bundles={"base": task_source("run")})

    code = cli.main(["list", str(tmp_path), "-b", "missing"])

    assert code == 2
    assert "unknown bundle: missing (available: base)" in capsys.readouterr().err


def test_unknown_bundle_dependency_is_exit_2(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "desktop": {
                "__init__.py": "from kitup import bundle\n\nbundle(depends_on=['missing'])\n",
                "tasks.py": task_source("run"),
            }
        },
    )

    code = cli.main(["apply", str(tmp_path)])

    assert code == 2
    assert "unknown bundle dependency 'missing'" in capsys.readouterr().err


def test_var_filters_select_merged_values(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="VALUE = 'base'\n",
        var_files={"laptop": "VALUE = 'laptop'\n", "work": "VALUE = 'work'\n"},
        bundles={
            "base": """
            from kitup import ctx, task

            @task
            def show():
                ctx.changed(summary=f"value={ctx.vars.VALUE}")
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color", "-i", "laptop"])

    out = capsys.readouterr().out
    assert code == 0
    assert "value=laptop" in out

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color", "-E", "laptop"])

    out = capsys.readouterr().out
    assert code == 0
    assert "value=work" in out


def test_unknown_var_filter_lists_available_ids(tmp_path, capsys):
    make_project(tmp_path, vars="VALUE = 1\n", bundles={"base": task_source("run")})

    code = cli.main(["list", str(tmp_path), "-i", "missing"])

    assert code == 2
    assert "unknown var: missing (available: base)" in capsys.readouterr().err


def test_preset_sets_options_and_real_cli_overrides_them(tmp_path, capsys):
    make_project(
        tmp_path,
        preset={"laptop": "--include-bundles desktop\n"},
        bundles={
            "base": task_source("base_task", body="ctx.changed(summary='base')"),
            "desktop": task_source("desktop_task", body="ctx.changed(summary='desktop')"),
        },
    )

    code = cli.main(["apply", str(tmp_path), "--preset", "laptop", "-n", "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Desktop task" in out
    assert "Base task" not in out

    code = cli.main(["apply", str(tmp_path), "--preset", "laptop", "-b", "base", "-n", "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Base task" in out
    assert "Desktop task" not in out


def test_init_scaffolds_a_working_project(tmp_path, capsys):
    root = tmp_path / "machine"

    code = cli.main(["init", str(root), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Initialized kitup project" in out
    assert "kitup check" in out
    assert "kitup apply -n" in out
    assert (root / "bundles" / "example" / "__init__.py").is_file()
    assert (root / "bundles" / "example" / "tasks.py").is_file()
    assert (root / "vars" / "base.py").is_file()
    assert (root / "vars" / "example.py").is_file()
    assert (root / "presets" / "laptop").is_file()

    assert cli.main(["check", str(root), "--no-color"]) == 0
    assert cli.main(["list", str(root), "--no-color"]) == 0
    assert "bundle: Example bundle (example)" in capsys.readouterr().out


def test_init_refuses_to_overwrite_without_force(tmp_path, capsys):
    make_project(tmp_path, vars="VALUE = 1\n")

    code = cli.main(["init", str(tmp_path), "--no-color"])

    assert code == 2
    assert "refusing to overwrite existing files" in capsys.readouterr().err
    assert (tmp_path / "vars" / "base.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    code = cli.main(["init", str(tmp_path), "--force", "--no-color"])

    assert code == 0
    assert "PACKAGES" in (tmp_path / "vars" / "base.py").read_text(encoding="utf-8")


def test_unknown_user_in_cli_path_is_exit_2(capsys):
    code = cli.main(["init", "~bogususer/machine", "--no-color"])

    assert code == 2
    assert "cannot resolve path '~bogususer/machine'" in capsys.readouterr().err


def test_sudo_validation_failure_is_exit_2(tmp_path, capsys, monkeypatch):
    make_project(tmp_path, bundles={"base": task_source("run")})

    def fail_validation(self):
        raise PrivilegeError("no credentials")

    monkeypatch.setattr(cli.PrivilegeManager, "validate", fail_validation)

    code = cli.main(["apply", str(tmp_path), "-s"])

    assert code == 2
    assert "error: no credentials" in capsys.readouterr().err


def test_apply_closes_context_on_failure(tmp_path, capsys, monkeypatch):
    contexts = []

    class Context:
        def __init__(self, **kwargs):
            self.closed = False
            contexts.append(self)

        def close(self):
            self.closed = True

    class Runner:
        def __init__(self, plan, context, output, **kwargs):
            pass

        def run(self):
            raise TaskFailed("boom")

    monkeypatch.setattr(cli, "TaskContext", Context)
    monkeypatch.setattr(cli, "Runner", Runner)
    make_project(tmp_path, bundles={"base": task_source("run")})

    code = cli.main(["apply", str(tmp_path)])

    assert code == 1
    assert contexts and contexts[0].closed
    assert "error: boom" in capsys.readouterr().err


def test_invalid_task_tags_are_exit_2(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import ctx, task

            @task(tags={'packages', 1})
            def run():
                pass
            """
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 2
    assert "task tags must contain strings" in capsys.readouterr().err


def test_side_effects_are_listed_on_apply_and_hidden_from_list(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import ctx, side_effect, task

            @side_effect(one_shot=False)
            def reload_nginx():
                ctx.changed(summary="+reloaded")

            @task
            def configure_nginx():
                ctx.changed(summary="+conf")
                ctx.side_effect(reload_nginx)
            """
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Configure nginx" in out
    assert "Reload nginx" not in out

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Configure nginx" in out
    assert "Reload nginx" in out


def test_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))

    assert kitup.__version__ == pyproject["project"]["version"]


def test_project_files_overlay_bundle_files(tmp_path, capsys):
    base_dest = tmp_path / "base.txt"
    other_dest = tmp_path / "other.txt"

    def copy_task(dest):
        return f"""
        from kitup import ctx, task
        from kitup.modules import files

        @task
        def copy_shared():
            files.copy("shared.conf", {str(dest)!r})
        """

    make_project(
        tmp_path,
        bundles={
            "base": {
                "tasks.py": copy_task(base_dest),
                "files/shared.conf": "bundle\n",
            },
            "other": {
                "tasks.py": copy_task(other_dest),
                "files/shared.conf": "other\n",
            },
        },
    )
    write(tmp_path / "files" / "base" / "shared.conf", "bundle-specific\n")

    code = cli.main(["apply", str(tmp_path), "--no-color"])

    assert code == 0
    assert base_dest.read_text(encoding="utf-8") == "bundle-specific\n"
    assert other_dest.read_text(encoding="utf-8") == "other\n"

    output = capsys.readouterr().out
    assert output.count("copy shared.conf ->") == 2
    assert "files/shared.conf" not in output


def test_project_dir_can_be_relative(tmp_path, monkeypatch, capsys):
    make_project(tmp_path / "machine", bundles={"base": task_source("run")})
    monkeypatch.chdir(tmp_path)

    code = cli.main(["list", "machine", "--no-color"])

    assert code == 0
    assert "bundle: base" in capsys.readouterr().out


def test_apply_with_unknown_var_in_preset_file_is_exit_2(tmp_path, capsys):
    make_project(
        tmp_path,
        preset={"laptop": "--include-vars missing\n"},
        bundles={"base": task_source("run")},
    )

    code = cli.main(["apply", str(tmp_path), "--preset", "laptop"])

    assert code == 2
    assert "unknown var: missing" in capsys.readouterr().err


def test_apply_with_missing_preset_file_is_exit_2(tmp_path, capsys):
    make_project(tmp_path, bundles={"base": task_source("run")})

    code = cli.main(["apply", str(tmp_path), "--preset", "missing"])

    assert code == 2
    assert "preset not found" in capsys.readouterr().err


def make_external_bundle(root, name, *, summary=None):
    folder = root / name
    text = summary or f"ctx.changed(summary='ran {name}')"
    write(
        folder / "tasks.py",
        f"from kitup import ctx, task\n\n@task\ndef run_{name}():\n    {text}\n",
    )
    return folder


def test_bundle_path_option_loads_external_bundles_directly_and_from_a_preset(tmp_path, capsys):
    root = make_project(tmp_path / "machine", bundles={"base": task_source("run")})
    external = tmp_path / "external-bundles"
    make_external_bundle(external, "extra")

    code = cli.main(["list", str(root), "--bundle-path", str(external), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "bundle: base" in out
    assert "bundle: extra" in out

    code = cli.main(["apply", str(root), "--bundle-path", str(external), "-n", "--no-color"])

    assert code == 0
    assert "ran extra" in capsys.readouterr().out

    preset_root = make_project(
        tmp_path / "preset-machine",
        bundles={"base": task_source("run")},
        preset={"shared": f"--bundle-path {external}\n"},
    )
    code = cli.main(["list", str(preset_root), "--preset", "shared", "--no-color"])

    assert code == 0
    assert "bundle: extra" in capsys.readouterr().out


def make_external_module(root, name):
    write(
        root / f"{name}.py",
        f"from kitup import module\nfrom kitup.context import current_context\n\n"
        f'module("{name}")\n\n\n'
        "def install():\n"
        "    ctx = current_context()\n"
        '    return ctx.changed(summary="external module")\n',
    )
    return root / f"{name}.py"


def test_files_path_option_loads_external_overlays_for_apply(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine",
        bundles={
            "base": """
            from kitup import ctx, task

            @task
            def show():
                ctx.changed(summary=(ctx.files / "message.txt").read_text().strip())
            """
        },
    )
    external = tmp_path / "external-files"
    write(external / "base" / "message.txt", "from external files\n")

    code = cli.main(["apply", str(root), "--files-path", str(external), "-n", "--no-color"])

    assert code == 0
    assert "from external files" in capsys.readouterr().out


def test_modules_path_option_loads_external_modules_for_apply_check_and_list_modules(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine",
        bundles={
            "base": """
            from kitup import ctx, task
            from modules import external

            @task
            def show():
                result = external.install()
                ctx.changed(summary=f"{result.summary}; used external")
            """
        },
    )
    external = tmp_path / "external-modules"
    make_external_module(external, "external")

    code = cli.main(["apply", str(root), "--modules-path", str(external), "-n", "--no-color"])

    assert code == 0
    assert "external module; used external" in capsys.readouterr().out

    assert cli.main(["check", str(root), "--modules-path", str(external), "--no-color"]) == 0
    capsys.readouterr()

    code = cli.main(["list-modules", str(root), "--modules-path", str(external), "--no-color"])

    assert code == 0
    assert "  - external" in capsys.readouterr().out


def test_missing_files_or_modules_path_is_exit_2(tmp_path, capsys):
    root = make_project(tmp_path / "machine", bundles={"base": task_source("run")})

    assert cli.main(["list", str(root), "--files-path", "missing", "--no-color"]) == 2
    assert "files path is not a directory" in capsys.readouterr().err

    assert cli.main(["list", str(root), "--modules-path", "missing", "--no-color"]) == 2
    assert "modules path is not a directory" in capsys.readouterr().err


def test_var_file_option_accepts_paths_and_loads_after_discovered_files(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine",
        vars="VALUE = 'base'\n",
        var_files={"late": "VALUE = 'var'\n"},
        bundles={
            "base": """
            from kitup import ctx, task

            @task
            def show():
                ctx.changed(summary=f"value={ctx.vars.VALUE}")
            """
        },
    )
    extra = root / "secrets.py"
    write(extra, "VALUE = 'extra'\n")

    for value in (str(extra), "secrets.py"):
        code = cli.main(["apply", str(root), "--var-file", value, "-n", "--no-color"])
        assert code == 0
        assert "value=extra" in capsys.readouterr().out


def test_var_filters_apply_to_extra_var_files(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine",
        vars="VALUE = 'base'\n",
        bundles={
            "base": """
            from kitup import ctx, task

            @task
            def show():
                ctx.changed(summary=f"value={ctx.vars.VALUE}")
            """
        },
    )
    extra = root / "secrets.py"
    write(extra, "VALUE = 'extra'\n")

    code = cli.main(["apply", str(root), "--var-file", "secrets.py", "--include-vars", "secrets", "-n", "--no-color"])
    assert code == 0
    assert "value=extra" in capsys.readouterr().out

    code = cli.main(["apply", str(root), "--var-file", "secrets.py", "--exclude-vars", "secrets", "-n", "--no-color"])
    assert code == 0
    assert "value=base" in capsys.readouterr().out


def test_bundle_vars_are_scoped_and_project_values_win(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="VALUE = 'project'\n",
        bundles={
            "base": {
                "tasks.py": """
                from kitup import ctx, task

                @task
                def show():
                    ctx.changed(
                        summary=(
                            f"base value={ctx.vars.get('VALUE', 'missing')} "
                            f"only={ctx.vars.get('BASE_ONLY', 'none')}"
                        )
                    )
                """,
                "vars/base.py": "VALUE = 'bundle'\nBASE_ONLY = 1\n",
            },
            "other": {
                "__init__.py": "from kitup import bundle\n\nbundle(depends_on=['base'])\n",
                "tasks.py": """
                from kitup import ctx, task

                @task
                def show():
                    ctx.changed(
                        summary=(
                            f"other value={ctx.vars.get('VALUE', 'missing')} "
                            f"only={ctx.vars.get('BASE_ONLY', 'none')}"
                        )
                    )
                """,
            },
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "base value=project only=1" in out
    assert "other value=project only=none" in out

    code = cli.main(["apply", str(tmp_path), "--exclude-vars", "base", "-n", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "base value=bundle only=1" in out
    assert "other value=missing only=none" in out


def test_missing_var_file_is_exit_2(tmp_path, capsys):
    root = make_project(tmp_path / "machine", bundles={"base": task_source("run")})

    code = cli.main(["list", str(root), "--var-file", "missing.py"])

    assert code == 2
    assert "var file not found" in capsys.readouterr().err


def test_duplicate_var_stem_is_exit_2(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine", var_files={"secrets": "VALUE = 1\n"}, bundles={"base": task_source("run")}
    )
    extra = tmp_path / "elsewhere" / "secrets.py"
    write(extra, "SECRET = 1\n")

    code = cli.main(["list", str(root), "--var-file", str(extra), "--no-color"])

    assert code == 2
    assert "duplicate var id 'secrets'" in capsys.readouterr().err


def test_list_modules_shows_builtin_and_empty_custom_sections(tmp_path, capsys):
    make_project(tmp_path, bundles={"base": task_source("run")})

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"project: {tmp_path.name}" in out
    assert "built-in modules:" in out
    assert "  - files: " in out
    assert "  - dnf: " in out
    assert "  - podman: " in out
    assert "custom modules:" in out
    assert "  (none)" in out


def test_list_modules_shows_custom_modules_by_registered_name(tmp_path, capsys):
    make_project(
        tmp_path,
        modules={
            "mytool.py": 'from kitup import module\n\nmodule("other")\n\n\ndef install():\n    pass\n',
            "webserver/__init__.py": 'from kitup import module\n\nmodule("webserver")\n',
            "_private.py": "VALUE = 1\n",
        },
    )

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "custom modules:" in out
    assert "  - other" in out
    assert "  - webserver" in out
    assert "  - mytool" not in out
    assert "_private" not in out


def test_list_modules_works_without_bundles_and_does_not_load_them(tmp_path, capsys):
    plain = make_project(tmp_path / "plain", modules={"mytool.py": 'from kitup import module\n\nmodule("mytool")\n'})
    assert cli.main(["list-modules", str(plain), "--no-color"]) == 0
    assert "  - mytool" in capsys.readouterr().out

    root = make_project(
        tmp_path / "broken-bundles",
        bundles={"base": "raise RuntimeError('broken bundle')\n"},
        modules={"mytool.py": 'from kitup import module\n\nmodule("mytool")\n'},
    )

    code = cli.main(["list-modules", str(root), "--no-color"])

    assert code == 0
    assert "  - mytool" in capsys.readouterr().out


def test_unused_custom_modules_are_not_imported_by_apply_or_list(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={"base": task_source("run")},
        modules={"broken.py": "raise RuntimeError('imported broken module')\n"},
    )

    assert cli.main(["apply", str(tmp_path), "-n", "--no-color"]) == 0
    assert "imported broken module" not in capsys.readouterr().err

    assert cli.main(["list", str(tmp_path), "--no-color"]) == 0
    assert "imported broken module" not in capsys.readouterr().err


def test_check_and_list_modules_import_custom_modules(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={"base": task_source("run")},
        modules={"broken.py": "raise RuntimeError('imported broken module')\n"},
    )

    code = cli.main(["check", str(tmp_path), "--no-color"])

    assert code == 2
    assert "failed to import module 'broken'" in capsys.readouterr().err

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 2
    assert "failed to import module 'broken'" in capsys.readouterr().err
