"""Integration test for project discovery, dependencies, and var merging."""

from pathlib import Path

import pytest

from ..helpers import make_project
from .helpers import run_kitup

pytestmark = pytest.mark.integration


def test_project_merges_vars_and_resolves_dependencies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import Bundle\n\nBundle('Base')\n",
                "tasks.py": """
                from kitup import task

                @task
                def show(ctx):
                    value = f"{ctx.vars.BASE}/{ctx.vars.CHILD}"
                    ctx.changed(summary=value, value=value)
                """,
            },
            "extra": {
                "__init__.py": "from kitup import Bundle\n\nBundle('Extra', depends_on=['base'])\n",
                "tasks.py": """
                from kitup import task

                @task
                def extra(ctx):
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
    assert out.index("bundle: base (Base)") < out.index("bundle: extra (Extra)")


def test_preset_selects_bundles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import Bundle\n\nBundle('Base')\n",
                "tasks.py": """
                from kitup import task

                @task
                def base_task(ctx):
                    ctx.changed(summary="base")
                """,
            },
            "extra": {
                "__init__.py": "from kitup import Bundle\n\nBundle('Extra')\n",
                "tasks.py": """
                from kitup import task

                @task
                def extra_task(ctx):
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
                "__init__.py": "from kitup import Bundle\n\nBundle('Base')\n",
                "tasks.py": "from kitup import task\n\n@task\ndef base_task(ctx):\n    ctx.changed(summary='base')\n",
            }
        },
    )
    external = tmp_path / "external-bundles"
    folder = external / "extra"
    folder.mkdir(parents=True)
    (folder / "tasks.py").write_text(
        "from kitup import task\n\n@task\ndef extra_task(ctx):\n    ctx.changed(summary='external')\n",
        encoding="utf-8",
    )

    assert run_kitup("apply", root, "--bundle-path", str(external), "--dry-run") == 0

    out = capsys.readouterr().out
    assert "Base task" in out
    assert "Extra task" in out


def test_var_file_adds_values_after_discovered_vars(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_project(
        tmp_path / "machine",
        bundles={
            "base": {
                "__init__.py": "from kitup import Bundle\n\nBundle('Base')\n",
                "tasks.py": """
                from kitup import task

                @task
                def show(ctx):
                    ctx.changed(summary=f"secret={ctx.vars.SECRET}")
                """,
            }
        },
        vars="SECRET = 'discovered'\n",
    )
    (root / "secrets.py").write_text("SECRET = 'external'\n", encoding="utf-8")

    assert run_kitup("apply", root, "--var-file", "secrets.py", "--dry-run") == 0

    assert "secret=external" in capsys.readouterr().out
