import importlib
import sys
import uuid
from pathlib import Path

import pytest

import kitup
from kitup.context import TaskContext
from kitup.errors import BundleError
from kitup.registry import Bundle, SideEffect, bundle_of, side_effect, task
from kitup.registry import bundle as define_bundle

from ..helpers import write


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


def test_bundle_name_is_optional_and_validated():
    bundle = Bundle()

    assert bundle.name == ""
    assert bundle.id == ""
    assert bundle.tasks == ()

    with pytest.raises(BundleError, match="bundle name must not be empty"):
        Bundle("")
    with pytest.raises(BundleError, match="bundle name must be a string"):
        Bundle(1)  # type: ignore[arg-type]


def test_package_reexports_the_authoring_api():
    assert kitup.Bundle is Bundle
    assert kitup.bundle is define_bundle
    assert kitup.bundle_of is bundle_of
    assert kitup.side_effect is side_effect
    assert kitup.task is task
    assert kitup.TaskContext is TaskContext


def test_bundle_defaults_to_no_tasks_outside_a_package(tmp_path):
    bundle = Bundle("Base")

    assert bundle.tasks == ()
    assert bundle.id == "Base"


def test_bundle_factory_outside_a_package_builds_without_registering():
    created = define_bundle("Base")

    assert created.tasks == ()
    assert created.id == "Base"
    assert not hasattr(sys.modules[__name__], "__kitup_bundle__")


def test_bundle_class_does_not_register_itself(tmp_path):
    _, module = import_package(
        tmp_path,
        {"__init__.py": "from kitup import Bundle\n\nBundle('Ignored')\n"},
    )

    assert getattr(module, "__kitup_bundle__", None) is None


def test_bundle_factory_supports_assignment(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": "from kitup import bundle\n\ncreated = bundle('Base')\n",
            "tasks.py": "from kitup import task\n\n@task\ndef install(ctx):\n    pass\n",
        },
    )

    created = bundle_of(module)  # type: ignore[arg-type]
    assert created is module.created  # type: ignore[attr-defined]
    assert [registered.func.__name__ for registered in created.tasks] == ["install"]


def test_modules_none_auto_discovers_sibling_task_modules(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": "from kitup import bundle\n\nbundle('Base')\n",
            "02_second.py": "from kitup import task\n\n@task\ndef second(ctx):\n    pass\n",
            "01_first.py": "from kitup import task\n\n@task\ndef first(ctx):\n    pass\n",
            "_helper.py": "VALUE = 1\n",
        },
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.func.__name__ for registered in bundle.tasks] == ["first", "second"]


def test_explicit_modules_define_order(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from kitup import bundle\nfrom . import first, second\nbundle('Base', modules=[second, first])\n"
            ),
            "first.py": "from kitup import task\n\n@task\ndef first(ctx):\n    pass\n",
            "second.py": "from kitup import task\n\n@task\ndef second(ctx):\n    pass\n",
        },
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.func.__name__ for registered in bundle.tasks] == ["second", "first"]


def test_empty_modules_disables_auto_discovery(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": "from kitup import bundle\n\nbundle('Base', modules=[])\n",
            "tasks.py": "from kitup import task\n\n@task\ndef first(ctx):\n    pass\n",
        },
    )

    assert bundle_of(module).tasks == ()  # type: ignore[arg-type]


def test_modules_reject_a_single_module_a_path_and_non_modules(tmp_path):
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


def test_modules_accept_relative_and_absolute_paths(tmp_path):
    bundle_dir = tmp_path / "bundle"
    write(
        bundle_dir / "tasks.py",
        """
        from kitup import task

        @task
        def install(ctx):
            pass
        """,
    )

    relative = Bundle("Relative", path=bundle_dir, modules=["tasks.py"])
    absolute = Bundle("Absolute", modules=[bundle_dir / "tasks.py"])

    for bundle in (relative, absolute):
        assert [registered.func.__name__ for registered in bundle.tasks] == ["install"]


def test_path_module_supports_sibling_relative_imports(tmp_path):
    bundle_dir = tmp_path / "bundle"
    write(bundle_dir / "_helper.py", "VALUE = 42\n")
    write(
        bundle_dir / "tasks.py",
        """
        from kitup import task
        from ._helper import VALUE

        @task
        def show(ctx):
            return VALUE
        """,
    )

    bundle = Bundle("Base", path=bundle_dir, modules=["tasks.py"])

    assert bundle.tasks[0].func(None) == 42


