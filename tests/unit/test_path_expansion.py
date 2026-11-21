"""Tests that public path arguments expand ``~`` and keep absolute paths."""

import gzip
import http.server
import json
import os
import zipfile

import pytest

from kitup.modules import command, decompress, dnf, download, files, git, pip, podman, sysctl, uv

from .helpers import FakeCommandRunner, FakeSudo, argvs, make_context, plain_output, response, serve_http


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def context(tmp_path, *, command_runner=None, dry_run=False, sudo=None):
    return make_context(
        tmp_path,
        dry_run=dry_run,
        command=command_runner or FakeCommandRunner(),
        sudo=sudo or FakeSudo(),
        output=plain_output(),
    )


def test_files_paths_expand_home(tmp_path, home):
    context(tmp_path)

    files.file("~/file", content="value\n")
    files.directory("~/dir")
    files.line("~/line", "value=1")
    files.block("~/block", "mark", "value=1")
    files.ini("~/file.ini", section="s", option="k", value="v")
    files.toml("~/file.toml", key="k", value="v")

    source = home / "source"
    source.write_text("source\n", encoding="utf-8")
    files.copy(source, "~/copy")
    files.move("~/copy", "~/moved")
    files.symlink("~/target", "~/link")

    assert (home / "file").read_text(encoding="utf-8") == "value\n"
    assert (home / "dir").is_dir()
    assert "value=1" in (home / "line").read_text(encoding="utf-8")
    assert "value=1" in (home / "block").read_text(encoding="utf-8")
    assert "k = v" in (home / "file.ini").read_text(encoding="utf-8")
    assert 'k = "v"' in (home / "file.toml").read_text(encoding="utf-8")
    assert (home / "moved").read_text(encoding="utf-8") == "source\n"
    assert os.readlink(home / "link") == str(home / "target")

    template_source = tmp_path / "source.j2"
    template_source.write_text("rendered\n", encoding="utf-8")
    files.template(template_source, "~/rendered")
    assert (home / "rendered").read_text(encoding="utf-8") == "rendered\n"


def test_artifact_destinations_expand_home(tmp_path, home, monkeypatch):
    # Single-file decompression, archive extraction, and creates markers.
    source = home / "data.txt.gz"
    with gzip.open(source, "wb") as stream:
        stream.write(b"value\n")
    archive_path = home / "data.zip"
    with zipfile.ZipFile(archive_path, "w") as archive_file:
        archive_file.writestr("inside.txt", "value\n")
    context(tmp_path)

    decompress.file(source, "~/data.txt")
    decompress.archive(archive_path, "~/archive")
    marker = home / "marker"
    marker.write_text("", encoding="utf-8")
    skipped = decompress.archive(archive_path, "~/archive2", creates="~/marker")

    assert (home / "data.txt").read_text(encoding="utf-8") == "value\n"
    assert (home / "archive" / "inside.txt").read_text(encoding="utf-8") == "value\n"
    assert skipped.changed is False

    # Downloaded files.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    context(tmp_path)

    with serve_http(DownloadHandler) as url:
        download.to_file(url, "~/downloaded")

    assert (home / "downloaded").read_text(encoding="utf-8") == "downloaded\n"

    # Git clone destinations.
    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: "git" if name == "git" else None)
    command_runner = FakeCommandRunner()
    command_runner.responses = [response(128)]
    context(tmp_path, command_runner=command_runner)

    git.repo("https://example.test/repo.git", "~/repo")

    assert argvs(command_runner)[-1] == ["git", "clone", "https://example.test/repo.git", str(home / "repo")]


def test_dnf_paths_expand_home(tmp_path, home):
    # Configuration file paths.
    context(tmp_path)

    dnf.config("keepcache", value=True, path="~/dnf.conf", sudo=False)

    assert "keepcache = True" in (home / "dnf.conf").read_text(encoding="utf-8")

    # Repository and RPM source paths.
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    command_runner = FakeCommandRunner()
    command_runner.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    context(tmp_path, command_runner=command_runner)

    dnf.repository("~/tool.repo")

    assert f"--from-repofile={home / 'tool.repo'}" in command_runner.calls[1][0]

    rpm_command = FakeCommandRunner()
    rpm_command.responses = [
        response(0, "tool-release\n"),
        response(1, "package tool-release is not installed\n"),
    ]
    context(tmp_path, command_runner=rpm_command)

    dnf.rpm("~/tool-release.rpm")

    assert rpm_command.calls[2][0][-1] == str(home / "tool-release.rpm")


class DownloadHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"downloaded\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_command_creates_cwd_and_shell_expand_home(tmp_path, home):
    command_runner = FakeCommandRunner()
    context(tmp_path, command_runner=command_runner)
    (home / "work").mkdir()
    (home / "work" / "marker").write_text("", encoding="utf-8")

    skipped = command.run(["true"], creates="marker", cwd="~/work")
    command.run(["true"], cwd="~/work")
    command.shell("echo hi", shell="~/bin/sh", cwd="~/work")

    assert skipped.changed is False
    assert skipped.summary == "skip true"
    assert command_runner.calls[0][1]["cwd"] == home / "work"
    assert command_runner.calls[1][0] == [str(home / "bin" / "sh"), "-c", "echo hi"]
    assert command_runner.calls[1][1]["cwd"] == home / "work"


def test_pip_and_uv_paths_expand_home(tmp_path, home, monkeypatch):
    # pip venv, config, and interpreter paths.
    monkeypatch.setattr(
        "kitup.modules.pip.shutil.which",
        lambda name: {"python3": "/usr/bin/python3", "python": "/usr/bin/python"}.get(name),
    )
    command_runner = FakeCommandRunner()
    command_runner.responses = [
        response(0, json.dumps({"version": "1", "install": []})),
    ]

    context(tmp_path, command_runner=command_runner, dry_run=True)
    venv_result = pip.packages(
        ["requests"],
        venv="~/venv",
    )
    assert venv_result["venv"] == str(home / "venv")

    context(tmp_path)
    pip.config("index-url", "https://example.test/simple", path="~/pip.conf", sudo=False)
    assert "index-url = https://example.test/simple" in (home / "pip.conf").read_text(encoding="utf-8")

    python = home / "bin" / "python3"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    context(tmp_path, command_runner=command_runner)
    pip.packages(["requests"], python="~/bin/python3")
    assert command_runner.calls[0][0][0] == str(python)

    # uv dependency, project config, and explicit config paths.
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    context(tmp_path)

    absent = uv.dependencies(["requests"], project="~/project", state="absent")
    assert absent["project"] == str(home / "project")

    uv.config("index-url", "https://example.test/simple", scope="project", project="~/project")
    assert 'index-url = "https://example.test/simple"' in (home / "project" / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    configured = home / "uv.toml"
    uv.config("cache-dir", "~/cache", path="~/uv.toml")
    assert 'cache-dir = "~/cache"' in configured.read_text(encoding="utf-8")


def test_sysctl_file_expands_home(tmp_path, home):
    command_runner = FakeCommandRunner()
    command_runner.responses = [response(0, "10\n")]
    context(tmp_path, command_runner=command_runner, sudo=FakeSudo(is_root=True))

    sysctl.key("vm.swappiness", 10, file="~/sysctl.conf")

    assert "vm.swappiness = 10" in (home / "sysctl.conf").read_text(encoding="utf-8")


def test_podman_paths_expand_home(tmp_path, home, monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    command_runner = FakeCommandRunner()
    context(tmp_path, command_runner=command_runner)
    (home / "quadlet").mkdir()
    (home / "units").mkdir()
    (home / "registries").mkdir()

    podman.quadlet("example", image="image", dest="~/quadlet", scope="user")
    assert (home / "quadlet" / "example.container").is_file()

    command_runner.responses = [response(0, "[Service]\nExecStart=/bin/true\n")]
    podman.generate_systemd("example", dest="~/units", scope="user")
    assert (home / "units" / "container-example.service").is_file()

    podman.registry("example", location="example.test", path="~/registries/example", scope="user")
    assert (home / "registries" / "example.conf").is_file()

    command_runner.responses = [response(1)]
    podman.login("example.test", username="user", password="secret", authfile="~/auth.json")
    assert f"--authfile={home / 'auth.json'}" in argvs(command_runner)[-1]

    command_runner.responses = [response(0, "user\n")]
    podman.logout("example.test", authfile="~/auth.json")
    assert f"--authfile={home / 'auth.json'}" in argvs(command_runner)[-1]
