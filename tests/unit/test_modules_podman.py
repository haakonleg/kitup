import stat

import pytest

from kitup.errors import TaskFailed
from kitup.modules import podman
from kitup.registry import Bundle

from .helpers import FakeCommandRunner, FakeSudo, argvs, call_data, make_context, response

PODMAN = "/usr/bin/podman"
IMAGE = "localhost/example:latest"
CONTAINER = "example"
REGISTRY = "registry.example.com"
UNIT = "[Unit]\nDescription=test\n\n[Service]\nExecStart=/usr/bin/podman start example\n"


@pytest.fixture(autouse=True)
def podman_available(monkeypatch):
    monkeypatch.setattr(
        "kitup._utils.executables.shutil.which",
        lambda name: PODMAN if name == "podman" else None,
    )


def missing_container():
    return response(125, stderr="Error: no such container\n")


def context(tmp_path, *, dry_run=False, command=None, sudo=None):
    return make_context(
        tmp_path,
        dry_run=dry_run,
        command=command or FakeCommandRunner(),
        sudo=sudo or FakeSudo(),
    )


def bundle_context(tmp_path, *, command=None):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = context(tmp_path, command=command)
    ctx.begin_task(bundle.tasks[0])
    return ctx, bundle_dir


def test_image_present_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE)
    assert argvs(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "pull", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {"image": IMAGE}
    assert ctx.changes[0].action == "podman.image"

    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE)
    assert argvs(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, pull="always")
    assert argvs(command) == [[PODMAN, "pull", IMAGE]]
    assert len(ctx.changes) == 1


def test_image_pull_never_fails_when_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="not present locally"):
        podman.image(ctx, IMAGE, pull="never")


def test_image_platform_adds_flag(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, platform="linux/arm64")

    assert argvs(command)[1] == [
        PODMAN,
        "pull",
        "--platform",
        "linux/arm64",
        IMAGE,
    ]


def test_image_absent_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(1)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, state="absent")
    assert argvs(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "rmi", IMAGE],
        [PODMAN, "image", "exists", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {"image": IMAGE}
    assert ctx.changes[0].action == "podman.image"

    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, state="absent")
    assert argvs(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(1)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, state="absent", force=True)
    assert argvs(command)[1] == [PODMAN, "rmi", "--force", IMAGE]

    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(0)]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="still present after removal"):
        podman.image(ctx, IMAGE, state="absent")


def test_image_exists_storage_error_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(125, stderr="storage error")]
    ctx = context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not check image"):
        podman.image(ctx, IMAGE)


def test_image_source_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar")
    assert argvs(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "pull", "oci-archive:/tmp/pull.tar"],
        [PODMAN, "image", "exists", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {
        "image": IMAGE,
        "source": "oci-archive:/tmp/pull.tar",
    }

    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar")
    assert argvs(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar", pull="always")
    assert argvs(command) == [
        [PODMAN, "pull", "oci-archive:/tmp/pull.tar"],
        [PODMAN, "image", "exists", IMAGE],
    ]

    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(1)]
    ctx = context(tmp_path, command=command)
    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar", state="absent")
    assert argvs(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "rmi", IMAGE],
        [PODMAN, "image", "exists", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {"image": IMAGE}
    assert ctx.changes[0].action == "podman.image"

    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(1)]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="missing after pull"):
        podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar")


def test_image_rejects_invalid_arguments(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="name must be a local image name"):
        podman.image(ctx, "oci-archive:/tmp/pull.tar")
    with pytest.raises(TaskFailed, match="source must be a transport-qualified"):
        podman.image(ctx, IMAGE, source="localhost/other:latest")
    with pytest.raises(TaskFailed, match="invalid image state"):
        podman.image(ctx, IMAGE, state="invalid")
    with pytest.raises(TaskFailed, match="invalid image pull policy"):
        podman.image(ctx, IMAGE, pull="newer")
    with pytest.raises(TaskFailed, match="image force must be a boolean"):
        podman.image(ctx, IMAGE, force="yes")
    with pytest.raises(TaskFailed, match="image name must not be empty"):
        podman.image(ctx, "  ")


