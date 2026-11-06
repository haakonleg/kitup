import stat

import pytest

from kitup.errors import TaskFailed
from kitup.modules import podman
from kitup.registry import Bundle

from .helpers import FakeCommandRunner, FakeSudo, call_data, completed, make_context

PODMAN = "/usr/bin/podman"
IMAGE = "localhost/example:latest"
CONTAINER = "example"
REGISTRY = "registry.example.com"
UNIT = "[Unit]\nDescription=test\n\n[Service]\nExecStart=/usr/bin/podman start example\n"


@pytest.fixture(autouse=True)
def podman_available(monkeypatch):
    monkeypatch.setattr(
        "kitup.modules.podman._common.shutil.which",
        lambda name: PODMAN if name == "podman" else None,
    )


def response(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return lambda argv: completed(argv, returncode, stdout, stderr)


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


def calls(command):
    return [call[0] for call in command.calls]


def test_image_present_pulls_missing_image(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE)

    assert calls(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "pull", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {"image": IMAGE}
    assert ctx.changes[0].action == "podman.image"


def test_image_present_is_a_noop_when_present(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE)

    assert calls(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []


def test_image_pull_always_pulls_even_when_present(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, pull="always")

    assert calls(command) == [[PODMAN, "pull", IMAGE]]
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

    assert calls(command)[1] == [
        PODMAN,
        "pull",
        "--platform",
        "linux/arm64",
        IMAGE,
    ]


def test_image_absent_removes_existing_image(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(1)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, state="absent")

    assert calls(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "rmi", IMAGE],
        [PODMAN, "image", "exists", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {"image": IMAGE}
    assert ctx.changes[0].action == "podman.image"


def test_image_absent_is_a_noop_when_missing(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, state="absent")

    assert calls(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []


def test_image_absent_force_adds_flag(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(1)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, state="absent", force=True)

    assert calls(command)[1] == [PODMAN, "rmi", "--force", IMAGE]


def test_image_removal_verification_failure_is_reported(tmp_path):
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


def test_image_source_pulls_and_verifies_local_name(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar")

    assert calls(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "pull", "oci-archive:/tmp/pull.tar"],
        [PODMAN, "image", "exists", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {
        "image": IMAGE,
        "source": "oci-archive:/tmp/pull.tar",
    }


def test_image_source_is_idempotent_when_local_name_exists(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar")

    assert calls(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []


def test_image_source_pull_always_pulls_and_verifies(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar", pull="always")

    assert calls(command) == [
        [PODMAN, "pull", "oci-archive:/tmp/pull.tar"],
        [PODMAN, "image", "exists", IMAGE],
    ]


def test_image_source_absent_removes_local_name(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0), response(0), response(1)]
    ctx = context(tmp_path, command=command)

    podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar", state="absent")

    assert calls(command) == [
        [PODMAN, "image", "exists", IMAGE],
        [PODMAN, "rmi", IMAGE],
        [PODMAN, "image", "exists", IMAGE],
    ]
    assert call_data(ctx.changes[0]) == {"image": IMAGE}
    assert ctx.changes[0].action == "podman.image"


def test_image_source_pull_verification_failure_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(1)]
    ctx = context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="missing after pull"):
        podman.image(ctx, IMAGE, source="oci-archive:/tmp/pull.tar")


def test_image_rejects_transport_name_without_source(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="name must be a local image name"):
        podman.image(ctx, "oci-archive:/tmp/pull.tar")


def test_image_rejects_non_transport_source(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="source must be a transport-qualified"):
        podman.image(ctx, IMAGE, source="localhost/other:latest")


def test_image_rejects_invalid_arguments(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid image state"):
        podman.image(ctx, IMAGE, state="invalid")
    with pytest.raises(TaskFailed, match="invalid image pull policy"):
        podman.image(ctx, IMAGE, pull="newer")
    with pytest.raises(TaskFailed, match="image force must be a boolean"):
        podman.image(ctx, IMAGE, force="yes")
    with pytest.raises(TaskFailed, match="image name must not be empty"):
        podman.image(ctx, "  ")


def test_build_creates_missing_tag(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx = context(tmp_path, command=command)

    podman.build(ctx, build_dir, tag=IMAGE)

    assert calls(command)[1] == [PODMAN, "build", "-t", IMAGE, str(build_dir)]
    assert call_data(ctx.changes[0]) == {
        "image": IMAGE,
        "context": str(build_dir),
    }


def test_build_existing_tag_is_a_noop_without_force(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)

    podman.build(ctx, build_dir, tag=IMAGE)

    assert calls(command) == [[PODMAN, "image", "exists", IMAGE]]
    assert ctx.changes == []


def test_build_force_rebuilds(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    command = FakeCommandRunner()
    command.responses = [response(0), response(0)]
    ctx = context(tmp_path, command=command)

    podman.build(ctx, build_dir, tag=IMAGE, force=True)

    assert calls(command)[0] == [PODMAN, "build", "-t", IMAGE, str(build_dir)]


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

    assert calls(command)[1] == [
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


def test_build_resolves_bare_context_under_files(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx, bundle_dir = bundle_context(tmp_path, command=command)
    build_dir = bundle_dir / "files" / "container"
    build_dir.mkdir()

    podman.build(ctx, "container", tag=IMAGE)

    assert calls(command)[1] == [PODMAN, "build", "-t", IMAGE, str(build_dir)]


def test_build_resolves_nested_context_against_bundle(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx, bundle_dir = bundle_context(tmp_path, command=command)
    build_dir = bundle_dir / "nested" / "container"
    build_dir.mkdir(parents=True)

    podman.build(ctx, "nested/container", tag=IMAGE)

    assert calls(command)[1] == [PODMAN, "build", "-t", IMAGE, str(build_dir)]


def test_build_resolves_containerfile_independently(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx, bundle_dir = bundle_context(tmp_path, command=command)
    build_dir = bundle_dir / "files" / "container"
    build_dir.mkdir()
    containerfile = bundle_dir / "files" / "Containerfile"
    containerfile.write_text("FROM scratch\n")

    podman.build(ctx, "container", tag=IMAGE, containerfile="Containerfile")

    assert calls(command)[1] == [
        PODMAN,
        "build",
        "-t",
        IMAGE,
        "-f",
        str(containerfile),
        str(build_dir),
    ]


def test_build_dot_resolves_to_bundle_root(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0), response(0)]
    ctx, bundle_dir = bundle_context(tmp_path, command=command)

    podman.build(ctx, ".", tag=IMAGE)

    assert calls(command)[1] == [PODMAN, "build", "-t", IMAGE, str(bundle_dir)]


def test_build_relative_path_requires_a_bundle(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="cannot resolve relative source path"):
        podman.build(ctx, "container", tag=IMAGE)


def test_build_rejects_missing_context_and_containerfile(tmp_path):
    ctx = context(tmp_path)
    missing = tmp_path / "missing"
    with pytest.raises(TaskFailed, match="not a directory"):
        podman.build(ctx, missing, tag=IMAGE)

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    with pytest.raises(TaskFailed, match="not a regular file"):
        podman.build(ctx, build_dir, tag=IMAGE, containerfile=tmp_path / "missing")


def test_build_rejects_invalid_build_argument_name(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid build argument name"):
        podman.build(ctx, build_dir, tag=IMAGE, build_args={"bad-name": "value"})


def test_tag_missing_source_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not inspect image"):
        podman.tag(ctx, IMAGE, "localhost/target:latest")


def test_tag_adds_missing_and_different_targets(tmp_path):
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

    assert calls(command)[4] == [
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


def test_tag_is_a_noop_when_targets_match(tmp_path):
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


def test_tag_target_check_storage_error_is_reported(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "sha256:source\n"), response(125, stderr="storage error")]
    ctx = context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="could not check image"):
        podman.tag(ctx, IMAGE, "localhost/target:latest")


def test_container_started_creates_and_starts_missing_container(tmp_path):
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

    assert calls(command) == [
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


def test_container_started_starts_existing_stopped_container(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "exited\n"),
        response(0),
        response(0, "running\n"),
    ]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="started")

    assert calls(command)[1] == [PODMAN, "start", CONTAINER]
    assert len(ctx.changes) == 1


def test_container_started_running_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "running\n")]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="started")

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_container_present_creates_without_starting(tmp_path):
    command = FakeCommandRunner()
    command.responses = [missing_container(), response(0), response(0, "created\n")]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="present", image=IMAGE)

    assert [change.summary for change in ctx.changes] == [f"create container {CONTAINER}"]
    assert calls(command)[1] == [PODMAN, "create", "--name", CONTAINER, IMAGE]


def test_container_present_existing_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "created\n")]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="present", image=IMAGE)

    assert ctx.changes == []


def test_container_stopped_stops_running_container(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(0, "running\n"),
        response(0),
        response(0, "exited\n"),
    ]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="stopped", stop_timeout=5)

    assert calls(command)[1] == [PODMAN, "stop", "--time=5", CONTAINER]
    assert len(ctx.changes) == 1


def test_container_stopped_missing_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [missing_container()]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="stopped")

    assert ctx.changes == []


def test_container_missing_alternate_error_message_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(125, stderr='Error: no container with name or ID "example" found: no such container\n')
    ]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="stopped")

    assert ctx.changes == []


def test_container_absent_removes_existing_container(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "exited\n"), response(0), missing_container()]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="absent")

    assert calls(command)[1] == [PODMAN, "rm", "--force", CONTAINER]
    assert len(ctx.changes) == 1


