from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path

import pytest

from kitup.errors import ProfileError
from kitup.profiles import RunDefinition, load_profile, profile


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")

def write_bundle(
    root: Path,
    folder: str,
    *,
    name: str | None = None,
    setup: str = "",
    depends: str = "()",
) -> None:
    write(root / "bundles" / "__init__.py", "")
    write(
        root / "bundles" / folder / "__init__.py",
        f"""
        from kitup.registry import Bundle

        {setup}

        Bundle({name or folder!r}, depends_on={depends})
        """,
    )

def load(root: Path, text: str, *, filename: str = "kitup.py") -> RunDefinition:
    write(root / filename, "from pathlib import Path\n\n" + textwrap.dedent(text))
    sys.path.insert(0, str(root))
    try:
        importlib.invalidate_caches()
        return load_profile(root / filename)
    finally:
        sys.path.remove(str(root))

def purge_project_modules() -> None:
    for name in tuple(sys.modules):
        if name == "bundles" or name.startswith("bundles."):
            sys.modules.pop(name, None)
        if name in {
            "base_profile",
            "middle_profile",
            "child_profile",
            "empty_profile",
            "helper_profile",
            "left_profile",
            "right_profile",
        }:
            sys.modules.pop(name, None)
        if name.startswith(("_kitup_profile_", "_kitup_config_")):
            sys.modules.pop(name, None)

@pytest.fixture(autouse=True)
def clean_imports():
    purge_project_modules()
    yield
    purge_project_modules()

def test_basic_profile_registers_without_a_variable(tmp_path):
    write_bundle(tmp_path, "base")
    definition = load(
        tmp_path,
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base], description="Base", tags=["one"], skip_tags=["two"])
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == ["base"]
    assert definition.description == "Base"
    assert definition.tags == frozenset({"one"})
    assert definition.skip_tags == frozenset({"two"})

def test_last_registration_wins(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "other")
    definition = load(
        tmp_path,
        """
        from bundles import base, other

        from kitup import profile

        profile(bundles=[base], description="first")
        profile(bundles=[other], description="last")
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == ["other"]
    assert definition.description == "last"

def test_bundle_option_validation(tmp_path):
    with pytest.raises(ProfileError, match="must not be empty"):
        load(
            tmp_path,
            "from kitup import profile\nprofile(bundles=[])\n",
        )

    with pytest.raises(ProfileError, match="not a single string"):
        load(
            tmp_path,
            "from kitup import profile\nprofile(bundles='base')\n",
        )

    with pytest.raises(ProfileError, match="bundle modules or Bundle objects"):
        load(
            tmp_path,
            "from kitup import profile\nprofile(bundles=[object()])\n",
        )

def test_duplicate_bundle_names_are_rejected(tmp_path):
    write_bundle(tmp_path, "one", name="same")
    write_bundle(tmp_path, "two", name="same")

    with pytest.raises(ProfileError, match="duplicate bundle name in profile: same"):
        load(
            tmp_path,
            """
            from bundles import one, two

            from kitup import profile

            profile(bundles=[one, two])
            """,
        )

def test_config_paths_resolve_against_the_declaring_file(tmp_path):
    write_bundle(tmp_path, "base")
    write(tmp_path / "profiles" / "config.py", "VALUE = 1\n")
    definition = load(
        tmp_path,
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base], config=[Path("config.py")])
        """,
        filename="profiles/kitup.py",
    )

    assert definition.config == ((tmp_path / "profiles" / "config.py").resolve(),)

def test_config_validation(tmp_path):
    write_bundle(tmp_path, "base")

    with pytest.raises(ProfileError, match="not a single string"):
        load(
            tmp_path,
            """
            from bundles import base

            from kitup import profile

            profile(bundles=[base], config="config.py")
            """,
        )

    with pytest.raises(ProfileError, match="must use pathlib.Path"):
        load(
            tmp_path,
            """
            from bundles import base

            from kitup import profile

            profile(bundles=[base], config=["config.py"])
            """,
        )

    for value in ("{}", '{"SSH_PORT": 2222}'):
        with pytest.raises(ProfileError, match="not a single mapping"):
            load(
                tmp_path,
                f"""
                from bundles import base

                from kitup import profile

                profile(bundles=[base], config={value})
                """,
            )

    with pytest.raises(ProfileError, match="keys must be strings"):
        load(
            tmp_path,
            """
            from bundles import base

            from kitup import profile

            profile(bundles=[base], config=[{1: "x"}])
            """,
        )

    with pytest.raises(ProfileError, match="key must not be private"):
        load(
            tmp_path,
            """
            from bundles import base

            from kitup import profile

            profile(bundles=[base], config=[{"_x": 1}])
            """,
        )

    with pytest.raises(ProfileError, match="Path objects, modules, or mappings"):
        load(
            tmp_path,
            """
            from bundles import base

            from kitup import profile

            profile(bundles=[base], config=[1])
            """,
        )

