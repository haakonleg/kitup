import sys
from pathlib import Path

import pytest

from kitup.errors import PresetError, ProjectError
from kitup.project import (
    discover_bundle_dirs,
    discover_var_paths,
    load_project,
    load_project_modules,
    on_project_path,
    resolve_preset,
)
from kitup.variables import load_vars

from ..helpers import make_project, write


def task_source(name: str) -> str:
    return f"from kitup import task\n\n@task\ndef {name}(ctx):\n    pass\n"


def bundle_source(priority: int = 0, *, depends_on: tuple[str, ...] = ()) -> str:
    depends = f", depends_on={list(depends_on)!r}" if depends_on else ""
    return f"from kitup import bundle\n\nbundle(priority={priority}{depends})\n"


def test_load_project_requires_an_existing_directory(tmp_path):
    with pytest.raises(ProjectError, match=r"project not found.*kitup init"):
        load_project(tmp_path / "missing")

    target = tmp_path / "file"
    target.write_text("", encoding="utf-8")
    with pytest.raises(ProjectError, match="project root is not a directory"):
        load_project(target)


def test_load_project_modules_skips_bundles(tmp_path):
    make_project(
        tmp_path,
        bundles={"base": "raise RuntimeError('broken bundle')\n"},
        modules={"mytool.py": 'from kitup import module\n\nmodule("mytool")\n'},
    )

    project = load_project_modules(tmp_path)

    assert project.bundles == ()
    assert [path.stem for path in project.module_paths] == ["mytool"]


def test_unknown_user_in_project_path_is_a_project_error():
    with pytest.raises(ProjectError, match="cannot resolve path '~bogususer/kitup'"):
        load_project("~bogususer/kitup")


def test_discovers_bundles_by_folder_id(tmp_path):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle('Base system', 'Core packages')\n",
                "tasks.py": task_source("install"),
            },
            "workstation": {
                "__init__.py": "from kitup import bundle\n\nbundle(depends_on=['base'])\n",
                "tasks.py": task_source("desktop"),
            },
        },
    )

    project = load_project(tmp_path)

    assert project.root == tmp_path
    assert [bundle.id for bundle in project.bundles] == ["base", "workstation"]
    base, workstation = project.bundles
    assert base.name == "Base system"
    assert base.description == "Core packages"
    assert workstation.name == "workstation"
    assert workstation.depends_on == ("base",)
    assert [task.name for task in base.tasks] == ["Install"]


def test_synthesizes_a_bundle_when_the_folder_has_no_init(tmp_path):
    make_project(
        tmp_path,
        bundles={"base": {"tasks.py": task_source("hello")}},
    )

    project = load_project(tmp_path)
    (bundle,) = project.bundles

    assert bundle.id == "base"
    assert bundle.name == "base"
    assert [task.name for task in bundle.tasks] == ["Hello"]


def test_auto_discovery_is_sorted_and_skips_private_modules(tmp_path):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "from kitup import bundle\n\nbundle()\n",
                "02_second.py": task_source("second"),
                "01_first.py": task_source("first"),
                "_helper.py": "VALUE = 1\n",
            }
        },
    )

    project = load_project(tmp_path)
    (bundle,) = project.bundles

    assert [task.func.__name__ for task in bundle.tasks] == ["first", "second"]


def test_invalid_bundle_folder_names_are_errors(tmp_path):
    for index, name in enumerate(["bad-name", "01_", "01_bad-name", "123", "01_2bad"]):
        root = tmp_path / str(index)
        write(root / "bundles" / name / "__init__.py", "from kitup import bundle\n\nbundle()\n")
        expected = f"invalid bundle name '{name}'" if name == "bad-name" else "invalid bundle name"
        with pytest.raises(ProjectError, match=expected):
            load_project(root)


