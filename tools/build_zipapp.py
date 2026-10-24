"""Build a standalone kitup zipapp."""

from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "dist" / "kitup"
LAUNCHER = """\
from kitup.cli import main

raise SystemExit(main())
"""


def build() -> Path:
    """Stage the runtime package and create ``dist/kitup``."""
    with tempfile.TemporaryDirectory() as temp:
        stage = Path(temp) / "app"
        shutil.copytree(
            ROOT / "kitup",
            stage / "kitup",
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