def test_build_scenarios(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = context(tmp_path, command=command)
    podman.build(ctx, build_dir, tag=IMAGE)
    assert argvs(command)[1] == [PODMAN, "build", "-t", IMAGE, str(build_dir)]
    assert call_data(ctx.changes[0]) == {
        "image": IMAGE,
        "context": str(build_dir),
    }

    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)
    podman.build(ctx, build_dir, tag=IMAGE)
    assert argvs(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = context(tmp_path, command=command)
    podman.build(ctx, build_dir, tag=IMAGE, force=True)
    assert argvs(command)[0] == [PODMAN, "build", "-t", IMAGE, str(build_dir)]


def test_build_flags(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text("FROM scratch\n")
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = context(tmp_path, command=command)

    podman.build(
        ctx,
        build_dir,
        tag=IMAGE,
        containerfile=containerfile,
        build_args={"MESSAGE": "kitup", "COUNT": 2, "ENABLED": True},
        pull=True,
        no_cache=True,
    )

    assert argvs(command)[1] == [
        PODMAN,
        "build",
        "-t",
        IMAGE,
        "-f",
        str(containerfile),
        "--build-arg",
        "MESSAGE=kitup",
        "--build-arg",
        "COUNT=2",
        "--build-arg",
        "ENABLED=true",
        "--pull=always",
        "--no-cache",
        str(build_dir),
    ]


def test_build_resolves_bundle_relative_paths(tmp_path):
    command = FakeCommandRunner()
    ctx, bundle_dir = bundle_context(tmp_path, command=command)
    bare = bundle_dir / "files" / "container"
    bare.mkdir()
    command.responses = [response(1), response(0), response(0)]
    podman.build(ctx, "container", tag=IMAGE)
    assert argvs(command)[1] == [PODMAN, "build", "-t", IMAGE, str(bare)]

    nested = bundle_dir / "nested" / "container"
    nested.mkdir(parents=True)
    command.responses = [response(1), response(0), response(0)]
    podman.build(ctx, "nested/container", tag=IMAGE)
    assert argvs(command)[4] == [PODMAN, "build", "-t", IMAGE, str(nested)]

    containerfile = bundle_dir / "files" / "Containerfile"
    containerfile.write_text("FROM scratch\n")
    command.responses = [response(1), response(0), response(0)]
    podman.build(ctx, "container", tag=IMAGE, containerfile="Containerfile")
    assert argvs(command)[7] == [
        PODMAN,
        "build",
        "-t",
        IMAGE,
        "-f",
        str(containerfile),
        str(bare),
    ]

    command.responses = [response(1), response(0), response(0)]
    podman.build(ctx, ".", tag=IMAGE)
    assert argvs(command)[10] == [PODMAN, "build", "-t", IMAGE, str(bundle_dir)]


def test_build_rejects_invalid_arguments(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="cannot resolve relative source path"):
        podman.build(ctx, "container", tag=IMAGE)

    missing = tmp_path / "missing"
    with pytest.raises(TaskFailed, match="not a directory"):
        podman.build(ctx, missing, tag=IMAGE)

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    with pytest.raises(TaskFailed, match="not a regular file"):
        podman.build(ctx, build_dir, tag=IMAGE, containerfile=tmp_path / "missing")
    with pytest.raises(TaskFailed, match="invalid build argument name"):
        podman.build(ctx, build_dir, tag=IMAGE, build_args={"bad-name": "value"})


def test_tag_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "sha256:source\n"),
        response(1),
        response(0),
        response(0, "sha256:other\n"),
        response(0),
    ]
    ctx = context(tmp_path, command=command)
    podman.tag(ctx, IMAGE, ["localhost/one:latest", "localhost/two:latest"])
    assert argvs(command)[4] == [
        PODMAN,
        "tag",
        IMAGE,
        "localhost/one:latest",
        "localhost/two:latest",
    ]
    assert call_data(ctx.changes[0]) == {
        "source": IMAGE,
        "targets": ["localhost/one:latest", "localhost/two:latest"],
    }

    command = FakeCommandRunner()
    command.responses = [
        response(0, "sha256:source\n"),
        response(0),
        response(0, "sha256:source\n"),
    ]
    ctx = context(tmp_path, command=command)
    podman.tag(ctx, IMAGE, ["localhost/one:latest", "localhost/one:latest"])
    assert len(command.calls) == 3
    assert ctx.changes == []


