import importlib
import sys
import uuid
from pathlib import Path

import pytest

from kitup.errors import BundleError
from kitup.registry import (
    Bundle,
    SideEffect,
    action_prefix,
    bundle_of,
    discover_task_modules,
    forget_module_prefix,
    module,
    module_of,
    registered_modules,
    side_effect,
    task,
)
from kitup.registry import bundle as define_bundle

from ..helpers import write


def import_package(tmp_path: Path, files: dict[str, str], *, entry: str | None = None) -> tuple[str, object]:
    name = f"pkg_{uuid.uuid4().hex}"
    for relative, text in files.items():
        write(tmp_path / name / relative, text)
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        module = importlib.import_module(name if entry is None else f"{name}.{entry}")
    finally:
        sys.path.remove(str(tmp_path))
    return name, module


def test_bundle_defaults_factory_and_validation():
    bundle = Bundle()

    assert bundle.name == ""
    assert bundle.id == ""
    assert bundle.tasks == ()
    assert Bundle("Base").priority == 0
    assert Bundle("Base", priority=5).priority == 5

    created = define_bundle("Base", priority=-3)
    assert created.tasks == ()
    assert created.id == "Base"
    assert created.priority == -3
    assert not hasattr(sys.modules[__name__], "__kitup_bundle__")

    with pytest.raises(BundleError, match="bundle name must not be empty"):
        Bundle("")
    with pytest.raises(BundleError, match="bundle name must be a string"):
        Bundle(1)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="bundle priority must be an integer"):
        Bundle("Base", priority="high")  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="bundle priority must be an integer"):
        Bundle("Base", priority=True)


def test_modules_auto_discovery_explicit_order_and_empty_override(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "main.py": "from kitup import bundle\n\nbundle('Base')\n",
            "02_second.py": "from kitup import task\n\n@task\ndef second():\n    pass\n",
            "01_first.py": "from kitup import task\n\n@task\ndef first():\n    pass\n",
            "_helper.py": "VALUE = 1\n",
        },
        entry="main",
    )
    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.func.__name__ for registered in bundle.tasks] == ["first", "second"]

    _, module = import_package(
        tmp_path,
        {
            "main.py": (
                "from kitup import bundle\nfrom . import first, second\nbundle('Base', modules=[second, first])\n"
            ),
            "first.py": "from kitup import task\n\n@task\ndef first():\n    pass\n",
            "second.py": "from kitup import task\n\n@task\ndef second():\n    pass\n",
        },
        entry="main",
    )
    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.func.__name__ for registered in bundle.tasks] == ["second", "first"]

    _, module = import_package(
        tmp_path,
        {
            "main.py": "from kitup import bundle\n\nbundle('Base', modules=[])\n",
            "tasks.py": "from kitup import task\n\n@task\ndef first():\n    pass\n",
        },
        entry="main",
    )
    assert bundle_of(module).tasks == ()  # type: ignore[arg-type]


def test_modules_validation_and_path_requirements(tmp_path):
    with pytest.raises(BundleError, match="not a single value"):
        Bundle("Base", modules=sys)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="not a single value"):
        Bundle("Base", modules="tasks.py")  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="not a single value"):
        Bundle("Base", modules=tmp_path / "tasks.py")  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="must contain task modules or task module paths"):
        Bundle("Base", modules=[object()])  # type: ignore[list-item]
    with pytest.raises(BundleError, match="duplicate task module"):
        Bundle("Base", modules=[sys, sys])

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "tasks.py").mkdir()
    write(bundle_dir / "notes.txt", "VALUE = 1\n")
    write(bundle_dir / "__init__.py", "")
    write(bundle_dir / "main.py", "")

    with pytest.raises(BundleError, match="task module not found"):
        Bundle("Base", path=bundle_dir, modules=["missing.py"])
    with pytest.raises(BundleError, match="task module is not a file"):
        Bundle("Base", path=bundle_dir, modules=["tasks.py"])
    with pytest.raises(BundleError, match=r"must end in \.py"):
        Bundle("Base", path=bundle_dir, modules=["notes.txt"])
    with pytest.raises(BundleError, match=r"must not be __init__\.py"):
        Bundle("Base", path=bundle_dir, modules=["__init__.py"])
    with pytest.raises(BundleError, match=r"must not be main\.py"):
        Bundle("Base", path=bundle_dir, modules=["main.py"])


