"""Integration tests for real gsettings behavior under a D-Bus session."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import SCHEMA, SCHEMA_DEFAULT, SCHEMA_KEY, SCHEMA_KEYS, run, run_kitup, write_bundle

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_key() -> Iterator[None]:
    for key in SCHEMA_KEYS:
        run(["gsettings", "reset", SCHEMA, key], check=False)
    yield
    for key in SCHEMA_KEYS:
        run(["gsettings", "reset", SCHEMA, key], check=False)


def test_set_and_reset_key(tmp_path: Path) -> None:
    set_root = write_bundle(
        tmp_path / "set",
        "gsettings_set",
        f"""
        from kitup.modules import gsettings

        @task("set integration key")
        def set_key():
            gsettings.key({SCHEMA!r}, {SCHEMA_KEY!r}, "kitup-value")
        """,
    )
    assert run_kitup("apply", set_root) == 0
    assert run(["gsettings", "get", SCHEMA, SCHEMA_KEY]).stdout.strip() == "'kitup-value'"

    reset_root = write_bundle(
        tmp_path / "reset",
        "gsettings_reset",
        f"""
        from kitup.modules import gsettings

        @task("reset integration key")
        def reset_key():
            gsettings.key({SCHEMA!r}, {SCHEMA_KEY!r}, state="absent")
        """,
    )
    assert run_kitup("apply", reset_root) == 0
    assert run(["gsettings", "get", SCHEMA, SCHEMA_KEY]).stdout.strip() == SCHEMA_DEFAULT


def test_native_and_variant_values_set_and_are_idempotent(tmp_path: Path, capsys) -> None:
    root = write_bundle(
        tmp_path / "types",
        "gsettings_types",
        f"""
        from kitup.modules import gsettings

        @task("set typed keys")
        def set_keys():
            gsettings.key({SCHEMA!r}, "example-uint", 5)
            gsettings.key({SCHEMA!r}, "example-empty-list", [])
            gsettings.key({SCHEMA!r}, "example-double", 0.1)
            gsettings.key({SCHEMA!r}, "example-maybe", None)
            gsettings.key({SCHEMA!r}, "example-variant", gsettings.variant("<[1, 2]>"))
            gsettings.key({SCHEMA!r}, "example-dict", {{"a": gsettings.variant("<'b'>")}})
            gsettings.key({SCHEMA!r}, "example-bytes", b"abc")
        """,
    )

    assert run_kitup("apply", root) == 0
    out = capsys.readouterr().out
    assert out.count("+ gsettings.key:") == 7
    assert "1 changed" in out
    assert run(["gsettings", "get", SCHEMA, "example-uint"]).stdout.strip() == "uint32 5"
    assert run(["gsettings", "get", SCHEMA, "example-empty-list"]).stdout.strip() == "@as []"
    assert run(["gsettings", "get", SCHEMA, "example-double"]).stdout.strip() == "0.10000000000000001"
    assert run(["gsettings", "get", SCHEMA, "example-maybe"]).stdout.strip() == "@ms nothing"
    assert run(["gsettings", "get", SCHEMA, "example-variant"]).stdout.strip() == "<[1, 2]>"
    assert run(["gsettings", "get", SCHEMA, "example-dict"]).stdout.strip() == "{'a': <'b'>}"
    assert run(["gsettings", "get", SCHEMA, "example-bytes"]).stdout.strip() == "[byte 0x61, 0x62, 0x63]"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out
