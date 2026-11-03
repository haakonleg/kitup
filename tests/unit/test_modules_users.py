import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from kitup.errors import TaskFailed
from kitup.modules import users

from .helpers import FakeCommandRunner, completed, make_context

PASSWORD = "$6$kitup-test$LOewqL.rBYltdCYVJzzJvj2GJPwRIYtIRATKZWYBHuVQqgpiBQ4EmSwr926LJv.g5VmhlqkdCa6.DgSfJ8I420"


class FakeAccounts(FakeCommandRunner):
    """Stateful fake for the account commands used by the users module."""

    def __init__(self) -> None:
        super().__init__()
        self.users: dict[str, dict[str, object]] = {}
        self.groups: dict[str, dict[str, object]] = {}
        self.shadows: dict[str, str] = {}
        self.fail: dict[str, str] = {}

    def add_user(
        self,
        name: str,
        *,
        uid: int,
        gid: int,
        home: str,
        shell: str,
        password: str | None = None,
    ) -> None:
        self.users[name] = {"uid": uid, "gid": gid, "home": home, "shell": shell}
        if password is not None:
            self.shadows[name] = password

    def add_group(self, name: str, *, gid: int, members: tuple[str, ...] = ()) -> None:
        self.groups[name] = {"gid": gid, "members": list(members)}

    def run(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = [str(part) for part in argv]
        self.calls.append((command, kwargs))
        if command[0] in self.fail:
            raise TaskFailed("command failed", command=command, stderr=self.fail[command[0]])
        if command[0] == "getent":
            return self._getent(command)
        if command[0] == "useradd":
            return self._useradd(command)
        if command[0] == "usermod":
            return self._usermod(command)
        if command[0] == "userdel":
            return self._userdel(command)
        if command[0] == "groupadd":
            return self._groupadd(command)
        if command[0] == "groupdel":
            return self._groupdel(command)
        if command[0] == "gpasswd":
            return self._gpasswd(command)
        if command[0] == "chpasswd":
            return self._chpasswd(command, kwargs.get("stdin"))
        return completed(command, 1, "", f"unexpected command: {command}")

    def _getent(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        database, key = command[1], command[2]
        if database == "passwd":
            user = self.users.get(key)
            if user is None:
                return completed(command, 2)
            line = f"{key}:x:{user['uid']}:{user['gid']}::{user['home']}:{user['shell']}"
            return completed(command, 0, f"{line}\n")
        if database == "group":
            group = self.groups.get(key)
            if group is None and key.isdigit():
                for name, candidate in self.groups.items():
                    if candidate["gid"] == int(key):
                        key, group = name, candidate
                        break
            if group is None:
                return completed(command, 2)
            members = ",".join(group["members"])
            return completed(command, 0, f"{key}:x:{group['gid']}:{members}\n")
        if database == "shadow":
            shadow = self.shadows.get(key)
            if shadow is None:
                return completed(command, 2)
            return completed(command, 0, f"{key}:{shadow}:19000:0:99999:7:::\n")
        return completed(command, 2)

    def _useradd(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        name = command[-1]
        options = self._options(command)
        uid = int(options["-u"]) if "-u" in options else self._next_uid()
        gid = self._gid(options["-g"]) if "-g" in options else 1000
        home = options.get("-d", f"/home/{name}")
        shell = options.get("-s", "/bin/bash")
        self.users[name] = {"uid": uid, "gid": gid, "home": home, "shell": shell}
        if "-p" in options:
            self.shadows[name] = options["-p"]
        return completed(command)

    def _usermod(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        name = command[-1]
        user = self.users[name]
        options = self._options(command)
        if "-g" in options:
            user["gid"] = self._gid(options["-g"])
        if "-d" in options:
            user["home"] = options["-d"]
        if "-s" in options:
            user["shell"] = options["-s"]
        if "-p" in options:
            self.shadows[name] = options["-p"]
        return completed(command)

    def _userdel(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        name = command[-1]
        self.users.pop(name, None)
        self.shadows.pop(name, None)
        return completed(command)

    def _groupadd(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        name = command[-1]
        options = self._options(command)
        gid = int(options["-g"]) if "-g" in options else self._next_gid()
        self.groups[name] = {"gid": gid, "members": []}
        return completed(command)

    def _groupdel(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        name = command[-1]
        group = self.groups[name]
        for user, record in self.users.items():
            if record["gid"] == group["gid"]:
                raise TaskFailed(
                    "command failed",
                    command=command,
                    stderr=f"cannot remove the primary group of user {user}",
                )
        self.groups.pop(name, None)
        return completed(command)

    def _gpasswd(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        action, user, group = command[1], command[2], command[3]
        members = self.groups[group]["members"]
        if action == "-a" and user not in members:
            members.append(user)
        elif action == "-d" and user in members:
            members.remove(user)
        return completed(command)

    def _chpasswd(self, command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        if not stdin:
            return completed(command, 1, "", "missing stdin")
        name, separator, password = stdin.strip().partition(":")
        if not separator or not name or not password:
            return completed(command, 1, "", "invalid stdin")
        self.shadows[name] = password
        return completed(command)

    def _options(self, command: list[str]) -> dict[str, str]:
        options: dict[str, str] = {}
        index = 1
        while index < len(command) - 1:
            option = command[index]
            if option in {"-u", "-g", "-d", "-s", "-p"}:
                options[option] = command[index + 1]
                index += 2
            else:
                index += 1
        return options

    def _gid(self, value: str) -> int:
        if value.isdigit():
            return int(value)
        return int(self.groups[value]["gid"])

    def _next_uid(self) -> int:
        return max((int(record["uid"]) for record in self.users.values()), default=1000) + 1

    def _next_gid(self) -> int:
        return max((int(record["gid"]) for record in self.groups.values()), default=1000) + 1


def make_users_context(
    tmp_path,
    *,
    dry_run: bool = False,
    accounts: FakeAccounts | None = None,
) -> tuple[Any, FakeAccounts]:
    store = accounts or FakeAccounts()
    return make_context(tmp_path, dry_run=dry_run, command=store), store


def commands(store: FakeAccounts) -> list[list[str]]:
    return [command for command, _ in store.calls]


def test_create_user_with_attributes(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, accounts=store)

    users.user(
        ctx,
        "alice",
        uid=1001,
        group="devs",
        home="/home/alice",
        shell="/bin/zsh",
        password=PASSWORD,
    )

    assert store.users["alice"] == {
        "uid": 1001,
        "gid": 1500,
        "home": "/home/alice",
        "shell": "/bin/zsh",
    }
    assert store.shadows["alice"] == PASSWORD
    assert ctx.changes[0].data == {"user": "alice", "action": "create"}
    assert ctx.changes[0].summary == "create user alice"
    assert commands(store) == [
        ["getent", "passwd", "alice"],
        ["getent", "group", "devs"],
        ["useradd", "-m", "-u", "1001", "-g", "devs", "-d", "/home/alice", "-s", "/bin/zsh", "alice"],
        ["getent", "passwd", "alice"],
        ["chpasswd", "-e"],
        ["getent", "shadow", "alice"],
    ]
    assert store.calls[2][1]["sudo"] is True
    assert store.calls[4][1]["stdin"] == f"alice:{PASSWORD}\n"
    assert all(PASSWORD not in command for command in commands(store))


def test_create_user_without_home(tmp_path):
    ctx, store = make_users_context(tmp_path)

    users.user(ctx, "alice", create_home=False)

    assert ["useradd", "-M", "alice"] in commands(store)
    assert store.users["alice"]["home"] == "/home/alice"


def test_create_user_with_numeric_primary_group(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, accounts=store)

    users.user(ctx, "alice", group=1500, create_home=False)

    assert store.users["alice"]["gid"] == 1500
    assert ["getent", "group", "1500"] in commands(store)


def test_create_user_is_idempotent(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, accounts=store)
    arguments = {
        "uid": 1001,
        "group": "devs",
        "home": "/home/alice",
        "shell": "/bin/zsh",
        "password": PASSWORD,
    }

    users.user(ctx, "alice", **arguments)
    before = len(store.calls)
    second, _ = make_users_context(tmp_path, accounts=store)
    users.user(second, "alice", **arguments)

    assert second.changes == []
    assert commands(store)[before:] == [
        ["getent", "passwd", "alice"],
        ["getent", "group", "devs"],
        ["getent", "shadow", "alice"],
    ]


def test_modify_user_attributes(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1000, home="/home/alice", shell="/bin/bash")
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, accounts=store)

    users.user(ctx, "alice", group="devs", home="/srv/alice", shell="/bin/zsh")

    assert store.users["alice"] == {
        "uid": 1001,
        "gid": 1500,
        "home": "/srv/alice",
        "shell": "/bin/zsh",
    }
    assert ctx.changes[0].data == {
        "user": "alice",
        "action": "modify",
        "fields": ["group", "home", "shell"],
    }
    assert ["usermod", "-g", "devs", "-d", "/srv/alice", "-s", "/bin/zsh", "alice"] in commands(store)


def test_existing_user_uid_mismatch_fails(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1000, home="/home/alice", shell="/bin/bash")
    ctx, store = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed, match="changing an existing UID is not supported"):
        users.user(ctx, "alice", uid=1002)

    assert not any(command[0] == "usermod" for command in commands(store))


def test_matching_uid_is_noop(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1000, home="/home/alice", shell="/bin/bash")
    ctx, _ = make_users_context(tmp_path, accounts=store)

    users.user(ctx, "alice", uid=1001)

    assert ctx.changes == []


def test_password_is_set_only_when_different(tmp_path):
    store = FakeAccounts()
    store.add_user(
        "alice",
        uid=1001,
        gid=1000,
        home="/home/alice",
        shell="/bin/bash",
        password="$6$old",
    )
    ctx, store = make_users_context(tmp_path, accounts=store)

    users.user(ctx, "alice", password=PASSWORD)

    assert store.shadows["alice"] == PASSWORD
    assert ctx.changes[0].data == {"user": "alice", "action": "password"}
    assert PASSWORD not in str(ctx.changes)
    assert all(PASSWORD not in command for command in commands(store))
    assert ["chpasswd", "-e"] in commands(store)

    before = len(store.calls)
    second, _ = make_users_context(tmp_path, accounts=store)
    users.user(second, "alice", password=PASSWORD)

    assert second.changes == []
    assert commands(store)[before:] == [
        ["getent", "passwd", "alice"],
        ["getent", "shadow", "alice"],
    ]


def test_remove_user_leaves_home(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1000, home="/home/alice", shell="/bin/bash")
    ctx, store = make_users_context(tmp_path, accounts=store)

    users.user(ctx, "alice", state="absent")

    assert "alice" not in store.users
    assert ctx.changes[0].data == {"user": "alice", "action": "remove"}
    assert ctx.changes[0].summary == "remove user alice"
    assert ["userdel", "alice"] in commands(store)


def test_remove_missing_user_is_noop(tmp_path):
    ctx, store = make_users_context(tmp_path)

    users.user(ctx, "alice", state="absent")

    assert ctx.changes == []
    assert not any(command[0] == "userdel" for command in commands(store))


def test_create_group(tmp_path):
    ctx, store = make_users_context(tmp_path)

    users.group(ctx, "devs", gid=1500)

    assert store.groups["devs"] == {"gid": 1500, "members": []}
    assert ctx.changes[0].data == {"group": "devs", "action": "create"}
    assert ["groupadd", "-g", "1500", "devs"] in commands(store)


def test_create_group_without_gid(tmp_path):
    ctx, store = make_users_context(tmp_path)

    users.group(ctx, "devs")

    assert ["groupadd", "devs"] in commands(store)


def test_create_group_is_idempotent(tmp_path):
    ctx, store = make_users_context(tmp_path)

    users.group(ctx, "devs", gid=1500)
    before = len(store.calls)
    second, _ = make_users_context(tmp_path, accounts=store)
    users.group(second, "devs", gid=1500)

    assert second.changes == []
    assert commands(store)[before:] == [["getent", "group", "devs"]]


def test_existing_group_gid_mismatch_fails(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, _ = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed, match="changing an existing GID is not supported"):
        users.group(ctx, "devs", gid=1600)


def test_matching_gid_is_noop(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, _ = make_users_context(tmp_path, accounts=store)

    users.group(ctx, "devs", gid=1500)

    assert ctx.changes == []


def test_remove_group(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, accounts=store)

    users.group(ctx, "devs", state="absent")

    assert "devs" not in store.groups
    assert ctx.changes[0].data == {"group": "devs", "action": "remove"}
    assert ["groupdel", "devs"] in commands(store)


def test_remove_missing_group_is_noop(tmp_path):
    ctx, store = make_users_context(tmp_path)

    users.group(ctx, "devs", state="absent")

    assert ctx.changes == []
    assert not any(command[0] == "groupdel" for command in commands(store))


def test_remove_primary_group_fails(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1500, home="/home/alice", shell="/bin/bash")
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed, match="command failed"):
        users.group(ctx, "devs", state="absent")

    assert "devs" in store.groups


def test_membership_add_and_remove(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1000, home="/home/alice", shell="/bin/bash")
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, accounts=store)

    users.membership(ctx, "alice", "devs")

    assert store.groups["devs"]["members"] == ["alice"]
    assert ctx.changes[0].data == {"user": "alice", "group": "devs", "action": "add"}

    before = len(store.calls)
    second, _ = make_users_context(tmp_path, accounts=store)
    users.membership(second, "alice", "devs")

    assert second.changes == []
    assert commands(store)[before:] == [
        ["getent", "passwd", "alice"],
        ["getent", "group", "devs"],
    ]

    third, _ = make_users_context(tmp_path, accounts=store)
    users.membership(third, "alice", "devs", state="absent")

    assert store.groups["devs"]["members"] == []
    assert third.changes[0].data == {"user": "alice", "group": "devs", "action": "remove"}


def test_membership_primary_group_present_is_noop(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1500, home="/home/alice", shell="/bin/bash")
    store.add_group("devs", gid=1500)
    ctx, _ = make_users_context(tmp_path, accounts=store)

    users.membership(ctx, "alice", "devs")

    assert ctx.changes == []


def test_membership_primary_group_absent_fails(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1500, home="/home/alice", shell="/bin/bash")
    store.add_group("devs", gid=1500)
    ctx, _ = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed, match="primary group"):
        users.membership(ctx, "alice", "devs", state="absent")


def test_membership_missing_user_fails(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, _ = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed, match="user does not exist"):
        users.membership(ctx, "alice", "devs")


def test_membership_missing_group_fails(tmp_path):
    store = FakeAccounts()
    store.add_user("alice", uid=1001, gid=1000, home="/home/alice", shell="/bin/bash")
    ctx, _ = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed, match="group does not exist"):
        users.membership(ctx, "alice", "devs")


def test_dry_run_reports_without_mutating(tmp_path):
    store = FakeAccounts()
    store.add_group("devs", gid=1500)
    ctx, store = make_users_context(tmp_path, dry_run=True, accounts=store)

    users.user(
        ctx,
        "alice",
        uid=1001,
        group="devs",
        home="/home/alice",
        shell="/bin/zsh",
        password=PASSWORD,
    )
    users.group(ctx, "newgroup", gid=1600)

    assert ctx.changes[0].data == {"user": "alice", "action": "create"}
    assert ctx.changes[1].data == {"group": "newgroup", "action": "create"}
    assert "alice" not in store.users
    assert "newgroup" not in store.groups
    assert not any(command[0] in {"useradd", "groupadd", "chpasswd"} for command in commands(store))


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda ctx: users.user(ctx, ""), "user name must not be empty"),
        (lambda ctx: users.user(ctx, "-bad"), "invalid user name"),
        (lambda ctx: users.user(ctx, "alice", uid=-1), "uid must be a non-negative integer"),
        (lambda ctx: users.user(ctx, "alice", home="relative"), "home must be an absolute path"),
        (lambda ctx: users.user(ctx, "alice", shell="relative"), "shell must be an absolute path"),
        (lambda ctx: users.user(ctx, "alice", password="bad\nhash"), "password must not contain"),
        (lambda ctx: users.user(ctx, "alice", password="bad:hash"), "password must not contain"),
        (
            lambda ctx: users.user(ctx, "alice", password=PASSWORD, state="absent"),
            "password is only supported",
        ),
        (lambda ctx: users.user(ctx, "alice", create_home="yes"), "create_home must be a boolean"),
        (lambda ctx: users.user(ctx, "alice", group="bad group"), "invalid primary group"),
        (lambda ctx: users.group(ctx, ""), "group name must not be empty"),
        (lambda ctx: users.group(ctx, "devs", gid=-1), "gid must be a non-negative integer"),
        (lambda ctx: users.membership(ctx, "", "devs"), "user name must not be empty"),
        (lambda ctx: users.membership(ctx, "alice", ""), "group name must not be empty"),
    ],
)
def test_invalid_arguments_are_rejected(tmp_path, call, message):
    ctx, _ = make_users_context(tmp_path)

    with pytest.raises(TaskFailed, match=message):
        call(ctx)


def test_command_failure_propagates(tmp_path):
    store = FakeAccounts()
    store.fail["useradd"] = "useradd: cannot create user"
    ctx, _ = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed, match="command failed"):
        users.user(ctx, "alice")


def test_password_failure_does_not_leak_hash(tmp_path):
    store = FakeAccounts()
    store.add_user(
        "alice",
        uid=1001,
        gid=1000,
        home="/home/alice",
        shell="/bin/bash",
        password="$6$old",
    )
    store.fail["chpasswd"] = "chpasswd: failure"
    ctx, _ = make_users_context(tmp_path, accounts=store)

    with pytest.raises(TaskFailed) as error:
        users.user(ctx, "alice", password=PASSWORD)

    assert PASSWORD not in str(error.value)
    assert PASSWORD not in str(error.value.command)
    assert PASSWORD not in str(error.value.stderr)


def test_malformed_passwd_output_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [lambda argv: completed(argv, 0, "bad\n")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not parse passwd entry"):
        users.user(ctx, "alice")


def test_malformed_group_output_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [lambda argv: completed(argv, 0, "bad\n")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not parse group entry"):
        users.group(ctx, "devs")


def test_getent_failure_raises(tmp_path):
    command = FakeCommandRunner()
    command.responses = [lambda argv: completed(argv, 1, "", "boom")]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not look up user alice"):
        users.user(ctx, "alice")
