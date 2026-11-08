"""Integration tests for real Podman image, container, and unit management."""

import base64
import os
import shutil
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from .helpers import run, run_kitup, sudo, write_bundle

pytestmark = pytest.mark.integration

PREFIX = "kitup-test"
FIXTURE = Path("/srv/podman-fixture/build")
BASE_IMAGE = "localhost/kitup-fixture:latest"
PULL_IMAGE = "localhost/kitup-pull:latest"
BUILT_IMAGE = "localhost/kitup-built:latest"
SYSTEM_REGISTRY_DIR = Path("/etc/containers/registries.conf.d")
USER_REGISTRY_DIR = Path.home() / ".config/containers/registries.conf.d"
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
USER_UNIT_DIR = Path.home() / ".config/systemd/user"
SYSTEM_QUADLET_DIR = Path("/etc/containers/systemd")
USER_QUADLET_DIR = Path.home() / ".config/containers/systemd"


def _podman_env() -> dict[str, str]:
    """Return an environment without the session bus for Podman subprocesses."""
    return {key: value for key, value in os.environ.items() if key != "DBUS_SESSION_BUS_ADDRESS"}


@pytest.fixture(autouse=True)
def without_session_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Podman from probing the unavailable systemd user manager."""
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)


def _cleanup_podman(prefix: list[str]) -> None:
    names = run(
        [*prefix, "podman", "ps", "-a", "--format", "{{.Names}}"],
        check=False,
    ).stdout.splitlines()
    for name in names:
        if name.startswith(PREFIX):
            run([*prefix, "podman", "rm", "-f", name], check=False)

    images = run(
        [*prefix, "podman", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        check=False,
    ).stdout.splitlines()
    for image in images:
        if image.startswith(("localhost/kitup-test-", "localhost/kitup-built", "localhost/kitup-tag-")):
            run([*prefix, "podman", "rmi", "-f", image], check=False)


def _cleanup() -> None:
    _cleanup_podman([])

    system_reload = False
    user_reload = False
    for directory, prefix, scope in (
        (SYSTEM_REGISTRY_DIR, sudo(), None),
        (USER_REGISTRY_DIR, [], None),
        (SYSTEM_UNIT_DIR, sudo(), "system"),
        (USER_UNIT_DIR, [], "user"),
        (SYSTEM_QUADLET_DIR, sudo(), "system"),
        (USER_QUADLET_DIR, [], "user"),
    ):
        if not directory.exists():
            continue
        result = run(
            [*prefix, "find", str(directory), "-maxdepth", "1", "-name", f"*{PREFIX}-*", "-print", "-delete"],
            check=False,
        )
        if not result.stdout.strip():
            continue
        if scope == "system":
            system_reload = True
        elif scope == "user":
            user_reload = True

    if system_reload:
        run(sudo("systemctl", "daemon-reload"), check=False)
    if user_reload:
        run(["systemctl", "--user", "daemon-reload"], check=False)


@pytest.fixture(scope="session", autouse=True)
def podman_dirs() -> None:
    for directory in (USER_REGISTRY_DIR, USER_UNIT_DIR, USER_QUADLET_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    run(
        [
            "sudo",
            "-n",
            "mkdir",
            "-p",
            str(SYSTEM_REGISTRY_DIR),
            str(SYSTEM_UNIT_DIR),
            str(SYSTEM_QUADLET_DIR),
        ],
        check=False,
    )


@pytest.fixture(autouse=True)
def clean_podman(without_session_bus: None) -> Iterator[None]:
    yield
    _cleanup()


@pytest.fixture
def clean_rootful(without_session_bus: None) -> Iterator[None]:
    _cleanup_podman(sudo())
    yield
    _cleanup_podman(sudo())


@pytest.fixture(scope="session", autouse=True)
def fixture_archive(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    archive_dir = tmp_path_factory.mktemp("podman-fixture")
    archive = archive_dir / "pull.tar"
    run(["podman", "build", "-t", BASE_IMAGE, str(FIXTURE)], timeout=300, env=_podman_env())
    run(["podman", "build", "-t", PULL_IMAGE, str(FIXTURE)], timeout=300, env=_podman_env())
    run(
        ["podman", "save", "--format", "oci-archive", "-o", str(archive), PULL_IMAGE],
        timeout=300,
        env=_podman_env(),
    )
    run(["podman", "rmi", "-f", PULL_IMAGE], check=False, env=_podman_env())
    yield archive
    run(["podman", "rmi", "-f", BASE_IMAGE, PULL_IMAGE], check=False, env=_podman_env())
    archive.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def rootful_image() -> Iterator[str]:
    image = "localhost/kitup-rootful:latest"
    build = run(
        sudo("podman", "build", "-t", image, str(FIXTURE)),
        check=False,
        timeout=300,
        env=_podman_env(),
    )
    if build.returncode != 0:
        pytest.skip("rootful Podman storage is unavailable")
    yield image
    run(sudo("podman", "rmi", "-f", image), check=False, env=_podman_env())


def unique(prefix: str = PREFIX) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def apply_bundle(tmp_path: Path, name: str, source: str, *args: str) -> int:
    root = write_bundle(tmp_path / name, name, source)
    return run_kitup("apply", root, *args)


def podman(*args: str, check: bool = True, timeout: float = 120):
    return run(["podman", *args], check=check, timeout=timeout)


def container_status(name: str) -> str:
    return podman("container", "inspect", "--format", "{{.State.Status}}", name).stdout.strip()


def container_id(name: str) -> str:
    return podman("container", "inspect", "--format", "{{.Id}}", name).stdout.strip()


def image_id(name: str) -> str:
    return podman("image", "inspect", "--format", "{{.Id}}", name).stdout.strip()


def start_fixture_container(name: str) -> None:
    podman("create", "--name", name, BASE_IMAGE)
    podman("start", name)


def test_image_lifecycle(tmp_path: Path, fixture_archive: Path) -> None:
    image = f"localhost/{unique()}:latest"
    podman("tag", BASE_IMAGE, image)
    remove_source = f"""
    from kitup.modules import podman

    @task("remove image")
    def remove_image(ctx):
        podman.image(ctx, {image!r}, state="absent")
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "image_absent", remove_source) == 0
    assert podman("image", "exists", image, check=False).returncode != 0

    pull_source = f"""
    from kitup.modules import podman

    @task("pull image")
    def pull_image(ctx):
        podman.image(
            ctx,
            {PULL_IMAGE!r},
            source={f"oci-archive:{fixture_archive}"!r},
        )
        assert len(ctx.changes) == 1
        podman.image(
            ctx,
            {PULL_IMAGE!r},
            source={f"oci-archive:{fixture_archive}"!r},
        )
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "image_pull", pull_source) == 0
    assert podman("image", "exists", PULL_IMAGE).returncode == 0

    absent_source = f"""
    from kitup.modules import podman

    @task("remove image")
    def remove_image(ctx):
        podman.image(ctx, {PULL_IMAGE!r}, state="absent")
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "image_pull_absent", absent_source) == 0
    assert podman("image", "exists", PULL_IMAGE, check=False).returncode != 0


