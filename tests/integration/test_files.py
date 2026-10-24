"""Integration tests for privileged file operations through the helper."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import run, run_kitup, write_bundle

pytestmark = pytest.mark.integration

MANAGED_PATH = Path("/etc/kitup-integration-test.conf")


@pytest.fixture(autouse=True)
def file_absent() -> Iterator[None]:
    run(["sudo", "-n", "rm", "-f", str(MANAGED_PATH)], check=False)
    yield
    run(["sudo", "-n", "rm", "-f", str(MANAGED_PATH)], check=False)


def test_privileged_file_converges_and_removes(tmp_path: Path) -> None:
    write_root = write_bundle(
        tmp_path / "write",
        "files_write",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("write privileged file", needs_sudo=True)
        def write_file(ctx):
            files.file(
                ctx,
                {str(MANAGED_PATH)!r},
                content="managed\\n",
                mode=0o640,
                owner="root",
                group="root",
            )
        """,
    )
    assert run_kitup("apply", write_root) == 0
    assert run(["sudo", "-n", "cat", str(MANAGED_PATH)]).stdout == "managed\n"
    details = run(["sudo", "-n", "stat", "-c", "%a %u %g", str(MANAGED_PATH)])
    assert details.stdout.strip() == "640 0 0"
    assert not os.access(MANAGED_PATH, os.R_OK)

    remove_root = write_bundle(
        tmp_path / "remove",
        "files_remove",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("remove privileged file", needs_sudo=True)
        def remove_file(ctx):
            files.file(ctx, {str(MANAGED_PATH)!r}, state="absent")
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert not MANAGED_PATH.exists()
