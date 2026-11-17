"""Integration test for project discovery, dependencies, and var merging."""

from pathlib import Path

import pytest

from ..helpers import make_project, write
from .helpers import run_kitup

pytestmark = pytest.mark.integration


def test_project_merges_vars_and_resolves_dependencies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base')\n",
                "tasks.py": """
                from kitup import ctx, task

                @task
                def show():
                    value = f"{ctx.vars.BASE}/{ctx.vars.CHILD}"
                    ctx.changed(summary=value, value=value)
                """,
            },
            "extra": {
                "__init__.py": "from kitup import bundle\n\nbundle('Extra', depends_on=['base'])\n",
                "tasks.py": """
                from kitup import ctx, task

                @task
                def extra():
                    ctx.changed(summary="extra")
                """,
            },
        },
        vars="BASE = 'base'\n",
        var_files={"child": "CHILD = 'child'\n"},
    )

    assert run_kitup("apply", tmp_path, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "base/child" in out
    assert out.index("bundle: Base (base)") < out.index("bundle: Extra (extra)")


def test_preset_selects_bundles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base')\n",
                "tasks.py": """
                from kitup import ctx, task

                @task
                def base_task():
                    ctx.changed(summary="base")
                """,
            },
            "extra": {
                "__init__.py": "from kitup import bundle\n\nbundle('Extra')\n",
                "tasks.py": """
                from kitup import ctx, task

                @task
                def extra_task():
                    ctx.changed(summary="extra")
                """,
            },
        },
        preset={"laptop": "--include-bundles extra\n"},
    )

    assert run_kitup("apply", tmp_path, "--preset", "laptop", "--dry-run") == 0

    out = capsys.readouterr().out
    assert "Extra task" in out
    assert "Base task" not in out


def test_bundle_path_loads_external_bundles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_project(
        tmp_path / "machine",
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base')\n",
                "tasks.py": "from kitup import ctx, task\n\n@task\ndef base_task():\n    ctx.changed(summary='base')\n",
            }
        },
    )
    external = tmp_path / "external-bundles"
    folder = external / "extra"
    folder.mkdir(parents=True)
    (folder / "tasks.py").write_text(
        "from kitup import ctx, task\n\n@task\ndef extra_task():\n    ctx.changed(summary='external')\n",
        encoding="utf-8",
    )

    assert run_kitup("apply", root, "--bundle-path", str(external), "--dry-run") == 0

    out = capsys.readouterr().out
    assert "Base task" in out
    assert "Extra task" in out


def test_files_and_modules_paths_load_external_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_project(
        tmp_path / "machine",
        bundles={
            "base": {
                "tasks.py": """
                from kitup import ctx, task
                from modules import external

                @task
                def show():
                    result = external.install()
                    content = (ctx.files / "message.txt").read_text().strip()
                    ctx.changed(summary=f"{result.summary}; {content}")
                """
            }
        },
    )
    external_files = tmp_path / "external-files"
    write(external_files / "base" / "message.txt", "external files\n")
    external_modules = tmp_path / "external-modules"
    write(
        external_modules / "external.py",
        """
        from kitup import module
        from kitup.context import current_context

        module("external")


        def install():
            ctx = current_context()
            return ctx.changed(summary="external module")
        """,
    )

    assert (
        run_kitup(
            "apply",
            root,
            "--files-path",
            str(external_files),
            "--modules-path",
            str(external_modules),
            "--dry-run",
        )
        == 0
    )

    assert "external module; external files" in capsys.readouterr().out


def test_var_file_adds_values_after_discovered_vars(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_project(
        tmp_path / "machine",
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base')\n",
                "tasks.py": """
                from kitup import ctx, task

                @task
                def show():
                    ctx.changed(summary=f"secret={ctx.vars.SECRET}")
                """,
            }
        },
        vars="SECRET = 'discovered'\n",
    )
    (root / "secrets.py").write_text("SECRET = 'external'\n", encoding="utf-8")

    assert run_kitup("apply", root, "--var-file", "secrets.py", "--dry-run") == 0

    assert "secret=external" in capsys.readouterr().out