def test_image_pull_never_fails_when_missing(tmp_path: Path) -> None:
    image = f"localhost/{unique()}:latest"
    source = f"""
    from kitup.modules import podman

    @task("pull image")
    def pull_image(ctx):
        podman.image(ctx, {image!r}, pull="never")
    """
    assert apply_bundle(tmp_path, "image_never", source) != 0


def test_build_scenarios(tmp_path: Path) -> None:
    # Basic build, idempotent re-run, and force rebuild.
    source = f"""
    from kitup.modules import podman

    @task("build image")
    def build_image(ctx):
        podman.build(ctx, {str(FIXTURE)!r}, tag={BUILT_IMAGE!r})
        assert len(ctx.changes) == 1
        podman.build(ctx, {str(FIXTURE)!r}, tag={BUILT_IMAGE!r})
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "build_image", source) == 0
    assert podman("image", "exists", BUILT_IMAGE).returncode == 0

    force_source = f"""
    from kitup.modules import podman

    @task("force build image")
    def build_image(ctx):
        podman.build(ctx, {str(FIXTURE)!r}, tag={BUILT_IMAGE!r}, force=True)
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "build_image_force", force_source) == 0

    # Build args are applied.
    args_context = tmp_path / "build-args"
    args_context.mkdir()
    shutil.copy(FIXTURE / "busybox", args_context / "busybox")
    shutil.copy(FIXTURE / "init", args_context / "init")
    (args_context / "Containerfile").write_text(
        "FROM scratch\nCOPY busybox /busybox\nCOPY init /init\n"
        'ARG MESSAGE=default\nLABEL message=$MESSAGE\nCMD ["/init"]\n',
        encoding="utf-8",
    )
    args_image = f"localhost/{unique()}:latest"
    args_source = f"""
    from kitup.modules import podman

    @task("build image with args")
    def build_image(ctx):
        podman.build(
            ctx,
            {str(args_context)!r},
            tag={args_image!r},
            build_args={{"MESSAGE": "kitup"}},
        )
    """
    assert apply_bundle(tmp_path, "build_args", args_source) == 0
    label = podman("image", "inspect", "--format", "{{.Labels.message}}", args_image).stdout.strip()
    assert label == "kitup"

    # A Containerfile outside the build context is supported.
    context = tmp_path / "context"
    context.mkdir()
    shutil.copy(FIXTURE / "busybox", context / "busybox")
    shutil.copy(FIXTURE / "init", context / "init")
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text(
        'FROM scratch\nCOPY busybox /busybox\nCOPY init /init\nCMD ["/init"]\n',
        encoding="utf-8",
    )
    image = f"localhost/{unique()}:latest"
    containerfile_source = f"""
    from kitup.modules import podman

    @task("build image with containerfile")
    def build_image(ctx):
        podman.build(
            ctx,
            {str(context)!r},
            tag={image!r},
            containerfile={str(containerfile)!r},
        )
    """
    assert apply_bundle(tmp_path, "build_containerfile", containerfile_source) == 0
    assert podman("image", "exists", image).returncode == 0