def test_tag_reports_inspection_errors(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="could not inspect image"):
        podman.tag(ctx, IMAGE, "localhost/target:latest")

    command = FakeCommandRunner()
    command.responses = [response(0, "sha256:source\n"), response(125, stderr="storage error")]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="could not check image"):
        podman.tag(ctx, IMAGE, "localhost/target:latest")


def test_container_started_scenarios(tmp_path):
    # Missing container: create then start.
    command = FakeCommandRunner()
    command.responses = [
        missing_container(),
        response(0),
        response(0, "running\n"),
        response(0),
        response(0, "running\n"),
    ]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, image=IMAGE)
    assert argvs(command) == [
        [PODMAN, "container", "inspect", "--format", "{{.State.Status}}", CONTAINER],
        [PODMAN, "create", "--name", CONTAINER, IMAGE],
        [PODMAN, "container", "inspect", "--format", "{{.State.Status}}", CONTAINER],
        [PODMAN, "start", CONTAINER],
        [PODMAN, "container", "inspect", "--format", "{{.State.Status}}", CONTAINER],
    ]
    assert [change.summary for change in ctx.changes] == [
        f"create container {CONTAINER}; start container {CONTAINER}",
    ]
    assert ctx.changes[0].action == "podman.container"
    assert call_data(ctx.changes[0])["actions"] == ["create", "start"]

    # Existing stopped container: start it.
    command = FakeCommandRunner()
    command.responses = [response(0, "exited\n"), response(0), response(0, "running\n")]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="started")
    assert argvs(command)[1] == [PODMAN, "start", CONTAINER]
    assert len(ctx.changes) == 1

    # Already running: no-op.
    command = FakeCommandRunner()
    command.responses = [response(0, "running\n")]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="started")
    assert len(command.calls) == 1
    assert ctx.changes == []


def test_container_present_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [missing_container(), response(0), response(0, "created\n")]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="present", image=IMAGE)
    assert [change.summary for change in ctx.changes] == [f"create container {CONTAINER}"]
    assert argvs(command)[1] == [PODMAN, "create", "--name", CONTAINER, IMAGE]

    command = FakeCommandRunner()
    command.responses = [response(0, "created\n")]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="present", image=IMAGE)
    assert ctx.changes == []


def test_container_stopped_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "running\n"), response(0), response(0, "exited\n")]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="stopped", stop_timeout=5)
    assert argvs(command)[1] == [PODMAN, "stop", "--time=5", CONTAINER]
    assert len(ctx.changes) == 1

    command = FakeCommandRunner()
    command.responses = [missing_container()]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="stopped")
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [
        response(125, stderr='Error: no container with name or ID "example" found: no such container\n')
    ]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="stopped")
    assert ctx.changes == []


def test_container_absent_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "exited\n"), response(0), missing_container()]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="absent")
    assert argvs(command)[1] == [PODMAN, "rm", "--force", CONTAINER]
    assert len(ctx.changes) == 1

    command = FakeCommandRunner()
    command.responses = [missing_container()]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="absent")
    assert ctx.changes == []


def test_container_recreate_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "running\n"),
        response(0),
        missing_container(),
        response(0),
        response(0, "created\n"),
        response(0),
        response(0, "running\n"),
    ]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, image=IMAGE, recreate=True)
    assert [change.summary for change in ctx.changes] == [
        f"remove container {CONTAINER}; create container {CONTAINER}; start container {CONTAINER}",
    ]
    assert call_data(ctx.changes[0])["actions"] == ["remove", "create", "start"]

    ctx = context(tmp_path)
    with pytest.raises(TaskFailed, match="recreate is only supported"):
        podman.container(ctx, CONTAINER, state="stopped", image=IMAGE, recreate=True)
    with pytest.raises(TaskFailed, match="recreate is only supported"):
        podman.container(ctx, CONTAINER, state="absent", image=IMAGE, recreate=True)


