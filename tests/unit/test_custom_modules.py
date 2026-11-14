"""Tests for the public custom-module API and project ``modules/`` discovery."""

import importlib
import sys
import uuid
from pathlib import Path

import pytest

from kitup.context import current_context
from kitup.errors import BundleError, ProjectError
from kitup.project import discover_custom_modules, load_project
from kitup.registry import Bundle, Module, action_prefix, module, registered_modules

from ..helpers import write
from .helpers import make_context

SINGLE_FILE = """
from kitup import module
from kitup.context import current_context

module("mytool")


def install():
    ctx = current_context()
    return ctx.changed(summary="install mytool")
"""

PACKAGE_INIT = """
from kitup import module

module("webserver")
"""

PACKAGE_MODULE = """
from kitup.context import current_context


def install():
    ctx = current_context()
    return ctx.changed(summary="install webserver")
"""

PACKAGE_WITH_HELPER = """
from kitup.context import current_context
from kitup.results import CallResult


def install():
    ctx = current_context()
    return ctx.changed(_result())


def _result():
    return CallResult(changed=True, summary="install webserver")
"""

MYTOOL = """
from kitup import module
from kitup.context import current_context
from kitup.modules import files

module("mytool")


def install():
    ctx = current_context()
    files.file(ctx.root / "out.txt", content="hello\\n")
    return ctx.changed(summary="install mytool")
"""

TASKS = """
from kitup import task
from modules import mytool


@task
def install():
    mytool.install()
"""


def import_tree(tmp_path: Path, files: dict[str, str]) -> str:
    """Write a unique package tree under ``tmp_path`` and import its root."""
    name = f"pkg_{uuid.uuid4().hex}"
    for relative, text in files.items():
        write(tmp_path / name / relative, text)
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        importlib.import_module(name)
    finally:
        sys.path.remove(str(tmp_path))
    return name


def task_context(tmp_path: Path):
    bundle = Bundle("app")

    @bundle.task
    def run():
        ctx = current_context()
        return ctx.changed(summary="run")

    context = make_context(tmp_path)
    context.begin_task(bundle.tasks[0])
    return context


@pytest.mark.parametrize("name", [1, "", "not valid", "class", "a.b"])
def test_module_rejects_invalid_names(name):
    with pytest.raises(BundleError, match="module name"):
        module(name)  # type: ignore[arg-type]


def test_module_registers_the_calling_module(tmp_path):
    name = import_tree(tmp_path, {"tool.py": SINGLE_FILE})
    tool = importlib.import_module(f"{name}.tool")

    registered = tool.__kitup_module__
    assert isinstance(registered, Module)
    assert registered.name == "mytool"
    assert registered.module_name == f"{name}.tool"
    assert registered in registered_modules()
    assert action_prefix(f"{name}.tool") == "mytool"
    assert action_prefix(f"{name}.tool.submodule") == "mytool"


def test_package_module_covers_submodules(tmp_path):
    name = import_tree(
        tmp_path,
        {"mod/__init__.py": PACKAGE_INIT, "mod/nginx.py": PACKAGE_MODULE},
    )
    nginx = importlib.import_module(f"{name}.mod.nginx")

    assert action_prefix(f"{name}.mod.nginx") == "webserver"
    task_context(tmp_path)
    assert nginx.install().action == "webserver.install"


def test_changed_infers_module_action(tmp_path):
    name = import_tree(tmp_path, {"tool.py": SINGLE_FILE})
    tool = importlib.import_module(f"{name}.tool")

    task_context(tmp_path)
    assert tool.install().action == "mytool.install"


def test_helper_results_are_recorded_by_the_public_function(tmp_path):
    name = import_tree(
        tmp_path,
        {"mod/__init__.py": PACKAGE_INIT, "mod/nginx.py": PACKAGE_WITH_HELPER},
    )
    nginx = importlib.import_module(f"{name}.mod.nginx")

    task_context(tmp_path)
    assert nginx.install().action == "webserver.install"


def test_changed_falls_back_to_task_action_for_unregistered_code(tmp_path):
    context = task_context(tmp_path)
    assert context.changed(summary="run").action == "task.run"


