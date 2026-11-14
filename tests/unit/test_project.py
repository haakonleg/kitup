import sys
from pathlib import Path

import pytest

from kitup.errors import BundleError, PresetError, ProjectError
from kitup.project import (
    discover_bundle_dirs,
    discover_var_paths,
    load_project,
    load_project_modules,
    on_project_path,
    resolve_preset,
)
from kitup.vars import load_vars

from ..helpers import make_project, write


def task_source(name: str) -> str:
    return f"from kitup import task\n\n@task\ndef {name}(ctx):\n    pass\n"


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


def test_unknown_user_in_on_project_path_is_a_project_error():
    with (
        pytest.raises(ProjectError, match="cannot resolve path '~bogususer/kitup'"),
        on_project_path("~bogususer/kitup"),
    ):
        pass


def test_unknown_user_in_preset_path_is_a_preset_error(tmp_path):
    with pytest.raises(PresetError, match="cannot resolve path '~bogususer/laptop'"):
        resolve_preset(tmp_path, "~bogususer/laptop")


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


def test_discovered_bundles_are_immutable(tmp_path):
    make_project(
        tmp_path,
        bundles={"base": {"__init__.py": "from kitup import bundle\n\nbundle('Base')\n"}},
    )

    (bundle,) = load_project(tmp_path).bundles

    assert isinstance(bundle.tasks, tuple)
    for attribute, value in (
        ("id", "other"),
        ("name", "Other"),
        ("description", "other"),
        ("path", tmp_path),
        ("var_paths", (tmp_path / "vars.py",)),
    ):
        with pytest.raises(AttributeError, match="immutable after discovery"):
            setattr(bundle, attribute, value)

    with pytest.raises(BundleError, match="immutable after discovery"):
        bundle.task(lambda ctx: None)


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


def test_synthesizes_a_bundle_when_init_does_not_construct_one(tmp_path):
    make_project(
        tmp_path,
        bundles={
            "base": {
                "__init__.py": "VALUE = 1\n",
                "tasks.py": task_source("hello"),
            }
        },
    )

    project = load_project(tmp_path)
    (bundle,) = project.bundles

    assert bundle.id == "base"
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


def test_invalid_bundle_folder_name_is_an_error(tmp_path):
    write(tmp_path / "bundles" / "bad-name" / "__init__.py", "from kitup import bundle\n\nbundle()\n")

    with pytest.raises(ProjectError, match="invalid bundle name 'bad-name'"):
        load_project(tmp_path)


def test_keyword_bundle_folder_name_is_an_error(tmp_path):
    write(tmp_path / "bundles" / "class" / "__init__.py", "from kitup import bundle\n\nbundle()\n")

    with pytest.raises(ProjectError, match="invalid bundle name 'class'"):
        load_project(tmp_path)


def test_keyword_var_name_is_an_error(tmp_path):
    write(tmp_path / "vars" / "for.py", "VALUE = 1\n")

    with pytest.raises(ProjectError, match="invalid var name 'for'"):
        load_project(tmp_path)


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


def test_bundles_without_vars_have_no_var_paths(tmp_path):
    make_project(tmp_path, bundles={"base": {"tasks.py": task_source("run")}})

    project = load_project(tmp_path)

    assert project.bundles[0].var_paths == ()


def test_invalid_bundle_var_name_is_reported_with_bundle_context(tmp_path):
    write(tmp_path / "bundles" / "base" / "vars" / "bad-name.py", "VALUE = 1\n")
    write(tmp_path / "bundles" / "base" / "tasks.py", task_source("run"))

    with pytest.raises(ProjectError, match=r"bundle 'base'.*invalid var name 'bad-name'"):
        load_project(tmp_path)


def test_bundle_vars_follow_an_explicit_bundle_path(tmp_path):
    resources = tmp_path / "shared"
    write(resources / "vars" / "defaults.py", "VALUE = 1\n")
    write(
        tmp_path / "bundles" / "base" / "__init__.py",
        f"from kitup import bundle\n\nbundle(path={str(resources)!r})\n",
    )
    write(tmp_path / "bundles" / "base" / "tasks.py", task_source("run"))

    project = load_project(tmp_path)

    assert project.bundles[0].var_paths == (resources / "vars" / "defaults.py",)


