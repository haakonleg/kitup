"""Local user and group management."""

import grp
import pwd
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .._utils.paths import expand_path
from .._utils.validation import (
    State,
    require_bool,
    require_identity,
    require_non_negative_int,
    require_path,
    require_state,
    require_text,
)
from ..context import TaskContext
from ..errors import TaskFailed
from ..registry import module
from ..results import CallResult

module("users")


_NAME_PATTERN: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\$?")


@dataclass(frozen=True, slots=True)
class _User:
    """A local passwd entry."""

    name: str
    uid: int
    gid: int
    home: str
    shell: str


@dataclass(frozen=True, slots=True)
class _Group:
    """A local group entry."""

    gid: int
    members: tuple[str, ...]


def user(
    ctx: TaskContext,
    name: str,
    *,
    state: State = "present",
    uid: int | None = None,
    group: str | int | None = None,
    home: str | Path | None = None,
    shell: str | Path | None = None,
    create_home: bool = True,
    password: str | None = None,
) -> CallResult:
    """Ensure a local user account exists with the requested attributes.

    ``state="absent"`` removes the account but leaves the home directory.
    ``password`` must be a pre-hashed value such as a ``$6$...`` crypt hash.
    """
    require_state(state, "user")
    _require_name(name, "user name")
    if uid is not None:
        require_non_negative_int(uid, "uid")
    if group is not None:
        require_identity(group, "primary group")
        if isinstance(group, str):
            _require_name(group, "primary group")
    home_path = _optional_absolute_path(home, "home")
    shell_path = _optional_absolute_path(shell, "shell")
    require_bool(create_home, "create_home")
    if password is not None:
        _require_password(password)
        if state == "absent":
            raise TaskFailed("password is only supported when state is 'present'")

    current = _probe_user(name)
    if state == "absent":
        if current is None:
            return ctx.changed(changed=False, summary=f"ensure user {name} is absent", user=name)
        call = CallResult(changed=True, summary=f"remove user {name}", user=name)
        if ctx.dry_run:
            return ctx.changed(call)
        ctx.run(["userdel", name], sudo=True)
        if _probe_user(name) is not None:
            raise TaskFailed(f"user still exists after removal: {name}")
        return ctx.changed(call)

    desired_gid = _resolve_primary_gid(ctx, group) if group is not None else None
    if current is None:
        return ctx.changed(
            _create_user(
                ctx,
                name,
                uid=uid,
                group=group,
                gid=desired_gid,
                home=home_path,
                shell=shell_path,
                create_home=create_home,
                password=password,
            )
        )

    return ctx.changed(
        _modify_user(
            ctx,
            current,
            uid=uid,
            group=group,
            gid=desired_gid,
            home=home_path,
            shell=shell_path,
            password=password,
        )
    )


def group(ctx: TaskContext, name: str, *, state: State = "present", gid: int | None = None) -> CallResult:
    """Ensure a local group exists or is absent."""
    require_state(state, "group")
    _require_name(name, "group name")
    if gid is not None:
        require_non_negative_int(gid, "gid")

    current = _probe_group(name)
    if state == "absent":
        if current is None:
            return ctx.changed(changed=False, summary=f"ensure group {name} is absent", group=name)
        call = CallResult(changed=True, summary=f"remove group {name}", group=name)
        if ctx.dry_run:
            return ctx.changed(call)
        ctx.run(["groupdel", name], sudo=True)
        if _probe_group(name) is not None:
            raise TaskFailed(f"group still exists after removal: {name}")
        return ctx.changed(call)

    if current is None:
        command = ["groupadd"]
        if gid is not None:
            command.extend(["-g", str(gid)])
        command.append(name)
        call = CallResult(changed=True, summary=f"create group {name}", group=name)
        if ctx.dry_run:
            return ctx.changed(call)
        ctx.run(command, sudo=True)
        created = _probe_group(name)
        if created is None:
            raise TaskFailed(f"group was not created: {name}")
        if gid is not None and created.gid != gid:
            raise TaskFailed(f"group {name} has GID {created.gid}, expected {gid}")
        return ctx.changed(call)

    if gid is not None and gid != current.gid:
        raise TaskFailed(f"changing an existing GID is not supported: {name} has GID {current.gid}, requested {gid}")
    return ctx.changed(changed=False, summary=f"ensure group {name}", group=name)