def test_duplicate_config_paths_are_rejected(tmp_path):
    write_bundle(tmp_path, "base")
    write(tmp_path / "config.py", "VALUE = 1\n")

    with pytest.raises(ProfileError, match="duplicate config path"):
        load(
            tmp_path,
            """
            from bundles import base

            from kitup import profile

            profile(bundles=[base], config=[Path("config.py"), Path("config.py")])
            """,
        )

def test_profile_extend_module_appends_and_inherits(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "dotfiles")
    write(tmp_path / "config.py", "VALUE = 'base'\n")
    write(
        tmp_path / "base_profile.py",
        """
        from pathlib import Path

        from bundles import base

        from kitup import profile

        profile(
            bundles=[base],
            config=[Path("config.py")],
            description="Base",
            tags=["base"],
            skip_tags=["optional"],
        )
        """,
    )
    definition = load(
        tmp_path,
        """
        import base_profile

        from bundles import dotfiles

        from kitup import profile

        profile.extend(
            base_profile,
            bundles=[dotfiles],
            description="Child",
            tags=["child"],
        )
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == ["base", "dotfiles"]
    assert definition.description == "Child"
    assert definition.tags == frozenset({"base", "child"})
    assert definition.skip_tags == frozenset({"optional"})
    assert definition.source == (tmp_path / "kitup.py").resolve()

def test_profile_extend_inherits_description(tmp_path):
    write_bundle(tmp_path, "base")
    write(
        tmp_path / "base_profile.py",
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base], description="Base")
        """,
    )
    definition = load(
        tmp_path,
        """
        import base_profile

        from kitup import profile

        profile.extend(base_profile)
        """,
    )

    assert definition.description == "Base"

def test_profile_extend_accepts_a_run_definition(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "dotfiles")
    write(
        tmp_path / "base_profile.py",
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base])
        """,
    )
    definition = load(
        tmp_path,
        """
        import base_profile

        from bundles import dotfiles

        from kitup import profile

        base_run = profile.of(base_profile)
        profile.extend(base_run, bundles=[dotfiles])
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == ["base", "dotfiles"]

def test_profile_extend_requires_a_definition(tmp_path):
    write(tmp_path / "empty_profile.py", "VALUE = 1\n")

    with pytest.raises(ProfileError, match="has no run definition"):
        load(
            tmp_path,
            """
            import empty_profile

            from kitup import profile

            profile.extend(empty_profile)
            """,
        )

def test_parent_definition_is_not_modified(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "dotfiles")
    write(
        tmp_path / "base_profile.py",
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base], description="Base")
        """,
    )
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module("base_profile")
        parent = profile.of(module)
        load(
            tmp_path,
            """
            import base_profile

            from bundles import dotfiles

            from kitup import profile

            profile.extend(base_profile, bundles=[dotfiles])
            """,
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert [bundle.name for bundle in parent.bundles] == ["base"]
    assert [bundle.name for bundle in module.__kitup_run__.bundles] == ["base"]

def test_profile_use_registers_unchanged(tmp_path):
    write_bundle(tmp_path, "base")
    write(
        tmp_path / "base_profile.py",
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base], description="Base")
        """,
    )
    definition = load(
        tmp_path,
        """
        import base_profile

        from kitup import profile

        profile.use(base_profile)
        """,
    )

    assert definition.description == "Base"
    assert [bundle.name for bundle in definition.bundles] == ["base"]

def test_three_level_extension(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "middle")
    write_bundle(tmp_path, "child")
    write(
        tmp_path / "base_profile.py",
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base])
        """,
    )
    write(
        tmp_path / "middle_profile.py",
        """
        import base_profile

        from bundles import middle

        from kitup import profile

        profile.extend(base_profile, bundles=[middle])
        """,
    )
    definition = load(
        tmp_path,
        """
        import middle_profile

        from bundles import child

        from kitup import profile

        profile.extend(middle_profile, bundles=[child])
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == [
        "base",
        "middle",
        "child",
    ]