def test_container_absent_missing_is_a_noop(tmp_path):
    command = FakeCommandRunner()
    command.responses = [missing_container()]
    ctx = context(tmp_path, command=command)

    podman.container(ctx, CONTAINER, state="absent")

    assert ctx.changes == []


def test_container_recreate_removes_creates_and_starts(tmp_path):
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


def test_container_recreate_rejects_stopped_and_absent(tmp_path):
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

    assert calls(command)[1] == [
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


def test_container_command_sequence_is_preserved(tmp_path):
    command = FakeCommandRunner()
    command.responses = [missing_container(), response(0), response(0, "created\n")]
    ctx = context(tmp_path, command=command)

    podman.container(
        ctx,
        CONTAINER,
        state="present",
        image=IMAGE,
        command=["/init", "exit"],
    )

    assert calls(command)[1][-2:] == ["/init", "exit"]


def test_container_paused_is_rejected(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "paused\n")]
    ctx = context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="paused"):
        podman.container(ctx, CONTAINER)


def test_container_inspect_storage_error_is_reported(tmp_path):
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


def test_container_started_short_lived_container_is_rejected(tmp_path):
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


def test_container_started_missing_after_start_is_reported(tmp_path):
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


def test_registry_present_writes_toml(tmp_path):
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


def test_registry_is_idempotent_and_rewrites_changed_content(tmp_path):
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


