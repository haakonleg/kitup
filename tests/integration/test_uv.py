"""Integration tests for real uv behavior: projects, tools, and config."""

from pathlib import Path

import pytest

from .helpers import run, run_kitup, sudo, write_bundle

pytestmark = pytest.mark.integration

PACKAGE = "kitup-pip-test"
MODULE = "kitup_pip_test"
TOOL = "kitup-tool-test"
WHEELS = "/srv/pip-repo"
EXTRA_ARGS = ["--no-index", "--find-links", WHEELS]


def bundle(root: Path, name: str, body: str) -> Path:
    return write_bundle(root / name, f"uv_{name}", body)


def installed_version(project: Path) -> str | None:
    result = run(
        [str(project / ".venv" / "bin" / "python"), "-c", f"import {MODULE}; print({MODULE}.__version__)"],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def tool_version() -> str | None:
    result = run(["uv", "tool", "list"], check=False)
    for line in result.stdout.splitlines():
        if line.startswith(f"{TOOL} v"):
            return line.partition(" v")[2].strip()
    return None


def test_dependencies_present_latest_and_absent(tmp_path: Path, capsys) -> None:
    project = tmp_path / "app"

    deps = bundle(
        tmp_path,
        "deps",
        f"""
        from kitup.modules import uv

        @task("declare the fixture dependency")
        def declare(ctx):
            uv.dependencies(ctx, ["{PACKAGE}==1.0"], project=r"{project}", extra_args={EXTRA_ARGS!r})
        """,
    )
    assert run_kitup("apply", deps) == 0
    assert (project / "pyproject.toml").is_file()
    assert (project / ".venv" / "bin" / "python").is_file()
    assert (project / "uv.lock").is_file()
    assert f'dependencies = ["{PACKAGE}==1.0"]' in (project / "pyproject.toml").read_text()
    assert installed_version(project) == "1.0"

    # Applying the same task again changes nothing.
    capsys.readouterr()
    assert run_kitup("apply", deps) == 0
    assert "0 changed" in capsys.readouterr().out
    assert installed_version(project) == "1.0"

    # state="latest" upgrades the bare name to the newest available version.
    latest = bundle(
        tmp_path,
        "latest",
        f"""
        from kitup.modules import uv

        @task("upgrade the fixture dependency")
        def upgrade(ctx):
            uv.dependencies(ctx, ["{PACKAGE}"], project=r"{project}", state="latest", extra_args={EXTRA_ARGS!r})
        """,
    )
    assert run_kitup("apply", latest) == 0
    assert f'dependencies = ["{PACKAGE}"]' in (project / "pyproject.toml").read_text()
    assert installed_version(project) == "1.1"

    capsys.readouterr()
    assert run_kitup("apply", latest) == 0
    assert "0 changed" in capsys.readouterr().out
    assert installed_version(project) == "1.1"

    remove = bundle(
        tmp_path,
        "remove",
        f"""
        from kitup.modules import uv

        @task("remove the fixture dependency")
        def remove_dep(ctx):
            uv.dependencies(ctx, ["{PACKAGE}"], project=r"{project}", state="absent")
        """,
    )
    assert run_kitup("apply", remove) == 0
    assert "dependencies = []" in (project / "pyproject.toml").read_text()
    assert installed_version(project) is None

    capsys.readouterr()
    assert run_kitup("apply", remove) == 0
    assert "0 changed" in capsys.readouterr().out


def test_dependencies_in_a_group(tmp_path: Path, capsys) -> None:
    project = tmp_path / "grp"

    group = bundle(
        tmp_path,
        "group",
        f"""
        from kitup.modules import uv

        @task("manage the lint dependency group")
        def lint(ctx):
            uv.dependencies(ctx, ["{PACKAGE}==1.0"], project=r"{project}", group="lint", extra_args={EXTRA_ARGS!r})
        """,
    )
    assert run_kitup("apply", group) == 0
    text = (project / "pyproject.toml").read_text()
    assert "[dependency-groups]" in text
    assert f'lint = ["{PACKAGE}==1.0"]' in text
    assert installed_version(project) == "1.0"

    capsys.readouterr()
    assert run_kitup("apply", group) == 0
    assert "0 changed" in capsys.readouterr().out


def test_dependencies_dry_run_does_not_create_the_project(tmp_path: Path, capsys) -> None:
    project = tmp_path / "dry-project"

    root = bundle(
        tmp_path,
        "dryrun",
        f"""
        from kitup.modules import uv

        @task("dry-run a dependency declaration")
        def dry(ctx):
            uv.dependencies(ctx, ["{PACKAGE}"], project=r"{project}", extra_args={EXTRA_ARGS!r})
        """,
    )

    assert run_kitup("apply", root, "--dry-run") == 0

    assert not project.exists()
    assert "1 changed" in capsys.readouterr().out


def test_tools_install_latest_and_uninstall(tmp_path: Path, capsys) -> None:
    tools = bundle(
        tmp_path,
        "tools",
        f"""
        from kitup.modules import uv

        @task("install the fixture tool")
        def install_tool(ctx):
            uv.tools(ctx, ["{TOOL}==1.0"], extra_args={EXTRA_ARGS!r})
        """,
    )
    assert run_kitup("apply", tools) == 0
    assert tool_version() == "1.0"

    capsys.readouterr()
    assert run_kitup("apply", tools) == 0
    assert "0 changed" in capsys.readouterr().out
    assert tool_version() == "1.0"

    latest = bundle(
        tmp_path,
        "tool_latest",
        f"""
        from kitup.modules import uv

        @task("upgrade the fixture tool")
        def upgrade_tool(ctx):
            uv.tools(ctx, ["{TOOL}"], state="latest", extra_args={EXTRA_ARGS!r})
        """,
    )
    assert run_kitup("apply", latest) == 0
    assert tool_version() == "1.1"

    capsys.readouterr()
    assert run_kitup("apply", latest) == 0
    assert "0 changed" in capsys.readouterr().out
    assert tool_version() == "1.1"

    remove = bundle(
        tmp_path,
        "tool_remove",
        f"""
        from kitup.modules import uv

        @task("uninstall the fixture tool")
        def remove_tool(ctx):
            uv.tools(ctx, ["{TOOL}"], state="absent")
        """,
    )
    assert run_kitup("apply", remove) == 0
    assert tool_version() is None

    capsys.readouterr()
    assert run_kitup("apply", remove) == 0
    assert "0 changed" in capsys.readouterr().out


def test_config_user_scope_written_and_read_by_uv(tmp_path: Path, capsys) -> None:
    root = bundle(
        tmp_path,
        "cfg",
        """
        from kitup.modules import uv

        @task("configure uv for the user")
        def configure(ctx):
            uv.config(ctx, "concurrent-downloads", 4, scope="user")
            uv.config(ctx, "default-groups", ["dev"], scope="user")
            uv.config(ctx, "native-tls", True, scope="user")
        """,
    )
    assert run_kitup("apply", root) == 0

    conf = Path.home() / ".config" / "uv" / "uv.toml"
    expected = 'concurrent-downloads = 4\ndefault-groups = ["dev"]\nnative-tls = true\n'
    assert conf.read_text() == expected

    capsys.readouterr()
    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out
    assert conf.read_text() == expected

    remove = bundle(
        tmp_path,
        "cfg_remove",
        """
        from kitup.modules import uv

        @task("remove a uv user option")
        def remove_option(ctx):
            uv.config(ctx, "native-tls", state="absent", scope="user")
        """,
    )
    assert run_kitup("apply", remove) == 0
    assert conf.read_text() == 'concurrent-downloads = 4\ndefault-groups = ["dev"]\n'


def test_config_system_scope_with_sudo(tmp_path: Path) -> None:
    target = Path("/etc/uv/uv.toml")
    run(sudo("rm", "-f", str(target)), check=False)

    root = bundle(
        tmp_path,
        "syscfg",
        """
        from kitup.modules import uv

        @task("configure uv system-wide")
        def configure(ctx):
            uv.config(ctx, "python-downloads", "never", scope="system")
        """,
    )
    assert run_kitup("apply", root) == 0
    assert target.read_text() == 'python-downloads = "never"\n'

    remove = bundle(
        tmp_path,
        "syscfg_remove",
        """
        from kitup.modules import uv

        @task("remove the system uv option")
        def remove_option(ctx):
            uv.config(ctx, "python-downloads", state="absent", scope="system")
        """,
    )
    assert run_kitup("apply", remove) == 0
    assert target.read_text() == ""
