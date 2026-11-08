"""Tests that public path arguments expand ``~`` and keep absolute paths."""

import gzip
import http.server
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from kitup.commands import CommandRunner
from kitup.modules import command, decompress, dnf, download, files, git, pip, podman, sysctl, template, users, uv
from kitup.privileges import PrivilegeManager
from kitup.registry import Bundle

from ..helpers import write
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


def test_resolve_source_expands_home(tmp_path, home):
    bundle_dir = tmp_path / "bundles" / "base"
    bundle_dir.mkdir(parents=True)
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = context(tmp_path)
    ctx.begin_task(bundle.tasks[0])

    assert ctx.resolve_source("~/config") == home / "config"
    assert ctx.resolve_source("/srv/config") == Path("/srv/config")
    assert ctx.resolve_source("config") == bundle_dir / "files" / "config"


def test_resolve_source_prefers_project_overlays_then_bundle(tmp_path):
    bundle_dir = tmp_path / "bundles" / "base"
    project_files = tmp_path / "files"
    project_bundle = project_files / "base"
    bundle_files = bundle_dir / "files"

    write(project_bundle / "shared.conf", "bundle-specific\n")
    write(project_files / "shared.conf", "global\n")
    write(bundle_files / "shared.conf", "bundle\n")
    write(project_files / "global.conf", "global\n")
    write(bundle_files / "global.conf", "bundle\n")
    write(bundle_files / "bundle.conf", "bundle\n")
    write(project_bundle / "nested" / "specific.conf", "bundle-specific\n")
    write(project_files / "nested" / "specific.conf", "global\n")
    write(bundle_dir / "nested" / "specific.conf", "bundle\n")
    write(project_files / "nested" / "global.conf", "global\n")
    write(bundle_dir / "nested" / "global.conf", "bundle\n")
    write(bundle_dir / "nested" / "bundle.conf", "bundle\n")

    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = context(tmp_path)
    ctx.begin_task(bundle.tasks[0])

    assert ctx.resolve_source("shared.conf") == project_bundle / "shared.conf"
    assert ctx.resolve_source("global.conf") == project_files / "global.conf"
    assert ctx.resolve_source("bundle.conf") == bundle_files / "bundle.conf"
    assert ctx.resolve_source("nested/specific.conf") == project_bundle / "nested" / "specific.conf"
    assert ctx.resolve_source("nested/global.conf") == project_files / "nested" / "global.conf"
    assert ctx.resolve_source("nested/bundle.conf") == bundle_dir / "nested" / "bundle.conf"


def test_resolve_source_uses_files_path_literally_for_overlays(tmp_path):
    bundle_dir = tmp_path / "bundles" / "base"
    project_files = tmp_path / "files"
    project_bundle = project_files / "base"
    bundle_files = bundle_dir / "files"

    write(project_bundle / "files" / "nested" / "specific.conf", "bundle-specific\n")
    write(project_files / "files" / "nested" / "specific.conf", "global\n")
    write(bundle_files / "nested" / "specific.conf", "bundle\n")
    write(project_files / "files" / "nested" / "global.conf", "global\n")
    write(bundle_files / "nested" / "global.conf", "bundle\n")
    write(bundle_files / "nested" / "bundle.conf", "bundle\n")

    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = context(tmp_path)
    ctx.begin_task(bundle.tasks[0])

    assert ctx.resolve_source("files/nested/specific.conf") == (project_bundle / "files" / "nested" / "specific.conf")
    assert ctx.resolve_source("files/nested/global.conf") == project_files / "files" / "nested" / "global.conf"
    assert ctx.resolve_source("files/nested/bundle.conf") == bundle_files / "nested" / "bundle.conf"


def test_resolve_source_overlays_are_bundle_scoped(tmp_path):
    base_dir = tmp_path / "bundles" / "base"
    other_dir = tmp_path / "bundles" / "other"
    write(tmp_path / "files" / "base" / "only-base.conf", "bundle-specific\n")
    write(tmp_path / "files" / "shared.conf", "global\n")
    write(base_dir / "files" / "shared.conf", "base\n")
    write(other_dir / "files" / "only-base.conf", "other\n")
    write(other_dir / "files" / "shared.conf", "other\n")

    base = Bundle("base", path=base_dir)
    other = Bundle("other", path=other_dir)
    base.task("Base")(lambda ctx: None)
    other.task("Other")(lambda ctx: None)
    ctx = context(tmp_path)

    ctx.begin_task(base.tasks[0])
    assert ctx.resolve_source("only-base.conf") == tmp_path / "files" / "base" / "only-base.conf"
    assert ctx.resolve_source("shared.conf") == tmp_path / "files" / "shared.conf"

    ctx.begin_task(other.tasks[0])
    assert ctx.resolve_source("only-base.conf") == other_dir / "files" / "only-base.conf"
    assert ctx.resolve_source("shared.conf") == tmp_path / "files" / "shared.conf"


