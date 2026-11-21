import pytest

from kitup.errors import TaskFailed
from kitup.modules import flatpak

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response

REMOTE = "flathub"
REMOTE_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
APP = "org.example.App"
APP_REF = "org.example.App/x86_64/stable"
RUNTIME = "org.example.Runtime"
RUNTIME_REF = "org.example.Runtime/x86_64/stable"

REMOTES = ["flatpak", "remotes", "--system", "--columns=name"]
LIST = ["flatpak", "list", "--system", "--columns=application,ref"]


@pytest.fixture(autouse=True)
def flatpak_available(monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )


def installed(*entries: tuple[str, str]) -> str:
    return "".join(f"{application}\t{ref}\n" for application, ref in entries)


def test_remote_present_adds_missing_remote_and_noops_or_dry_runs(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(REMOTE, REMOTE_URL)

    assert argvs(command) == [
        REMOTES,
        [
            "flatpak",
            "remote-add",
            "--if-not-exists",
            "--system",
            REMOTE,
            REMOTE_URL,
        ],
    ]
    assert command.calls[0][1]["sudo"] is False
    assert command.calls[1][1]["sudo"] is True
    assert call_data(ctx.changes[0]) == {
        "remote": REMOTE,
        "url": REMOTE_URL,
        "scope": "system",
    }
    assert ctx.changes[0].action == "flatpak.remote"
    assert ctx.changes[0].summary == f"add remote {REMOTE}"

    command = FakeCommandRunner()
    command.responses = [response(0, f"{REMOTE}\n")]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(REMOTE, "https://example.test/other.flatpakrepo")

    assert argvs(command) == [REMOTES]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    flatpak.remote(REMOTE, REMOTE_URL)

    assert argvs(command) == [REMOTES]
    assert len(ctx.changes) == 1

    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    make_context(tmp_path, command=command)

    flatpak.remote(REMOTE, REMOTE_URL, gpg_verify=False)

    assert command.calls[1][0] == [
        "flatpak",
        "remote-add",
        "--if-not-exists",
        "--system",
        "--no-gpg-verify",
        REMOTE,
        REMOTE_URL,
    ]


def test_remote_absent_removes_existing_and_noops_when_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, f"{REMOTE}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(REMOTE, state="absent")

    assert command.calls[1][0] == [
        "flatpak",
        "remote-delete",
        "--system",
        REMOTE,
    ]
    assert command.calls[1][1]["sudo"] is True
    assert call_data(ctx.changes[0]) == {"remote": REMOTE, "scope": "system"}
    assert ctx.changes[0].summary == f"remove remote {REMOTE}"

    command = FakeCommandRunner()
    command.responses = [response(0, "other\n")]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(REMOTE, state="absent")

    assert argvs(command) == [REMOTES]
    assert ctx.changes == []


def test_invalid_arguments_are_rejected(tmp_path):
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="URL is required"):
        flatpak.remote(REMOTE)
    with pytest.raises(TaskFailed, match="remote name must not be empty"):
        flatpak.remote("  ", REMOTE_URL)
    with pytest.raises(TaskFailed, match="remote URL must not be empty"):
        flatpak.remote(REMOTE, "  ")
    with pytest.raises(TaskFailed, match="invalid flatpak state"):
        flatpak.remote(REMOTE, REMOTE_URL, state="invalid")
    with pytest.raises(TaskFailed, match="invalid flatpak scope"):
        flatpak.remote(REMOTE, REMOTE_URL, scope="session")
    with pytest.raises(TaskFailed, match="flatpak gpg_verify must be a boolean"):
        flatpak.remote(REMOTE, REMOTE_URL, gpg_verify="yes")

    with pytest.raises(TaskFailed, match="invalid flatpak state"):
        flatpak.packages([APP], state="invalid")
    with pytest.raises(TaskFailed, match="invalid flatpak scope"):
        flatpak.packages([APP], scope="session")
    with pytest.raises(TaskFailed, match="flatpak refs must not be empty"):
        flatpak.packages([])
    with pytest.raises(TaskFailed, match="flatpak ref must not be empty"):
        flatpak.packages("")
    with pytest.raises(TaskFailed, match="flatpak ref must not be empty"):
        flatpak.packages([APP, "  "])
    with pytest.raises(TaskFailed, match="flatpak remote must not be empty"):
        flatpak.packages([APP], remote="  ")