def test_registry_absent_removes_file(tmp_path):
    target = tmp_path / "example.conf"
    target.write_text("old\n")
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.registry(ctx, "example", state="absent", path=target)

    assert not target.exists()
    assert len(ctx.changes) == 1

    podman.registry(ctx, "example", state="absent", path=target)
    assert len(ctx.changes) == 1
    assert ctx.calls[-1].summary == f"ensure registry example is absent from {target}"


def test_registry_absent_rejects_directory(tmp_path):
    target = tmp_path / "example.conf"
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


def test_registry_scope_validation(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid podman scope"):
        podman.registry(ctx, "example", location="docker.io", scope="session")
    with pytest.raises(TaskFailed, match="cannot run as root"):
        podman.registry(
            context(tmp_path, sudo=FakeSudo(is_root=True)),
            "example",
            location="docker.io",
            scope="user",
        )


def test_registry_rejects_invalid_arguments(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="invalid registry name"):
        podman.registry(ctx, "../escape", location="docker.io")
    with pytest.raises(TaskFailed, match="location is required"):
        podman.registry(ctx, "example")
    with pytest.raises(TaskFailed, match="invalid registry state"):
        podman.registry(ctx, "example", state="invalid")


def test_registry_system_scope_uses_sudo(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "kitup.modules.podman._registries.file_ops.apply_bytes",
        lambda *args, **kwargs: captured.update(kwargs),
    )
    ctx = context(tmp_path)

    podman.registry(ctx, "example", location="docker.io", path=tmp_path / "example.conf")

    assert captured["sudo"] is True


def test_login_uses_password_stdin(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1), response(0)]
    ctx = context(tmp_path, command=command)

    podman.login(ctx, REGISTRY, username="kitup", password="secret")

    assert calls(command)[1] == [
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


def test_login_is_a_noop_for_the_same_user(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "kitup\n")]
    ctx = context(tmp_path, command=command)

    podman.login(ctx, REGISTRY, username="kitup", password="secret")

    assert len(command.calls) == 1
    assert ctx.changes == []