def test_command_runner_expands_cwd(tmp_path, home, monkeypatch):
    calls = []

    class Process:
        pid = 1
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return "", ""

    def popen(argv, **kwargs):
        calls.append(kwargs["cwd"])
        return Process()

    monkeypatch.setattr(subprocess, "Popen", popen)

    CommandRunner(PrivilegeManager()).run(["true"], cwd="~/work")

    assert calls == [home / "work"]


def test_files_paths_expand_home(tmp_path, home):
    ctx = context(tmp_path)

    files.file(ctx, "~/file", content="value\n")
    files.directory(ctx, "~/dir")
    files.line(ctx, "~/line", "value=1")
    files.block(ctx, "~/block", "mark", "value=1")
    files.ini(ctx, "~/file.ini", section="s", option="k", value="v")
    files.toml(ctx, "~/file.toml", key="k", value="v")

    source = home / "source"
    source.write_text("source\n", encoding="utf-8")
    files.copy(ctx, source, "~/copy")
    files.move(ctx, "~/copy", "~/moved")
    files.symlink(ctx, "~/target", "~/link")

    assert (home / "file").read_text(encoding="utf-8") == "value\n"
    assert (home / "dir").is_dir()
    assert "value=1" in (home / "line").read_text(encoding="utf-8")
    assert "value=1" in (home / "block").read_text(encoding="utf-8")
    assert "k = v" in (home / "file.ini").read_text(encoding="utf-8")
    assert 'k = "v"' in (home / "file.toml").read_text(encoding="utf-8")
    assert (home / "moved").read_text(encoding="utf-8") == "source\n"
    assert os.readlink(home / "link") == str(home / "target")


def test_template_destination_expands_home(tmp_path, home):
    source = tmp_path / "source.j2"
    source.write_text("rendered\n", encoding="utf-8")
    ctx = context(tmp_path)

    template.file(ctx, source, "~/rendered")

    assert (home / "rendered").read_text(encoding="utf-8") == "rendered\n"


def test_decompress_destinations_and_creates_expand_home(tmp_path, home):
    source = home / "data.txt.gz"
    with gzip.open(source, "wb") as stream:
        stream.write(b"value\n")
    archive_path = home / "data.zip"
    with zipfile.ZipFile(archive_path, "w") as archive_file:
        archive_file.writestr("inside.txt", "value\n")
    ctx = context(tmp_path)

    decompress.file(ctx, source, "~/data.txt")
    decompress.archive(ctx, archive_path, "~/archive")
    marker = home / "marker"
    marker.write_text("", encoding="utf-8")
    skipped = decompress.archive(ctx, archive_path, "~/archive2", creates="~/marker")

    assert (home / "data.txt").read_text(encoding="utf-8") == "value\n"
    assert (home / "archive" / "inside.txt").read_text(encoding="utf-8") == "value\n"
    assert skipped.changed is False


def test_dnf_config_path_expands_home(tmp_path, home):
    ctx = context(tmp_path)

    dnf.config(ctx, "keepcache", value=True, path="~/dnf.conf", sudo=False)

    assert "keepcache = True" in (home / "dnf.conf").read_text(encoding="utf-8")


def test_dnf_source_paths_expand_home(tmp_path, home):
    reposdir = tmp_path / "repos.d"
    reposdir.mkdir()
    command_runner = FakeCommandRunner()
    command_runner.responses = [response(0, f"reposdir = {reposdir}\n"), response(0)]
    repo_ctx = context(tmp_path, command_runner=command_runner)

    dnf.repository(repo_ctx, "~/tool.repo")

    assert f"--from-repofile={home / 'tool.repo'}" in command_runner.calls[1][0]

    rpm_command = FakeCommandRunner()
    rpm_command.responses = [response(0, "tool-release\n"), response(1)]
    rpm_ctx = context(tmp_path, command_runner=rpm_command)

    dnf.rpm(rpm_ctx, "~/tool-release.rpm")

    assert rpm_command.calls[2][0][-1] == str(home / "tool-release.rpm")


class DownloadHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"downloaded\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_download_destination_expands_home(tmp_path, home, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    ctx = context(tmp_path)

    with serve_http(DownloadHandler) as url:
        download.to_file(ctx, url, "~/downloaded")

    assert (home / "downloaded").read_text(encoding="utf-8") == "downloaded\n"


def test_git_destination_expands_home(tmp_path, home, monkeypatch):
    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: "git" if name == "git" else None)
    command_runner = FakeCommandRunner()
    command_runner.responses = [response(128)]
    ctx = context(tmp_path, command_runner=command_runner)

    git.repo(ctx, "https://example.test/repo.git", "~/repo")

    assert argvs(command_runner)[-1] == ["git", "clone", "https://example.test/repo.git", str(home / "repo")]