def test_tag_scenarios(tmp_path: Path) -> None:
    first = f"localhost/{unique('kitup-tag')}:latest"
    second = f"localhost/{unique('kitup-tag')}:latest"
    source = f"""
    from kitup.modules import podman

    @task("tag image")
    def tag_image(ctx):
        podman.tag(ctx, {BASE_IMAGE!r}, [{first!r}, {second!r}])
        assert len(ctx.changes) == 1
        podman.tag(ctx, {BASE_IMAGE!r}, [{first!r}, {second!r}])
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "tag_image", source) == 0
    source_id = image_id(BASE_IMAGE)
    assert image_id(first) == source_id
    assert image_id(second) == source_id

    target = f"localhost/{unique('kitup-tag')}:latest"
    other = f"localhost/{unique()}:latest"
    podman("tag", BASE_IMAGE, target)
    podman("build", "-t", other, "--label", "kitup=other", str(FIXTURE))
    assert image_id(target) != image_id(other)

    update_source = f"""
    from kitup.modules import podman

    @task("retag image")
    def tag_image(ctx):
        podman.tag(ctx, {BASE_IMAGE!r}, {target!r})
    """
    assert apply_bundle(tmp_path, "tag_update", update_source) == 0
    assert image_id(target) == image_id(BASE_IMAGE)


def test_container_lifecycle(tmp_path: Path) -> None:
    name = unique()
    source = f"""
    from kitup.modules import podman

    @task("start container")
    def start_container(ctx):
        podman.container(ctx, {name!r}, image={BASE_IMAGE!r})
        assert len(ctx.changes) == 1
        podman.container(ctx, {name!r}, image={BASE_IMAGE!r})
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "container_start", source) == 0
    assert container_status(name) == "running"
    first_id = container_id(name)

    stop_source = f"""
    from kitup.modules import podman

    @task("stop container")
    def stop_container(ctx):
        podman.container(ctx, {name!r}, state="stopped", stop_timeout=5)
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "container_stop", stop_source) == 0
    assert container_status(name) != "running"

    restart_source = f"""
    from kitup.modules import podman

    @task("start container")
    def start_container(ctx):
        podman.container(ctx, {name!r}, image={BASE_IMAGE!r})
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "container_restart", restart_source) == 0
    assert container_status(name) == "running"
    assert container_id(name) == first_id

    absent_source = f"""
    from kitup.modules import podman

    @task("remove container")
    def remove_container(ctx):
        podman.container(ctx, {name!r}, state="absent")
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "container_absent", absent_source) == 0
    assert podman("container", "exists", name, check=False).returncode != 0


def test_container_present_does_not_start(tmp_path: Path) -> None:
    name = unique()
    source = f"""
    from kitup.modules import podman

    @task("create container")
    def create_container(ctx):
        podman.container(ctx, {name!r}, state="present", image={BASE_IMAGE!r})
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "container_present", source) == 0
    assert container_status(name) == "created"