def membership(ctx: TaskContext, user: str, group: str, *, state: State = "present") -> CallResult:
    """Ensure a user is a supplementary member of a group."""
    require_state(state, "group membership")
    _require_name(user, "user name")
    _require_name(group, "group name")

    account = _probe_user(user)
    if account is None:
        raise TaskFailed(f"user does not exist: {user}")
    target = _probe_group(group)
    if target is None:
        raise TaskFailed(f"group does not exist: {group}")

    if account.gid == target.gid:
        if state == "present":
            return ctx.changed(changed=False, summary=f"ensure {user} is in group {group}", user=user, group=group)
        raise TaskFailed(f"cannot remove {user} from primary group {group}; change the primary group with users.user()")

    is_member = user in target.members
    if state == "present":
        if is_member:
            return ctx.changed(changed=False, summary=f"ensure {user} is in group {group}", user=user, group=group)
        call = CallResult(changed=True, summary=f"add {user} to group {group}", user=user, group=group)
        if ctx.dry_run:
            return ctx.changed(call)
        ctx.run(["gpasswd", "-a", user, group], sudo=True)
        updated = _probe_group(group)
        if updated is None or user not in updated.members:
            raise TaskFailed(f"{user} is still not a member of group {group}")
        return ctx.changed(call)

    if not is_member:
        return ctx.changed(changed=False, summary=f"ensure {user} is not in group {group}", user=user, group=group)
    call = CallResult(changed=True, summary=f"remove {user} from group {group}", user=user, group=group)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(["gpasswd", "-d", user, group], sudo=True)
    updated = _probe_group(group)
    if updated is None or user in updated.members:
        raise TaskFailed(f"{user} is still a member of group {group}")
    return ctx.changed(call)


def _create_user(
    ctx: TaskContext,
    name: str,
    *,
    uid: int | None,
    group: str | int | None,
    gid: int | None,
    home: str | None,
    shell: str | None,
    create_home: bool,
    password: str | None,
) -> CallResult:
    command = ["useradd"]
    command.append("-m" if create_home else "-M")
    if uid is not None:
        command.extend(["-u", str(uid)])
    if group is not None:
        command.extend(["-g", str(group)])
    if home is not None:
        command.extend(["-d", home])
    if shell is not None:
        command.extend(["-s", shell])
    command.append(name)

    summary = f"create user {name}"
    if password is not None:
        summary = f"{summary}; set password for {name}"
    call = CallResult(changed=True, summary=summary, user=name)
    if ctx.dry_run:
        return call
    ctx.run(command, sudo=True)

    created = _probe_user(name)
    if created is None:
        raise TaskFailed(f"user was not created: {name}")
    _verify_user(created, uid=uid, gid=gid, home=home, shell=shell)
    if password is not None:
        _write_password(ctx, name, password)
        _verify_password(ctx, name, password)
    return call


def _modify_user(
    ctx: TaskContext,
    current: _User,
    *,
    uid: int | None,
    group: str | int | None,
    gid: int | None,
    home: str | None,
    shell: str | None,
    password: str | None,
) -> CallResult:
    if uid is not None and uid != current.uid:
        raise TaskFailed(
            f"changing an existing UID is not supported: {current.name} has UID {current.uid}, requested {uid}"
        )

    command = ["usermod"]
    fields: list[str] = []
    # A missing group during dry-run means an earlier task would have created
    # it; report the primary-group change without a GID to verify.
    if group is not None and (gid is None or gid != current.gid):
        command.extend(["-g", str(group)])
        fields.append("group")
    if home is not None and home != current.home:
        command.extend(["-d", home])
        fields.append("home")
    if shell is not None and shell != current.shell:
        command.extend(["-s", shell])
        fields.append("shell")

    password_changed = password is not None and _probe_shadow(ctx, current.name) != password
    changed_fields = [*fields, "password"] if password_changed else fields
    summaries: list[str] = []
    if fields:
        summaries.append(f"modify user {current.name}")
    if password_changed:
        summaries.append(f"set password for {current.name}")
    call = CallResult(
        changed=bool(changed_fields),
        summary="; ".join(summaries) if summaries else f"ensure user {current.name}",
        user=current.name,
        changed_attributes=changed_fields,
    )
    if ctx.dry_run:
        return call

    if fields:
        command.append(current.name)
        ctx.run(command, sudo=True)
        updated = _probe_user(current.name)
        if updated is None:
            raise TaskFailed(f"user disappeared after modification: {current.name}")
        _verify_user(
            updated,
            uid=None,
            gid=gid if "group" in fields else None,
            home=home if "home" in fields else None,
            shell=shell if "shell" in fields else None,
        )

    if password_changed and password is not None:
        _write_password(ctx, current.name, password)
        _verify_password(ctx, current.name, password)
    return call