def test_changed_explicit_action_wins(tmp_path):
    name = import_tree(
        tmp_path,
        {
            "tool.py": """
from kitup import module
from kitup.context import current_context
from kitup.results import CallResult

module("mytool")


def install():
    ctx = current_context()
    return ctx.changed(CallResult(action="custom.step", changed=True, summary="install"))
"""
        },
    )
    tool = importlib.import_module(f"{name}.tool")

    task_context(tmp_path)
    assert tool.install().action == "custom.step"


def test_changed_fills_a_call_result_sentinel(tmp_path):
    name = import_tree(
        tmp_path,
        {
            "tool.py": """
from kitup import module
from kitup.context import current_context
from kitup.results import CallResult

module("mytool")


def install():
    ctx = current_context()
    return ctx.changed(CallResult(changed=True, summary="install"))
"""
        },
    )
    tool = importlib.import_module(f"{name}.tool")

    task_context(tmp_path)
    assert tool.install().action == "mytool.install"


def build_project(root: Path, modules: dict[str, str]) -> Path:
    for relative, text in modules.items():
        write(root / "modules" / relative, text)
    return root


def test_project_discovers_custom_modules(tmp_path):
    build_project(
        tmp_path,
        {
            "mytool.py": SINGLE_FILE,
            "webserver/__init__.py": PACKAGE_INIT,
            "_private.py": "VALUE = 1\n",
            "notes.txt": "ignored\n",
        },
    )

    project = load_project(tmp_path)

    names = sorted(path.stem for path in project.module_paths)
    assert names == ["mytool", "webserver"]


def test_load_project_does_not_import_custom_modules(tmp_path):
    build_project(tmp_path, {"mytool.py": "raise RuntimeError('imported')\n"})

    project = load_project(tmp_path)

    assert [path.stem for path in project.module_paths] == ["mytool"]
    assert "modules.mytool" not in sys.modules


def test_discover_custom_modules_imports_them(tmp_path):
    build_project(tmp_path, {"mytool.py": SINGLE_FILE})

    project = load_project(tmp_path)
    registered = discover_custom_modules(project)

    assert [entry.name for entry in registered] == ["mytool"]
    assert "modules.mytool" in sys.modules


def test_modules_path_discovers_external_modules(tmp_path):
    root = tmp_path / "machine"
    root.mkdir()
    external = tmp_path / "external-modules"
    write(external / "mytool.py", SINGLE_FILE)
    write(external / "webserver" / "__init__.py", PACKAGE_INIT)

    project = load_project(root, modules_paths=[external])
    registered = discover_custom_modules(project)

    assert [path.stem for path in project.module_paths] == ["mytool", "webserver"]
    assert [entry.name for entry in registered] == ["mytool", "webserver"]
    assert importlib.import_module("modules.mytool") is not None


def test_project_modules_init_is_kept_when_extra_roots_are_added(tmp_path):
    root = tmp_path / "machine"
    write(root / "modules" / "__init__.py", "VALUE = 'project init'\n")
    write(root / "modules" / "local.py", 'from kitup import module\n\nmodule("local")\n')
    external = tmp_path / "external-modules"
    write(external / "external.py", 'from kitup import module\n\nmodule("external")\n')

    project = load_project(root, modules_paths=[external])
    registered = discover_custom_modules(project)

    assert [entry.name for entry in registered] == ["external", "local"]
    assert importlib.import_module("modules").VALUE == "project init"


def test_bundle_tasks_can_import_external_modules(tmp_path):
    external = tmp_path / "external-modules"
    write(external / "mytool.py", MYTOOL)
    root = tmp_path / "machine"
    write(root / "bundles" / "app" / "__init__.py", 'from kitup import bundle\n\nbundle("App")\n')
    write(root / "bundles" / "app" / "tasks.py", TASKS)

    project = load_project(root, modules_paths=[external])
    task = project.bundles[0].tasks[0]
    context = make_context(root, project=project)
    context.begin_task(task)
    task.func()

    assert (root / "out.txt").read_text(encoding="utf-8") == "hello\n"
    assert [call.action for call in context.calls] == ["files.file", "mytool.install"]


