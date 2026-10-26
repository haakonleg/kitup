from __future__ import annotations

import pytest

from kitup.errors import TaskFailed
from kitup.modules import flatpak

from .helpers import FakeCommandRunner, FakeSudo, completed, make_context

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
        "kitup.modules.flatpak.shutil.which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


def installed(*entries: tuple[str, str]) -> str:
    return "".join(f"{application}\t{ref}\n" for application, ref in entries)


def test_remote_present_adds_missing_remote(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(ctx, REMOTE, REMOTE_URL)

    assert [call[0] for call in command.calls] == [
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
    assert ctx.changes[0].data == {
        "remote": REMOTE,
        "url": REMOTE_URL,
        "scope": "system",
    }
    assert ctx.changes[0].summary == f"add remote {REMOTE}"


def test_remote_present_is_a_noop_when_it_exists(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, f"{REMOTE}\n")]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(ctx, REMOTE, "https://example.test/other.flatpakrepo")

    assert [call[0] for call in command.calls] == [REMOTES]
    assert ctx.changes == []


def test_remote_present_can_disable_gpg_verification(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(ctx, REMOTE, REMOTE_URL, gpg_verify=False)

    assert command.calls[1][0] == [
        "flatpak",
        "remote-add",
        "--if-not-exists",
        "--system",
        "--no-gpg-verify",
        REMOTE,
        REMOTE_URL,
    ]


def test_remote_present_dry_run_does_not_add(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    flatpak.remote(ctx, REMOTE, REMOTE_URL)

    assert [call[0] for call in command.calls] == [REMOTES]
    assert len(ctx.changes) == 1


def test_remote_absent_removes_existing_remote(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, f"{REMOTE}\n"), response(0)]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(ctx, REMOTE, state="absent")

    assert command.calls[1][0] == [
        "flatpak",
        "remote-delete",
        "--system",
        REMOTE,
    ]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].data == {"remote": REMOTE, "scope": "system"}
    assert ctx.changes[0].summary == f"remove remote {REMOTE}"


def test_remote_absent_is_a_noop_when_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "other\n")]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(ctx, REMOTE, state="absent")

    assert [call[0] for call in command.calls] == [REMOTES]
    assert ctx.changes == []


def test_remote_present_requires_url_and_text(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="URL is required"):
        flatpak.remote(ctx, REMOTE)
    with pytest.raises(TaskFailed, match="remote name must not be empty"):
        flatpak.remote(ctx, "  ", REMOTE_URL)
    with pytest.raises(TaskFailed, match="remote URL must not be empty"):
        flatpak.remote(ctx, REMOTE, "  ")


def test_remote_invalid_state_and_scope_are_errors(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid flatpak state"):
        flatpak.remote(ctx, REMOTE, REMOTE_URL, state="invalid")
    with pytest.raises(TaskFailed, match="invalid flatpak scope"):
        flatpak.remote(ctx, REMOTE, REMOTE_URL, scope="session")


def test_packages_present_is_a_noop_when_installed(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, installed((APP, APP_REF)))]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, [APP])

    assert [call[0] for call in command.calls] == [LIST]
    assert ctx.changes == []


def test_packages_present_installs_missing_refs(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0),
        response(0, installed((APP, APP_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, [APP], remote=REMOTE)

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
    assert ctx.changes[0].data == {"added": [APP], "scope": "system"}
    assert ctx.changes[0].summary == f"+{APP}"


def test_packages_present_installs_only_missing_refs(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF))),
        response(0),
        response(0, installed((APP, APP_REF), (RUNTIME, RUNTIME_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, [APP, RUNTIME])

    assert command.calls[1][0] == [
        "flatpak",
        "install",
        "-y",
        "--noninteractive",
        "--system",
        RUNTIME,
    ]
    assert ctx.changes[0].data == {"added": [RUNTIME], "scope": "system"}


def test_packages_full_refs_and_kind_prefixes_match_exactly(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF), (RUNTIME, RUNTIME_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, [f"app/{APP_REF}", f"runtime/{RUNTIME_REF}"])

    assert [call[0] for call in command.calls] == [LIST]
    assert ctx.changes == []


def test_packages_full_ref_for_another_branch_is_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF))),
        response(0),
        response(
            0,
            installed((APP, APP_REF), (APP, "org.example.App/x86_64/beta")),
        ),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, ["org.example.App/x86_64/beta"])

    assert command.calls[1][0][-1] == "org.example.App/x86_64/beta"
    assert ctx.changes[0].data == {
        "added": ["org.example.App/x86_64/beta"],
        "scope": "system",
    }