def test_container_options_and_command_encoding(tmp_path):
    command = FakeCommandRunner()
    command.responses = [missing_container(), response(0), response(0, "created\n")]
    ctx = context(tmp_path, command=command)

    podman.container(
        ctx,
        CONTAINER,
        state="present",
        image=IMAGE,
        command="sh -c 'echo hello world'",
        options={
            "publish": "8080:80",
            "env": ["FOO=bar", "BAZ=qux"],
            "label": {"app": "web"},
            "read_only": True,
            "rm": False,
            "stop_timeout": 10,
        },
    )

    assert argvs(command)[1] == [
        PODMAN,
        "create",
        "--name",
        CONTAINER,
        "--publish=8080:80",
        "--env=FOO=bar",
        "--env=BAZ=qux",
        "--label=app=web",
        "--read-only",
        "--rm=false",
        "--stop-timeout=10",
        IMAGE,
        "sh",
        "-c",
        "echo hello world",
    ]

    # Sequence commands are preserved verbatim.
    command = FakeCommandRunner()
    command.responses = [missing_container(), response(0), response(0, "created\n")]
    ctx = context(tmp_path, command=command)
    podman.container(ctx, CONTAINER, state="present", image=IMAGE, command=["/init", "exit"])
    assert argvs(command)[1][-2:] == ["/init", "exit"]


def test_container_inspect_failures(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "paused\n")]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="paused"):
        podman.container(ctx, CONTAINER)

    command = FakeCommandRunner()
    command.responses = [response(125, stderr="storage error")]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="could not inspect container"):
        podman.container(ctx, CONTAINER, image=IMAGE)


def test_container_verification_failures_are_reported(tmp_path):
    create = FakeCommandRunner()
    create.responses = [missing_container(), response(0), missing_container()]
    with pytest.raises(TaskFailed, match="missing after create"):
        podman.container(context(tmp_path, command=create), CONTAINER, image=IMAGE)

    start = FakeCommandRunner()
    start.responses = [
        missing_container(),
        response(0),
        response(0, "created\n"),
        response(0),
        response(0, "created\n"),
    ]
    with pytest.raises(TaskFailed, match="not running after start"):
        podman.container(context(tmp_path, command=start), CONTAINER, image=IMAGE)

    stop = FakeCommandRunner()
    stop.responses = [
        response(0, "running\n"),
        response(0),
        response(0, "running\n"),
    ]
    with pytest.raises(TaskFailed, match="did not stop"):
        podman.container(context(tmp_path, command=stop), CONTAINER, state="stopped")

    remove = FakeCommandRunner()
    remove.responses = [response(0, "exited\n"), response(0), response(0, "exited\n")]
    with pytest.raises(TaskFailed, match="still present after removal"):
        podman.container(context(tmp_path, command=remove), CONTAINER, state="absent")


def test_container_start_verification_failures_are_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        missing_container(),
        response(0),
        response(0, "created\n"),
        response(0),
        response(0, "exited\n"),
    ]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match=r"not running after start.*one-shot"):
        podman.container(ctx, CONTAINER, image=IMAGE)

    command = FakeCommandRunner()
    command.responses = [
        missing_container(),
        response(0),
        response(0, "created\n"),
        response(0),
        missing_container(),
    ]
    ctx = context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="missing after start"):
        podman.container(ctx, CONTAINER, image=IMAGE)


def test_registry_write_scenarios(tmp_path):
    target = tmp_path / "registries.conf.d" / "example.conf"
    target.parent.mkdir()
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.registry(
        ctx,
        "example",
        location="docker.io",
        prefix="mirror.example.com",
        insecure=True,
        blocked=False,
        mirrors=["mirror.example.com", "mirror.example.com"],
        path=target,
    )
    assert target.read_text() == (
        "# Managed by kitup.\n"
        "[[registry]]\n"
        'location = "docker.io"\n'
        'prefix = "mirror.example.com"\n'
        "insecure = true\n"
        "blocked = false\n"
        "\n"
        "[[registry.mirror]]\n"
        'location = "mirror.example.com"\n'
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert call_data(ctx.changes[0]) == {
        "path": str(target),
        "registry": "example",
    }

    target = tmp_path / "example.conf"
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))
    podman.registry(ctx, "example", location="docker.io", path=target)
    podman.registry(ctx, "example", location="docker.io", path=target)
    assert len(ctx.changes) == 1
    assert ctx.calls[-1].summary == f"ensure registry example in {target}"
    assert "prefix =" not in target.read_text()

    podman.registry(ctx, "example", location="quay.io", path=target)
    assert len(ctx.changes) == 2
    assert 'location = "quay.io"' in target.read_text()