def test_command_creates_cwd_and_shell_expand_home(tmp_path, home):
    command_runner = FakeCommandRunner()
    ctx = context(tmp_path, command_runner=command_runner)
    (home / "work").mkdir()
    (home / "work" / "marker").write_text("", encoding="utf-8")

    skipped = command.run(ctx, ["true"], creates="marker", cwd="~/work")
    command.run(ctx, ["true"], cwd="~/work")
    command.shell(ctx, "echo hi", shell="~/bin/sh", cwd="~/work")

    assert skipped.changed is False
    assert skipped.summary == "skip true"
    assert command_runner.calls[0][1]["cwd"] == home / "work"
    assert command_runner.calls[1][0] == [str(home / "bin" / "sh"), "-c", "echo hi"]
    assert command_runner.calls[1][1]["cwd"] == home / "work"


def test_pip_paths_expand_home(tmp_path, home, monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.pip.shutil.which",
        lambda name: {"python3": "/usr/bin/python3", "python": "/usr/bin/python"}.get(name),
    )
    command_runner = FakeCommandRunner()
    command_runner.responses = [
        response(0, json.dumps({"version": "1", "install": []})),
    ]

    venv_result = pip.packages(
        context(tmp_path, command_runner=command_runner, dry_run=True),
        ["requests"],
        venv="~/venv",
    )
    assert venv_result["venv"] == str(home / "venv")

    config_ctx = context(tmp_path)
    pip.config(config_ctx, "index-url", "https://example.test/simple", path="~/pip.conf", sudo=False)
    assert "index-url = https://example.test/simple" in (home / "pip.conf").read_text(encoding="utf-8")

    python = home / "bin" / "python3"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    installed_ctx = context(tmp_path, command_runner=command_runner)
    pip.packages(installed_ctx, ["requests"], python="~/bin/python3")
    assert command_runner.calls[0][0][0] == str(python)


def test_uv_paths_expand_home(tmp_path, home, monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    ctx = context(tmp_path)

    absent = uv.dependencies(ctx, ["requests"], project="~/project", state="absent")
    assert absent["project"] == str(home / "project")

    uv.config(ctx, "index-url", "https://example.test/simple", scope="project", project="~/project")
    assert 'index-url = "https://example.test/simple"' in (home / "project" / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    configured = home / "uv.toml"
    uv.config(ctx, "cache-dir", "~/cache", path="~/uv.toml")
    assert 'cache-dir = "~/cache"' in configured.read_text(encoding="utf-8")


def test_uv_project_environment_expands_home(tmp_path, home, monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "~/env")
    (home / "env").mkdir()
    project = home / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["requests"]\n',
        encoding="utf-8",
    )
    command_runner = FakeCommandRunner()
    command_runner.responses = [response(0), response(0)]

    result = uv.dependencies(context(tmp_path, command_runner=command_runner), ["requests"], project="~/project")

    assert result.changed is False
    assert len(command_runner.calls) == 2


def test_users_home_and_shell_expand_before_the_absolute_check(home):
    assert users._optional_absolute_path("~/alice", "home") == str(home / "alice")
    assert users._optional_absolute_path("~/bin/zsh", "shell") == str(home / "bin" / "zsh")


def test_sysctl_file_expands_home(tmp_path, home):
    command_runner = FakeCommandRunner()
    command_runner.responses = [response(0, "10\n")]
    ctx = context(tmp_path, command_runner=command_runner, sudo=FakeSudo(is_root=True))

    sysctl.key(ctx, "vm.swappiness", 10, file="~/sysctl.conf")

    assert "vm.swappiness = 10" in (home / "sysctl.conf").read_text(encoding="utf-8")


def test_podman_paths_expand_home(tmp_path, home, monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    command_runner = FakeCommandRunner()
    ctx = context(tmp_path, command_runner=command_runner)
    (home / "quadlet").mkdir()
    (home / "units").mkdir()
    (home / "registries").mkdir()

    podman.quadlet(ctx, "example", image="image", dest="~/quadlet", scope="user")
    assert (home / "quadlet" / "example.container").is_file()

    command_runner.responses = [response(0, "[Service]\nExecStart=/bin/true\n")]
    podman.generate_systemd(ctx, "example", dest="~/units", scope="user")
    assert (home / "units" / "container-example.service").is_file()

    podman.registry(ctx, "example", location="example.test", path="~/registries/example", scope="user")
    assert (home / "registries" / "example.conf").is_file()

    command_runner.responses = [response(1)]
    podman.login(ctx, "example.test", username="user", password="secret", authfile="~/auth.json")
    assert f"--authfile={home / 'auth.json'}" in argvs(command_runner)[-1]

    command_runner.responses = [response(0, "user\n")]
    podman.logout(ctx, "example.test", authfile="~/auth.json")
    assert f"--authfile={home / 'auth.json'}" in argvs(command_runner)[-1]