def test_path_modules_must_exist_be_files_and_use_the_py_suffix(tmp_path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "tasks.py").mkdir()
    write(bundle_dir / "notes.txt", "VALUE = 1\n")
    write(bundle_dir / "__init__.py", "")

    with pytest.raises(BundleError, match="task module not found"):
        Bundle("Base", path=bundle_dir, modules=["missing.py"])
    with pytest.raises(BundleError, match="task module is not a file"):
        Bundle("Base", path=bundle_dir, modules=["tasks.py"])
    with pytest.raises(BundleError, match=r"must end in \.py"):
        Bundle("Base", path=bundle_dir, modules=["notes.txt"])
    with pytest.raises(BundleError, match=r"must not be __init__\.py"):
        Bundle("Base", path=bundle_dir, modules=["__init__.py"])


def test_duplicate_task_module_paths_are_rejected(tmp_path):
    write(
        tmp_path / "tasks.py",
        "from kitup import task\n\n@task\ndef install(ctx):\n    pass\n",
    )

    with pytest.raises(BundleError, match="duplicate task module path"):
        Bundle("Base", modules=[tmp_path / "tasks.py", tmp_path / "tasks.py"])


def test_path_module_without_tasks_is_an_error(tmp_path):
    write(tmp_path / "helpers.py", "VALUE = 1\n")

    with pytest.raises(BundleError, match=r"does not register any tasks.*_private"):
        Bundle("Base", modules=[tmp_path / "helpers.py"])


def test_same_path_module_is_shared_between_bundles(tmp_path):
    write(
        tmp_path / "tasks.py",
        "from kitup import task\n\n@task\ndef shared(ctx):\n    pass\n",
    )

    first = Bundle("One", modules=[tmp_path / "tasks.py"])
    second = Bundle("Two", modules=[tmp_path / "tasks.py"])

    assert first.tasks[0].func is second.tasks[0].func


def test_discovered_module_without_tasks_is_an_error(tmp_path):
    with pytest.raises(BundleError, match=r"does not register any tasks.*_private"):
        import_package(
            tmp_path,
            {
                "__init__.py": "from kitup import bundle\n\nbundle('Base')\n",
                "helpers.py": "VALUE = 1\n",
            },
        )


def test_task_module_can_be_shared_between_bundles(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": "from . import tasks, one, two\n",
            "tasks.py": "from kitup import task\n\n@task\ndef shared(ctx):\n    pass\n",
            "one/__init__.py": "from kitup import bundle\nfrom .. import tasks\n\nbundle('One', modules=[tasks])\n",
            "two/__init__.py": "from kitup import bundle\nfrom .. import tasks\n\nbundle('Two', modules=[tasks])\n",
        },
    )

    one = bundle_of(module.one)  # type: ignore[attr-defined]
    two = bundle_of(module.two)  # type: ignore[attr-defined]

    assert [registered.id for registered in one.tasks] == ["One.shared"]
    assert [registered.id for registered in two.tasks] == ["Two.shared"]
    assert one.tasks[0] is not two.tasks[0]
    assert one.tasks[0].func is two.tasks[0].func


def test_reexported_task_is_not_claimed_by_the_importing_module(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": (
                "from kitup import bundle\nfrom . import first, second\nbundle('Base', modules=[first, second])\n"
            ),
            "first.py": "from kitup import task\n\n@task\ndef shared(ctx):\n    pass\n",
            "second.py": ("from kitup import task\nfrom .first import shared\n\n@task\ndef other(ctx):\n    pass\n"),
        },
    )

    bundle = bundle_of(module)  # type: ignore[arg-type]
    assert [registered.func.__name__ for registered in bundle.tasks] == ["shared", "other"]


def test_depends_on_accepts_ids_and_bundle_objects(tmp_path):
    _, base_module = import_package(
        tmp_path,
        {"__init__.py": "from kitup import bundle\n\nbundle('Base')\n"},
    )
    _, desktop_module = import_package(
        tmp_path,
        {
            "__init__.py": (
                f"from kitup import bundle, bundle_of\nimport {base_module.__name__}\n"
                f"bundle('Desktop', depends_on=['base', bundle_of({base_module.__name__})])\n"
            )
        },
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


def test_module_task_decorator_forms(tmp_path):
    _, module = import_package(
        tmp_path,
        {
            "__init__.py": "from kitup import bundle\nfrom . import tasks\n\nbundle('Base', modules=[tasks])\n",
            "tasks.py": (
                "from kitup import task\n"
                "\n"
                "@task\n"
                "def install_base_packages(ctx):\n"
                "    '''Install base packages.'''\n"
                "    pass\n"
                "\n"
                "@task('Second', tags={'packages'}, description='details')\n"
                "def second(ctx):\n"
                "    pass\n"
                "\n"
                "@task(tags={'other'})\n"
                "def third(ctx):\n"
                "    pass\n"
            ),
        },
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


def test_bundle_task_decorator_forms(tmp_path):
    bundle = Bundle("Base", path=tmp_path)

    @bundle.task
    def install_base_packages(ctx):
        """Install base packages."""

    @bundle.task("Custom")
    def custom(ctx):
        pass

    @bundle.task(tags={"other"})
    def tagged(ctx):
        pass

    assert [registered.name for registered in bundle.tasks] == [
        "Install base packages",
        "Custom",
        "Tagged",
    ]
    assert bundle.tasks[0].description == "Install base packages."
    assert bundle.tasks[2].tags == frozenset({"other"})


def test_humanized_names_collapse_underscores(tmp_path):
    bundle = Bundle("Base", path=tmp_path)

    def _private_task(ctx):
        pass

    def install__packages(ctx):
        pass

    def trailing_(ctx):
        pass

    bundle.task(_private_task)
    bundle.task(install__packages)
    bundle.task(trailing_)

    assert [registered.name for registered in bundle.tasks] == [
        "Private task",
        "Install packages",
        "Trailing",
    ]


def test_task_names_must_be_strings():
    bundle = Bundle("Base")

    with pytest.raises(BundleError, match="task name must be a string"):
        bundle.task(123)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="task name must not be empty"):
        bundle.task("")
    with pytest.raises(BundleError, match="task name must be a string"):
        task(123)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="task name must not be empty"):
        task("")