def test_packages_present_dry_run_does_not_install(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = make_context(tmp_path, dry_run=True, command=command)

    flatpak.packages(ctx, [APP])

    assert [call[0] for call in command.calls] == [LIST]
    assert len(ctx.changes) == 1


def test_packages_absent_removes_installed_ref(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF))),
        response(0),
        response(0),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, [APP], state="absent")

    assert command.calls[1][0] == [
        "flatpak",
        "uninstall",
        "-y",
        "--noninteractive",
        "--system",
        APP_REF,
    ]
    assert command.calls[1][1]["sudo"] is True
    assert ctx.changes[0].data == {"removed": [APP_REF], "scope": "system"}
    assert ctx.changes[0].summary == f"-{APP_REF}"


def test_packages_absent_removes_all_branches_of_a_bare_id(tmp_path):
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

    flatpak.packages(ctx, [APP], state="absent")

    assert command.calls[1][0] == [
        "flatpak",
        "uninstall",
        "-y",
        "--noninteractive",
        "--system",
        APP_REF,
        "org.example.App/x86_64/beta",
    ]
    assert ctx.changes[0].data == {
        "removed": [APP_REF, "org.example.App/x86_64/beta"],
        "scope": "system",
    }


def test_packages_absent_is_a_noop_when_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, installed((RUNTIME, RUNTIME_REF)))]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, [APP], state="absent")

    assert [call[0] for call in command.calls] == [LIST]
    assert ctx.changes == []


def test_packages_absent_rejects_remote(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="remote is only supported"):
        flatpak.packages(ctx, [APP], state="absent", remote=REMOTE)


def test_user_scope_uses_user_flag_without_sudo(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0),
        response(0),
        response(0),
        response(0, installed((APP, APP_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.remote(ctx, REMOTE, REMOTE_URL, scope="user")
    flatpak.packages(ctx, [APP], scope="user")

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
    ctx = make_context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="cannot run as root"):
        flatpak.remote(ctx, REMOTE, REMOTE_URL, scope="user")
    with pytest.raises(TaskFailed, match="cannot run as root"):
        flatpak.packages(ctx, [APP], scope="user")


def test_packages_invalid_state_and_scope_are_errors(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid flatpak state"):
        flatpak.packages(ctx, [APP], state="invalid")
    with pytest.raises(TaskFailed, match="invalid flatpak scope"):
        flatpak.packages(ctx, [APP], scope="session")


def test_empty_package_inputs_are_rejected(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="flatpak refs must not be empty"):
        flatpak.packages(ctx, [])
    with pytest.raises(TaskFailed, match="flatpak ref must not be empty"):
        flatpak.packages(ctx, "")
    with pytest.raises(TaskFailed, match="flatpak ref must not be empty"):
        flatpak.packages(ctx, [APP, "  "])
    with pytest.raises(TaskFailed, match="flatpak remote must not be empty"):
        flatpak.packages(ctx, [APP], remote="  ")


def test_install_verification_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(0)]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still missing after install"):
        flatpak.packages(ctx, [APP])


def test_removal_verification_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, installed((APP, APP_REF))),
        response(0),
        response(0, installed((APP, APP_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="still installed after removal"):
        flatpak.packages(ctx, [APP], state="absent")


def test_packages_accepts_a_single_string(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0),
        response(0),
        response(0, installed((APP, APP_REF))),
    ]
    ctx = make_context(tmp_path, command=command)

    flatpak.packages(ctx, APP)

    assert command.calls[1][0][-1] == APP


