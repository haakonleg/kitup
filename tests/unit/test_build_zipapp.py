from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

BUILD_SCRIPT = Path(__file__).parents[2] / "tools" / "zipapp" / "build.py"


def load_build_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zipapp_build", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zipapp_bundles_jinja2_and_licenses(tmp_path):
    module = load_build_module()
    target = tmp_path / "kitup"

    module.build(target, interpreter=sys.executable)

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())

    assert "jinja2/__init__.py" in names
    assert "markupsafe/__init__.py" in names
    assert "markupsafe/_native.py" in names
    assert "kitup/__init__.py" in names
    assert "jinja2/__init__.pyc" in names
    assert "markupsafe/__init__.pyc" in names
    assert "kitup/__init__.pyc" in names
    assert "THIRD_PARTY_NOTICES.txt" in names
    assert not [name for name in names if name.endswith(".so")]
    assert not [name for name in names if "/__pycache__/" in name]

    result = subprocess.run(
        [sys.executable, "-S", str(target), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "kitup " in result.stdout