def test_external_bundle_vars_are_discovered(tmp_path):
    external = tmp_path / "bundles"
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


def test_load_project_resolves_relative_preset_paths(tmp_path):
    project = load_project(tmp_path, preset_paths=[Path("presets/laptop")])

    assert project.preset_paths == (tmp_path / "presets" / "laptop",)


def test_load_project_restores_sys_path(tmp_path):
    make_project(tmp_path, bundles={"base": {"__init__.py": "from kitup import bundle\n\nbundle()\n"}})
    before = list(sys.path)

    load_project(tmp_path)

    assert sys.path == before


def test_load_project_can_run_repeatedly_on_the_same_project(tmp_path):
    make_project(tmp_path, bundles={"base": {"tasks.py": task_source("hello")}})

    first = load_project(tmp_path)
    second = load_project(tmp_path)

    assert [task.id for task in first.bundles[0].tasks] == ["base.hello"]
    assert [task.id for task in second.bundles[0].tasks] == ["base.hello"]


def test_load_project_isolates_projects_with_the_same_bundle_names(tmp_path):
    def bundle_files(marker):
        return {
            "__init__.py": "from kitup import bundle\n\nbundle('Same')\n",
            "tasks.py": f"from kitup import task\n\n@task\ndef mark(ctx):\n    return {marker!r}\n",
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
    assert first_bundle.tasks[0].func(None) == "first"
    assert second_bundle.tasks[0].func(None) == "second"


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
        "from kitup import task\nfrom ._helper import VALUE\n\n@task\ndef show(ctx):\n    return VALUE\n",
    )
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    project = load_project(root, bundle_paths=[external])
    (extra,) = [bundle for bundle in project.bundles if bundle.id == "extra"]

    assert extra.tasks[0].func(None) == "from helper"


def test_external_bundle_can_depend_on_a_local_bundle(tmp_path):
    external = tmp_path / "bundles"
    external_bundle(external, "extra", depends_on=("local",))
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    project = load_project(root, bundle_paths=[external])

    extra = next(bundle for bundle in project.bundles if bundle.id == "extra")
    assert extra.depends_on == ("local",)


def test_duplicate_bundle_ids_across_roots_are_rejected(tmp_path):
    external = tmp_path / "bundles"
    external_bundle(external, "base")
    root = make_project(tmp_path / "machine", bundles={"base": task_source("local")})

    with pytest.raises(ProjectError, match=r"duplicate bundle id 'base'.*bundles/base.*bundles/base"):
        load_project(root, bundle_paths=[external])


def test_bundle_paths_must_exist_and_be_directories(tmp_path):
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    with pytest.raises(ProjectError, match="bundle path is not a directory"):
        load_project(root, bundle_paths=["missing"])

    target = root / "not-a-directory"
    target.write_text("", encoding="utf-8")
    with pytest.raises(ProjectError, match="bundle path is not a directory"):
        load_project(root, bundle_paths=[target])


def test_bundle_paths_and_extra_var_paths_expand_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    external = home / "bundles"
    external_bundle(external, "extra")
    extra_var = home / "secrets.py"
    write(extra_var, "SECRET = 1\n")
    root = make_project(tmp_path / "machine", bundles={"local": task_source("local")})

    project = load_project(root, bundle_paths=["~/bundles"], extra_var_paths=["~/secrets.py"])

    assert project.bundle_roots == (external,)
    assert project.var_paths[-1] == extra_var
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


def test_duplicate_var_ids_across_discovered_and_extra_files_are_rejected(tmp_path):
    root = make_project(tmp_path / "machine", var_files={"secrets": "VALUE = 1\n"})
    extra = root / "elsewhere" / "secrets.py"
    write(extra, "SECRET = 1\n")

    with pytest.raises(ProjectError, match=r"duplicate var id 'secrets'.*vars/secrets.py.*secrets.py"):
        load_project(root, extra_var_paths=[extra])


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