def test_packages_present_installs_missing_refs_and_noops_when_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, installed((APP, APP_REF)))]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages([APP])

    assert argvs(command) == [LIST]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0),
        response(0, installed((APP, APP_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages([APP], remote=REMOTE)

    assert command.calls[1][0] == [
        "flatpak",
        "install",
        "-y",
        "--noninteractive",
        "--system",
        REMOTE,
        APP,
    ]
    assert command.calls[1][1]["sudo"] is True
    assert command.calls[2][0] == LIST
    assert call_data(ctx.changes[0]) == {"added": [APP], "scope": "system"}
    assert ctx.changes[0].summary == f"+{APP}"


def test_packages_present_installs_only_missing_refs_and_matches_full_refs(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF))),
        response(0),
        response(0, installed((APP, APP_REF), (RUNTIME, RUNTIME_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages([APP, RUNTIME])

    assert command.calls[1][0] == [
        "flatpak",
        "install",
        "-y",
        "--noninteractive",
        "--system",
        RUNTIME,
    ]
    assert call_data(ctx.changes[0]) == {"added": [RUNTIME], "scope": "system"}

    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF), (RUNTIME, RUNTIME_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages([f"app/{APP_REF}", f"runtime/{RUNTIME_REF}"])

    assert argvs(command) == [LIST]
    assert ctx.changes == []


def test_packages_absent_removes_refs_all_branches_and_noops_when_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF))),
        response(0),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages([APP], state="absent")

    assert command.calls[1][0] == [
        "flatpak",
        "uninstall",
        "-y",
        "--noninteractive",
        "--system",
        APP_REF,
    ]
    assert command.calls[1][1]["sudo"] is True
    assert call_data(ctx.changes[0]) == {"removed": [APP_REF], "scope": "system"}
    assert ctx.changes[0].summary == f"-{APP_REF}"

    command = FakeCommandRunner()
    command.responses = [
        response(
            0,
            installed(
                (APP, APP_REF),
                (APP, "org.example.App/x86_64/beta"),
            ),
        ),
        response(0),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages([APP], state="absent")

    assert command.calls[1][0] == [
        "flatpak",
        "uninstall",
        "-y",
        "--noninteractive",
        "--system",
        APP_REF,
        "org.example.App/x86_64/beta",
    ]
    assert call_data(ctx.changes[0]) == {
        "removed": [APP_REF, "org.example.App/x86_64/beta"],
        "scope": "system",
    }

    command = FakeCommandRunner()
    command.responses = [response(0, installed((RUNTIME, RUNTIME_REF)))]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages([APP], state="absent")

    assert argvs(command) == [LIST]
    assert ctx.changes == []


def test_packages_absent_rejects_remote(tmp_path):
    make_context(tmp_path)

    with pytest.raises(TaskFailed, match="remote is only supported"):
        flatpak.packages([APP], state="absent", remote=REMOTE)


def test_user_scope_uses_user_flag_without_sudo(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0),
        response(0),
        response(0),
        response(0, installed((APP, APP_REF))),
    ]
    make_context(tmp_path, command=command)

    flatpak.remote(REMOTE, REMOTE_URL, scope="user")
    flatpak.packages([APP], scope="user")

    assert command.calls[0][0] == [
        "flatpak",
        "remotes",
        "--user",
        "--columns=name",
    ]
    assert command.calls[1][0] == [
        "flatpak",
        "remote-add",
        "--if-not-exists",
        "--user",
        REMOTE,
        REMOTE_URL,
    ]
    assert command.calls[1][1]["sudo"] is False
    assert command.calls[2][0] == [
        "flatpak",
        "list",
        "--user",
        "--columns=application,ref",
    ]
    assert command.calls[3][1]["sudo"] is False


def test_user_scope_refuses_root(tmp_path):
    make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="cannot run as root"):
        flatpak.remote(REMOTE, REMOTE_URL, scope="user")
    with pytest.raises(TaskFailed, match="cannot run as root"):
        flatpak.packages([APP], scope="user")


def test_install_and_removal_verification_failures_are_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(0)]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing after install"):
        flatpak.packages([APP])

    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF))),
        response(0),
        response(0, installed((APP, APP_REF))),
    ]
    make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still installed after removal"):
        flatpak.packages([APP], state="absent")
