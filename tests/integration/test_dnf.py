"""Integration tests for real dnf5 behavior."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import (
    TEST_PACKAGE,
    TEST_REPO,
    TEST_RPM,
    dnf_reposdir,
    remove_package,
    rpm_installed,
    run,
    run_kitup,
    write_bundle,
)

pytestmark = pytest.mark.integration

TEST_GROUP = "kitup-test-group"


@pytest.fixture(autouse=True)
def package_absent() -> Iterator[None]:
    remove_package()
    yield
    remove_package()


@pytest.fixture
def group_absent() -> Iterator[None]:
    run(["sudo", "-n", "dnf5", "group", "remove", "-y", TEST_GROUP], check=False)
    yield
    run(["sudo", "-n", "dnf5", "group", "remove", "-y", TEST_GROUP], check=False)


def test_install_and_remove_package(tmp_path: Path) -> None:
    install_root = write_bundle(
        tmp_path / "install",
        "dnf_install",
        """
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("install fixture package")
        def install_fixture(ctx):
            dnf.packages(ctx, ["kitup-test"])
        """,
    )
    assert run_kitup("apply", install_root) == 0
    assert rpm_installed()

    remove_root = write_bundle(
        tmp_path / "remove",
        "dnf_remove",
        """
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("remove fixture package")
        def remove_fixture(ctx):
            dnf.packages(ctx, ["kitup-test"], state="absent")
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert not rpm_installed()


def test_install_and_remove_group_and_package(
    tmp_path: Path,
    group_absent: None,
) -> None:
    install_root = write_bundle(
        tmp_path / "install",
        "dnf_group_install",
        f"""
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("install fixture group and package")
        def install_group(ctx):
            dnf.packages(ctx, ["@{TEST_GROUP}", {TEST_PACKAGE!r}])
        """,
    )
    assert run_kitup("apply", install_root) == 0
    assert rpm_installed()
    assert TEST_GROUP in run(["dnf5", "group", "list", "--installed", "--hidden"]).stdout

    remove_root = write_bundle(
        tmp_path / "remove",
        "dnf_group_remove",
        f"""
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("remove fixture group and package")
        def remove_group(ctx):
            dnf.packages(ctx, ["@{TEST_GROUP}", {TEST_PACKAGE!r}], state="absent")
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert not rpm_installed()
    assert TEST_GROUP not in run(["dnf5", "group", "list", "--installed", "--hidden"]).stdout


def test_install_and_remove_explicit_group(
    tmp_path: Path,
    group_absent: None,
) -> None:
    install_root = write_bundle(
        tmp_path / "install",
        "dnf_group_only_install",
        f"""
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("install fixture group")
        def install_group(ctx):
            dnf.packages(ctx, [{TEST_GROUP!r}], group=True)
        """,
    )
    assert run_kitup("apply", install_root) == 0
    assert rpm_installed()
    assert TEST_GROUP in run(["dnf5", "group", "list", "--installed", "--hidden"]).stdout

    remove_root = write_bundle(
        tmp_path / "remove",
        "dnf_group_only_remove",
        f"""
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("remove fixture group")
        def remove_group(ctx):
            dnf.packages(ctx, [{TEST_GROUP!r}], group=True, state="absent")
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert not rpm_installed()
    assert TEST_GROUP not in run(["dnf5", "group", "list", "--installed", "--hidden"]).stdout


def test_install_local_rpm(tmp_path: Path) -> None:
    root = write_bundle(
        tmp_path,
        "dnf_rpm",
        f"""
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("install local RPM")
        def install_rpm(ctx):
            dnf.rpm(ctx, {str(TEST_RPM)!r})
        """,
    )
    assert run_kitup("apply", root) == 0
    assert rpm_installed(TEST_PACKAGE)


def test_repository_add_and_remove(tmp_path: Path) -> None:
    repository_name = f"kitup-integration-{uuid.uuid4().hex[:8]}.repo"
    repository_file = tmp_path / repository_name
    repository_file.write_text(
        "[kitup-integration-test]\n"
        "name=kitup integration fixture\n"
        f"baseurl=file://{TEST_REPO}\n"
        "enabled=1\n"
        "gpgcheck=0\n",
        encoding="utf-8",
    )
    installed_path = dnf_reposdir() / repository_name
    run(["sudo", "-n", "rm", "-f", str(installed_path)], check=False)

    add_root = write_bundle(
        tmp_path / "add",
        "dnf_repository_add",
        f"""
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("add repository")
        def add_repository(ctx):
            dnf.repository(ctx, {str(repository_file)!r})
        """,
    )
    assert run_kitup("apply", add_root) == 0
    assert installed_path.is_file()

    remove_root = write_bundle(
        tmp_path / "remove",
        "dnf_repository_remove",
        f"""
        from . import bundle
        from kitup.modules import dnf

        @bundle.task("remove repository")
        def remove_repository(ctx):
            dnf.repository(ctx, state="absent", name={repository_name!r})
        """,
    )
    assert run_kitup("apply", remove_root) == 0
    assert not installed_path.exists()
