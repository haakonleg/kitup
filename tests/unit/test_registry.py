from __future__ import annotations

import importlib
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from kitup.errors import BundleError
from kitup.registry import Bundle, bundle_of, task


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")

def import_package(tmp_path: Path, files: dict[str, str]) -> tuple[str, object]:
    name = f"pkg_{uuid.uuid4().hex}"
    for relative, text in files.items():
        write(tmp_path / name / relative, text)
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        module = importlib.import_module(name)
    finally:
        sys.path.remove(str(tmp_path))
    return name, module

def test_bundle_name_must_not_be_empty():
    with pytest.raises(BundleError, match="bundle name must not be empty"):
        Bundle("")

def test_depends_on_accepts_modules_and_bundle_objects(tmp_path):
    name, base_module = import_package(
        tmp_path,
        {"__init__.py": "from kitup.registry import Bundle\nBundle('Base')\n"},
    )
    _, desktop_module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from kitup.registry import Bundle\n"
                f"import {name}\n"
                f"Bundle('Desktop', depends_on=[{name}])\n"
            )
        },
    )

    desktop = bundle_of(desktop_module)  # type: ignore[arg-type]
    assert [dependency.name for dependency in desktop.depends_on] == ["Base"]
    assert bundle_of(base_module) is desktop.depends_on[0]  # type: ignore[arg-type]

    other = Bundle("Other")
    dependent = Bundle("Dependent", depends_on=[other])
    assert dependent.depends_on == (other,)

def test_depends_on_rejects_a_single_string():
    with pytest.raises(BundleError, match="depends_on must be a sequence"):
        Bundle("base", depends_on="other")  # type: ignore[arg-type]

def test_task_decorator_registers_on_the_calling_bundle(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from kitup.registry import Bundle\n"
                "Bundle('Base')\n"
                "from . import tasks  # noqa: F401\n"
            ),
            "tasks.py": (
                "from kitup.registry import task\n"
                "@task('Install', tags={'packages'}, description='details')\n"
                "def install(ctx):\n"
                "    pass\n"
            ),
        },
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    (registered,) = bundle.tasks
    assert registered.name == "Install"
    assert registered.tags == frozenset({"packages"})
    assert registered.description == "details"
    assert registered.bundle is bundle
    assert registered.bundle_path == bundle.path
    assert registered.id == "Base.install"

def test_task_decorator_fails_when_bundle_was_not_constructed_first(tmp_path):
    with pytest.raises(BundleError, match="no bundle found for module"):
        import_package(
            tmp_path,
            {
                "__init__.py": "from . import tasks  # noqa: F401\n",
                "tasks.py": (
                    "from kitup.registry import task\n"
                    "@task('Too early')\n"
                    "def too_early(ctx):\n"
                    "    pass\n"
                ),
            },
        )

def test_bundle_paths_and_resources(tmp_path):
    resource_root = tmp_path / "resources"
    resource_root.mkdir()
    resource = resource_root / "files" / "example.conf"
    write(resource, "value = 1\n")

    bundle = Bundle("Base", path=resource_root)

    assert bundle.path == resource_root
    assert bundle.resource_path("files", "example.conf") == resource
    assert bundle.read_resource("files", "example.conf") == "value = 1\n"

def test_task_names_must_be_strings():
    bundle = Bundle("Base")

    with pytest.raises(BundleError, match="task name must be a string"):
        bundle.task(None)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="task name must be a string"):
        bundle.task(123)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="task name must not be empty"):
        bundle.task("")

    with pytest.raises(BundleError, match="task name must be a string"):
        @task
        def decorated(ctx):
            pass