def test_login_force_logs_in_again(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0)]
    ctx = context(tmp_path, command=command)

    podman.login(ctx, REGISTRY, username="kitup", password="secret", force=True)

    assert calls(command) == [[PODMAN, "login", "--username=kitup", "--password-stdin", REGISTRY]]


def test_login_authfile_and_tls_flags(tmp_path):
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

    assert calls(command)[1] == [
        PODMAN,
        "login",
        "--username=kitup",
        "--password-stdin",
        f"--authfile={authfile}",
        "--tls-verify=false",
        REGISTRY,
    ]


def test_logout_logs_out_when_logged_in(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, "kitup\n"), response(0)]
    ctx = context(tmp_path, command=command)

    podman.logout(ctx, REGISTRY)

    assert calls(command)[1] == [PODMAN, "logout", REGISTRY]
    assert len(ctx.changes) == 1


def test_logout_is_a_noop_when_not_logged_in(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1)]
    ctx = context(tmp_path, command=command)

    podman.logout(ctx, REGISTRY)

    assert ctx.changes == []


def test_login_rejects_invalid_credentials(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="username must not be empty"):
        podman.login(ctx, REGISTRY, username=" ", password="secret")
    with pytest.raises(TaskFailed, match="password must not be empty"):
        podman.login(ctx, REGISTRY, username="kitup", password=" ")
    with pytest.raises(TaskFailed, match="must not contain newlines"):
        podman.login(ctx, REGISTRY, username="kitup", password="secret\nmore")


def test_generate_systemd_writes_unit(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, UNIT)]
    ctx = context(tmp_path, command=command, sudo=FakeSudo(is_root=True))

    podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)

    target = tmp_path / f"container-{CONTAINER}.service"
    assert target.read_text() == UNIT
    assert calls(command) == [[PODMAN, "generate", "systemd", "--name", "--no-header", CONTAINER]]
    assert call_data(ctx.changes[0]) == {
        "path": str(target),
        "container": CONTAINER,
    }


def test_generate_systemd_is_idempotent(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(0, UNIT), response(0, UNIT)]
    ctx = context(tmp_path, command=command, sudo=FakeSudo(is_root=True))

    podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)
    podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)

    assert len(ctx.changes) == 1
    target = tmp_path / f"container-{CONTAINER}.service"
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

    assert calls(command)[0] == [
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


def test_generate_systemd_scope_validation(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid podman scope"):
        podman.generate_systemd(ctx, CONTAINER, scope="session")
    with pytest.raises(TaskFailed, match="cannot run as root"):
        podman.generate_systemd(
            context(tmp_path, sudo=FakeSudo(is_root=True)),
            CONTAINER,
            scope="user",
        )


def test_generate_systemd_rejects_invalid_arguments(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="invalid systemd restart policy"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, restart_policy="sometimes")
    with pytest.raises(TaskFailed, match="non-negative integer"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, restart_sec=-1)
    with pytest.raises(TaskFailed, match="invalid systemd wants"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, wants=["bad unit"])
    with pytest.raises(TaskFailed, match="invalid environment variable name"):
        podman.generate_systemd(ctx, CONTAINER, dest=tmp_path, env={"bad-key": "value"})


def test_generate_systemd_system_scope_uses_sudo(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "kitup.modules.podman._units.file_ops.apply_bytes",
        lambda *args, **kwargs: captured.update(kwargs),
    )
    command = FakeCommandRunner()
    command.responses = [response(0, UNIT)]
    ctx = context(tmp_path, command=command)

    podman.generate_systemd(ctx, CONTAINER, dest=tmp_path)

    assert captured["sudo"] is True


def test_quadlet_renders_sections(tmp_path):
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


def test_quadlet_command_string_is_verbatim(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, command="/init exit")

    assert "Exec=/init exit\n" in (tmp_path / f"{CONTAINER}.container").read_text()


@pytest.mark.parametrize("command", ["", "   "])
def test_quadlet_rejects_empty_command_string(tmp_path, command):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="quadlet command must not be empty"):
        podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, command=command)

    assert not (tmp_path / f"{CONTAINER}.container").exists()