def test_invalid_var_names_are_errors(tmp_path):
    for index, name in enumerate(["for", "bad-name"]):
        root = tmp_path / str(index)
        write(root / "vars" / f"{name}.py", "VALUE = 1\n")
        with pytest.raises(ProjectError, match=f"invalid var name '{name}'"):
            load_project(root)


def test_numbered_bundle_folders_sort_numerically_and_strip_their_prefix(tmp_path):
    make_project(
        tmp_path,
        bundles={
            "10_ten": task_source("ten"),
            "2_two": task_source("two"),
            "01_one": task_source("one"),
            "alpha": task_source("alpha"),
            "zeta": task_source("zeta"),
        },
    )

    project = load_project(tmp_path)

    assert [bundle.id for bundle in project.bundles] == ["one", "two", "ten", "alpha", "zeta"]
    setup = load_project(make_project(tmp_path / "setup", bundles={"01_setup": task_source("install")})).bundles[0]
    assert setup.id == "setup"
    assert setup.name == "setup"
    assert [task.id for task in setup.tasks] == ["setup.install"]


def test_numbered_and_plain_folders_with_the_same_id_are_rejected(tmp_path):
    make_project(tmp_path, bundles={"01_setup": task_source("a"), "setup": task_source("b")})

    with pytest.raises(ProjectError, match=r"duplicate bundle id 'setup'.*01_setup.*setup"):
        load_project(tmp_path)


def test_priority_orders_bundles_before_folder_names(tmp_path):
    make_project(
        tmp_path,
        bundles={
            "base": {"__init__.py": bundle_source(20), "tasks.py": task_source("b")},
            "alpha": {"__init__.py": bundle_source(10), "tasks.py": task_source("a")},
        },
    )

    assert [bundle.id for bundle in load_project(tmp_path).bundles] == ["alpha", "base"]


def test_bundles_py_shadowing_is_reported(tmp_path):
    make_project(tmp_path, bundles={"base": {"tasks.py": task_source("hello")}})
    write(tmp_path / "bundles.py", "VALUE = 1\n")

    with pytest.raises(ProjectError, match=r"bundles\.py.*remove or rename"):
        load_project(tmp_path)


def test_bundle_import_errors_are_project_errors(tmp_path):
    write(tmp_path / "bundles" / "base" / "__init__.py", "raise RuntimeError('boom')\n")

    with pytest.raises(ProjectError, match=r"failed to import bundle 'base'.*boom"):
        load_project(tmp_path)


def test_var_discovery_order_and_ignored_entries(tmp_path):
    make_project(
        tmp_path,
        vars="BASE = 1\n",
        var_files={"work": "WORK = 1\n", "laptop": "LAPTOP = 1\n", "_private": "X = 1\n", ".hidden": "X = 1\n"},
    )
    write(tmp_path / "vars" / "sub" / "nested.py", "X = 1\n")
    write(tmp_path / "vars" / "notes.txt", "ignored\n")

    project = load_project(tmp_path)

    assert [path.name for path in project.var_paths] == ["base.py", "laptop.py", "work.py"]


def test_missing_var_directory_is_ignored(tmp_path):
    assert discover_var_paths(tmp_path) == ()
    assert discover_bundle_dirs(tmp_path) == ()
    assert load_project(tmp_path).bundles == ()


def test_var_stem_must_be_an_identifier(tmp_path):
    write(tmp_path / "vars" / "bad-name.py", "VALUE = 1\n")

    with pytest.raises(ProjectError, match="invalid var name 'bad-name'"):
        load_project(tmp_path)


def test_bundle_vars_are_discovered_from_the_bundle_folder(tmp_path):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "tasks.py": task_source("run"),
                "vars/base.py": "VALUE = 1\n",
                "vars/work.py": "VALUE = 2\n",
                "vars/_private.py": "VALUE = 3\n",
                "vars/.hidden.py": "VALUE = 4\n",
                "vars/notes.txt": "ignored\n",
            }
        },
    )
    write(tmp_path / "bundles" / "base" / "vars" / "sub" / "nested.py", "VALUE = 5\n")

    project = load_project(tmp_path)
    (bundle,) = project.bundles

    assert [path.name for path in bundle.var_paths] == ["base.py", "work.py"]
    assert all(path.parent == tmp_path / "bundles" / "base" / "vars" for path in bundle.var_paths)


