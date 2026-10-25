from __future__ import annotations

import subprocess

import pytest

from kitup.errors import PrivilegeError
from kitup.privileges import PrivilegeManager


def test_root_runs_without_sudo(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 0)
    manager = PrivilegeManager()

    manager.refresh()
    assert manager.wrap(["dnf5"]) == ["dnf5"]


def test_cached_credentials_are_used_without_output(monkeypatch, capsys):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sudo")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    PrivilegeManager().refresh('task "Install packages" requires sudo privileges')

    assert calls == [["sudo", "-n", "-v"]]
    assert capsys.readouterr().err == ""


def test_credentials_valid_checks_the_sudo_cache(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sudo")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert PrivilegeManager().credentials_valid() is True
    assert calls == [["sudo", "-n", "-v"]]


def test_credentials_valid_reports_an_expired_cache(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1),
    )

    assert PrivilegeManager().credentials_valid() is False


def test_missing_credentials_without_tty_fail(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(PrivilegeError, match="stdin is not a TTY"):
        PrivilegeManager().refresh()


def test_tty_prompts_when_cache_is_missing(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []
    calls = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return "secret"

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1 if argv == ["sudo", "-n", "-v"] else 0)

    monkeypatch.setattr("kitup.privileges.getpass.getpass", fake_getpass)
    monkeypatch.setattr(subprocess, "run", fake_run)

    PrivilegeManager().refresh()

    assert prompts == ["sudo password: "]
    assert calls[0][0] == ["sudo", "-n", "-v"]
    assert calls[1][0] == ["sudo", "-S", "-p", "", "-v"]
    assert calls[1][1]["input"] == "secret\n"


def test_failed_prompt_is_an_error(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("kitup.privileges.getpass.getpass", lambda prompt: "wrong")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, "", "Sorry, try again.\n"
        ),
    )

    with pytest.raises(PrivilegeError, match="authentication failed: Sorry, try again."):
        PrivilegeManager().refresh()


def test_wrap_uses_non_interactive_sudo(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)

    assert PrivilegeManager().wrap(["dnf5", "install"]) == [
        "sudo",
        "-n",
        "--",
        "dnf5",
        "install",
    ]