def test_duplicate_task_module_paths_are_rejected(tmp_path):
    write(
        tmp_path / "tasks.py",
        "from kitup import task\n\n@task\ndef install():\n    pass\n",
    )

    with pytest.raises(BundleError, match="duplicate task module path"):
        Bundle("Base", modules=[tmp_path / "tasks.py", tmp_path / "tasks.py"])


def test_modules_accept_relative_and_absolute_paths(tmp_path):
    bundle_dir = tmp_path / "bundle"
    write(
        bundle_dir / "tasks.py",
        """
        from kitup import task

        @task
        def install():
            pass
        """,
    )

    relative = Bundle("Relative", path=bundle_dir, modules=["tasks.py"])
    absolute = Bundle("Absolute", modules=[bundle_dir / "tasks.py"])

    for bundle in (relative, absolute):
        assert [registered.func.__name__ for registered in bundle.tasks] == ["install"]


def test_discovered_module_without_tasks_is_an_error(tmp_path):
    with pytest.raises(BundleError, match=r"does not register any tasks.*_private"):
        import_package(
            tmp_path,
            {
                "main.py": "from kitup import bundle\n\nbundle('Base')\n",
                "helpers.py": "VALUE = 1\n",
            },
            entry="main",
        )


def test_task_module_can_be_shared_between_bundles(tmp_path):
    name, _ = import_package(
        tmp_path,
        {
            "tasks.py": "from kitup import task\n\n@task\ndef shared():\n    pass\n",
            "one/main.py": "from kitup import bundle\nfrom .. import tasks\n\nbundle('One', modules=[tasks])\n",
            "two/main.py": "from kitup import bundle\nfrom .. import tasks\n\nbundle('Two', modules=[tasks])\n",
        },
    )

    one = bundle_of(importlib.import_module(f"{name}.one.main"))
    two = bundle_of(importlib.import_module(f"{name}.two.main"))

    assert [registered.id for registered in one.tasks] == ["One.shared"]
    assert [registered.id for registered in two.tasks] == ["Two.shared"]
    assert one.tasks[0] is not two.tasks[0]
    assert one.tasks[0].func is two.tasks[0].func


def test_reexported_task_is_not_claimed_by_the_importing_module(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "main.py": (
                "from kitup import bundle\nfrom . import first, second\nbundle('Base', modules=[first, second])\n"
            ),
            "first.py": "from kitup import task\n\n@task\ndef shared():\n    pass\n",
            "second.py": ("from kitup import task\nfrom .first import shared\n\n@task\ndef other():\n    pass\n"),
        },
        entry="main",
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.func.__name__ for registered in bundle.tasks] == ["shared", "other"]


def test_depends_on_accepts_ids_and_bundle_objects(tmp_path):
    _, base_module = import_package(
        tmp_path,
        {"main.py": "from kitup import bundle\n\nbundle('Base')\n"},
        entry="main",
    )
    _, desktop_module = import_package(
        tmp_path,
        {
            "main.py": (
                f"from kitup import bundle, bundle_of\nimport {base_module.__name__}\n"
                f"bundle('Desktop', depends_on=['base', bundle_of({base_module.__name__})])\n"
            )
        },
        entry="main",
    )

    desktop = bundle_of(desktop_module)  # type: ignore[arg-type]
    assert desktop.depends_on[0] == "base"
    assert isinstance(desktop.depends_on[1], Bundle)
    assert bundle_of(base_module) is desktop.depends_on[1]  # type: ignore[arg-type]


def test_depends_on_rejects_a_single_string_and_invalid_values():
    with pytest.raises(BundleError, match="depends_on must be a sequence"):
        Bundle("base", depends_on="other")  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="must contain bundle ids or Bundle objects"):
        Bundle("base", depends_on=[object()])  # type: ignore[list-item]
    with pytest.raises(BundleError, match="dependency id must not be empty"):
        Bundle("base", depends_on=[""])