def test_invalid_bundle_var_name_is_reported_with_bundle_context(tmp_path):
    write(tmp_path / "bundles" / "base" / "vars" / "bad-name.py", "VALUE = 1\n")
    write(tmp_path / "bundles" / "base" / "tasks.py", task_source("run"))

    with pytest.raises(ProjectError, match=r"bundle 'base'.*invalid var name 'bad-name'"):
        load_project(tmp_path)


def test_bundle_vars_follow_explicit_and_external_bundle_paths(tmp_path):
    resources = tmp_path / "shared"
    write(resources / "vars" / "defaults.py", "VALUE = 1\n")
    write(
        tmp_path / "bundles" / "base" / "__init__.py",
        f"from kitup import bundle\n\nbundle(path={str(resources)!r})\n",
    )
    write(tmp_path / "bundles" / "base" / "tasks.py", task_source("run"))

    project = load_project(tmp_path)

    assert project.bundles[0].var_paths == (resources / "vars" / "defaults.py",)

    external = tmp_path / "external-bundles"
    external_bundle(external, "extra")
    write(external / "extra" / "vars" / "local.py", "VALUE = 1\n")
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    project = load_project(root, bundle_paths=[external])
    extra = next(bundle for bundle in project.bundles if bundle.id == "extra")

    assert extra.var_paths == (external / "extra" / "vars" / "local.py",)


def test_preset_paths_resolve_under_presets(tmp_path):
    relative = resolve_preset(tmp_path, "laptop")
    absolute = tmp_path / "elsewhere" / "laptop"

    assert relative == tmp_path / "presets" / "laptop"
    assert resolve_preset(tmp_path, absolute) == absolute

    with pytest.raises(PresetError, match="preset path must stay inside presets/"):
        resolve_preset(tmp_path, "../laptop")

    project = load_project(tmp_path, preset_paths=[relative])
    assert project.preset_paths == (relative,)


def test_load_project_isolates_projects_with_the_same_bundle_names(tmp_path):
    def bundle_files(marker):
        return {
            "__init__.py": "from kitup import bundle\n\nbundle('Same')\n",
            "tasks.py": f"from kitup import task\n\n@task\ndef mark():\n    return {marker!r}\n",
        }

    first_root = make_project(tmp_path / "first", bundles={"base": bundle_files("first")})
    second_root = make_project(tmp_path / "second", bundles={"base": bundle_files("second")})

    first = load_project(first_root)
    second = load_project(second_root)

    first_bundle = first.bundles[0]
    second_bundle = second.bundles[0]
    assert first_bundle is not second_bundle
    assert first_bundle.path == first_root / "bundles" / "base"
    assert second_bundle.path == second_root / "bundles" / "base"
    assert first_bundle.tasks[0].func() == "first"
    assert second_bundle.tasks[0].func() == "second"


def test_vars_are_importable_as_a_package(tmp_path):
    make_project(
        tmp_path,
        vars="from vars.laptop import VALUE\n",
        var_files={"laptop": "VALUE = 1\n"},
    )
    project = load_project(tmp_path)

    with on_project_path(project.root):
        values = load_vars(project.var_paths)

    assert values.VALUE == 1


def external_bundle(root, name, *, init=True, depends_on=()):
    folder = root / name
    if init:
        write(
            folder / "__init__.py",
            f"from kitup import bundle\n\nbundle({name!r}, depends_on={list(depends_on)!r})\n",
        )
    write(
        folder / "tasks.py",
        f"from kitup import task\n\n@task\ndef run_{name}(ctx):\n    pass\n",
    )
    return folder


