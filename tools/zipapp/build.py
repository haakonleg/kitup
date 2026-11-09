"""Build a standalone zipapp with bundled runtime dependencies."""

import compileall
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import zipapp
from collections.abc import Mapping
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[2]
PROJECT: Final = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
COMMAND: Final[str] = next(iter(PROJECT["scripts"]))
ENTRY_POINT: Final[str] = PROJECT["scripts"][COMMAND]
MODULE: Final[str] = ENTRY_POINT.split(":", 1)[0]
FUNCTION: Final[str] = ENTRY_POINT.split(":", 1)[1]
PACKAGE: Final = MODULE.split(".", 1)[0]
TARGET: Final = ROOT / "dist" / COMMAND

_REQUIREMENT_NAME: Final = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _dependency_names(requirements: object) -> tuple[str, ...]:
    """Return distribution names from ``project.dependencies`` in pyproject.toml."""
    if not isinstance(requirements, list):
        raise SystemExit("project.dependencies must be a list in pyproject.toml")
    names: list[str] = []
    for requirement in requirements:
        if not isinstance(requirement, str):
            raise SystemExit(f"invalid project dependency: {requirement!r}")
        match = _REQUIREMENT_NAME.match(requirement)
        if match is None:
            raise SystemExit(f"cannot parse project dependency: {requirement!r}")
        names.append(match.group(1))
    return tuple(names)


BUNDLED_DISTRIBUTIONS: Final = _dependency_names(PROJECT.get("dependencies"))
NOTICES: Final = Path(__file__).with_name("THIRD_PARTY_NOTICES.txt")
IGNORE: Final = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.so")

LAUNCHER: Final = f"""\
from {MODULE} import {FUNCTION}

raise SystemExit({FUNCTION}())
"""

LOCATE_PACKAGES: Final = textwrap.dedent(
    """\
    import importlib.metadata
    import importlib.util
    import json
    import re
    import sys

    REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")

    def canonical(name):
        return re.sub(r"[-_.]+", "-", name).lower()

    def requirement_name(value):
        match = REQUIREMENT_NAME.match(value)
        return match.group(0) if match else None

    roots = {canonical(name) for name in sys.argv[1:]}
    owners = {}
    for module, distributions in importlib.metadata.packages_distributions().items():
        for distribution in distributions:
            owners.setdefault(canonical(distribution), set()).add(module)

    pending = list(sys.argv[1:])
    seen = set()
    imports = set()
    while pending:
        name = pending.pop()
        key = canonical(name)
        if key in seen:
            continue
        seen.add(key)
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            if key in roots:
                raise SystemExit(f"missing distribution: {name}")
            continue
        imports.update(owners.get(key, ()))
        for requirement in distribution.requires or ():
            if "extra ==" in requirement:
                continue
            required = requirement_name(requirement)
            if required is not None:
                pending.append(required)

    paths = {}
    for name in sorted(imports):
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.submodule_search_locations:
            continue
        paths[name] = next(iter(spec.submodule_search_locations))

    print(json.dumps(paths))
    """
)


def build(target: Path = TARGET, *, interpreter: str | Path | None = None) -> Path:
    """Build the zipapp, installing runtime dependencies into a temporary uv environment.

    ``interpreter`` overrides the environment used to locate bundled packages;
    tests use it to avoid creating a uv environment.
    """
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / "app"
        python = Path(interpreter) if interpreter is not None else _create_build_venv(Path(temporary) / "venv")

        _stage_package(stage)
        _stage_dependencies(stage, python)
        _stage_notices(stage)
        _stage_launcher(stage)
        _compile_stage(stage)
        _create_archive(stage, target)

    return target


def _create_build_venv(directory: Path) -> Path:
    """Create a uv-managed build environment and return its Python."""
    environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(directory)}
    _run(
        [
            "uv",
            "sync",
            "--locked",
            "--no-default-groups",
            "--no-install-project",
            "--python",
            sys.executable,
        ],
        "create the build environment",
        cwd=ROOT,
        env=environment,
    )
    return directory / "bin" / "python"


def _stage_package(stage: Path) -> None:
    """Copy the kitup package into the staging directory."""
    shutil.copytree(ROOT / PACKAGE, stage / PACKAGE, ignore=IGNORE)


def _stage_dependencies(stage: Path, python: Path) -> None:
    """Copy bundled runtime packages from ``python`` into the staging directory."""
    for name, directory in _find_packages(python, BUNDLED_DISTRIBUTIONS).items():
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


def _find_packages(python: Path, distributions: tuple[str, ...]) -> dict[str, Path]:
    """Return package directories for ``distributions`` and their dependencies."""
    result = subprocess.run(
        [str(python), "-c", LOCATE_PACKAGES, *distributions],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"could not locate bundled packages: {detail}")

    paths = json.loads(result.stdout)
    return {name: Path(path) for name, path in paths.items()}


def _run(
    command: list[str],
    description: str,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Run a command and turn failures into a user-facing build error."""
    result = subprocess.run(
        command,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=cwd,
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"could not {description}: {detail}")


if __name__ == "__main__":
    print(build())
