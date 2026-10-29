"""Build a standalone zipapp with bundled runtime dependencies."""

from __future__ import annotations

import compileall
import json
import shutil
import subprocess
import tempfile
import textwrap
import tomllib
import venv
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
COMMAND, ENTRY_POINT = next(iter(PROJECT["scripts"].items()))
MODULE, FUNCTION = ENTRY_POINT.split(":", 1)
PACKAGE = MODULE.split(".", 1)[0]
TARGET = ROOT / "dist" / COMMAND

BUNDLED_PACKAGES = ("jinja2", "markupsafe")
NOTICES = Path(__file__).with_name("THIRD_PARTY_NOTICES.txt")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.so")

LAUNCHER = f"""\
from {MODULE} import {FUNCTION}

raise SystemExit({FUNCTION}())
"""

LOCATE_PACKAGES = textwrap.dedent(
    """\
    import importlib.util
    import json
    import sys

    paths = {}
    for name in sys.argv[1:]:
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.submodule_search_locations:
            raise SystemExit(f"missing package: {name}")
        paths[name] = next(iter(spec.submodule_search_locations))

    print(json.dumps(paths))
    """
)


def build(target: Path = TARGET, *, interpreter: str | Path | None = None) -> Path:
    """Build the zipapp, installing runtime dependencies into a temporary venv.

    ``interpreter`` overrides the environment used to locate bundled packages;
    tests use it to avoid creating a virtualenv.
    """
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / "app"
        python = (
            Path(interpreter)
            if interpreter is not None
            else _create_build_venv(Path(temporary) / "venv")
        )

        _stage_package(stage)
        _stage_dependencies(stage, python)
        _stage_notices(stage)
        _stage_launcher(stage)
        _compile_stage(stage)
        _create_archive(stage, target)

    return target


def _create_build_venv(directory: Path) -> Path:
    """Create a virtualenv, install runtime dependencies, and return its Python."""
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(directory)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"could not create the build virtualenv: {exc}") from exc

    python = directory / "bin" / "python"
    dependencies = PROJECT.get("dependencies", [])
    if dependencies:
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                *dependencies,
            ],
            "install build dependencies",
        )
    return python


def _stage_package(stage: Path) -> None:
    """Copy the kitup package into the staging directory."""
    shutil.copytree(ROOT / PACKAGE, stage / PACKAGE, ignore=IGNORE)


def _stage_dependencies(stage: Path, python: Path) -> None:
    """Copy bundled runtime packages from ``python`` into the staging directory."""
    for name, directory in _find_packages(python, BUNDLED_PACKAGES).items():
        shutil.copytree(directory, stage / name, ignore=IGNORE)


def _stage_notices(stage: Path) -> None:
    """Copy the bundled third-party notices into the zipapp."""
    shutil.copy2(NOTICES, stage / NOTICES.name)


def _stage_launcher(stage: Path) -> None:
    """Write the zipapp entry point."""
    (stage / "__main__.py").write_text(LAUNCHER, encoding="utf-8")


def _compile_stage(stage: Path) -> None:
    """Write legacy ``.pyc`` files beside sources for faster imports.

    Sources remain in the archive so a different Python version can fall back
    to compiling them at runtime.
    """
    compiled = compileall.compile_dir(
        stage,
        legacy=True,
        force=True,
        quiet=1,
        stripdir=str(stage),
    )
    if not compiled:
        raise SystemExit("could not compile zipapp bytecode")


def _create_archive(stage: Path, target: Path) -> None:
    """Create the executable zipapp from the staging directory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    zipapp.create_archive(
        stage,
        target,
        interpreter="/usr/bin/env python3",
        compressed=True,
    )
    target.chmod(0o755)


def _find_packages(python: Path, names: tuple[str, ...]) -> dict[str, Path]:
    """Return the source directories of packages importable by ``python``."""
    result = subprocess.run(
        [str(python), "-c", LOCATE_PACKAGES, *names],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"could not locate bundled packages: {detail}")

    paths = json.loads(result.stdout)
    return {name: Path(path) for name, path in paths.items()}


def _run(command: list[str], description: str) -> None:
    """Run a command and turn failures into a user-facing build error."""
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"could not {description}: {detail}")


if __name__ == "__main__":
    print(build())