def _write_password(ctx: TaskContext, name: str, password: str) -> None:
    # Keep the hash out of argv so it cannot leak through process listings or
    # command-failure output.
    ctx.run(["chpasswd", "-e"], stdin=f"{name}:{password}\n", sudo=True)


def _verify_password(ctx: TaskContext, name: str, password: str) -> None:
    if _probe_shadow(ctx, name) != password:
        raise TaskFailed(f"password was not updated for user: {name}")


def _verify_user(account: _User, *, uid: int | None, gid: int | None, home: str | None, shell: str | None) -> None:
    if uid is not None and account.uid != uid:
        raise TaskFailed(f"user {account.name} has UID {account.uid}, expected {uid}")
    if gid is not None and account.gid != gid:
        raise TaskFailed(f"user {account.name} has GID {account.gid}, expected {gid}")
    if home is not None and account.home != home:
        raise TaskFailed(f"user {account.name} has home {account.home}, expected {home}")
    if shell is not None and account.shell != shell:
        raise TaskFailed(f"user {account.name} has shell {account.shell}, expected {shell}")


def _probe_user(name: str) -> _User | None:
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return None
    except OSError as exc:
        raise TaskFailed(f"could not look up user {name}: {exc}") from exc
    return _User(name=entry.pw_name, uid=entry.pw_uid, gid=entry.pw_gid, home=entry.pw_dir, shell=entry.pw_shell)


def _probe_group(value: str | int) -> _Group | None:
    try:
        entry = _group_entry(value)
    except KeyError, OverflowError:
        return None
    except OSError as exc:
        raise TaskFailed(f"could not look up group {value}: {exc}") from exc
    return _Group(gid=entry.gr_gid, members=tuple(member for member in entry.gr_mem if member))


def _group_entry(value: str | int) -> grp.struct_group:
    """Return a group entry, resolving a numeric name as a GID like ``getent``."""
    if isinstance(value, int):
        return grp.getgrgid(value)
    try:
        return grp.getgrnam(value)
    except KeyError:
        if not value.isdigit():
            raise
        return grp.getgrgid(int(value))


def _probe_shadow(ctx: TaskContext, name: str) -> str | None:
    result = ctx.probe(["getent", "shadow", name], sudo=True)
    line = _getent_line(result, f"shadow entry for {name}")
    if line is None:
        return None
    fields = line.split(":")
    if len(fields) < 2:
        raise TaskFailed(
            f"could not parse shadow entry for {name}",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    return fields[1]


def _getent_line(result: subprocess.CompletedProcess[str], label: str) -> str | None:
    if result.returncode == 2:
        return None
    if result.returncode != 0:
        raise TaskFailed(
            f"could not look up {label}",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    line = result.stdout.strip()
    return line or None


def _resolve_primary_gid(ctx: TaskContext, group: str | int) -> int | None:
    target = _probe_group(group)
    if target is None:
        if ctx.dry_run:
            return None
        raise TaskFailed(f"group does not exist: {group}")
    return target.gid


def _require_name(value: str, label: str) -> None:
    require_text(value, label)
    if _NAME_PATTERN.fullmatch(value) is None:
        raise TaskFailed(f"invalid {label}: {value!r}")


def _optional_absolute_path(value: str | Path | None, label: str) -> str | None:
    if value is None:
        return None
    require_path(value, label)
    path = expand_path(value, error=TaskFailed)
    if not path.is_absolute():
        raise TaskFailed(f"{label} must be an absolute path: {value}")
    text = str(path)
    _reject_unsafe(text, label)
    return text


def _require_password(value: str) -> None:
    require_text(value, "password")
    _reject_unsafe(value, "password")


def _reject_unsafe(value: str, label: str) -> None:
    if any(char in value for char in ("\0", "\n", "\r", ":")):
        raise TaskFailed(f"{label} must not contain null bytes, newlines, or ':'")