def test_registry_absent_scenarios(tmp_path):
    target = tmp_path / "example.conf"
    target.write_text("old\n")
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    result = podman.registry(ctx, "example", state="absent", path=target)
    assert not target.exists()
    assert len(ctx.changes) == 1
    assert result.action == "podman.registry"

    result = podman.registry(ctx, "example", state="absent", path=target)
    assert len(ctx.changes) == 1
    assert result.action == "podman.registry"
    assert ctx.calls[-1].summary == f"ensure registry example is absent from {target}"

    target = tmp_path / "dir.conf"
    target.mkdir()
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))
    with pytest.raises(TaskFailed, match="path is a directory"):
        podman.registry(ctx, "example", state="absent", path=target)
    assert target.is_dir()


def test_registry_appends_conf_suffix(tmp_path):
    target = tmp_path / "example"
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.registry(ctx, "example", location="docker.io", path=target)

    assert (tmp_path / "example.conf").exists()


def test_registry_user_scope_uses_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    directory = tmp_path / ".config" / "containers" / "registries.conf.d"
    directory.mkdir(parents=True)
    ctx = context(tmp_path)

    podman.registry(ctx, "example", location="docker.io", scope="user")

    assert (directory / "example.conf").exists()
    assert ctx.sudo.refresh_count == 0


def test_login_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0)]
    ctx = context(tmp_path, command=command)
    podman.login(ctx, REGISTRY, username="kitup", password="secret")
    assert argvs(command)[1] == [
        PODMAN,
        "login",
        "--username=kitup",
        "--password-stdin",
        REGISTRY,
    ]
    assert command.calls[1][1]["stdin"] == "secret\n"
    assert call_data(ctx.changes[0]) == {
        "registry": REGISTRY,
        "username": "kitup",
    }

    command = FakeCommandRunner()
    command.responses = [response(0, "kitup\n")]
    ctx = context(tmp_path, command=command)
    podman.login(ctx, REGISTRY, username="kitup", password="secret")
    assert len(command.calls) == 1
    assert ctx.changes == []

    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)
    podman.login(ctx, REGISTRY, username="kitup", password="secret", force=True)
    assert argvs(command) == [[PODMAN, "login", "--username=kitup", "--password-stdin", REGISTRY]]

    authfile = tmp_path / "auth.json"
    command = FakeCommandRunner()
    command.responses = [response(1), response(0)]
    ctx = context(tmp_path, command=command)
    podman.login(
        ctx,
        REGISTRY,
        username="kitup",
        password="secret",
        authfile=authfile,
        tls_verify=False,
    )
    assert argvs(command)[1] == [
        PODMAN,
        "login",
        "--username=kitup",
        "--password-stdin",
        f"--authfile={authfile}",
        "--tls-verify=false",
        REGISTRY,
    ]


def test_logout_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "kitup\n"), response(0)]
    ctx = context(tmp_path, command=command)
    podman.logout(ctx, REGISTRY)
    assert argvs(command)[1] == [PODMAN, "logout", REGISTRY]
    assert len(ctx.changes) == 1

    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = context(tmp_path, command=command)
    podman.logout(ctx, REGISTRY)
    assert ctx.changes == []


def test_generate_systemd_scenarios(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, UNIT), response(0, UNIT)]
    ctx = context(tmp_path, command=command, sudo=FakeSudo(is_root=True))

    podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)
    target = tmp_path / f"container-{CONTAINER}.service"
    assert target.read_text() == UNIT
    assert argvs(command) == [[PODMAN, "generate", "systemd", "--name", "--no-header", CONTAINER]]
    assert call_data(ctx.changes[0]) == {
        "path": str(target),
        "container": CONTAINER,
    }

    podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)
    assert len(ctx.changes) == 1
    assert ctx.calls[-1].summary == f"ensure systemd unit for {CONTAINER} in {target}"