def test_container_started_short_lived_container_is_rejected(tmp_path: Path) -> None:
    name = unique()
    source = f"""
    from kitup.modules import podman

    @task("start short-lived container")
    def start_container(ctx):
        podman.container(
            ctx,
            {name!r},
            image={BASE_IMAGE!r},
            command=["/init", "exit"],
        )
    """
    assert apply_bundle(tmp_path, "container_short_lived", source) != 0
    assert container_status(name) == "exited"


def test_container_options_and_command_string(tmp_path: Path) -> None:
    name = unique()
    source = f"""
    from kitup.modules import podman

    @task("create container with options")
    def create_container(ctx):
        podman.container(
            ctx,
            {name!r},
            state="present",
            image={BASE_IMAGE!r},
            command=["/init"],
            options={{"publish": "18080:80", "env": ["FOO=bar"]}},
        )
    """
    assert apply_bundle(tmp_path, "container_options", source) == 0
    ports = podman(
        "container",
        "inspect",
        "--format",
        "{{json .HostConfig.PortBindings}}",
        name,
    ).stdout
    assert "18080" in ports
    environment = podman(
        "container",
        "inspect",
        "--format",
        "{{json .Vars.Env}}",
        name,
    ).stdout
    assert "FOO=bar" in environment
    command = podman(
        "container",
        "inspect",
        "--format",
        "{{json .Vars.Cmd}}",
        name,
    ).stdout
    assert "/init" in command

    name = unique()
    string_source = f"""
    from kitup.modules import podman

    @task("create container with string command")
    def create_container(ctx):
        podman.container(
            ctx,
            {name!r},
            state="present",
            image={BASE_IMAGE!r},
            command="/init exit",
        )
    """
    assert apply_bundle(tmp_path, "container_command", string_source) == 0
    command = podman(
        "container",
        "inspect",
        "--format",
        "{{json .Vars.Cmd}}",
        name,
    ).stdout
    assert "exit" in command


def test_container_recreate_replaces_container(tmp_path: Path) -> None:
    name = unique()
    start_fixture_container(name)
    first_id = container_id(name)

    source = f"""
    from kitup.modules import podman

    @task("recreate container")
    def recreate_container(ctx):
        podman.container(ctx, {name!r}, image={BASE_IMAGE!r}, recreate=True)
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "container_recreate", source) == 0
    assert container_id(name) != first_id
    assert container_status(name) == "running"


def test_registry_system_scope(tmp_path: Path) -> None:
    name = unique()
    path = SYSTEM_REGISTRY_DIR / f"{name}.conf"
    source = f"""
    from kitup.modules import podman

    @task("write registry")
    def write_registry(ctx):
        podman.registry(
            ctx,
            {name!r},
            location="docker.io",
            prefix="mirror.example.com",
            insecure=True,
            blocked=False,
            mirrors=["mirror.example.com"],
        )
        assert len(ctx.changes) == 1
        podman.registry(
            ctx,
            {name!r},
            location="docker.io",
            prefix="mirror.example.com",
            insecure=True,
            blocked=False,
            mirrors=["mirror.example.com"],
        )
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "registry_system", source) == 0
    content = run(sudo("cat", str(path))).stdout
    assert 'location = "docker.io"' in content
    assert 'prefix = "mirror.example.com"' in content
    assert "insecure = true" in content
    assert "[[registry.mirror]]" in content
    mode = run(sudo("stat", "-c", "%a", str(path))).stdout.strip()
    assert mode == "644"

    absent_source = f"""
    from kitup.modules import podman

    @task("remove registry")
    def remove_registry(ctx):
        podman.registry(ctx, {name!r}, state="absent")
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "registry_system_absent", absent_source) == 0
    assert run(sudo("test", "-e", str(path)), check=False).returncode != 0


def test_registry_user_scope(tmp_path: Path) -> None:
    name = unique()
    path = USER_REGISTRY_DIR / f"{name}.conf"
    source = f"""
    from kitup.modules import podman

    @task("write user registry")
    def write_registry(ctx):
        podman.registry(ctx, {name!r}, location="docker.io", scope="user")
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "registry_user", source) == 0
    assert path.exists()
    assert 'location = "docker.io"' in path.read_text()

    absent_source = f"""
    from kitup.modules import podman

    @task("remove user registry")
    def remove_registry(ctx):
        podman.registry(ctx, {name!r}, state="absent", scope="user")
    """
    assert apply_bundle(tmp_path, "registry_user_absent", absent_source) == 0
    assert not path.exists()


