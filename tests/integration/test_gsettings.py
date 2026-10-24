"""Integration tests for real gsettings behavior under a D-Bus session."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import SCHEMA, SCHEMA_DEFAULT, SCHEMA_KEY, run, run_kitup, write_bundle

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_key() -> Iterator[None]:
    run(["gsettings", "reset", SCHEMA, SCHEMA_KEY], check=False)
    yield
    run(["gsettings", "reset", SCHEMA, SCHEMA_KEY], check=False)


def test_set_and_reset_key(tmp_path: Path) -> None:
    set_root = write_bundle(
        tmp_path / "set",
        "gsettings_set",
        f"""
        from . import bundle
        from kitup.modules import gsettings

        @bundle.task("set integration key")
        def set_key(ctx):
            gsettings.key(ctx, {SCHEMA!r}, {SCHEMA_KEY!r}, "kitup-value")
        """,
    )
    assert run_kitup("apply", set_root) == 0
    assert run(["gsettings", "get", SCHEMA, SCHEMA_KEY]).stdout.strip() == "'kitup-value'"

    reset_root = write_bundle(
        tmp_path / "reset",
        "gsettings_reset",
        f"""
        from . import bundle
        from kitup.modules import gsettings

        @bundle.task("reset integration key")
        def reset_key(ctx):
            gsettings.key(ctx, {SCHEMA!r}, {SCHEMA_KEY!r}, state="absent")
        """,
    )
    assert run_kitup("apply", reset_root) == 0
    assert run(["gsettings", "get", SCHEMA, SCHEMA_KEY]).stdout.strip() == SCHEMA_DEFAULT
