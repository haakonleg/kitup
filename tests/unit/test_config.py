from __future__ import annotations

import textwrap

import pytest

from kitup.config import load_config
from kitup.errors import ConfigError


def write(path, text: str) -> None:
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_load_base_config(tmp_path):
    write(tmp_path / "config.py", """
        PACKAGES = ["git", "vim"]
        DNF_CONF = "/etc/dnf/dnf.conf"
    """)

    config = load_config(tmp_path)

    assert config.PACKAGES == ["git", "vim"]
    assert config.DNF_CONF == "/etc/dnf/dnf.conf"


def test_missing_base_config_is_empty(tmp_path):
    config = load_config(tmp_path)
    assert vars(config) == {}


def test_override_replaces_retains_and_adds(tmp_path):
    write(tmp_path / "config.py", """
        KEEP = "base"
        REPLACE = "base"
        COLLECTION = [1, 2]
    """)
    override = tmp_path / "work.py"
    write(override, """
        REPLACE = "override"
        COLLECTION = [3]
        ADDED = True
    """)

    config = load_config(tmp_path, override)

    assert config.KEEP == "base"
    assert config.REPLACE == "override"
    assert config.COLLECTION == [3]
    assert config.ADDED is True


def test_missing_override_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="override not found"):
        load_config(tmp_path, tmp_path / "missing.py")


def test_syntax_error_in_config_is_reported(tmp_path):
    write(tmp_path / "config.py", "VALUE = (\n")

    with pytest.raises(ConfigError, match="error while importing"):
        load_config(tmp_path)


def test_import_exception_is_reported(tmp_path):
    write(tmp_path / "config.py", "raise RuntimeError('boom')\n")

    with pytest.raises(ConfigError, match="boom"):
        load_config(tmp_path)


def test_public_fields_exclude_imports_functions_and_classes(tmp_path):
    write(tmp_path / "config.py", """
        import os

        VALUE = 1

        def helper():
            return 2

        class Thing:
            pass
    """)

    config = load_config(tmp_path)

    assert vars(config) == {"VALUE": 1}
