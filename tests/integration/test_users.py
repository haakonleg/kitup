"""Integration tests for real local user and group management."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from .helpers import run, run_kitup, sudo, write_bundle

pytestmark = pytest.mark.integration

USER = "kitup-it-user"
GROUP = "kitup-it-group"
OTHER_GROUP = "kitup-it-other"
HOME = Path(f"/home/{USER}")
ALT_HOME = Path(f"/srv/{USER}")
PASSWORD_HASH = "$6$kitup-test$LOewqL.rBYltdCYVJzzJvj2GJPwRIYtIRATKZWYBHuVQqgpiBQ4EmSwr926LJv.g5VmhlqkdCa6.DgSfJ8I420"


@pytest.fixture(autouse=True)
def accounts_absent() -> Iterator[None]:
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    run(sudo("userdel", "-r", USER), check=False)
    run(sudo("groupdel", OTHER_GROUP), check=False)
    run(sudo("groupdel", GROUP), check=False)
    run(sudo("rm", "-rf", str(HOME), str(ALT_HOME)), check=False)


def getent(database: str, key: str) -> str | None:
    result = run(["getent", database, key], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def user_exists(name: str = USER) -> bool:
    return getent("passwd", name) is not None


def group_exists(name: str = GROUP) -> bool:
    return getent("group", name) is not None


def shadow_hash(name: str = USER) -> str | None:
    result = run(sudo("getent", "shadow", name), check=False)
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split(":")
    return fields[1] if len(fields) > 1 else None


def group_members(name: str) -> set[str]:
    entry = getent("group", name)
    if entry is None:
        return set()
    fields = entry.split(":")
    if len(fields) < 4:
        return set()
    return {member for member in fields[3].split(",") if member}


def user_groups(name: str = USER) -> set[str]:
    result = run(["id", "-nG", name])
    return set(result.stdout.split())


def path_exists(path: Path) -> bool:
    return run(sudo("test", "-e", str(path)), check=False).returncode == 0


def free_id(database: str) -> int:
    result = run(["getent", database])
    used = {int(line.split(":")[2]) for line in result.stdout.splitlines() if line.strip()}
    for candidate in range(40000, 50000):
        if candidate not in used:
            return candidate
    raise RuntimeError(f"no free ID in {database}")


def test_create_user_and_group(tmp_path: Path, capsys) -> None:
    uid = free_id("passwd")
    gid = free_id("group")
    root = write_bundle(
        tmp_path / "create",
        "users_create",
        f"""
        from kitup.modules import users

        @task("create group")
        def create_group(ctx):
            users.group(ctx, {GROUP!r}, gid={gid})

        @task("create user")
        def create_user(ctx):
            users.user(
                ctx,
                {USER!r},
                uid={uid},
                group={GROUP!r},
                home={str(HOME)!r},
                shell="/bin/bash",
                password={PASSWORD_HASH!r},
            )
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "2 changed" in capsys.readouterr().out
    assert group_exists()
    assert user_exists()
    assert shadow_hash() == PASSWORD_HASH
    assert path_exists(HOME)

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_membership_add_and_remove(tmp_path: Path, capsys) -> None:
    root = write_bundle(
        tmp_path / "membership",
        "users_membership",
        f"""
        from kitup.modules import users

        @task("create user and groups")
        def create(ctx):
            users.group(ctx, {GROUP!r})
            users.group(ctx, {OTHER_GROUP!r})
            users.user(ctx, {USER!r}, group={GROUP!r}, create_home=False)
            users.membership(ctx, {USER!r}, {OTHER_GROUP!r})
        """,
    )

    assert run_kitup("apply", root) == 0
    assert user_groups() >= {GROUP, OTHER_GROUP}

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out

    remove_root = write_bundle(
        tmp_path / "membership_remove",
        "users_membership_remove",
        f"""
        from kitup.modules import users

        @task("remove membership")
        def remove(ctx):
            users.membership(ctx, {USER!r}, {OTHER_GROUP!r}, state="absent")
        """,
    )

    assert run_kitup("apply", remove_root) == 0
    assert OTHER_GROUP not in user_groups()
    assert group_members(OTHER_GROUP) == set()


