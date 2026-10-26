"""Integration tests for privileged file operations through the worker."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import run, run_kitup, write_bundle

pytestmark = pytest.mark.integration

MANAGED_PATH = Path("/etc/kitup-integration-test.conf")
WORKER_ROOT = Path("/etc/kitup-integration-files")
WORKER_FILE = WORKER_ROOT / "file.conf"
WORKER_COPY = WORKER_ROOT / "copy.conf"
WORKER_LINK = WORKER_ROOT / "link"
WORKER_DIR = WORKER_ROOT / "dir"
WORKER_NESTED = WORKER_DIR / "nested.conf"
DRY_FILE = Path("/etc/kitup-integration-dry.conf")
DRY_LINK = Path("/etc/kitup-integration-dry.link")
NONEMPTY_DIR = WORKER_ROOT / "nonempty"
NONEMPTY_FILE = NONEMPTY_DIR / "keep.conf"


@pytest.fixture(autouse=True)
def managed_paths_absent() -> Iterator[None]:
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    for path in (MANAGED_PATH, WORKER_ROOT, DRY_FILE, DRY_LINK):
        run(["sudo", "-n", "rm", "-rf", str(path)], check=False)


def exists(path: Path) -> bool:
    return run(["sudo", "-n", "test", "-e", str(path)], check=False).returncode == 0


def link_exists(path: Path) -> bool:
    return run(["sudo", "-n", "test", "-L", str(path)], check=False).returncode == 0


def test_privileged_file_converges_and_removes(tmp_path: Path) -> None:
    write_root = write_bundle(
        tmp_path / "write",
        "files_write",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("write privileged file")
        def write_file(ctx):
            files.file(
                ctx,
                {str(MANAGED_PATH)!r},
                content="managed\\n",
                mode=0o640,
                owner="root",
                group="root",
                sudo=True,
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

        @bundle.task("remove privileged file")
        def remove_file(ctx):
            files.file(ctx, {str(MANAGED_PATH)!r}, state="absent", sudo=True)
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert not MANAGED_PATH.exists()


def test_privileged_worker_handles_multiple_operations(tmp_path: Path) -> None:
    root = write_bundle(
        tmp_path / "worker",
        "files_worker",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("exercise privileged worker")
        def exercise(ctx):
            path = {str(MANAGED_PATH)!r}
            files.file(ctx, path, content="one\\n", mode=0o600, sudo=True)
            files.line(ctx, path, "two", sudo=True)
            files.file(ctx, path, mode=0o640, sudo=True)

            content = ctx.probe(["cat", path], sudo=True).stdout
            assert content == "one\\ntwo\\n"

            mode = ctx.probe(["stat", "-c", "%a", path], sudo=True).stdout.strip()
            assert mode == "640"

            files.file(ctx, path, state="absent", sudo=True)
        """,
    )

    assert run_kitup("apply", root) == 0
    assert not MANAGED_PATH.exists()


