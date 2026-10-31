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

def test_bundle_defaults_to_no_tasks():
    bundle = Bundle("Base")

    assert bundle.tasks == []

def test_modules_reject_none():
    with pytest.raises(BundleError, match="not None"):
        Bundle("Base", modules=None)  # type: ignore[arg-type]

def test_modules_reject_a_single_module():
    with pytest.raises(BundleError, match="not a single module"):
        Bundle("Base", modules=sys)  # type: ignore[arg-type]

def test_modules_reject_non_modules():
    with pytest.raises(BundleError, match="must contain task modules"):
        Bundle("Base", modules=["tasks"])  # type: ignore[list-item]

def test_modules_reject_duplicates():
    with pytest.raises(BundleError, match="duplicate task module"):
        Bundle("Base", modules=[sys, sys])

def test_task_module_cannot_be_shared_between_bundles(tmp_path):
    with pytest.raises(BundleError, match="already registered by bundle 'One'"):
        import_package(
            tmp_path,
            {
                "__init__.py": "from . import tasks, one, two\n",
                "tasks.py": (
                    "from kitup.registry import task\n"
                    "@task('Shared')\n"
                    "def shared(ctx):\n"
                    "    pass\n"
                ),
                "one/__init__.py": (
                    "from kitup.registry import Bundle\n"
                    "from .. import tasks\n"
                    "Bundle('One', modules=[tasks])\n"
                ),
                "two/__init__.py": (
                    "from kitup.registry import Bundle\n"
                    "from .. import tasks\n"
                    "Bundle('Two', modules=[tasks])\n"
                ),
            },
        )

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

def test_task_decorator_registers_listed_modules(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from kitup.registry import Bundle\n"
                "from . import tasks\n"
                "Bundle('Base', modules=[tasks])\n"
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

def test_task_imports_may_precede_bundle_construction(tmp_path):
    """A formatter may move imports above the Bundle(...) call."""
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from . import tasks\n"
                "from kitup.registry import Bundle\n"
                "Bundle('Base', modules=[tasks])\n"
            ),
            "tasks.py": (
                "from kitup.registry import task\n"
                "@task('First')\n"
                "def first(ctx):\n"
                "    pass\n"
                "@task('Second')\n"
                "def second(ctx):\n"
                "    pass\n"
            ),
        },
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.name for registered in bundle.tasks] == ["First", "Second"]

def test_module_order_defines_task_order(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from . import first, second\n"
                "from kitup.registry import Bundle\n"
                "Bundle('Base', modules=[second, first])\n"
            ),
            "first.py": (
                "from kitup.registry import task\n"
                "@task('First')\n"
                "def first(ctx):\n"
                "    pass\n"
            ),
            "second.py": (
                "from kitup.registry import task\n"
                "@task('Second')\n"
                "def second(ctx):\n"
                "    pass\n"
            ),
        },
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.name for registered in bundle.tasks] == ["Second", "First"]

def test_listed_module_must_register_a_task(tmp_path):
    with pytest.raises(BundleError, match="does not register any tasks"):
        import_package(
            tmp_path,
            {
                "__init__.py": (
                    "from kitup.registry import Bundle\n"
                    "from . import helpers\n"
                    "Bundle('Base', modules=[helpers])\n"
                ),
                "helpers.py": "VALUE = 1\n",
            },
        )

def test_nested_bundles_register_their_own_tasks(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from . import child\n"
                "from kitup.registry import Bundle\n"
                "Bundle('Parent')\n"
            ),
            "child/__init__.py": (
                "from kitup.registry import Bundle\n"
                "from . import tasks\n"
                "Bundle('Child', modules=[tasks])\n"
            ),
            "child/tasks.py": (
                "from kitup.registry import task\n"
                "@task('Child task')\n"
                "def child_task(ctx):\n"
                "    pass\n"
            ),
        },
    )

    parent = bundle_of(module)  # type: ignore[arg-type]
    assert parent.tasks == []
    child = bundle_of(sys.modules[f"{module.__name__}.child"])  # type: ignore[attr-defined]
    assert [registered.name for registered in child.tasks] == ["Child task"]

def test_inline_bundle_tasks_need_no_modules(tmp_path):
    bundle = Bundle("Local", path=tmp_path)

    @bundle.task("Write marker")
    def write_marker(ctx):
        pass

    assert [registered.name for registered in bundle.tasks] == ["Write marker"]

def test_bundle_of_requires_a_bundle(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": "",
            "tasks.py": (
                "from kitup.registry import task\n"
                "@task('Deferred')\n"
                "def deferred(ctx):\n"
                "    pass\n"
            ),
        },
    )

    with pytest.raises(BundleError, match="does not define a bundle"):
        bundle_of(module)  # type: ignore[arg-type]

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
