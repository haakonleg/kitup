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


def build_zipapp(tmp_path: Path) -> Path:
    """Build the zipapp with the current interpreter and return its path."""
    module = load_build_module()
    target = tmp_path / "kitup"
    module.build(target, interpreter=sys.executable)
    return target


def run_zipapp(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the zipapp without site packages and return the completed process."""
    return subprocess.run(
        [sys.executable, "-S", str(target), *args],
        capture_output=True,
        text=True,
        check=False,
    )


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


def test_bundled_distributions_come_from_pyproject():
    module = load_build_module()

    assert module.BUNDLED_DISTRIBUTIONS == ("Jinja2", "PyYAML")


def test_zipapp_bundles_runtime_dependencies_and_licenses(tmp_path):
    target = build_zipapp(tmp_path)

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        notices = archive.read("THIRD_PARTY_NOTICES.txt").decode("utf-8")

    assert "jinja2/__init__.py" in names
    assert "markupsafe/__init__.py" in names
    assert "markupsafe/_native.py" in names
    assert "yaml/__init__.py" in names
    assert "kitup/__init__.py" in names
    assert "jinja2/__init__.pyc" in names
    assert "markupsafe/__init__.pyc" in names
    assert "yaml/__init__.pyc" in names
    assert "kitup/__init__.pyc" in names
    assert "THIRD_PARTY_NOTICES.txt" in names
    assert "_yaml/__init__.py" not in names
    assert "PyYAML" in notices
    assert not [name for name in names if name.endswith(".so")]
    assert not [name for name in names if "/__pycache__/" in name]

    result = run_zipapp(target, "--version")

    assert result.returncode == 0, result.stderr
    assert "kitup " in result.stdout


def test_zipapp_lists_builtin_modules_and_renders_yaml_helpers(tmp_path):
    target = build_zipapp(tmp_path)

    result = run_zipapp(target, "list-modules", str(tmp_path), "--no-color")

    assert result.returncode == 0, result.stderr
    assert "built-in modules:" in result.stdout
    assert "  - files" in result.stdout

    root = tmp_path / "project"
    bundle = root / "bundles" / "demo"
    bundle.mkdir(parents=True)
    (bundle / "main.py").write_text("from kitup import bundle\n\nbundle()\n", encoding="utf-8")
    rendered = tmp_path / "rendered.json"
    (bundle / "tasks.py").write_text(
        "from kitup import task\n"
        "from kitup.modules import files\n\n"
        "@task\n"
        "def render():\n"
        f"    files.template('message.j2', {str(rendered)!r})\n",
        encoding="utf-8",
    )
    (bundle / "files").mkdir()
    (bundle / "files" / "message.j2").write_text(
        "{{ {'a': [1, 2]} | to_yaml | from_yaml | to_json }}\n",
        encoding="utf-8",
    )

    result = run_zipapp(target, "apply", str(root), "--no-color")

    assert result.returncode == 0, result.stderr
    assert rendered.read_text(encoding="utf-8") == '{"a": [1, 2]}\n'