def test_generate_systemd_flags(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, UNIT)]
    ctx = context(tmp_path, command=command, sudo=FakeSudo(is_root=True))

    podman.generate_systemd(
        ctx,
        CONTAINER,
        dest=tmp_path,
        new=True,
        restart_policy="always",
        restart_sec=5,
        start_timeout=10,
        stop_timeout=20,
        container_prefix="svc",
        separator=".",
        env={"FOO": "bar", "COUNT": 2},
        wants=["network-online.target"],
        requires="network.target",
        after=["network.target"],
    )

    assert argvs(command)[0] == [
        PODMAN,
        "generate",
        "systemd",
        "--name",
        "--new",
        "--no-header",
        "--restart-policy=always",
        "--restart-sec=5",
        "--start-timeout=10",
        "--stop-timeout=20",
        "--container-prefix=svc",
        "--separator=.",
        "--env=FOO=bar",
        "--env=COUNT=2",
        "--wants=network-online.target",
        "--requires=network.target",
        "--after=network.target",
        CONTAINER,
    ]
    assert (tmp_path / f"svc.{CONTAINER}.service").exists()


def test_generate_systemd_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="boom")]
    ctx = context(tmp_path, command=command, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="could not generate systemd unit"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)


def test_generate_systemd_user_scope_uses_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    directory = tmp_path / ".config" / "systemd" / "user"
    directory.mkdir(parents=True)
    command = FakeCommandRunner()
    command.responses = [response(0, UNIT)]
    ctx = context(tmp_path, command=command)

    podman.generate_systemd(ctx, CONTAINER, scope="user")

    assert (directory / f"container-{CONTAINER}.service").exists()
    assert command.calls[0][1]["sudo"] is False


def test_quadlet_scenarios(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.quadlet(
        ctx,
        CONTAINER,
        image=IMAGE,
        dest=tmp_path,
        command=["/init", "serve"],
        stop_timeout=10,
        options={"publish": "8080:80", "env": ["FOO=bar"]},
        extra_options={"Volume": ["/srv/data:/data", "/srv/cache:/cache"]},
    )
    target = tmp_path / f"{CONTAINER}.container"
    assert target.read_text() == (
        "[Unit]\n"
        f"Description=kitup managed container {CONTAINER}\n"
        "\n"
        "[Container]\n"
        f"ContainerName={CONTAINER}\n"
        f"Image={IMAGE}\n"
        "Exec=/init serve\n"
        "StopTimeout=10\n"
        "PodmanArgs=--publish=8080:80\n"
        "PodmanArgs=--env=FOO=bar\n"
        "Volume=/srv/data:/data\n"
        "Volume=/srv/cache:/cache\n"
        "\n"
        "[Service]\n"
        "Restart=always\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    assert call_data(ctx.changes[0]) == {
        "path": str(target),
        "container": CONTAINER,
    }

    # A command string is emitted verbatim.
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))
    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, command="/init exit")
    assert "Exec=/init exit\n" in target.read_text()

    # An empty install target omits the [Install] section.
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))
    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, install_target="")
    assert "[Install]" not in target.read_text()

    # Repeated runs are idempotent.
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))
    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path)
    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path)
    assert len(ctx.changes) == 1
    assert ctx.calls[-1].summary == f"ensure quadlet {CONTAINER} in {target}"


def test_quadlet_user_scope_uses_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    directory = tmp_path / ".config" / "containers" / "systemd"
    directory.mkdir(parents=True)
    ctx = context(tmp_path)

    podman.quadlet(ctx, CONTAINER, image=IMAGE, scope="user")

    assert (directory / f"{CONTAINER}.container").exists()
    assert ctx.sudo.refresh_count == 0