class _RegistryHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/v2/":
            self.send_response(404)
            self.end_headers()
            return
        expected = "Basic " + base64.b64encode(b"kitup:secret").decode()
        if self.headers.get("Authorization") == expected:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Docker-Distribution-Api-Version", "registry/2.0")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="kitup-test"')
        self.end_headers()

    def do_HEAD(self) -> None:
        self.do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def local_registry() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RegistryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_login_scenarios(tmp_path: Path, local_registry: str) -> None:
    authfile = tmp_path / "auth.json"
    login_source = f"""
    from kitup.modules import podman

    @task("login")
    def login(ctx):
        podman.login(
            ctx,
            {local_registry!r},
            username="kitup",
            password="secret",
            authfile={str(authfile)!r},
            tls_verify=False,
        )
        assert len(ctx.changes) == 1
        podman.login(
            ctx,
            {local_registry!r},
            username="kitup",
            password="secret",
            authfile={str(authfile)!r},
            tls_verify=False,
        )
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "login", login_source) == 0
    assert (
        podman(
            "login",
            "--get-login",
            f"--authfile={authfile}",
            local_registry,
        ).stdout.strip()
        == "kitup"
    )

    logout_source = f"""
    from kitup.modules import podman

    @task("logout")
    def logout(ctx):
        podman.logout(ctx, {local_registry!r}, authfile={str(authfile)!r})
        assert len(ctx.changes) == 1
        podman.logout(ctx, {local_registry!r}, authfile={str(authfile)!r})
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "logout", logout_source) == 0
    assert (
        podman(
            "login",
            "--get-login",
            f"--authfile={authfile}",
            local_registry,
            check=False,
        ).returncode
        != 0
    )

    force_authfile = tmp_path / "force-auth.json"
    force_source = f"""
    from kitup.modules import podman

    @task("force login")
    def login(ctx):
        podman.login(
            ctx,
            {local_registry!r},
            username="kitup",
            password="secret",
            authfile={str(force_authfile)!r},
            tls_verify=False,
            force=True,
        )
        assert len(ctx.changes) == 1
        podman.login(
            ctx,
            {local_registry!r},
            username="kitup",
            password="secret",
            authfile={str(force_authfile)!r},
            tls_verify=False,
            force=True,
        )
        assert len(ctx.changes) == 2
    """
    assert apply_bundle(tmp_path, "login_force", force_source) == 0

    wrong_authfile = tmp_path / "wrong-auth.json"
    wrong_source = f"""
    from kitup.modules import podman

    @task("login")
    def login(ctx):
        podman.login(
            ctx,
            {local_registry!r},
            username="kitup",
            password="wrong",
            authfile={str(wrong_authfile)!r},
            tls_verify=False,
        )
    """
    assert apply_bundle(tmp_path, "login_wrong", wrong_source) != 0


