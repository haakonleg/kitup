from __future__ import annotations

import textwrap
from pathlib import Path
from types import ModuleType

import pytest

from kitup.config import load_config
from kitup.errors import ConfigError


def write(path, text: str) -> None:
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def module(**values) -> ModuleType:
    result = ModuleType("config_source")
    for name, value in values.items():
        setattr(result, name, value)
    return result


def test_path_sources_merge_in_order(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    write(first, """
        KEEP = "first"
        REPLACE = "first"
        COLLECTION = [1, 2]
    """)
    write(second, """
        REPLACE = "second"
        COLLECTION = [3]
        ADDED = True
    """)

    config = load_config([first, second])

    assert config.KEEP == "first"
    assert config.REPLACE == "second"
    assert config.COLLECTION == [3]
    assert config.ADDED is True


def test_module_and_mapping_sources_contribute_public_fields():
    module_config = load_config(
        [module(VALUE="module", _PRIVATE="hidden", helper=lambda: None)]
    )
    mapping_config = load_config([{"VALUE": 1, "helper": lambda: None}])

    assert vars(module_config) == {"VALUE": "module"}
    assert vars(mapping_config) == {"VALUE": 1}


def test_mapping_sources_reject_invalid_keys():
    with pytest.raises(ConfigError, match="key must not be private: _PRIVATE"):
        load_config([{"_PRIVATE": 1}])
    with pytest.raises(ConfigError, match="keys must be strings, not int"):
        load_config([{1: "value"}])  # type: ignore[list-item]


def test_mixed_sources_merge_left_to_right():
    config = load_config(
        [
            {"VALUE": "mapping", "MAPPING_ONLY": True},
            module(VALUE="module", MODULE_ONLY=True),
        ]
    )

    assert config.VALUE == "module"
    assert config.MAPPING_ONLY is True
    assert config.MODULE_ONLY is True


def test_invalid_source_shapes_are_rejected():
    for source in ({"VALUE": 1}, "config.py", Path("config.py"), module(VALUE=1)):
        with pytest.raises(ConfigError, match="not a single source"):
            load_config(source)  # type: ignore[arg-type]

    with pytest.raises(ConfigError, match="Path objects"):
        load_config(["config.py"])  # type: ignore[list-item]


def test_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="config not found"):
        load_config([tmp_path / "missing.py"])


def test_import_exception_is_reported(tmp_path):
    write(tmp_path / "config.py", "raise RuntimeError('boom')\n")

    with pytest.raises(ConfigError, match="boom"):
        load_config([tmp_path / "config.py"])


def test_bundle_instances_are_not_config_values(tmp_path):
    write(tmp_path / "config.py", """
        from kitup.registry import Bundle

        VALUE = 1
        BUNDLE = Bundle("nested")
    """)

    config = load_config([tmp_path / "config.py"])

    assert vars(config) == {"VALUE": 1}
