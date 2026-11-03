# `users`

Manage local user accounts, groups, and group membership.

```python
from kitup.modules import users
from kitup.registry import task
```

## `user(ctx, name, *, state="present", uid=None, group=None, home=None, shell=None, create_home=True, password=None)`

Ensure a local user account exists or is absent.

- `name` — login name.
- `state` — `"present"` or `"absent"`.
- `uid` — numeric UID. Applied only when creating a user; an existing user with
  a different UID fails.
- `group` — primary group, as a name or numeric GID.
- `home` — home directory. Must be an absolute path. On an existing user this
  changes the recorded home directory without moving files.
- `shell` — login shell. Must be an absolute path.
- `create_home` — create the home directory when creating a user. Ignored for
  existing users.
- `password` — pre-hashed password, for example a `$6$...` SHA-512 crypt hash.
  Plain text passwords are not accepted. Use `!` or `*` to disable password
  login.

`state="absent"` removes the account but leaves the home directory in place.
Remove files separately if needed.

Supplementary group membership is managed with `membership()`; `user()` does
not accept a group list.

```python
@task("Create deployment user", tags={"users"})
def create_deploy_user(ctx):
    users.group(ctx, "deploy")
    users.user(
        ctx,
        "deploy",
        group="deploy",
        home="/home/deploy",
        shell="/bin/bash",
        password="$6$...",
    )
    users.membership(ctx, "deploy", "docker")


@task("Remove old user", tags={"users"})
def remove_old_user(ctx):
    users.user(ctx, "olduser", state="absent")
```

## `group(ctx, name, *, state="present", gid=None)`

Ensure a local group exists or is absent.

- `name` — group name.
- `state` — `"present"` or `"absent"`.
- `gid` — numeric GID. Applied only when creating a group; an existing group
  with a different GID fails.

```python
@task("Create application group", tags={"users"})
def create_app_group(ctx):
    users.group(ctx, "app", gid=1500)


@task("Remove old group", tags={"users"})
def remove_old_group(ctx):
    users.group(ctx, "oldgroup", state="absent")
```

## `membership(ctx, user, group, *, state="present")`

Ensure a user is a supplementary member of a group.

- `user` — existing login name.
- `group` — existing group name.
- `state="present"` adds the user to the group's supplementary member list.
- `state="absent"` removes the user from the group's supplementary member
  list.

```python
@task("Grant docker access", tags={"users"})
def grant_docker_access(ctx):
    users.membership(ctx, "deploy", "docker")


@task("Revoke docker access", tags={"users"})
def revoke_docker_access(ctx):
    users.membership(ctx, "deploy", "docker", state="absent")
```

## Behavior

- A missing user or group is created; existing accounts are changed only when
  the requested attributes differ.
- Removing a user from their primary group fails; change the primary group with
  `user(..., group=...)` instead.
- Passwords are only changed when the stored hash differs. Password hashes are
  never shown in output.
- All mutating operations request sudo automatically. `--dry-run` reports what
  would change without modifying the system.

## Notes

- Requires the standard `shadow-utils` account tools.
- UID and GID are only applied when creating an account. Changing an existing
  UID or GID is rejected.
- `create_home` only applies when creating a user.
- Passwords must be pre-hashed. Generate one with `openssl passwd -6` or
  `mkpasswd -m sha-512`.