def test_password_is_idempotent(tmp_path: Path, capsys) -> None:
    create_root = write_bundle(
        tmp_path / "password_create",
        "users_password_create",
        f"""
        from kitup.modules import users

        @task("create user")
        def create(ctx):
            users.user(ctx, {USER!r}, create_home=False)
        """,
    )
    assert run_kitup("apply", create_root) == 0
    assert shadow_hash() != PASSWORD_HASH

    password_root = write_bundle(
        tmp_path / "password_set",
        "users_password_set",
        f"""
        from kitup.modules import users

        @task("set password")
        def set_password(ctx):
            users.user(ctx, {USER!r}, password={PASSWORD_HASH!r})
        """,
    )

    assert run_kitup("apply", password_root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert shadow_hash() == PASSWORD_HASH

    assert run_kitup("apply", password_root) == 0
    assert "0 changed" in capsys.readouterr().out
    assert shadow_hash() == PASSWORD_HASH


def test_modify_user_shell_and_home(tmp_path: Path, capsys) -> None:
    create_root = write_bundle(
        tmp_path / "modify_create",
        "users_modify_create",
        f"""
        from kitup.modules import users

        @task("create user")
        def create(ctx):
            users.user(
                ctx,
                {USER!r},
                home={str(HOME)!r},
                shell="/bin/bash",
                create_home=True,
            )
        """,
    )
    assert run_kitup("apply", create_root) == 0
    assert path_exists(HOME)

    modify_root = write_bundle(
        tmp_path / "modify",
        "users_modify",
        f"""
        from kitup.modules import users

        @task("modify user")
        def modify(ctx):
            users.user(ctx, {USER!r}, home={str(ALT_HOME)!r}, shell="/bin/zsh")
        """,
    )

    assert run_kitup("apply", modify_root) == 0
    entry = getent("passwd", USER)
    assert entry is not None
    fields = entry.split(":")
    assert fields[5] == str(ALT_HOME)
    assert fields[6] == "/bin/zsh"
    assert path_exists(HOME)
    assert not path_exists(ALT_HOME)

    assert run_kitup("apply", modify_root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_remove_user_leaves_home(tmp_path: Path) -> None:
    create_root = write_bundle(
        tmp_path / "remove_create",
        "users_remove_create",
        f"""
        from kitup.modules import users

        @task("create user")
        def create(ctx):
            users.user(ctx, {USER!r}, home={str(HOME)!r}, create_home=True)
        """,
    )
    assert run_kitup("apply", create_root) == 0
    assert path_exists(HOME)

    remove_root = write_bundle(
        tmp_path / "remove",
        "users_remove",
        f"""
        from kitup.modules import users

        @task("remove user")
        def remove(ctx):
            users.user(ctx, {USER!r}, state="absent")
        """,
    )

    assert run_kitup("apply", remove_root) == 0
    assert not user_exists()
    assert path_exists(HOME)


def test_remove_group(tmp_path: Path) -> None:
    create_root = write_bundle(
        tmp_path / "group_create",
        "users_group_create",
        f"""
        from kitup.modules import users

        @task("create group")
        def create(ctx):
            users.group(ctx, {GROUP!r})
        """,
    )
    assert run_kitup("apply", create_root) == 0
    assert group_exists()

    remove_root = write_bundle(
        tmp_path / "group_remove",
        "users_group_remove",
        f"""
        from kitup.modules import users

        @task("remove group")
        def remove(ctx):
            users.group(ctx, {GROUP!r}, state="absent")
        """,
    )

    assert run_kitup("apply", remove_root) == 0
    assert not group_exists()


def test_dry_run_does_not_create(tmp_path: Path) -> None:
    root = write_bundle(
        tmp_path / "dry",
        "users_dry",
        f"""
        from kitup.modules import users

        @task("create user and group")
        def create(ctx):
            users.group(ctx, {GROUP!r})
            users.user(ctx, {USER!r}, group={GROUP!r}, create_home=False)
        """,
    )

    assert run_kitup("apply", root, "--dry-run") == 0
    assert not group_exists()
    assert not user_exists()


def test_remove_primary_group_membership_fails(tmp_path: Path, capsys) -> None:
    root = write_bundle(
        tmp_path / "primary",
        "users_primary",
        f"""
        from kitup.modules import users

        @task("create user and remove primary membership")
        def create(ctx):
            users.group(ctx, {GROUP!r})
            users.user(ctx, {USER!r}, group={GROUP!r}, create_home=False)
            users.membership(ctx, {USER!r}, {GROUP!r}, state="absent")
        """,
    )

    assert run_kitup("apply", root) == 1
    assert "primary group" in capsys.readouterr().out
