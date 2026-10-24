"""Integration tests for the standalone zipapp."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .helpers import run, write_bundle

pytestmark = pytest.mark.integration

ZIPAPP = Path("/kitup-integration-test/dist/kitup")


def test_zipapp_lists_and_applies_outside_the_source_tree(tmp_path: Path) -> None:
    assert ZIPAPP.is_file(), f"prebuilt zipapp is missing: {ZIPAPP}"
    assert os.access(ZIPAPP, os.X_OK), f"zipapp is not executable: {ZIPAPP}"

    root = write_bundle(
        tmp_path / "config",
        "zipapp_bundle",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("write marker")
        def write_marker(ctx):
            files.file(ctx, {str(tmp_path / "marker.txt")!r}, content="applied\\n")
        """,
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    listed = run([str(ZIPAPP), "list", "-D", str(root), "--no-color"], cwd=elsewhere, check=False)
    assert listed.returncode == 0

    dry_run = run(
        [str(ZIPAPP), "apply", "-D", str(root), "--no-color", "--dry-run"],
        cwd=elsewhere,
        check=False,
    )
    assert dry_run.returncode == 0
    assert not (tmp_path / "marker.txt").exists()

    applied = run(
        [str(ZIPAPP), "apply", "-D", str(root), "--no-color"],
        cwd=elsewhere,
        check=False,
    )
    assert applied.returncode == 0
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "applied\n"
