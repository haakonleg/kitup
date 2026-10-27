"""Integration tests for the standalone zipapp."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import run, write_bundle

pytestmark = pytest.mark.integration

ZIPAPP = Path("/kitup-integration-test/dist/kitup")
WORKER_PATH = "/etc/kitup-zipapp-worker-test.conf"


@pytest.fixture(autouse=True)
def worker_file_absent() -> Iterator[None]:
    run(["sudo", "-n", "rm", "-f", WORKER_PATH], check=False)
    yield
    run(["sudo", "-n", "rm", "-f", WORKER_PATH], check=False)


def test_zipapp_lists_and_applies_outside_the_source_tree(tmp_path: Path) -> None:
    assert ZIPAPP.is_file(), f"prebuilt zipapp is missing: {ZIPAPP}"
    assert os.access(ZIPAPP, os.X_OK), f"zipapp is not executable: {ZIPAPP}"

    root = write_bundle(
        tmp_path / "config",
        "zipapp_bundle",
        f"""
        from kitup.modules import files

        @task("write privileged marker")
        def write_privileged(ctx):
            path = {WORKER_PATH!r}
            files.file(ctx, path, content="applied\\n", mode=0o600, sudo=True)
            if not ctx.dry_run:
                assert ctx.probe(["cat", path], sudo=True).stdout == "applied\\n"
                files.file(ctx, path, state="absent", sudo=True)

        @task("write marker")
        def write_marker(ctx):
            files.file(ctx, {str(tmp_path / "marker.txt")!r}, content="applied\\n")
        """,
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    listed = run(
        [str(ZIPAPP), "list", str(root / "kitup.py"), "--no-color"],
        cwd=elsewhere,
        check=False,
    )
    assert listed.returncode == 0

    dry_run = run(
        [str(ZIPAPP), "apply", str(root / "kitup.py"), "--no-color", "--dry-run"],
        cwd=elsewhere,
        check=False,
    )
    assert dry_run.returncode == 0
    assert not (tmp_path / "marker.txt").exists()

    applied = run(
        [str(ZIPAPP), "apply", str(root / "kitup.py"), "--no-color"],
        cwd=elsewhere,
        check=False,
    )
    assert applied.returncode == 0
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "applied\n"
    assert not Path(WORKER_PATH).exists()