def test_scope_validation(tmp_path):
    ctx = context(tmp_path)
    for call in (
        lambda: podman.registry(ctx, "example", location="docker.io", scope="session"),
        lambda: podman.generate_systemd(ctx, CONTAINER, scope="session"),
        lambda: podman.quadlet(ctx, CONTAINER, image=IMAGE, scope="session"),
    ):
        with pytest.raises(TaskFailed, match="invalid podman scope"):
            call()

    root_ctx = context(tmp_path, sudo=FakeSudo(is_root=True))
    for call in (
        lambda: podman.registry(root_ctx, "example", location="docker.io", scope="user"),
        lambda: podman.generate_systemd(root_ctx, CONTAINER, scope="user"),
        lambda: podman.quadlet(root_ctx, CONTAINER, image=IMAGE, scope="user"),
    ):
        with pytest.raises(TaskFailed, match="cannot run as root"):
            call()


def test_invalid_arguments_are_rejected(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="invalid registry name"):
        podman.registry(ctx, "../escape", location="docker.io")
    with pytest.raises(TaskFailed, match="location is required"):
        podman.registry(ctx, "example")
    with pytest.raises(TaskFailed, match="invalid registry state"):
        podman.registry(ctx, "example", state="invalid")

    with pytest.raises(TaskFailed, match="username must not be empty"):
        podman.login(ctx, REGISTRY, username=" ", password="secret")
    with pytest.raises(TaskFailed, match="password must not be empty"):
        podman.login(ctx, REGISTRY, username="kitup", password=" ")
    with pytest.raises(TaskFailed, match="must not contain newlines"):
        podman.login(ctx, REGISTRY, username="kitup", password="secret\nmore")

    with pytest.raises(TaskFailed, match="invalid systemd restart policy"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, restart_policy="sometimes")
    with pytest.raises(TaskFailed, match="non-negative integer"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, restart_sec=-1)
    with pytest.raises(TaskFailed, match="invalid systemd wants"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, wants=["bad unit"])
    with pytest.raises(TaskFailed, match="invalid environment variable name"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, env={"bad-key": "value"})

    for command in ("", "   "):
        with pytest.raises(TaskFailed, match="quadlet command must not be empty"):
            podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, command=command)
    with pytest.raises(TaskFailed, match="options must be a mapping"):
        podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, options=["--rm"])
    with pytest.raises(TaskFailed, match="managed by kitup"):
        podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, extra_options={"Image": "other"})
    with pytest.raises(TaskFailed, match="must be a string or a sequence"):
        podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, extra_options={"Volume": 1})
    with pytest.raises(TaskFailed, match="must contain strings"):
        podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, extra_options={"Volume": [1]})

    assert not (tmp_path / f"{CONTAINER}.container").exists()


def test_missing_podman_binary_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: None)
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="podman is required"):
        podman.image(ctx, IMAGE)
    with pytest.raises(TaskFailed, match="podman is required"):
        podman.container(ctx, CONTAINER, image=IMAGE)
    with pytest.raises(TaskFailed, match="podman is required"):
        podman.login(ctx, REGISTRY, username="kitup", password="secret")
    with pytest.raises(TaskFailed, match="podman is required"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)


def test_file_backed_functions_do_not_require_podman(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup._utils.executables.shutil.which", lambda name: None)
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.registry(ctx, "example", location="docker.io", path=tmp_path / "example.conf")
    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path)

    assert (tmp_path / "example.conf").exists()
    assert (tmp_path / f"{CONTAINER}.container").exists()


def test_dry_run_does_not_write_files(tmp_path):
    ctx = context(tmp_path, dry_run=True, sudo=FakeSudo(is_root=True))

    podman.registry(ctx, "example", location="docker.io", path=tmp_path / "example.conf")
    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path)

    assert not (tmp_path / "example.conf").exists()
    assert not (tmp_path / f"{CONTAINER}.container").exists()
    assert len(ctx.changes) == 2


def test_dry_run_does_not_mutate_containers(tmp_path):
    command = FakeCommandRunner()
    command.responses = [missing_container()]
    ctx = context(tmp_path, dry_run=True, command=command)

    podman.container(ctx, CONTAINER, image=IMAGE)

    assert argvs(command) == [[PODMAN, "container", "inspect", "--format", "{{.State.Status}}", CONTAINER]]
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0])["actions"] == ["create", "start"]
