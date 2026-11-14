"""Tests for relocating and disabling bytecode caches for project code."""

import sys
from pathlib import Path

import pytest

from kitup import cli
from kitup._utils import importer
from kitup.presets import parse_with_presets
from kitup.project import discover_custom_modules, load_project
from kitup.variables import load_vars

from ..helpers import make_project


def build_project(root: Path) -> Path:
    """Create a project with a bundle, a var file, and a custom module."""
    return make_project(
        root,
        vars="VALUE = 1\n",
        bundles={"base": "from kitup import task\n\n@task\ndef run():\n    pass\n"},
        modules={"mytool.py": 'from kitup import module\n\nmodule("mytool")\n'},
    )


def load_everything(root: Path) -> None:
    """Load bundles, vars, and custom modules from ``root``."""
    project = load_project(root)
    load_vars(project.var_paths)
    discover_custom_modules(project)


def test_project_imports_write_cache_under_the_prefix(tmp_path, monkeypatch):
    root = build_project(tmp_path / "proj")
    cache = tmp_path / "cache"
    monkeypatch.setattr(importer, "default_pycache_prefix", lambda: cache)
    importer.configure_pycache()

    load_everything(root)

    assert not list(root.rglob("__pycache__"))
    assert list(cache.rglob("*.pyc"))


def test_disabled_pycache_writes_nothing(tmp_path, monkeypatch):
    root = build_project(tmp_path / "proj")
    cache = tmp_path / "cache"
    monkeypatch.setattr(importer, "default_pycache_prefix", lambda: cache)
    importer.configure_pycache(disabled=True)

    load_everything(root)

    assert not list(root.rglob("__pycache__"))
    assert not list(cache.rglob("*.pyc"))


def test_context_restores_previous_sys_settings(tmp_path, monkeypatch):
    prefix = tmp_path / "cache"
    monkeypatch.setattr(importer, "default_pycache_prefix", lambda: prefix)
    monkeypatch.setattr(sys, "pycache_prefix", "/sentinel/prefix")
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    importer.configure_pycache()

    with importer.project_bytecode_cache():
        assert sys.pycache_prefix == str(prefix)
        with importer.project_bytecode_cache():
            assert sys.pycache_prefix == str(prefix)
        assert sys.pycache_prefix == str(prefix)

    assert sys.pycache_prefix == "/sentinel/prefix"
    assert sys.dont_write_bytecode is False


def test_disabled_context_sets_and_restores_dont_write_bytecode(monkeypatch):
    monkeypatch.setattr(sys, "pycache_prefix", None)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    importer.configure_pycache(disabled=True)

    with importer.project_bytecode_cache():
        assert sys.dont_write_bytecode is True
        assert sys.pycache_prefix is None

    assert sys.dont_write_bytecode is False


def test_default_prefix_honors_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert importer.default_pycache_prefix() == tmp_path / "kitup" / "pycache"


def test_default_prefix_ignores_relative_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert importer.default_pycache_prefix() == tmp_path / ".cache" / "kitup" / "pycache"


@pytest.mark.parametrize("command", ["apply", "list", "check", "list-modules", "init"])
def test_no_pycache_flag_is_accepted_by_every_command(command):
    args = cli.build_parser().parse_args([command, "--no-pycache"])

    assert args.no_pycache is True


def test_no_pycache_can_come_from_a_preset(tmp_path):
    root = make_project(tmp_path, preset={"quiet": "--no-pycache\n"})

    args, _ = parse_with_presets(cli.build_parser(), ["list", str(root), "--preset", "quiet"])

    assert args.no_pycache is True


def test_check_writes_cache_under_the_default_prefix(tmp_path, monkeypatch):
    root = build_project(tmp_path / "proj")
    cache = tmp_path / "default-cache"
    monkeypatch.setattr(importer, "default_pycache_prefix", lambda: cache)

    code = cli.main(["check", str(root), "--no-color"])

    assert code == 0
    assert not list(root.rglob("__pycache__"))
    assert list(cache.rglob("*.pyc"))


def test_check_with_no_pycache_writes_nothing(tmp_path, monkeypatch):
    root = build_project(tmp_path / "proj")
    cache = tmp_path / "default-cache"
    monkeypatch.setattr(importer, "default_pycache_prefix", lambda: cache)

    code = cli.main(["check", str(root), "--no-pycache", "--no-color"])

    assert code == 0
    assert not list(root.rglob("__pycache__"))
    assert not list(cache.rglob("*.pyc"))
