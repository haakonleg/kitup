from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from kitup import cli
from kitup.errors import PrivilegeError, TaskFailed


def write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")

def make_bundle(
    root,
    folder,
    *,
    name=None,
    setup="",
    depends="()",
    tasks="",
):
    name = name or folder
    has_tasks = bool(tasks.strip())
    task_import = "from . import tasks\n" if has_tasks else ""
    task_modules = ", modules=[tasks]" if has_tasks else ""
    write(root / "bundles" / "__init__.py", "")
    write(
        root / "bundles" / folder / "__init__.py",
        f"""
        from kitup.registry import Bundle

        {setup}

        {task_import}
        Bundle({name!r}, "description", depends_on={depends}{task_modules})
        """,
    )
    write(
        root / "bundles" / folder / "tasks.py",
        "from kitup.registry import task\n\n" + textwrap.dedent(tasks),
    )

def make_profile(root, names, *, filename="kitup.py", **options):
    imports = ", ".join(names)
    values = [f"bundles=[{imports}]"]
    for key, value in options.items():
        values.append(f"{key}={_format_option(value)}")
    write(
        root / filename,
        f"""
        from pathlib import Path

        from bundles import {imports}

        from kitup import profile

        profile({", ".join(values)})
        """,
    )

def _format_option(value):
    if isinstance(value, Path):
        return f"Path({str(value)!r})"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_option(item) for item in value) + "]"
    return repr(value)

def purge_project_modules() -> None:
    for name in tuple(sys.modules):
        if name == "bundles" or name.startswith("bundles."):
            sys.modules.pop(name, None)
        if name in {"base_profile", "laptop_profile", "config"}:
            sys.modules.pop(name, None)
        if name.startswith(("_kitup_profile_", "_kitup_config_")):
            sys.modules.pop(name, None)

@pytest.fixture(autouse=True)
def clean_imports():
    purge_project_modules()
    yield
    purge_project_modules()

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

def test_apply_uses_profile_config_sources(tmp_path, capsys):
    (tmp_path / "config.py").write_text("VALUE = 'base'\n")
    (tmp_path / "work.py").write_text("VALUE = 'work'\n")
    make_bundle(
        tmp_path,
        "base",
        tasks="""
        @task("Show config")
        def show(ctx):
            ctx.changed({"value": ctx.config.VALUE}, f"value={ctx.config.VALUE}")
        """,
    )
    make_profile(
        tmp_path,
        ["base"],
        config=[Path("config.py"), Path("work.py")],
    )

    code = cli.main(["apply", str(tmp_path / "kitup.py"), "-n"])

    assert code == 0
    assert "value=work" in capsys.readouterr().out

def test_default_profile_is_kitup_py_in_cwd(tmp_path, monkeypatch, capsys):
    make_bundle(tmp_path, "base")
    make_profile(tmp_path, ["base"])
    monkeypatch.chdir(tmp_path)

    code = cli.main(["list", "--no-color"])

    assert code == 0
    assert "bundle: base" in capsys.readouterr().out

