# `users`

Manage local user accounts, groups, and group membership.

```python
from kitup.modules import users
```

## `user(name, *, state="present", uid=None, group=None, home=None, shell=None, create=True, create_home=True, password=None)`

Ensure a local user account exists or is absent.

### Parameters

- `name` — login name.
- `state` — `"present"` or `"absent"`.
- `uid` — numeric UID, applied only when creating a user.
- `group` — primary group, as a name or numeric GID.
- `home` — home directory. Must be an absolute path. On an existing user this
  changes the recorded home directory without moving files.
- `shell` — login shell. Must be an absolute path.
- `create` — create the user when it does not exist. With `create=False`, a
  missing user is left uncreated; existing users are still modified. Ignored
  for `state="absent"`.
- `create_home` — create the home directory when creating a user. Ignored for
  existing users.
- `password` — pre-hashed password, for example a `$6$...` SHA-512 crypt hash.
  Plain text passwords are not accepted. Use `!` or `*` to disable password
  login.

`state="absent"` removes the account but leaves the home directory in place.
Remove files separately if needed.

Supplementary group membership is managed with `membership()`; `user()` does
not accept a group list.

### Result fields

- `user` — login name.
- `changed_attributes` — changed attributes such as `group`, `home`, `shell`, or
  `password` (modify results only).

```python
users.group("deploy")
users.user(
    "deploy",
    group="deploy",
    home="/home/deploy",
    shell="/bin/bash",
    password="$6$...",
)
users.membership("deploy", "docker")
users.user("olduser", state="absent")
```

## `group(name, *, state="present", gid=None)`

Ensure a local group exists or is absent.

### Parameters

- `name` — group name.
- `state` — `"present"` or `"absent"`.
- `gid` — numeric GID, applied only when creating a group.

### Result fields

- `group` — group name.

```python
users.group("app", gid=1500)
users.group("oldgroup", state="absent")
```

## `membership(user, group, *, state="present")`

Ensure a user is a supplementary member of a group.

### Parameters

- `user` — existing login name.
- `group` — existing group name.
- `state="present"` adds the user to the group's supplementary member list.
- `state="absent"` removes the user from the group's supplementary member
  list.

### Result fields

- `user` — login name.
- `group` — group name.

```python
users.membership("deploy", "docker")
users.membership("deploy", "docker", state="absent")
```

## Behavior

- A missing user or group is created, unless `user(..., create=False)` is used,
  which does not create a missing user.
- A user cannot be removed from their primary group; change the primary group
  with `user(..., group=...)` instead.
- Password hashes are never shown in output.
- All mutating operations request sudo automatically. `--dry-run` reports what
  would change without modifying the system.

## Notes

- Requires the standard `shadow-utils` account tools.
- `create_home` only applies when creating a user.
- `create=False` only prevents creation; it still applies the requested
  attributes to an existing user.
- Passwords must be pre-hashed. Generate one with `openssl passwd -6` or
  `mkpasswd -m sha-512`.
