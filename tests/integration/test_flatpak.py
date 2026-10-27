"""Integration tests for real Flatpak remote and package management."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import (
    FLATPAK_APP,
    FLATPAK_REMOTE,
    FLATPAK_REPO,
    FLATPAK_RUNTIME,
    FLATPAK_USER_REMOTE,
    flatpak_installed,
    flatpak_remotes,
    run,
    run_kitup,
    write_bundle,
)

pytestmark = pytest.mark.integration

REPO_URL = f"file://{FLATPAK_REPO}"


def cleanup() -> None:
    for scope, prefix in (("system", ["sudo", "-n"]), ("user", [])):
        for ref in (FLATPAK_APP, FLATPAK_RUNTIME):
            run(
                [
                    *prefix,
                    "flatpak",
                    "uninstall",
                    f"--{scope}",
                    "--noninteractive",
                    "-y",
                    ref,
                ],
                check=False,
            )
        for name in (FLATPAK_REMOTE, FLATPAK_USER_REMOTE):
            run(
                [*prefix, "flatpak", "remote-delete", f"--{scope}", name],
                check=False,
            )


@pytest.fixture(autouse=True)
def clean_flatpak() -> Iterator[None]:
    cleanup()
    yield
    cleanup()


@pytest.fixture
def system_remote() -> Iterator[None]:
    run(
        [
            "sudo",
            "-n",
            "flatpak",
            "remote-add",
            "--system",
            "--if-not-exists",
            "--no-gpg-verify",
            FLATPAK_REMOTE,
            REPO_URL,
        ]
    )
    yield


def test_system_remote_add_and_remove(tmp_path: Path) -> None:
    add_root = write_bundle(
        tmp_path / "add",
        "flatpak_remote_add",
        f"""
        from kitup.modules import flatpak

        @task("add flatpak remote")
        def add_remote(ctx):
            flatpak.remote(
                ctx,
                {FLATPAK_REMOTE!r},
                {REPO_URL!r},
                gpg_verify=False,
            )
        """,
    )
    assert run_kitup("apply", add_root) == 0
    assert FLATPAK_REMOTE in flatpak_remotes()

    remove_root = write_bundle(
        tmp_path / "remove",
        "flatpak_remote_remove",
        f"""
        from kitup.modules import flatpak

        @task("remove flatpak remote")
        def remove_remote(ctx):
            flatpak.remote(ctx, {FLATPAK_REMOTE!r}, state="absent")
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert FLATPAK_REMOTE not in flatpak_remotes()


def test_system_package_install_and_remove(
    tmp_path: Path,
    system_remote: None,
) -> None:
    install_root = write_bundle(
        tmp_path / "install",
        "flatpak_package_install",
        f"""
        from kitup.modules import flatpak

        @task("install flatpak app")
        def install_app(ctx):
            flatpak.packages(ctx, [{FLATPAK_APP!r}], remote={FLATPAK_REMOTE!r})
        """,
    )
    assert run_kitup("apply", install_root) == 0
    installed = flatpak_installed()
    assert FLATPAK_APP in installed
    assert FLATPAK_RUNTIME in installed

    remove_root = write_bundle(
        tmp_path / "remove",
        "flatpak_package_remove",
        f"""
        from kitup.modules import flatpak

        @task("remove flatpak app")
        def remove_app(ctx):
            flatpak.packages(ctx, [{FLATPAK_APP!r}], state="absent")
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert FLATPAK_APP not in flatpak_installed()


def test_user_remote_add_and_remove(tmp_path: Path) -> None:
    add_root = write_bundle(
        tmp_path / "add",
        "flatpak_user_remote_add",
        f"""
        from kitup.modules import flatpak

        @task("add user flatpak remote")
        def add_remote(ctx):
            flatpak.remote(
                ctx,
                {FLATPAK_USER_REMOTE!r},
                {REPO_URL!r},
                scope="user",
                gpg_verify=False,
            )
        """,
    )
    assert run_kitup("apply", add_root) == 0
    assert FLATPAK_USER_REMOTE in flatpak_remotes("user")

    remove_root = write_bundle(
        tmp_path / "remove",
        "flatpak_user_remote_remove",
        f"""
        from kitup.modules import flatpak

        @task("remove user flatpak remote")
        def remove_remote(ctx):
            flatpak.remote(ctx, {FLATPAK_USER_REMOTE!r}, state="absent", scope="user")
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert FLATPAK_USER_REMOTE not in flatpak_remotes("user")