def test_task_decorator_forms_and_normalized_metadata(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "main.py": "from kitup import bundle\nfrom . import tasks\n\nbundle('Base', modules=[tasks])\n",
            "tasks.py": (
                "from kitup import task\n"
                "\n"
                "@task\n"
                "def install_base_packages():\n"
                "    '''Install base packages.'''\n"
                "    pass\n"
                "\n"
                "@task('Second', tags={'packages'}, description='details')\n"
                "def second():\n"
                "    pass\n"
                "\n"
                "@task(tags={'other'})\n"
                "def third():\n"
                "    pass\n"
            ),
        },
        entry="main",
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.name for registered in bundle.tasks] == [
        "Install base packages",
        "Second",
        "Third",
    ]
    assert bundle.tasks[0].description == "Install base packages."
    assert bundle.tasks[1].tags == frozenset({"packages"})
    assert bundle.tasks[1].description == "details"
    assert bundle.tasks[2].tags == frozenset({"other"})
    assert bundle.tasks[0].id == "Base.install_base_packages"

    bundle = Bundle("Base", path=tmp_path)

    @bundle.task
    def install_base_packages():
        """Install base packages."""

    @bundle.task("  Custom  ")
    def custom():
        pass

    @bundle.task(tags={"other"})
    def tagged():
        pass

    def _private_task():
        pass

    def install__packages():
        pass

    bundle.task(_private_task)
    bundle.task(install__packages)

    assert [registered.name for registered in bundle.tasks] == [
        "Install base packages",
        "Custom",
        "Tagged",
        "Private task",
        "Install packages",
    ]
    assert bundle.tasks[0].description == "Install base packages."
    assert bundle.tasks[1].name == "Custom"
    assert bundle.tasks[2].tags == frozenset({"other"})
    assert side_effect("  Refresh  ")(lambda: None).name == "Refresh"


def test_task_metadata_validation():
    bundle = Bundle("Base")

    with pytest.raises(BundleError, match="task name must be a string"):
        bundle.task(123)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="task name must not be empty"):
        bundle.task("")
    with pytest.raises(BundleError, match="task name must be a string"):
        task(123)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="task name must not be empty"):
        task("")

    with pytest.raises(BundleError, match="bundle description must be a string"):
        Bundle("Base", description=1)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="description must be a string"):
        bundle.task(description=1)(lambda: None)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="description must be a string"):
        task(description=1)(lambda: None)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="description must be a string"):
        side_effect(description=1)(lambda: None)  # type: ignore[arg-type]

    class CallableObject:
        def __call__(self, ctx):
            pass

    with pytest.raises(BundleError, match="task decorator requires a Python function"):
        task(CallableObject())  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="task decorator requires a Python function"):
        bundle.task(CallableObject())  # type: ignore[arg-type]


def test_duplicate_task_ids_across_modules_are_rejected(tmp_path):
    with pytest.raises(BundleError, match=r"duplicate task id 'Base\.install'"):
        import_package(
            tmp_path,
            {
                "main.py": "from kitup import bundle\n\nbundle('Base')\n",
                "first.py": "from kitup import task\n\n@task\ndef install():\n    pass\n",
                "second.py": "from kitup import task\n\n@task\ndef install():\n    pass\n",
            },
            entry="main",
        )


def test_task_tags_are_validated_and_normalized():
    bundle = Bundle("Base")

    @bundle.task(tags={" packages ", "config,docs"})
    def tagged():
        pass

    assert bundle.tasks[0].tags == frozenset({"packages", "config", "docs"})

    with pytest.raises(BundleError, match="must be a sequence"):
        bundle.task(tags="packages")

    with pytest.raises(BundleError, match="must contain strings, not int"):
        bundle.task(tags={"packages", 1})

    with pytest.raises(BundleError, match="must not contain empty tags"):
        bundle.task(tags={"packages", " "})

    with pytest.raises(BundleError, match="must not contain empty tags"):
        bundle.task(tags={"packages,"})


def test_side_effect_decorator_forms_and_validation():
    @side_effect
    def reload_nginx():
        """Reload nginx."""

    explicit = side_effect("Refresh", one_shot=False, description="details")(lambda: None)

    assert isinstance(reload_nginx, SideEffect)
    assert reload_nginx.name == "Reload nginx"
    assert reload_nginx.description == "Reload nginx."
    assert reload_nginx.one_shot is True
    assert explicit.name == "Refresh"
    assert explicit.one_shot is False
    assert explicit.description == "details"

    with pytest.raises(BundleError, match="side effect name must be a string"):
        side_effect(123)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="side effect name must not be empty"):
        side_effect("")
    with pytest.raises(BundleError, match="side effect one_shot must be a boolean"):
        side_effect("Refresh", one_shot="yes")  # type: ignore[arg-type]


