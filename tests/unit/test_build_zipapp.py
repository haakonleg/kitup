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


def test_build_environment_uses_uv(monkeypatch, tmp_path):
    module = load_build_module()
    calls = []

    def fake_run(command, description, *, cwd=None, env=None):
        calls.append((command, description, cwd, env))

    monkeypatch.setattr(module, "_run", fake_run)

    environment = tmp_path / "venv"
    python = module._create_build_venv(environment)

    assert python == environment / "bin" / "python"
    assert len(calls) == 1

    command, description, cwd, env = calls[0]
    assert command[:2] == ["uv", "sync"]
    assert "--locked" in command
    assert "--no-default-groups" in command
    assert "--no-install-project" in command
    assert env["UV_PROJECT_ENVIRONMENT"] == str(environment)
    assert cwd == module.ROOT
    assert description == "create the build environment"


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
