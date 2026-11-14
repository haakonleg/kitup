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
    return f"from kitup import task\n\n{decorator}\ndef {name}(ctx):\n    {body}\n"


def test_apply_uses_discovered_vars(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="VALUE = 'base'\n",
        var_files={"work": "VALUE = 'work'\n"},
        bundles={
            "base": """
            from kitup import task

            @task
            def show(ctx):
                ctx.changed(summary=f"value={ctx.vars.VALUE}")
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    assert code == 0
    assert "value=work" in capsys.readouterr().out


def test_default_project_is_the_current_directory(tmp_path, monkeypatch, capsys):
    make_project(tmp_path, bundles={"base": task_source("run")})
    monkeypatch.chdir(tmp_path)

    code = cli.main(["list", "--no-color"])

    assert code == 0
    assert "bundle: base" in capsys.readouterr().out


def test_unknown_preset_option_warns_on_stderr(tmp_path, capsys):
    make_project(
        tmp_path,
        preset={"laptop": "--dry-runn\n"},
        bundles={"base": task_source("run")},
    )

    code = cli.main(["list", str(tmp_path), "--preset", "laptop", "--no-color"])

    assert code == 0
    err = capsys.readouterr().err
    assert "warning: preset" in err
    assert "--dry-runn" in err


def test_list_shows_project_preset_and_active_vars(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="VALUE = 1\n",
        var_files={"laptop": "VALUE = 2\n"},
        preset={"laptop": "--include-vars laptop\n"},
        bundles={"base": task_source("run")},
    )

    code = cli.main(["list", str(tmp_path), "--preset", "laptop", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"project: {tmp_path.name}" in out
    assert "preset: laptop" in out
    assert "vars: laptop.py" in out
    assert "base.py" not in out
    assert "include vars" not in out
    assert "exclude vars" not in out
    assert "include bundles" not in out
    assert "exclude bundles" not in out
    assert "bundle: base" in out
    assert str(tmp_path) not in out

    code = cli.main(["list", str(tmp_path), "--preset", "laptop", "--no-color", "-v"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"project: {tmp_path}" in out
    assert f"preset: {tmp_path / 'presets' / 'laptop'}" in out
    assert f"vars: {tmp_path / 'vars' / 'laptop.py'}" in out


def test_list_vars_header_shows_only_active_vars(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="VALUE = 1\n",
        var_files={"laptop": "VALUE = 2\n", "work": "VALUE = 3\n"},
        bundles={"base": task_source("run")},
    )

    code = cli.main(["list", str(tmp_path), "--exclude-vars", "laptop", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "vars: base.py, work.py" in out
    assert "laptop.py" not in out
    assert "include vars" not in out
    assert "exclude vars" not in out


def test_list_does_not_load_var_files(tmp_path, capsys):
    make_project(tmp_path, vars="1 / 0\n", bundles={"base": task_source("run")})

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "bundle: base" in out
    assert "vars: base.py" in out


def test_check_loads_var_files(tmp_path, capsys):
    make_project(tmp_path, vars="1 / 0\n", bundles={"base": task_source("run")})

    code = cli.main(["check", str(tmp_path), "--no-color"])

    assert code == 2
    assert "division by zero" in capsys.readouterr().err


def test_list_and_check_do_not_collect_facts(tmp_path, monkeypatch):
    make_project(tmp_path, bundles={"base": task_source("run")})

    def fail():
        raise AssertionError("collect_facts called")

    monkeypatch.setattr(cli, "collect_facts", fail)

    assert cli.main(["list", str(tmp_path), "--no-color"]) == 0
    assert cli.main(["check", str(tmp_path), "--no-color"]) == 0


def test_list_shows_ids_and_display_names(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base system')\n",
                "tasks.py": task_source("install"),
            }
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 0
    assert "bundle: base (Base system)" in capsys.readouterr().out


def test_list_non_verbose_shows_only_task_names(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base system', 'Core packages')\n",
                "tasks.py": """
                from kitup import task

                @task(tags={'packages'})
                def install_packages(ctx):
                    '''Install base packages.'''
                    pass
                """,
            }
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "bundle: base (Base system)" in out
    assert "- Install packages" in out
    assert "Core packages" not in out
    assert "tags:" not in out
    assert "id:" not in out


def test_list_verbose_shows_task_details(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle()\n",
                "tasks.py": """
                from kitup import task

                @task(tags={'packages'})
                def install_packages(ctx):
                    '''Install base packages.'''
                    pass
                """,
            }
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color", "-v"])

    assert code == 0
    out = capsys.readouterr().out
    assert "- Install packages" in out
    assert "id: base.install_packages" in out
    assert "tags: packages" in out
    assert "description: Install base packages." in out


def test_verbose_output_indents_multiline_descriptions(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base', 'Bundle first.\\n\\nBundle second.')\n",
                "tasks.py": """
                from kitup import task

                @task
                def install(ctx):
                    '''First line.

                    Second paragraph.
                    '''
                    pass
                """,
            }
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color", "-v"])

    assert code == 0
    out = capsys.readouterr().out
    assert "  Bundle first." in out
    assert "\n  Bundle second." in out
    assert "      description: First line." in out
    assert "\n      Second paragraph." in out

    code = cli.main(["apply", str(tmp_path), "-n", "-v", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "\n    First line." in out
    assert "\n    Second paragraph." in out


def test_shared_task_module_is_listed_and_applied_in_each_bundle(tmp_path, capsys):
    write(tmp_path / "bundles" / "__init__.py", "")
    write(
        tmp_path / "bundles" / "shared.py",
        """
        from kitup import task

        @task
        def shared(ctx):
            ctx.changed(summary="ran")
        """,
    )
    for bundle_id, display in (("one", "One"), ("two", "Two")):
        write(
            tmp_path / "bundles" / bundle_id / "__init__.py",
            f"""
            from kitup import bundle
            from .. import shared

            bundle({display!r}, modules=[shared])
            """,
        )

    code = cli.main(["list", str(tmp_path), "--no-color", "-v"])

    assert code == 0
    out = capsys.readouterr().out
    assert "id: one.shared" in out
    assert "id: two.shared" in out

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "bundle: one (One)" in out
    assert "bundle: two (Two)" in out
    assert out.count("Shared") == 2


def test_apply_with_missing_bundles_dir_suggests_init(tmp_path, capsys):
    code = cli.main(["apply", str(tmp_path)])

    assert code == 2
    error = capsys.readouterr().err
    assert "no bundles found" in error
    assert "kitup init" in error


def test_apply_with_empty_bundles_dir_is_exit_2(tmp_path, capsys):
    (tmp_path / "bundles").mkdir()

    code = cli.main(["apply", str(tmp_path)])

    assert code == 2
    assert "no bundles found" in capsys.readouterr().err


def test_check_with_zero_bundles_is_exit_2(tmp_path, capsys):
    code = cli.main(["check", str(tmp_path)])

    assert code == 2
    assert "no bundles found" in capsys.readouterr().err


def test_list_with_zero_bundles_succeeds(tmp_path, capsys):
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
            from kitup import task

            @task
            def explode(ctx):
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

            from kitup import task

            @task
            def write_marker(ctx):
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
            from kitup import task

            @task(tags={'packages'})
            def packages(ctx):
                ctx.changed(summary="packages")

            @task(tags={'config'})
            def config(ctx):
                ctx.changed(summary="config")

            @task(tags={'docs'})
            def docs(ctx):
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


def test_apply_include_and_exclude_bundles(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": task_source("base_task", body="ctx.changed(summary='base')"),
            "desktop": task_source("desktop_task", body="ctx.changed(summary='desktop')"),
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
    assert "Desktop task" not in out


def test_bundle_filters_accept_comma_separated_and_repeated_values(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": task_source("base_task"),
            "desktop": task_source("desktop_task"),
            "gaming": task_source("gaming_task"),
        },
    )

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
            from kitup import task

            @task
            def show(ctx):
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


def test_preset_sets_options(tmp_path, capsys):
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


def test_real_cli_overrides_preset(tmp_path, capsys):
    make_project(
        tmp_path,
        preset={"laptop": "--include-bundles base\n"},
        bundles={
            "base": task_source("base_task", body="ctx.changed(summary='base')"),
            "desktop": task_source("desktop_task", body="ctx.changed(summary='desktop')"),
        },
    )

    code = cli.main(["apply", str(tmp_path), "--preset", "laptop", "-b", "desktop", "-n", "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Desktop task" in out
    assert "Base task" not in out


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
    assert "bundle: example" in capsys.readouterr().out


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


def test_apply_closes_context_on_success(tmp_path, monkeypatch):
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
            return 0

    monkeypatch.setattr(cli, "TaskContext", Context)
    monkeypatch.setattr(cli, "Runner", Runner)
    make_project(tmp_path, bundles={"base": task_source("run")})

    code = cli.main(["apply", str(tmp_path)])

    assert code == 0
    assert contexts and contexts[0].closed


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


def test_apply_passes_root_and_project_to_the_context(tmp_path, monkeypatch):
    seen = {}

    class Context:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def close(self):
            pass

    class Runner:
        def __init__(self, plan, context, output, **kwargs):
            pass

        def run(self):
            return 0

    monkeypatch.setattr(cli, "TaskContext", Context)
    monkeypatch.setattr(cli, "Runner", Runner)
    make_project(tmp_path, bundles={"base": task_source("run")})

    code = cli.main(["apply", str(tmp_path)])

    assert code == 0
    assert seen["root"] == tmp_path.resolve()
    assert seen["project"].root == tmp_path.resolve()


def test_apply_verbose_shows_environment(tmp_path, capsys):
    make_project(tmp_path, vars="VALUE = 1\n", bundles={"base": task_source("run")})

    code = cli.main(["apply", str(tmp_path), "-n", "-v", "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert f"project: {tmp_path}" in out
    assert f"vars: {tmp_path / 'vars' / 'base.py'}" in out


def test_invalid_task_tags_are_exit_2(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import task

            @task(tags={'packages', 1})
            def run(ctx):
                pass
            """
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 2
    assert "task tags must contain strings" in capsys.readouterr().err


def test_list_does_not_show_side_effect_names(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import side_effect, task

            @side_effect
            def refresh(ctx):
                pass

            @task
            def install(ctx):
                ctx.side_effect(refresh)
            """
        },
    )

    code = cli.main(["list", str(tmp_path), "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Install" in out
    assert "Refresh" not in out


def test_apply_shows_and_prefixes_side_effect_lines(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import side_effect, task

            @side_effect(one_shot=False)
            def reload_nginx(ctx):
                ctx.changed(summary="+reloaded")

            @side_effect
            def refresh_package_metadata(ctx):
                ctx.changed(summary="+refreshed")

            @task
            def configure_nginx(ctx):
                ctx.changed(summary="+conf")
                ctx.side_effect(reload_nginx)
                ctx.side_effect(refresh_package_metadata)
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert code == 0
    assert any(line.startswith("    Configure nginx") for line in lines)
    assert any(line.startswith("  ↳ Reload nginx") for line in lines)
    assert any(line.startswith("  ↳ Refresh package metadata") for line in lines)
    assert not any(line.startswith("  ↳ Configure nginx") for line in lines)


def test_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))

    assert kitup.__version__ == pyproject["project"]["version"]


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert "kitup 3.0.0" in capsys.readouterr().out


@pytest.mark.parametrize("options", [["--include-bundl", "base"], ["-nm", "other"]])
def test_cli_rejects_undocumented_option_forms(options, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["apply", *options])

    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_cli_accepts_exact_option_forms():
    args = cli.build_parser().parse_args(["apply", "-p", "laptop", "--dry-run"])

    assert args.preset == ["laptop"]
    assert args.dry_run is True


def test_defaults_cover_every_parser_option():
    dests = {option.dest for option in cli._ALL_OPTIONS if option.default is not cli._NO_DEFAULT}

    assert dests == set(cli._DEFAULTS)
    assert cli._DEFAULTS["include_bundles"] == ()
    assert cli._DEFAULTS["dry_run"] is False
    assert cli._DEFAULTS["project_dir"] is None


def test_apply_uses_vars_get_and_require(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="VALUE = 1\n",
        bundles={
            "base": """
            from kitup import task

            @task
            def show(ctx):
                ctx.changed(summary=f"value={ctx.vars.get('VALUE')} missing={ctx.vars.get('MISSING', 'none')}")
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    assert code == 0
    assert "value=1 missing=none" in capsys.readouterr().out


def test_project_files_overlay_bundle_files(tmp_path):
    base_dest = tmp_path / "base.txt"
    other_dest = tmp_path / "other.txt"

    def copy_task(dest):
        return f"""
        from kitup import task
        from kitup.modules import files

        @task
        def copy_shared(ctx):
            files.copy(ctx, "shared.conf", {str(dest)!r})
        """

    make_project(
        tmp_path,
        bundles={
            "base": {
                "tasks.py": copy_task(base_dest),
                "files/shared.conf": "bundle\n",
            },
            "other": {"tasks.py": copy_task(other_dest)},
        },
    )
    write(tmp_path / "files" / "base" / "shared.conf", "bundle-specific\n")
    write(tmp_path / "files" / "shared.conf", "global\n")

    code = cli.main(["apply", str(tmp_path), "--no-color"])

    assert code == 0
    assert base_dest.read_text(encoding="utf-8") == "bundle-specific\n"
    assert other_dest.read_text(encoding="utf-8") == "global\n"


def test_write_marker_task_uses_project_root(tmp_path):
    """A task can reach the project root through ``ctx.root``."""
    marker = tmp_path / "root.txt"
    make_project(
        tmp_path,
        bundles={
            "base": """
            from kitup import task

            @task
            def write_root(ctx):
                (ctx.root / "root.txt").write_text(str(ctx.root))
                ctx.changed(summary="write root")
            """
        },
    )

    code = cli.main(["apply", str(tmp_path), "--no-color"])

    assert code == 0
    assert marker.read_text(encoding="utf-8") == str(tmp_path.resolve())


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
        f"from kitup import task\n\n@task\ndef run_{name}(ctx):\n    {text}\n",
    )
    return folder


def test_bundle_path_option_loads_external_bundles_for_list_and_apply(tmp_path, capsys):
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
    out = capsys.readouterr().out
    assert "ran extra" in out


def test_bundle_path_option_can_come_from_a_preset(tmp_path, capsys):
    external = tmp_path / "external-bundles"
    make_external_bundle(external, "extra")
    root = make_project(
        tmp_path / "machine",
        bundles={"base": task_source("run")},
        preset={"shared": f"--bundle-path {external}\n"},
    )

    code = cli.main(["list", str(root), "--preset", "shared", "--no-color"])

    assert code == 0
    assert "bundle: extra" in capsys.readouterr().out


def test_var_file_option_accepts_absolute_and_relative_paths(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine",
        vars="VALUE = 'base'\n",
        bundles={
            "base": """
            from kitup import task

            @task
            def show(ctx):
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


def test_extra_var_files_load_after_discovered_files(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine",
        var_files={"late": "VALUE = 'var'\n"},
        bundles={
            "base": """
            from kitup import task

            @task
            def show(ctx):
                ctx.changed(summary=f"value={ctx.vars.VALUE}")
            """
        },
    )
    extra = root / "secrets.py"
    write(extra, "VALUE = 'extra'\n")

    code = cli.main(["apply", str(root), "--var-file", "secrets.py", "-n", "--no-color"])

    assert code == 0
    assert "value=extra" in capsys.readouterr().out


def test_var_filters_apply_to_extra_var_files(tmp_path, capsys):
    root = make_project(
        tmp_path / "machine",
        vars="VALUE = 'base'\n",
        bundles={
            "base": """
            from kitup import task

            @task
            def show(ctx):
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
                from kitup import task

                @task
                def show(ctx):
                    ctx.changed(
                        summary=f"base value={ctx.vars.VALUE} only={ctx.vars.get('BASE_ONLY', 'none')}"
                    )
                """,
                "vars/base.py": "VALUE = 'bundle'\nBASE_ONLY = 1\n",
            },
            "other": {
                "__init__.py": "from kitup import bundle\n\nbundle(depends_on=['base'])\n",
                "tasks.py": """
                from kitup import task

                @task
                def show(ctx):
                    ctx.changed(
                        summary=f"other value={ctx.vars.VALUE} only={ctx.vars.get('BASE_ONLY', 'none')}"
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


def test_check_and_apply_load_bundle_vars_but_list_does_not(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "tasks.py": task_source("run"),
                "vars/base.py": "1 / 0\n",
            }
        },
    )

    assert cli.main(["list", str(tmp_path), "--no-color"]) == 0
    capsys.readouterr()

    assert cli.main(["check", str(tmp_path), "--no-color"]) == 2
    assert "division by zero" in capsys.readouterr().err

    assert cli.main(["apply", str(tmp_path), "-n", "--no-color"]) == 2
    assert "division by zero" in capsys.readouterr().err


def test_var_filters_do_not_remove_bundle_vars(tmp_path, capsys):
    make_project(
        tmp_path,
        vars="PROJECT_ONLY = 1\n",
        bundles={
            "base": {
                "tasks.py": """
                from kitup import task

                @task
                def show(ctx):
                    project = ctx.vars.get('PROJECT_ONLY', 'missing')
                    bundle = ctx.vars.get('BUNDLE_ONLY')
                    ctx.changed(summary=f"project={project} bundle={bundle}")
                """,
                "vars/local.py": "BUNDLE_ONLY = 1\n",
            }
        },
    )

    code = cli.main(["apply", str(tmp_path), "--exclude-vars", "base", "-n", "--no-color"])

    assert code == 0
    assert "project=missing bundle=1" in capsys.readouterr().out


def test_list_verbose_shows_bundle_var_paths(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "tasks.py": task_source("run"),
                "vars/base.py": "VALUE = 1\n",
            }
        },
    )

    code = cli.main(["list", str(tmp_path), "-v", "--no-color"])

    assert code == 0
    assert f"  vars: {tmp_path / 'bundles' / 'base' / 'vars' / 'base.py'}" in capsys.readouterr().out


def test_list_output_includes_extra_var_files(tmp_path, capsys):
    root = make_project(tmp_path / "machine", vars="VALUE = 1\n", bundles={"base": task_source("run")})
    write(root / "secrets.py", "SECRET = 1\n")

    code = cli.main(["list", str(root), "--var-file", "secrets.py", "--no-color"])

    assert code == 0
    assert "vars: base.py, secrets.py" in capsys.readouterr().out


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


def test_verbose_output_shows_bundle_paths(tmp_path, capsys):
    root = make_project(tmp_path / "machine", bundles={"base": task_source("run")})
    external = tmp_path / "external-bundles"
    make_external_bundle(external, "extra")

    code = cli.main(["list", str(root), "--bundle-path", str(external), "-v", "--no-color"])

    assert code == 0
    assert f"bundle path: {external}" in capsys.readouterr().out


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
            "mytool.py": 'from kitup import module\n\nmodule("other")\n\n\ndef install(ctx):\n    pass\n',
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


def test_list_modules_shows_descriptions_in_both_modes(tmp_path, capsys):
    make_project(
        tmp_path,
        modules={"mytool.py": 'from kitup import module\n\nmodule("mytool", "Install mytool.")\n'},
    )

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "  - mytool: Install mytool." in out

    code = cli.main(["list-modules", str(tmp_path), "-v", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "  - mytool\n" in out
    assert "      description: Install mytool." in out


def test_list_modules_indents_multiline_descriptions(tmp_path, capsys):
    make_project(
        tmp_path,
        modules={"mytool.py": 'from kitup import module\n\nmodule("mytool", "First line.\\nSecond line.")\n'},
    )

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "  - mytool: First line.\n      Second line." in out


def test_list_modules_works_without_bundles(tmp_path, capsys):
    make_project(tmp_path, modules={"mytool.py": 'from kitup import module\n\nmodule("mytool")\n'})

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 0
    assert "  - mytool" in capsys.readouterr().out


def test_list_modules_defaults_to_the_current_directory(tmp_path, monkeypatch, capsys):
    make_project(tmp_path, modules={"mytool.py": 'from kitup import module\n\nmodule("mytool")\n'})
    monkeypatch.chdir(tmp_path)

    code = cli.main(["list-modules", "--no-color"])

    assert code == 0
    assert "  - mytool" in capsys.readouterr().out


def test_list_modules_does_not_load_bundles(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={"base": "raise RuntimeError('broken bundle')\n"},
        modules={"mytool.py": 'from kitup import module\n\nmodule("mytool")\n'},
    )

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 0
    assert "  - mytool" in capsys.readouterr().out


def test_list_modules_does_not_collect_facts(tmp_path, monkeypatch):
    make_project(tmp_path)

    def fail():
        raise AssertionError("collect_facts called")

    monkeypatch.setattr(cli, "collect_facts", fail)

    assert cli.main(["list-modules", str(tmp_path), "--no-color"]) == 0


def test_list_modules_verbose_shows_paths_and_functions(tmp_path, capsys):
    make_project(
        tmp_path,
        modules={
            "mytool.py": (
                "from kitup import module\n\n"
                'module("mytool")\n\n\n'
                "def install(ctx):\n"
                '    """Install mytool."""\n\n\n'
                "def _helper():\n"
                "    pass\n"
            )
        },
    )

    code = cli.main(["list-modules", str(tmp_path), "-v", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"project: {tmp_path.resolve()}" in out
    assert f"path: {tmp_path.resolve() / 'modules' / 'mytool.py'}" in out
    assert "- install: Install mytool." in out
    assert "_helper" not in out


def test_list_modules_verbose_ignores_public_classes(tmp_path, capsys):
    make_project(
        tmp_path,
        modules={
            "mytool.py": (
                "from kitup import module\n\n"
                'module("mytool")\n\n\n'
                "class Helper:\n"
                '    """A public class."""\n\n\n'
                "def install(ctx):\n"
                '    """Install mytool."""\n'
            )
        },
    )

    code = cli.main(["list-modules", str(tmp_path), "-v", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "- install: Install mytool." in out
    assert "Helper" not in out


def test_list_modules_verbose_separates_modules_with_blank_lines(tmp_path, capsys):
    make_project(
        tmp_path,
        modules={
            "one.py": 'from kitup import module\n\nmodule("one")\n\n\ndef first(ctx):\n    """First."""\n',
            "two.py": 'from kitup import module\n\nmodule("two")\n\n\ndef second(ctx):\n    """Second."""\n',
        },
    )

    code = cli.main(["list-modules", str(tmp_path), "-v", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "        - first: First.\n\n  - two\n" in out


def test_list_modules_verbose_shows_builtin_functions(tmp_path, capsys):
    make_project(tmp_path)

    code = cli.main(["list-modules", str(tmp_path), "-v", "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "  - files" in out
    assert "- file: Ensure a text file exists" in out
    assert "      description: Files, directories" in out
    assert "path:" not in out


def test_apply_does_not_import_unused_custom_modules(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={"base": task_source("run")},
        modules={"broken.py": "raise RuntimeError('imported broken module')\n"},
    )

    code = cli.main(["apply", str(tmp_path), "-n", "--no-color"])

    assert code == 0
    assert "imported broken module" not in capsys.readouterr().err


def test_list_does_not_import_unused_custom_modules(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={"base": task_source("run")},
        modules={"broken.py": "raise RuntimeError('imported broken module')\n"},
    )

    code = cli.main(["list", str(tmp_path), "--no-color"])

    assert code == 0
    assert "imported broken module" not in capsys.readouterr().err


def test_check_imports_custom_modules(tmp_path, capsys):
    make_project(
        tmp_path,
        bundles={"base": task_source("run")},
        modules={"broken.py": "raise RuntimeError('imported broken module')\n"},
    )

    code = cli.main(["check", str(tmp_path), "--no-color"])

    assert code == 2
    assert "failed to import module 'broken'" in capsys.readouterr().err


def test_list_modules_imports_custom_modules(tmp_path, capsys):
    make_project(tmp_path, modules={"broken.py": "raise RuntimeError('imported broken module')\n"})

    code = cli.main(["list-modules", str(tmp_path), "--no-color"])

    assert code == 2
    assert "failed to import module 'broken'" in capsys.readouterr().err