def test_external_bundle_paths_resolve_relative_to_the_project_root(tmp_path):
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})
    external = tmp_path / "machine" / "shared" / "bundles"
    external.mkdir(parents=True)
    external_bundle(external, "extra")

    relative = load_project(root, bundle_paths=["shared/bundles"])
    absolute = load_project(root, bundle_paths=[external])

    for project in (relative, absolute):
        assert [bundle.id for bundle in project.bundles] == ["local", "extra"]
        assert project.bundle_roots == (external,)

    duplicate = load_project(root, bundle_paths=[external, external])
    assert duplicate.bundle_roots == (external,)
    assert [bundle.id for bundle in duplicate.bundles] == ["local", "extra"]


def test_external_bundle_with_init_and_without_init(tmp_path):
    external = tmp_path / "bundles"
    external_bundle(external, "withinit")
    external_bundle(external, "plain", init=False)
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    project = load_project(root, bundle_paths=[external])

    bundles = {bundle.id: bundle for bundle in project.bundles}
    assert bundles["withinit"].name == "withinit"
    assert [task.func.__name__ for task in bundles["withinit"].tasks] == ["run_withinit"]
    assert bundles["plain"].name == "plain"
    assert [task.func.__name__ for task in bundles["plain"].tasks] == ["run_plain"]


def test_external_bundles_support_numeric_prefixes(tmp_path):
    external = tmp_path / "shared"
    external_bundle(external, "02_extra", init=False)
    root = make_project(tmp_path / "machine", bundles={"01_local": task_source("local")})

    project = load_project(root, bundle_paths=[external])

    assert [bundle.id for bundle in project.bundles] == ["local", "extra"]
    assert project.bundles[1].name == "extra"
    assert [task.func.__name__ for task in project.bundles[1].tasks] == ["run_02_extra"]


def test_relative_imports_inside_an_external_bundle(tmp_path):
    external = tmp_path / "bundles"
    folder = external / "extra"
    write(folder / "_helper.py", "VALUE = 'from helper'\n")
    write(
        folder / "__init__.py",
        "from kitup import bundle\n\nbundle('Extra')\n",
    )
    write(
        folder / "tasks.py",
        "from kitup import task\nfrom ._helper import VALUE\n\n@task\ndef show():\n    return VALUE\n",
    )
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    project = load_project(root, bundle_paths=[external])
    (extra,) = [bundle for bundle in project.bundles if bundle.id == "extra"]

    assert extra.tasks[0].func() == "from helper"


def test_duplicate_bundle_ids_across_roots_are_rejected(tmp_path):
    external = tmp_path / "bundles"
    external_bundle(external, "base")
    root = make_project(tmp_path / "machine", bundles={"base": task_source("local")})

    with pytest.raises(ProjectError, match=r"duplicate bundle id 'base'.*bundles/base.*bundles/base"):
        load_project(root, bundle_paths=[external])


def test_path_roots_must_exist_and_be_directories(tmp_path):
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})
    target = root / "not-a-directory"
    target.write_text("", encoding="utf-8")

    for kind, keyword in (
        ("bundle", "bundle_paths"),
        ("files", "files_paths"),
        ("modules", "modules_paths"),
    ):
        with pytest.raises(ProjectError, match=f"{kind} path is not a directory"):
            load_project(root, **{keyword: ["missing"]})
        with pytest.raises(ProjectError, match=f"{kind} path is not a directory"):
            load_project(root, **{keyword: [target]})


def test_modules_paths_resolve_relative_to_the_project_root(tmp_path):
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})
    external = tmp_path / "machine" / "shared" / "modules"
    write(external / "extra.py", 'from kitup import module\n\nmodule("extra")\n')

    relative = load_project(root, modules_paths=["shared/modules"])
    absolute = load_project(root, modules_paths=[external])

    for project in (relative, absolute):
        assert project.module_roots == (external,)
        assert [path.stem for path in project.module_paths] == ["extra"]

    duplicate = load_project(root, modules_paths=[external, external])
    assert duplicate.module_roots == (external,)
    assert [path.stem for path in duplicate.module_paths] == ["extra"]


