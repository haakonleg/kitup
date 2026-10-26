"""Build a standalone zipapp."""

from __future__ import annotations

import shutil
import tempfile
import tomllib
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
COMMAND, ENTRY_POINT = next(iter(CONFIG["project"]["scripts"].items()))
MODULE, FUNCTION = ENTRY_POINT.split(":", 1)
PACKAGE = MODULE.split(".", 1)[0]
TARGET = ROOT / "dist" / COMMAND
LAUNCHER = f"""\
from {MODULE} import {FUNCTION}

raise SystemExit({FUNCTION}())
"""


def build() -> Path:
    """Stage the runtime package and create the zipapp."""
    with tempfile.TemporaryDirectory() as temp:
        stage = Path(temp) / "app"
        shutil.copytree(
            ROOT / PACKAGE,
            stage / PACKAGE,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        (stage / "__main__.py").write_text(LAUNCHER, encoding="utf-8")

        TARGET.parent.mkdir(exist_ok=True)
        zipapp.create_archive(
            stage,
            TARGET,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
        TARGET.chmod(0o755)
    return TARGET


if __name__ == "__main__":
    print(build())
