"""Integration tests for real pip behavior inside a venv."""

from pathlib import Path

import pytest

from .helpers import run, run_kitup, write_bundle

pytestmark = pytest.mark.integration

PACKAGE = "kitup-pip-test"
WHEELS = "/srv/pip-repo"
EXTRA_ARGS = ["--no-index", "--find-links", WHEELS]


def installed_version(venv: Path, name: str) -> str | None:
    result = run([str(venv / "bin" / "python"), "-m", "pip", "show", name], check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Version:"):
            return line.partition(":")[2].strip()
    return None


def pip_config_get(venv: Path, key: str) -> str:
    result = run([str(venv / "bin" / "python"), "-m", "pip", "config", "get", key])
    return result.stdout.strip()


def bundle(root: Path, name: str, body: str) -> Path:
    return write_bundle(root / name, f"pip_{name}", body)


def test_install_latest_and_remove_in_venv(tmp_path: Path, capsys) -> None:
    venv = tmp_path / "venv"

    install = bundle(
        tmp_path,
        "install",
        f"""
        from kitup.modules import pip

        @task("install fixture package")
        def install_fixture(ctx):
            pip.packages(ctx, ["{PACKAGE}==1.0"], venv=r"{venv}", extra_args={EXTRA_ARGS!r})
        """,
    )
    assert run_kitup("apply", install) == 0
    assert (venv / "bin" / "python").is_file()
    assert installed_version(venv, PACKAGE) == "1.0"

    # Applying the same task again changes nothing.
    capsys.readouterr()
    assert run_kitup("apply", install) == 0
    assert "0 changed" in capsys.readouterr().out
    assert installed_version(venv, PACKAGE) == "1.0"

    # state="latest" upgrades the bare name to the newest available version.
    latest = bundle(
        tmp_path,
        "latest",
        f"""
        from kitup.modules import pip

        @task("upgrade fixture package to the latest version")
        def upgrade_fixture(ctx):
            pip.packages(ctx, ["{PACKAGE}"], state="latest", venv=r"{venv}", extra_args={EXTRA_ARGS!r})
        """,
    )
    assert run_kitup("apply", latest) == 0
    assert installed_version(venv, PACKAGE) == "1.1"

    # Already at the newest version: no change.
    capsys.readouterr()
    assert run_kitup("apply", latest) == 0
    assert "0 changed" in capsys.readouterr().out
    assert installed_version(venv, PACKAGE) == "1.1"

    remove = bundle(
        tmp_path,
        "remove",
        f"""
        from kitup.modules import pip

        @task("remove fixture package")
        def remove_fixture(ctx):
            pip.packages(ctx, ["{PACKAGE}"], state="absent", venv=r"{venv}")
        """,
    )
    assert run_kitup("apply", remove) == 0
    assert installed_version(venv, PACKAGE) is None

    capsys.readouterr()
    assert run_kitup("apply", remove) == 0
    assert "0 changed" in capsys.readouterr().out


def test_dry_run_does_not_create_the_venv(tmp_path: Path, capsys) -> None:
    venv = tmp_path / "dry-venv"
    root = bundle(
        tmp_path,
        "dry",
        f"""
        from kitup.modules import pip

        @task("install fixture package in dry-run")
        def install_dry(ctx):
            pip.packages(ctx, ["{PACKAGE}"], venv=r"{venv}", extra_args={EXTRA_ARGS!r})
        """,
    )

    assert run_kitup("apply", root, "--dry-run") == 0

    assert not venv.exists()
    assert "1 changed" in capsys.readouterr().out


def test_config_site_scope_written_and_read_by_pip(tmp_path: Path, capsys) -> None:
    venv = tmp_path / "cfg-venv"
    run(["python3", "-m", "venv", str(venv)])
    # a single-item list stays on the option line; several items use pip's
    # multi-line appendable syntax
    expected = (
        "[global]\n"
        f"timeout = 30\nfind-links = {WHEELS}\n"
        "trusted-host =\n    mirror1.example.com\n    mirror2.example.com\n"
    )

    root = bundle(
        tmp_path,
        "cfg",
        f"""
        from kitup.modules import pip

        @task("configure pip in the venv")
        def configure(ctx):
            pip.config(ctx, "timeout", 30, scope="site", venv=r"{venv}")
            pip.config(ctx, "find-links", ["{WHEELS}"], scope="site", venv=r"{venv}")
            pip.config(
                ctx,
                "trusted-host",
                ["mirror1.example.com", "mirror2.example.com"],
                scope="site",
                venv=r"{venv}",
            )
        """,
    )
    assert run_kitup("apply", root) == 0

    conf = venv / "pip.conf"
    assert conf.read_text() == expected
    # pip itself reads the site-level config written by the module
    assert pip_config_get(venv, "global.timeout") == "30"
    assert pip_config_get(venv, "global.find-links") == WHEELS
    hosts = pip_config_get(venv, "global.trusted-host")
    assert "mirror1.example.com" in hosts
    assert "mirror2.example.com" in hosts

    capsys.readouterr()
    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out
    assert conf.read_text() == expected