def test_privileged_file_lifecycle_covers_worker_actions(tmp_path: Path) -> None:
    root = write_bundle(
        tmp_path / "lifecycle",
        "files_lifecycle",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("exercise privileged file actions")
        def exercise(ctx):
            files.directory(
                ctx,
                {str(WORKER_ROOT)!r},
                mode=0o750,
                owner="root",
                group="root",
                sudo=True,
            )
            files.file(
                ctx,
                {str(WORKER_FILE)!r},
                content="alpha\\n",
                mode=0o600,
                owner="root",
                group="root",
                sudo=True,
            )
            files.line(ctx, {str(WORKER_FILE)!r}, "beta", sudo=True)
            files.block(ctx, {str(WORKER_FILE)!r}, "managed", "inside\\n", sudo=True)
            files.file(ctx, {str(WORKER_FILE)!r}, mode=0o640, sudo=True)
            files.copy(
                ctx,
                "source.conf",
                {str(WORKER_COPY)!r},
                mode=0o600,
                owner="root",
                group="root",
                sudo=True,
            )
            files.symlink(
                ctx,
                {str(WORKER_FILE)!r},
                {str(WORKER_LINK)!r},
                sudo=True,
            )
            files.directory(
                ctx,
                {str(WORKER_DIR)!r},
                mode=0o700,
                owner="root",
                group="root",
                sudo=True,
            )
            files.file(
                ctx,
                {str(WORKER_NESTED)!r},
                content="nested\\n",
                mode=0o600,
                sudo=True,
            )
        """,
    )
    source = root / "bundles" / "files_lifecycle" / "files" / "source.conf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("copied\n", encoding="utf-8")

    assert run_kitup("apply", root) == 0

    assert run(["sudo", "-n", "cat", str(WORKER_FILE)]).stdout == (
        "alpha\nbeta\n# BEGIN managed\ninside\n# END managed\n"
    )
    assert (
        run(["sudo", "-n", "stat", "-c", "%a %u %g", str(WORKER_FILE)]).stdout.strip()
        == "640 0 0"
    )
    assert run(["sudo", "-n", "stat", "-c", "%a", str(WORKER_ROOT)]).stdout.strip() == "750"
    assert run(["sudo", "-n", "cat", str(WORKER_COPY)]).stdout == "copied\n"
    assert run(["sudo", "-n", "stat", "-c", "%a", str(WORKER_COPY)]).stdout.strip() == "600"
    assert run(["sudo", "-n", "readlink", str(WORKER_LINK)]).stdout.strip() == str(
        WORKER_FILE
    )
    assert run(["sudo", "-n", "stat", "-c", "%a", str(WORKER_DIR)]).stdout.strip() == "700"
    assert run(["sudo", "-n", "cat", str(WORKER_NESTED)]).stdout == "nested\n"

    cleanup_root = write_bundle(
        tmp_path / "cleanup",
        "files_cleanup",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("remove privileged file actions")
        def cleanup(ctx):
            files.file(ctx, {str(WORKER_FILE)!r}, state="absent", sudo=True)
            files.file(ctx, {str(WORKER_COPY)!r}, state="absent", sudo=True)
            files.file(ctx, {str(WORKER_LINK)!r}, state="absent", sudo=True)
            files.directory(
                ctx, {str(WORKER_DIR)!r}, state="absent", force=True, sudo=True
            )
            files.directory(
                ctx, {str(WORKER_ROOT)!r}, state="absent", force=True, sudo=True
            )
        """,
    )
    assert run_kitup("apply", cleanup_root) == 0
    assert not exists(WORKER_ROOT)


def test_privileged_dry_run_does_not_mutate(tmp_path: Path) -> None:
    root = write_bundle(
        tmp_path / "dry",
        "files_dry",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("dry privileged file actions")
        def dry(ctx):
            files.file(
                ctx,
                {str(DRY_FILE)!r},
                content="dry\\n",
                mode=0o600,
                sudo=True,
            )
            files.symlink(
                ctx,
                {str(DRY_FILE)!r},
                {str(DRY_LINK)!r},
                sudo=True,
            )
        """,
    )

    assert run_kitup("apply", root, "--dry-run") == 0
    assert not exists(DRY_FILE)
    assert not link_exists(DRY_LINK)

    assert run_kitup("apply", root) == 0
    assert run(["sudo", "-n", "cat", str(DRY_FILE)]).stdout == "dry\n"
    assert run(["sudo", "-n", "readlink", str(DRY_LINK)]).stdout.strip() == str(DRY_FILE)


def test_privileged_directory_requires_force_for_non_empty(tmp_path: Path) -> None:
    root = write_bundle(
        tmp_path / "nonempty",
        "files_nonempty",
        f"""
        from . import bundle
        from kitup.modules import files

        @bundle.task("create non-empty directory")
        def create(ctx):
            files.directory(ctx, {str(WORKER_ROOT)!r}, mode=0o750, sudo=True)
            files.directory(ctx, {str(NONEMPTY_DIR)!r}, sudo=True)
            files.file(
                ctx,
                {str(NONEMPTY_FILE)!r},
                content="keep\\n",
                sudo=True,
            )

        @bundle.task("remove non-empty directory without force")
        def remove(ctx):
            files.directory(ctx, {str(NONEMPTY_DIR)!r}, state="absent", sudo=True)
        """,
    )

    assert run_kitup("apply", root) == 1
    assert exists(NONEMPTY_FILE)