def test_generate_systemd_scenarios(tmp_path: Path) -> None:
    name = unique()
    start_fixture_container(name)
    unit = tmp_path / f"container-{name}.service"
    source = f"""
    from kitup.modules import podman

    @task("generate systemd")
    def generate(ctx):
        podman.generate_systemd(
            ctx,
            {name!r},
            dest={str(tmp_path)!r},
            scope="user",
        )
        assert len(ctx.changes) == 1
        podman.generate_systemd(
            ctx,
            {name!r},
            dest={str(tmp_path)!r},
            scope="user",
        )
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "systemd_generate", source) == 0
    content = unit.read_text()
    assert "autogenerated by Podman" not in content
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "ExecStart=" in content
    assert name in content

    verify = run(["systemd-analyze", "verify", str(unit)], check=False)
    assert "Failed to parse" not in verify.stderr

    name = unique()
    start_fixture_container(name)
    unit = tmp_path / f"svc.{name}.service"
    flags_source = f"""
    from kitup.modules import podman

    @task("generate systemd")
    def generate(ctx):
        podman.generate_systemd(
            ctx,
            {name!r},
            dest={str(tmp_path)!r},
            scope="user",
            new=True,
            restart_policy="always",
            restart_sec=5,
            start_timeout=10,
            stop_timeout=20,
            container_prefix="svc",
            separator=".",
            env={{"FOO": "bar"}},
            wants=["network-online.target"],
            requires=["network.target"],
            after=["network.target"],
        )
    """
    assert apply_bundle(tmp_path, "systemd_flags", flags_source) == 0
    content = unit.read_text()
    assert "podman run" in content
    assert "Restart=always" in content
    assert "RestartSec=5" in content
    assert "FOO=bar" in content
    assert "network-online.target" in content


def test_generate_systemd_system_scope(
    tmp_path: Path,
    rootful_image: str,
    clean_rootful: None,
) -> None:
    name = unique()
    run(sudo("podman", "create", "--name", name, rootful_image))
    run(sudo("podman", "start", name))
    unit = SYSTEM_UNIT_DIR / f"container-{name}.service"
    source = f"""
    from kitup.modules import podman

    @task("generate systemd")
    def generate(ctx):
        podman.generate_systemd(ctx, {name!r})
    """
    assert apply_bundle(tmp_path, "systemd_system", source) == 0
    assert run(sudo("test", "-e", str(unit)), check=False).returncode == 0


def test_quadlet_scenarios(tmp_path: Path) -> None:
    name = unique()
    path = tmp_path / f"{name}.container"
    source = f"""
    from kitup.modules import podman

    @task("write quadlet")
    def write_quadlet(ctx):
        podman.quadlet(
            ctx,
            {name!r},
            image={BASE_IMAGE!r},
            dest={str(tmp_path)!r},
            scope="user",
            command=["/init"],
            stop_timeout=10,
            options={{"publish": "18081:80"}},
            extra_options={{"Volume": ["/srv/data:/data", "/srv/cache:/cache"]}},
        )
        assert len(ctx.changes) == 1
        podman.quadlet(
            ctx,
            {name!r},
            image={BASE_IMAGE!r},
            dest={str(tmp_path)!r},
            scope="user",
            command=["/init"],
            stop_timeout=10,
            options={{"publish": "18081:80"}},
            extra_options={{"Volume": ["/srv/data:/data", "/srv/cache:/cache"]}},
        )
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "quadlet_write", source) == 0
    content = path.read_text()
    assert "[Unit]" in content
    assert "[Container]" in content
    assert f"ContainerName={name}" in content
    assert f"Image={BASE_IMAGE}" in content
    assert "Exec=/init" in content
    assert "StopTimeout=10" in content
    assert "PodmanArgs=--publish=18081:80" in content
    assert content.count("Volume=") == 2
    assert "[Service]" in content
    assert "[Install]" in content

    generators = run(["systemd-path", "systemd-system-generator"], check=False).stdout.strip()
    generator = Path(generators.split(":", 1)[0]) / "podman-system-generator"
    if not generator.exists():
        generator = Path("/usr/lib/systemd/system-generators/podman-system-generator")
    if not generator.exists():
        pytest.skip("the Podman Quadlet generator is not installed")
    result = run(
        [str(generator), "--dryrun"],
        check=False,
        env={**os.environ, "QUADLET_UNIT_DIRS": str(tmp_path)},
    )
    assert "Failed to parse" not in result.stderr

    name = unique()
    no_install_source = f"""
    from kitup.modules import podman

    @task("write quadlet")
    def write_quadlet(ctx):
        podman.quadlet(
            ctx,
            {name!r},
            image={BASE_IMAGE!r},
            dest={str(tmp_path)!r},
            scope="user",
            install_target="",
        )
    """
    assert apply_bundle(tmp_path, "quadlet_no_install", no_install_source) == 0
    assert "[Install]" not in (tmp_path / f"{name}.container").read_text()


def test_quadlet_system_scope(tmp_path: Path) -> None:
    name = unique()
    path = SYSTEM_QUADLET_DIR / f"{name}.container"
    source = f"""
    from kitup.modules import podman

    @task("write quadlet")
    def write_quadlet(ctx):
        podman.quadlet(ctx, {name!r}, image={BASE_IMAGE!r})
    """
    assert apply_bundle(tmp_path, "quadlet_system", source) == 0
    assert run(sudo("test", "-e", str(path)), check=False).returncode == 0


def test_rootful_path(tmp_path: Path, rootful_image: str, clean_rootful: None) -> None:
    name = unique()
    source = f"""
    from kitup.modules import podman

    @task("rootful image and container")
    def rootful(ctx):
        podman.image(ctx, {rootful_image!r}, sudo=True)
        podman.container(ctx, {name!r}, image={rootful_image!r}, sudo=True)
        assert len(ctx.changes) == 1
    """
    assert apply_bundle(tmp_path, "rootful", source) == 0
    status = run(
        sudo("podman", "container", "inspect", "--format", "{{.State.Status}}", name),
        check=False,
    )
    assert status.stdout.strip() == "running"