def test_external_package_modules_support_relative_imports(tmp_path):
    external = tmp_path / "external-modules"
    write(external / "webserver" / "__init__.py", PACKAGE_INIT)
    write(
        external / "webserver" / "nginx.py",
        "from ._helper import VALUE\n\n"
        "from kitup.context import current_context\n\n\n"
        "def install():\n"
        "    ctx = current_context()\n"
        "    return ctx.changed(summary=VALUE)\n",
    )
    write(external / "webserver" / "_helper.py", "VALUE = 'external helper'\n")
    root = tmp_path / "machine"
    root.mkdir()

    project = load_project(root, modules_paths=[external])
    discover_custom_modules(project)
    nginx = importlib.import_module("modules.webserver.nginx")
    task_context(root)

    assert nginx.install().summary == "external helper"


def test_project_requires_module_registration(tmp_path):
    build_project(tmp_path, {"mytool.py": "def install():\n    pass\n"})

    project = load_project(tmp_path)
    with pytest.raises(ProjectError, match=r"must call module\(\.\.\.\)"):
        discover_custom_modules(project)


def test_project_allows_a_custom_module_name(tmp_path):
    build_project(
        tmp_path,
        {
            "mytool.py": """
from kitup import module
from kitup.context import current_context

module("other")


def install():
    ctx = current_context()
    return ctx.changed(summary="install")
"""
        },
    )

    project = load_project(tmp_path)

    assert [path.stem for path in project.module_paths] == ["mytool"]
    discover_custom_modules(project)
    tool = importlib.import_module("modules.mytool")
    task_context(tmp_path)
    assert tool.install().action == "other.install"


def test_project_rejects_package_without_init(tmp_path):
    build_project(tmp_path, {"webserver/nginx.py": PACKAGE_MODULE})

    with pytest.raises(ProjectError, match=r"missing __init__\.py"):
        load_project(tmp_path)


def test_project_rejects_module_file_shadowing_directory(tmp_path):
    build_project(tmp_path, {"mytool.py": SINGLE_FILE})
    write(tmp_path / "modules.py", "VALUE = 1\n")

    with pytest.raises(ProjectError, match="shadows"):
        load_project(tmp_path)


def test_project_reloads_modules_between_roots(tmp_path):
    first = build_project(
        tmp_path / "first",
        {"tool.py": 'from kitup import module\n\nmodule("tool")\n\n\ndef value():\n    return "first"\n'},
    )
    second = build_project(
        tmp_path / "second",
        {"tool.py": 'from kitup import module\n\nmodule("tool")\n\n\ndef value():\n    return "second"\n'},
    )

    first_project = load_project(first)
    discover_custom_modules(first_project)
    assert importlib.import_module("modules.tool").value() == "first"

    second_project = load_project(second)
    discover_custom_modules(second_project)
    assert importlib.import_module("modules.tool").value() == "second"


@pytest.fixture
def custom_project(tmp_path: Path) -> Path:
    build_project(tmp_path, {"mytool.py": MYTOOL})
    write(tmp_path / "bundles" / "app" / "__init__.py", 'from kitup import bundle\n\nbundle("App")\n')
    write(tmp_path / "bundles" / "app" / "tasks.py", TASKS)
    return tmp_path


def run_task(root: Path, *, dry_run: bool = False):
    project = load_project(root)
    task = project.bundles[0].tasks[0]
    context = make_context(root, project=project, dry_run=dry_run)
    context.begin_task(task)
    task.func()
    return context


def test_project_task_uses_custom_module(custom_project):
    context = run_task(custom_project)

    assert (custom_project / "out.txt").read_text() == "hello\n"
    assert [call.action for call in context.calls] == ["files.file", "mytool.install"]


def test_project_task_is_idempotent(custom_project):
    run_task(custom_project)
    context = run_task(custom_project)

    assert context.calls[0].action == "files.file"
    assert context.calls[0].changed is False


def test_project_task_honors_dry_run(custom_project):
    context = run_task(custom_project, dry_run=True)

    assert not (custom_project / "out.txt").exists()
    assert context.calls[0].action == "files.file"
    assert context.calls[0].changed is True