def test_list_shows_file_names_unless_verbose(tmp_path, capsys):
    make_bundle(tmp_path, "base")
    write(tmp_path / "settings.py", "VALUE = 1\n")
    write(
        tmp_path / "kitup.py",
        """
        from pathlib import Path

        from bundles import base

        from kitup import profile

        profile(bundles=[base], config=[Path("settings.py")])
        """,
    )

    code = cli.main(["list", str(tmp_path / "kitup.py"), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "profile: kitup.py" in out
    assert "config: settings.py" in out
    assert str(tmp_path) not in out

    code = cli.main(["list", str(tmp_path / "kitup.py"), "--no-color", "-v"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"profile: {tmp_path / 'kitup.py'}" in out
    assert f"config: {tmp_path / 'settings.py'}" in out

def test_list_shows_imported_config_module_files(tmp_path, capsys):
    make_bundle(tmp_path, "base")
    write(tmp_path / "config.py", "VALUE = 1\n")
    write(
        tmp_path / "kitup.py",
        """
        import config

        from bundles import base

        from kitup import profile

        profile(bundles=[base], config=[config])
        """,
    )

    code = cli.main(["list", str(tmp_path / "kitup.py"), "--no-color"])

    assert code == 0
    out = capsys.readouterr().out
    assert "config: config.py" in out
    assert "<module" not in out

    code = cli.main(["list", str(tmp_path / "kitup.py"), "--no-color", "-v"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"config: {tmp_path / 'config.py'}" in out

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
    make_bundle(tmp_path, "base")
    make_profile(tmp_path, ["base"])

    code = cli.main(["apply", str(tmp_path / "kitup.py")])

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
    make_bundle(tmp_path, "base")
    make_profile(tmp_path, ["base"])

    code = cli.main(["apply", str(tmp_path / "kitup.py")])

    assert code == 1
    assert contexts and contexts[0].closed
    assert "error: boom" in capsys.readouterr().err

def test_sudo_validation_failure_is_exit_2(tmp_path, capsys, monkeypatch):
    make_bundle(tmp_path, "base")
    make_profile(tmp_path, ["base"])

    def fail_validation(self):
        raise PrivilegeError("no credentials")

    monkeypatch.setattr(cli.PrivilegeManager, "validate", fail_validation)

    code = cli.main(["apply", str(tmp_path / "kitup.py"), "-s"])

    assert code == 2
    assert "error: no credentials" in capsys.readouterr().err

def test_bundle_errors_are_exit_2(tmp_path, capsys):
    write(tmp_path / "bundles" / "__init__.py", "")
    write(tmp_path / "bundles" / "bad" / "__init__.py", "x = 1\n")
    write(
        tmp_path / "kitup.py",
        """
        from bundles import bad

        from kitup import profile

        profile(bundles=[bad])
        """,
    )

    code = cli.main(["list", str(tmp_path / "kitup.py")])

    assert code == 2
    assert "does not define a bundle" in capsys.readouterr().err

def test_profile_named_kitup_can_import_the_package(tmp_path, capsys):
    make_bundle(tmp_path, "base")
    make_profile(tmp_path, ["base"])

    code = cli.main(["list", str(tmp_path / "kitup.py"), "--no-color"])

    assert code == 0
    assert "bundle: base" in capsys.readouterr().out

def make_shared_profile(tmp_path):
    shared = tmp_path / "shared"
    write(
        shared / "base_profile.py",
        """
        from kitup.registry import Bundle

        from kitup import profile

        base = Bundle("Base")

        profile(bundles=[base], description="Shared base")
        """,
    )
    write(
        tmp_path / "kitup.py",
        f"""
        import sys

        sys.path.insert(0, {str(shared)!r})

        import base_profile

        from kitup import profile

        profile.use(base_profile)
        """,
    )
    return shared

def test_profile_use_keeps_the_invoked_profile_as_config_root(tmp_path, monkeypatch):
    make_shared_profile(tmp_path)
    seen = {}

    class Context:
        def __init__(self, **kwargs):
            seen["config_dir"] = kwargs["config_dir"]

        def close(self):
            pass

    class Runner:
        def __init__(self, plan, context, output, **kwargs):
            pass

        def run(self):
            return 0

    monkeypatch.setattr(cli, "TaskContext", Context)
    monkeypatch.setattr(cli, "Runner", Runner)

    code = cli.main(["apply", str(tmp_path / "kitup.py"), "--no-color"])

    assert code == 0
    assert seen["config_dir"] == tmp_path.resolve()

def test_profile_tags_are_split_and_stripped(tmp_path, capsys):
    make_bundle(
        tmp_path,
        "base",
        tasks="""
        @task("Packages", tags={"packages"})
        def packages(ctx):
            ctx.changed({}, "packages")

        @task("Config", tags={"config"})
        def config(ctx):
            ctx.changed({}, "config")

        @task("Docs", tags={"docs"})
        def docs(ctx):
            ctx.changed({}, "docs")
        """,
    )
    make_profile(tmp_path, ["base"], tags=[" packages, config "])

    code = cli.main(["apply", str(tmp_path / "kitup.py"), "-n", "--no-color"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Packages" in out
    assert "Config" in out
    assert "Docs" not in out

def test_missing_profile_or_config_is_exit_2(tmp_path, capsys):
    code = cli.main(["list", str(tmp_path / "missing.py")])
    assert code == 2
    assert "error: profile not found" in capsys.readouterr().err

    make_bundle(tmp_path, "base")
    make_profile(tmp_path, ["base"], config=[Path("missing.py")])
    code = cli.main(["list", str(tmp_path / "kitup.py")])
    assert code == 2
    assert "error: config not found" in capsys.readouterr().err

def test_cli_tag_filters_merge_with_profile_defaults(tmp_path, capsys):
    make_bundle(
        tmp_path,
        "base",
        tasks="""
        @task("Packages", tags={"packages"})
        def packages(ctx):
            ctx.changed({}, "packages")

        @task("Config", tags={"config"})
        def config(ctx):
            ctx.changed({}, "config")

        @task("Docs", tags={"docs"})
        def docs(ctx):
            ctx.changed({}, "docs")
        """,
    )
    make_profile(tmp_path, ["base"], tags=["packages", "config"])

    code = cli.main(
        [
            "apply",
            str(tmp_path / "kitup.py"),
            "-n",
            "--no-color",
            "-t",
            "packages,docs",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Packages" in out
    assert "Config" not in out
    assert "Docs" not in out

    make_profile(tmp_path, ["base"], skip_tags=["config"])
    code = cli.main(
        [
            "apply",
            str(tmp_path / "kitup.py"),
            "-n",
            "--no-color",
            "-x",
            "packages",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Packages" not in out
    assert "Config" not in out
    assert "Docs" in out
