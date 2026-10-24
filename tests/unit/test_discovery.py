from __future__ import annotations

import sys
import textwrap

import pytest

from kitup.discovery import discover_bundles
from kitup.errors import DiscoveryError


def write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def make_bundle(root, folder, *, name=None, depends=(), body=""):
    name = name or folder
    depends_repr = repr(tuple(depends))
    write(
        root / "bundles" / folder / "__init__.py",
        f"""
        from kitup.registry import Bundle
        bundle = Bundle({name!r}, "desc", depends_on={depends_repr})
        {body}
        """,
    )


def test_discovers_packages_in_folder_name_order(tmp_path):
    make_bundle(tmp_path, "zulu")
    make_bundle(tmp_path, "alpha")
    (tmp_path / "bundles" / "not-a-bundle").mkdir(parents=True)
    (tmp_path / "bundles" / "file.txt").write_text("ignored")

    bundles = discover_bundles(tmp_path)

    assert [bundle.name for bundle in bundles] == ["alpha", "zulu"]
    assert bundles[0].path == tmp_path / "bundles" / "alpha"


def test_missing_bundles_directory_is_an_error(tmp_path):
    with pytest.raises(DiscoveryError, match="no bundles found"):
        discover_bundles(tmp_path)


def test_empty_bundles_directory_is_an_error(tmp_path):
    (tmp_path / "bundles").mkdir()
    with pytest.raises(DiscoveryError, match="no bundles found"):
        discover_bundles(tmp_path)


def test_bundle_must_define_bundle_object(tmp_path):
    write(tmp_path / "bundles" / "bad" / "__init__.py", "x = 1\n")

    with pytest.raises(DiscoveryError, match="does not define a 'bundle'"):
        discover_bundles(tmp_path)


def test_bundle_object_must_be_a_bundle(tmp_path):
    write(tmp_path / "bundles" / "bad" / "__init__.py", "bundle = object()\n")

    with pytest.raises(DiscoveryError, match="non-Bundle"):
        discover_bundles(tmp_path)


def test_duplicate_bundle_names_are_rejected(tmp_path):
    make_bundle(tmp_path, "one", name="same")
    make_bundle(tmp_path, "two", name="same")

    with pytest.raises(DiscoveryError, match="duplicate bundle name"):
        discover_bundles(tmp_path)


def test_import_errors_are_wrapped(tmp_path):
    write(tmp_path / "bundles" / "bad" / "__init__.py", "raise RuntimeError('boom')\n")

    with pytest.raises(DiscoveryError, match="failed to load bundle 'bad': boom"):
        discover_bundles(tmp_path)


def test_failed_bundle_import_leaves_no_modules_behind(tmp_path):
    write(
        tmp_path / "bundles" / "bad" / "__init__.py",
        "from . import helper\nraise RuntimeError('boom')\n",
    )
    write(tmp_path / "bundles" / "bad" / "helper.py", "VALUE = 1\n")
    before = set(sys.modules)

    with pytest.raises(DiscoveryError):
        discover_bundles(tmp_path)

    new_modules = {
        name
        for name in sys.modules
        if name not in before and name.startswith("_kitup_bundle_")
    }
    assert new_modules == set()


def test_multi_file_task_order(tmp_path):
    make_bundle(
        tmp_path,
        "base",
        body="from . import second, first  # noqa: E402,F401",
    )
    write(
        tmp_path / "bundles" / "base" / "first.py",
        """
        from . import bundle

        @bundle.task("First")
        def first(ctx):
            pass
        """,
    )
    write(
        tmp_path / "bundles" / "base" / "second.py",
        """
        from . import bundle

        @bundle.task("Second", needs_sudo=True, tags={"one", "two"})
        def second(ctx):
            pass
        """,
    )

    (bundle,) = discover_bundles(tmp_path)

    assert [task.name for task in bundle.tasks] == ["Second", "First"]
    assert bundle.tasks[0].needs_sudo is True
    assert bundle.tasks[0].tags == frozenset({"one", "two"})


def test_bundle_resources_resolve_relative_to_bundle_folder(tmp_path):
    make_bundle(tmp_path, "base")
    resource = tmp_path / "bundles" / "base" / "resources" / "example.conf"
    write(resource, "value = 1\n")

    (bundle,) = discover_bundles(tmp_path)

    assert bundle.resource_path("resources", "example.conf") == resource
    assert bundle.read_resource("resources", "example.conf") == "value = 1\n"