def test_bundle_of_requires_a_bundle(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "main.py": "",
            "tasks.py": "from kitup import task\n\n@task\ndef deferred():\n    pass\n",
        },
        entry="main",
    )

    with pytest.raises(BundleError, match="does not define a bundle"):
        bundle_of(module)  # type: ignore[arg-type]


def test_main_registers_tasks_after_discovered_modules(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "main.py": ("from kitup import bundle, task\n\nbundle('Base')\n\n@task\ndef inline():\n    pass\n"),
            "tasks.py": "from kitup import task\n\n@task\ndef discovered():\n    pass\n",
        },
        entry="main",
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.func.__name__ for registered in bundle.tasks] == ["discovered", "inline"]
    assert [registered.id for registered in bundle.tasks] == ["Base.discovered", "Base.inline"]


def test_task_before_bundle_in_main_is_rejected(tmp_path):
    with pytest.raises(BundleError, match=r"bundle\(\) must be called before defining tasks"):
        import_package(
            tmp_path,
            {"main.py": ("from kitup import bundle, task\n\n@task\ndef early():\n    pass\n\nbundle()\n")},
            entry="main",
        )


def test_task_in_main_without_bundle_is_rejected(tmp_path):
    with pytest.raises(BundleError, match=r"bundle\(\) must be called before defining tasks"):
        import_package(
            tmp_path,
            {"main.py": "from kitup import task\n\n@task\ndef orphan():\n    pass\n"},
            entry="main",
        )


def test_bundle_called_twice_in_main_is_rejected(tmp_path):
    with pytest.raises(BundleError, match="at most once"):
        import_package(
            tmp_path,
            {"main.py": "from kitup import bundle\n\nbundle()\nbundle()\n"},
            entry="main",
        )


def test_main_py_is_not_discovered_as_a_task_module(tmp_path):
    name, _ = import_package(
        tmp_path,
        {
            "main.py": "raise RuntimeError('main.py must not be discovered')\n",
            "tasks.py": "from kitup import task\n\n@task\ndef run():\n    pass\n",
        },
    )

    modules = discover_task_modules(name, tmp_path / name)

    assert [module.__name__ for module in modules] == [f"{name}.tasks"]


def test_module_registry_lifecycle(tmp_path):
    name, _ = import_package(
        tmp_path,
        {
            "tool.py": 'from kitup import module\n\nmodule("mytool")\n',
            "plain.py": 'from kitup import module\n\nmodule("plain")\n',
            "described.py": 'from kitup import module\n\nmodule("described", "A described module.")\n',
        },
    )
    tool = importlib.import_module(f"{name}.tool")
    importlib.import_module(f"{name}.plain")
    importlib.import_module(f"{name}.described")

    entries = {entry.module_name: entry for entry in registered_modules(name)}
    assert entries[f"{name}.tool"].name == "mytool"
    assert entries[f"{name}.tool"].description == ""
    assert entries[f"{name}.plain"].description == ""
    assert entries[f"{name}.described"].description == "A described module."
    assert module_of(tool) is entries[f"{name}.tool"]
    assert action_prefix(f"{name}.tool") == "mytool"
    assert registered_modules(f"{name}.tool") == (entries[f"{name}.tool"],)
    assert registered_modules(f"{name}.missing") == ()

    with pytest.raises(BundleError, match="module description must be a string"):
        module("bad", 1)  # type: ignore[arg-type]

    replaced_name, _ = import_package(
        tmp_path,
        {"tool.py": 'from kitup import module\n\nmodule("first")\nmodule("second")\n'},
    )
    replaced_tool = importlib.import_module(f"{replaced_name}.tool")
    registered = module_of(replaced_tool)
    assert [entry.name for entry in registered_modules(replaced_name)] == ["second"]
    assert registered is not None
    assert registered.name == "second"
    assert action_prefix(f"{replaced_name}.tool") == "second"

    forget_module_prefix(replaced_name)

    assert action_prefix(f"{replaced_name}.tool") is None
    assert registered_modules(replaced_name) == ()