def test_names_and_descriptions_are_normalized():
    bundle = Bundle("Base")

    @bundle.task("  Custom  ")
    def custom(ctx):
        pass

    effect = side_effect("  Refresh  ")(lambda ctx: None)

    assert bundle.tasks[0].name == "Custom"
    assert effect.name == "Refresh"


def test_descriptions_must_be_strings():
    with pytest.raises(BundleError, match="bundle description must be a string"):
        Bundle("Base", description=1)  # type: ignore[arg-type]

    bundle = Bundle("Base")
    with pytest.raises(BundleError, match="description must be a string"):
        bundle.task(description=1)(lambda ctx: None)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="description must be a string"):
        task(description=1)(lambda ctx: None)  # type: ignore[arg-type]
    with pytest.raises(BundleError, match="description must be a string"):
        side_effect(description=1)(lambda ctx: None)  # type: ignore[arg-type]


def test_dependency_ids_are_stripped():
    bundle = Bundle("Base", depends_on=[" other "])

    assert bundle.depends_on == ("other",)


def test_duplicate_task_ids_are_rejected():
    bundle = Bundle("Base")

    def install(ctx):
        pass

    bundle.task(install)

    with pytest.raises(BundleError, match=r"duplicate task id 'Base\.install'"):
        bundle.task(install)


def test_duplicate_task_ids_across_modules_are_rejected(tmp_path):
    with pytest.raises(BundleError, match=r"duplicate task id 'Base\.install'"):
        import_package(
            tmp_path,
            {
                "__init__.py": "from kitup import bundle\n\nbundle('Base')\n",
                "first.py": "from kitup import task\n\n@task\ndef install(ctx):\n    pass\n",
                "second.py": "from kitup import task\n\n@task\ndef install(ctx):\n    pass\n",
            },
        )


def test_task_decorator_requires_a_function():
    class CallableObject:
        def __call__(self, ctx):
            pass

    with pytest.raises(BundleError, match="task decorator requires a Python function"):
        task(CallableObject())  # type: ignore[arg-type]

    bundle = Bundle("Base")
    with pytest.raises(BundleError, match="task decorator requires a Python function"):
        bundle.task(CallableObject())  # type: ignore[arg-type]


def test_task_tags_are_validated_and_normalized():
    bundle = Bundle("Base")

    @bundle.task(tags={" packages ", "config,docs"})
    def tagged(ctx):
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


def test_task_functions_may_return_values(tmp_path):
    bundle = Bundle("Local", path=tmp_path)

    @bundle.task("Return value")
    def return_value(ctx):
        return "value"

    assert return_value(None) == "value"
    assert bundle.tasks[0].func is return_value


def test_side_effect_decorator_forms():
    @side_effect
    def reload_nginx(ctx):
        """Reload nginx."""

    explicit = side_effect("Refresh", one_shot=False, description="details")(lambda ctx: None)

    assert isinstance(reload_nginx, SideEffect)
    assert reload_nginx.name == "Reload nginx"
    assert reload_nginx.description == "Reload nginx."
    assert reload_nginx.one_shot is True
    assert explicit.name == "Refresh"
    assert explicit.one_shot is False
    assert explicit.description == "details"


def test_side_effect_names_and_one_shot_are_validated():
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
            "__init__.py": "",
            "tasks.py": "from kitup import task\n\n@task\ndef deferred(ctx):\n    pass\n",
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


def test_unknown_user_in_bundle_path_is_a_bundle_error():
    with pytest.raises(BundleError, match="cannot resolve path '~bogususer/bundle'"):
        Bundle("Base", path=Path("~bogususer/bundle"))