def test_load_project_modules_accepts_modules_paths(tmp_path):
    root = make_project(tmp_path / "machine")
    external = tmp_path / "external-modules"
    write(external / "extra.py", 'from kitup import module\n\nmodule("extra")\n')

    project = load_project_modules(root, modules_paths=[external])

    assert project.module_roots == (external,)
    assert [path.stem for path in project.module_paths] == ["extra"]


def test_duplicate_module_ids_across_roots_are_rejected(tmp_path):
    root = make_project(
        tmp_path / "machine",
        modules={"tool.py": 'from kitup import module\n\nmodule("tool")\n'},
    )
    external = tmp_path / "external-modules"
    write(external / "tool.py", 'from kitup import module\n\nmodule("tool")\n')

    with pytest.raises(ProjectError, match=r"duplicate module id 'tool'.*modules/tool.py.*tool.py"):
        load_project(root, modules_paths=[external])


def test_all_public_paths_expand_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    files = home / "files"
    files.mkdir()
    modules = home / "modules"
    write(modules / "extra.py", 'from kitup import module\n\nmodule("extra")\n')
    external = home / "bundles"
    external_bundle(external, "extra")
    extra_var = home / "secrets.py"
    write(extra_var, "SECRET = 1\n")
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    project = load_project(
        root,
        bundle_paths=["~/bundles"],
        files_paths=["~/files"],
        modules_paths=["~/modules"],
        extra_var_paths=["~/secrets.py"],
    )

    assert project.bundle_roots == (external,)
    assert project.files_roots == (files,)
    assert project.module_roots == (modules,)
    assert project.var_paths[-1] == extra_var
    assert [path.stem for path in project.module_paths] == ["extra"]
    assert [bundle.id for bundle in project.bundles] == ["local", "extra"]


def test_extra_var_paths_are_resolved_and_validated(tmp_path):
    root = make_project(tmp_path / "machine", var_files={"base": "VALUE = 1\n"})
    extra = root / "secrets.py"
    write(extra, "SECRET = 1\n")

    project = load_project(root, extra_var_paths=["secrets.py"])
    assert project.var_paths == (root / "vars" / "base.py", extra)

    relative = load_project(root, extra_var_paths=[Path("secrets.py")])
    assert relative.var_paths == project.var_paths

    with pytest.raises(ProjectError, match="var file not found"):
        load_project(root, extra_var_paths=["missing.py"])

    text = root / "secrets.txt"
    write(text, "SECRET = 1\n")
    with pytest.raises(ProjectError, match=r"must end in \.py"):
        load_project(root, extra_var_paths=[text])

    duplicate_root = make_project(tmp_path / "duplicates", var_files={"secrets": "VALUE = 1\n"})
    duplicate = duplicate_root / "elsewhere" / "secrets.py"
    write(duplicate, "SECRET = 1\n")
    with pytest.raises(ProjectError, match=r"duplicate var id 'secrets'.*vars/secrets.py.*secrets.py"):
        load_project(duplicate_root, extra_var_paths=[duplicate])


def test_external_bundle_modules_are_purged_between_project_loads(tmp_path):
    external = tmp_path / "bundles"
    external_bundle(external, "extra")
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    first = load_project(root, bundle_paths=[external])
    module_name = first.bundles[-1].tasks[0].func.__module__
    assert module_name.startswith("_kitup_bundle_extra")
    old_module = sys.modules[module_name]

    second = load_project(root, bundle_paths=[external])

    assert sys.modules[module_name] is not old_module
    assert second.bundles[-1].tasks[0].func is not first.bundles[-1].tasks[0].func