def test_run_definition_extend_and_replace(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "other")
    write(tmp_path / "base.py", "VALUE = 1\n")
    write(tmp_path / "other.py", "OTHER = 2\n")
    definition = load(
        tmp_path,
        """
        from bundles import base, other

        from kitup import profile

        run = profile(bundles=[base], config=[Path("base.py")], description="Base")
        run = run.extend(bundles=[other], config=[Path("other.py")], tags=["extra"])
        run.replace(description="Replaced", skip_tags=["skip"])
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == ["base", "other"]
    assert definition.description == "Replaced"
    assert definition.tags == frozenset({"extra"})
    assert definition.skip_tags == frozenset({"skip"})
    assert [path.name for path in definition.config] == ["base.py", "other.py"]

def test_replace_can_change_bundles_and_config_wholesale(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "other")
    write(tmp_path / "base.py", "VALUE = 1\n")
    definition = load(
        tmp_path,
        """
        from bundles import base, other

        from kitup import profile

        run = profile(bundles=[base], config=[Path("base.py")], description="Base")
        run.replace(bundles=[other], config=[])
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == ["other"]
    assert definition.config == ()
    assert definition.description == "Base"

def test_importing_a_profile_does_not_select_it(tmp_path):
    write_bundle(tmp_path, "base")
    write_bundle(tmp_path, "other")
    write(
        tmp_path / "base_profile.py",
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base])
        """,
    )
    definition = load(
        tmp_path,
        """
        import base_profile

        from bundles import other

        from kitup import profile

        profile(bundles=[other])
        """,
    )

    assert [bundle.name for bundle in definition.bundles] == ["other"]

def test_extended_config_paths_resolve_against_the_child_file(tmp_path):
    write_bundle(tmp_path, "base")
    write(tmp_path / "base_profile" / "config.py", "BASE = 1\n")
    write(
        tmp_path / "base_profile" / "__init__.py",
        """
        from pathlib import Path

        from bundles import base

        from kitup import profile

        profile(bundles=[base], config=[Path("config.py")])
        """,
    )
    write(tmp_path / "child.py", "CHILD = 2\n")
    definition = load(
        tmp_path,
        """
        import base_profile

        from kitup import profile

        profile.extend(base_profile, config=[Path("child.py")])
        """,
    )

    assert definition.config == (
        (tmp_path / "base_profile" / "config.py").resolve(),
        (tmp_path / "child.py").resolve(),
    )

def test_inline_bundle_uses_the_profile_directory(tmp_path):
    write(
        tmp_path / "kitup.py",
        """
        from kitup import profile
        from kitup.registry import Bundle

        local = Bundle("Local")

        profile(bundles=[local])
        """,
    )
    sys.path.insert(0, str(tmp_path))
    try:
        definition = load_profile(tmp_path / "kitup.py")
    finally:
        sys.path.remove(str(tmp_path))

    assert definition.bundles[0].path == tmp_path.resolve()

def test_diamond_config_paths_are_deduplicated(tmp_path):
    write_bundle(tmp_path, "base")
    write(tmp_path / "config.py", "VALUE = 1\n")
    write(
        tmp_path / "base_profile.py",
        """
        from pathlib import Path

        from bundles import base

        from kitup import profile

        profile(bundles=[base], config=[Path("config.py")])
        """,
    )
    write(
        tmp_path / "left_profile.py",
        """
        import base_profile

        from kitup import profile

        profile.extend(base_profile)
        """,
    )
    write(
        tmp_path / "right_profile.py",
        """
        import base_profile

        from kitup import profile

        profile.extend(base_profile)
        """,
    )
    definition = load(
        tmp_path,
        """
        import left_profile
        import right_profile

        from kitup import profile

        right = profile.of(right_profile)
        profile.extend(left_profile, config=right.config)
        """,
    )

    assert definition.config == ((tmp_path / "config.py").resolve(),)

def test_replace_rejects_empty_bundles(tmp_path):
    write_bundle(tmp_path, "base")

    with pytest.raises(ProfileError, match="must not be empty"):
        load(
            tmp_path,
            """
            from bundles import base

            from kitup import profile

            run = profile(bundles=[base])
            run.replace(bundles=[])
            """,
        )

def test_extend_rejects_duplicate_config_paths_in_one_declaration(tmp_path):
    write_bundle(tmp_path, "base")
    write(tmp_path / "config.py", "VALUE = 1\n")
    write(
        tmp_path / "base_profile.py",
        """
        from bundles import base

        from kitup import profile

        profile(bundles=[base])
        """,
    )

    with pytest.raises(ProfileError, match="duplicate config path"):
        load(
            tmp_path,
            """
            import base_profile

            from pathlib import Path

            from kitup import profile

            profile.extend(
                base_profile,
                config=[Path("config.py"), Path("config.py")],
            )
            """,
        )

def test_profile_loading_errors(tmp_path):
    write(tmp_path / "run.py", "VALUE = 1\n")

    with pytest.raises(ProfileError, match="profile not found"):
        load_profile(tmp_path / "missing.py")
    with pytest.raises(ProfileError, match="no run definition found"):
        load_profile(tmp_path / "run.py")

def test_profile_of(tmp_path):
    write_bundle(tmp_path, "base")
    write(
        tmp_path / "base_profile.py",
        """
        from pathlib import Path

        from bundles import base

        from kitup import profile

        profile(bundles=[base], description="Base")
        """,
    )
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module("base_profile")
        namespace = {
            "__file__": str(tmp_path / "inspect.py"),
            "base_profile": module,
            "profile": profile,
        }
        exec("base_run = profile.of(base_profile)\n", namespace)  # noqa: S102
    finally:
        sys.path.remove(str(tmp_path))

    assert "__kitup_run__" not in namespace
    assert namespace["base_run"].description == "Base"  # type: ignore[index]

    write(tmp_path / "empty_profile.py", "VALUE = 1\n")
    sys.path.insert(0, str(tmp_path))
    try:
        empty = importlib.import_module("empty_profile")
    finally:
        sys.path.remove(str(tmp_path))

    with pytest.raises(ProfileError, match="has no run definition"):
        profile.of(empty)