def test_quadlet_rejects_non_mapping_options(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="options must be a mapping"):
        podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, options=["--rm"])


def test_quadlet_install_target_empty_omits_install(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path, install_target="")

    content = (tmp_path / f"{CONTAINER}.container").read_text()
    assert "[Install]" not in content


def test_quadlet_rejects_native_extra_options(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="managed by kitup"):
        podman.quadlet(
            ctx,
            CONTAINER,
            image=IMAGE,
            dest=tmp_path,
            extra_options={"Image": "other"},
        )


def test_quadlet_rejects_invalid_extra_option_values(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    with pytest.raises(TaskFailed, match="must be a string or a sequence"):
        podman.quadlet(
            ctx,
            CONTAINER,
            image=IMAGE,
            dest=tmp_path,
            extra_options={"Volume": 1},
        )
    with pytest.raises(TaskFailed, match="must contain strings"):
        podman.quadlet(
            ctx,
            CONTAINER,
            image=IMAGE,
            dest=tmp_path,
            extra_options={"Volume": [1]},
        )


def test_quadlet_is_idempotent(tmp_path):
    ctx = context(tmp_path, sudo=FakeSudo(is_root=True))

    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path)
    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path)

    assert len(ctx.changes) == 1
    target = tmp_path / f"{CONTAINER}.container"
    assert ctx.calls[-1].summary == f"ensure quadlet {CONTAINER} in {target}"


def test_quadlet_user_scope_uses_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    directory = tmp_path / ".config" / "containers" / "systemd"
    directory.mkdir(parents=True)
    ctx = context(tmp_path)

    podman.quadlet(ctx, CONTAINER, image=IMAGE, scope="user")

    assert (directory / f"{CONTAINER}.container").exists()
    assert ctx.sudo.refresh_count == 0


def test_quadlet_scope_validation(tmp_path):
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="invalid podman scope"):
        podman.quadlet(ctx, CONTAINER, image=IMAGE, scope="session")
    with pytest.raises(TaskFailed, match="cannot run as root"):
        podman.quadlet(
            context(tmp_path, sudo=FakeSudo(is_root=True)),
            CONTAINER,
            image=IMAGE,
            scope="user",
        )


def test_quadlet_system_scope_uses_sudo(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "kitup.modules.podman._quadlet.file_ops.apply_bytes",
        lambda *args, **kwargs: captured.update(kwargs),
    )
    ctx = context(tmp_path)

    podman.quadlet(ctx, CONTAINER, image=IMAGE, dest=tmp_path)

    assert captured["sudo"] is True


def test_missing_podman_binary_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup.modules.podman._common.shutil.which", lambda name: None)
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
    monkeypatch.setattr("kitup.modules.podman._common.shutil.which", lambda name: None)
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

    assert calls(command) == [[PODMAN, "container", "inspect", "--format", "{{.State.Status}}", CONTAINER]]
    assert len(ctx.changes) == 1
    assert call_data(ctx.changes[0])["actions"] == ["create", "start"]
