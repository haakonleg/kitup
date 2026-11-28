# `podman`

Manage Podman images, containers, registry drop-ins, registry logins, and
systemd or Quadlet units.

```python
from kitup.modules import podman, systemd
```

## `image(name, *, source=None, state="present", pull="missing", platform=None, force=False, timeout=600, sudo=False)`

Ensure an image is present locally or absent.

### Parameters

- `name` — local image name.
- `source` — optional transport-qualified pull source such as
  `oci-archive:...`. The pull is verified against `name`.
- `state` — `"present"` or `"absent"`.
- `pull` — `"missing"` pulls only when the image is absent, `"always"`
  re-pulls even when it is present, and `"never"` fails when it is absent.
- `platform` — optional platform such as `linux/arm64`.
- `force` — pass `--force` when removing an image.
- `timeout` — command timeout in seconds.
- `sudo=True` — run as root.

Pulls without `source` are not verified afterwards because short-name
resolution may store the image under a different local name. Pulls with
`source` are verified against `name`. Removals are verified.

### Result fields

- `image` — local image name.
- `source` — pull source, when supplied (pull results only).

```python
podman.image("docker.io/library/fedora:latest")
podman.image(
    "localhost/example:latest",
    source="oci-archive:/tmp/example.tar",
)
podman.image("localhost/example:old", state="absent", force=True)
```

## `build(context, *, tag, containerfile=None, build_args=None, pull=False, force=False, no_cache=False, timeout=600, sudo=False)`

Build an image from a context directory.

### Parameters

- `context` — build context. Relative paths use the project and bundle lookup
  described in [Projects](../project.md#resources). Absolute paths are used
  as-is, and `~` is expanded.
- `tag` — image name to create.
- `containerfile` — optional Containerfile path, resolved independently of
  `context`.
- `build_args` — mapping of build argument names to values.
- `pull` — always pull the base image.
- `force` — rebuild even when the tag already exists.
- `no_cache` — disable the build cache.
- `timeout` — command timeout in seconds.
- `sudo=True` — run as root.

`containerfile` is not resolved relative to `context`; pass
`"container/Containerfile"` or an absolute path when the file lives inside the
build context.

Content changes in the context are not detected. Use `force=True` to rebuild.

### Result fields

- `image` — resulting image tag.
- `context` — resolved build context.

```python
podman.build(
    "container",
    tag="localhost/example:latest",
    build_args={"VERSION": "1.0"},
)
```

## `tag(image, targets, *, sudo=False)`

Add one or more target names to a local image. Targets that already point at
the source image are skipped.

### Parameters

- `image` — source image name.
- `targets` — one or more target names.
- `sudo=True` — run as root.

### Result fields

- `source` — source image name.
- `targets` — tags added; empty when nothing changed.

```python
podman.tag("localhost/example:latest", ["localhost/example:stable"])
```

## `container(name, *, state="started", image=None, command=None, options=None, recreate=False, stop_timeout=None, timeout=600, sudo=False)`

Ensure a container is created, started, stopped, or absent.

### Parameters

- `name` — container name.
- `state` — `"present"` creates without starting, `"started"` creates and
  starts, `"stopped"` stops a running container, and `"absent"` removes it.
  `"started"` verifies that the container is running immediately after the
  start command.
- `image` — required when the container must be created.
- `command` — a string split with shell quoting rules, or an argv sequence.
- `options` — Podman option mapping. Keys are normalized to `--flag` form;
  booleans, scalars, sequences, and mappings are supported.
- `recreate` — remove and recreate an existing container. Only valid with
  `"present"` or `"started"`.
- `stop_timeout` — seconds passed to `podman create` and `podman stop`.
- `timeout` — command timeout in seconds.
- `sudo=True` — run as root.

Existing containers are never modified. Changing `image`, `command`, or
`options` requires `recreate=True`. Paused containers are rejected.

`"started"` requires a process that stays running. A container whose command
exits immediately is rejected with a clear error; use `"present"` to create
one-shot containers without starting them, or run one-shot workloads
separately with `podman run --rm`.

### Result fields

- `container` — container name.
- `image` — requested image, or `None` when none was supplied.
- `actions` — list of `"create"`, `"start"`, `"stop"`, or `"remove"` actions
  performed; empty when nothing changed.

```python
podman.container(
    "web",
    image="localhost/web:latest",
    options={"publish": "8080:80", "env": ["MODE=production"]},
    command=["/app/serve"],
)
podman.container("web", state="absent")
```

## `registry(name, *, state="present", location=None, prefix=None, insecure=False, blocked=False, mirrors=(), scope="system", path=None, mode=0o644)`

Ensure a `registries.conf.d` drop-in exists or is absent.

### Parameters

- `name` — drop-in name; the default file is `<name>.conf`.
- `state` — `"present"` or `"absent"`.
- `location` — registry location. Required for `"present"`.
- `prefix` — optional registry prefix.
- `insecure` and `blocked` — registry policy flags.
- `mirrors` — one or more mirror locations.
- `scope` — `"system"` or `"user"`.
- `path` — override the default path. A missing `.conf` suffix is appended.
- `mode` — file mode. Accepts an integer or an octal string such as `"0644"`.

Parent directories must already exist; create them with
[`files.directory`](files.md) first.

### Result fields

- `path` — registry drop-in file.
- `registry` — registry name.

```python
podman.registry(
    "example",
    location="docker.io",
    prefix="mirror.example.com",
    mirrors=["mirror.example.com"],
)
```

## `login(registry, *, username, password, authfile=None, tls_verify=None, force=False, sudo=False)`

Log in to a container registry. The password is passed with
`--password-stdin`, not in argv. Logging in again for the same user requires
`force=True`.

### Parameters

- `registry` — registry host.
- `username` — account name.
- `password` — password, passed to Podman on stdin.
- `authfile` — optional authentication file.
- `tls_verify` — optional TLS verification override.
- `force` — log in even when the same user is already logged in.
- `sudo=True` — run as root.

### Result fields

- `registry` — registry host.
- `username` — account name.

```python
podman.login("registry.example.com", username="kitup", password="s3cret")
```

## `logout(registry, *, authfile=None, sudo=False)`

Log out of a container registry.

### Parameters

- `registry` — registry host.
- `authfile` — optional authentication file.
- `sudo=True` — run as root.

### Result fields

- `registry` — registry host.

## `generate_systemd(name, *, dest=None, scope="system", new=False, restart_policy=None, restart_sec=None, start_timeout=None, stop_timeout=None, container_prefix=None, separator=None, env=None, wants=(), requires=(), after=(), mode=0o644)`

Generate a systemd unit for a container.

### Parameters

- `name` — container name.
- `dest` — destination directory. Defaults to `/etc/systemd/system` for
  system scope and `~/.config/systemd/user` for user scope.
- `scope` — `"system"` or `"user"`.
- `new`, `restart_policy`, `restart_sec`, `start_timeout`, `stop_timeout`,
  `container_prefix`, and `separator` map to the corresponding
  `podman generate systemd` options.
- `env` — environment variables added to the unit.
- `wants`, `requires`, and `after` — systemd dependency unit names.
- `mode` — file mode. Accepts an integer or an octal string such as `"0644"`.

Podman's generated header, which contains a timestamp, is omitted.

The filename follows Podman's naming rules, for example
`container-web.service`. Parent directories must already exist.

`podman generate systemd` is deprecated upstream in favor of Quadlet. The
module writes the unit but does not run `systemctl daemon-reload`; use
[`systemd.daemon_reload`](systemd.md) afterwards.

### Result fields

- `path` — generated unit file.
- `container` — container name.

```python
podman.generate_systemd("web", restart_policy="always")
systemd.daemon_reload()
```

## `quadlet(name, *, image, dest=None, scope="system", command=None, options=None, extra_options=None, install_target=None, stop_timeout=None, mode=0o644)`

Write a Quadlet `.container` file.

### Parameters

- `name` — container name.
- `image` — container image.
- `dest` — destination directory. Defaults to `/etc/containers/systemd` for
  system scope and `~/.config/containers/systemd` for user scope.
- `scope` — `"system"` or `"user"`.
- `command` — a string written verbatim after `Exec=`, or an argv sequence.
- `options` — Podman options encoded as `PodmanArgs=` lines.
- `extra_options` — raw `[Container]` keys such as `PublishPort` or `Volume`.
  Values may be a string or a sequence of strings.
- `install_target` — `[Install]` target. `None` uses `multi-user.target` for
  system scope and `default.target` for user scope. An empty string omits the
  `[Install]` section.
- `stop_timeout` — container stop timeout in seconds.
- `mode` — file mode. Accepts an integer or an octal string such as `"0644"`.

Keys managed by the module (`ContainerName`, `Image`, `Exec`, `StopTimeout`,
and `PodmanArgs`) cannot be used in `extra_options`. The generated file always
includes `[Service] Restart=always`; this is not configurable. Parent
directories must already exist. The module writes the Quadlet but does not run
`systemctl daemon-reload`; use [`systemd.daemon_reload`](systemd.md)
afterwards.

### Result fields

- `path` — generated Quadlet file.
- `container` — container name.

```python
podman.quadlet(
    "web",
    image="localhost/web:latest",
    options={"publish": "8080:80"},
    extra_options={"Volume": ["/srv/web:/data"]},
)
```

## Notes

- `scope="system"` requests sudo. `scope="user"` never uses sudo and fails
  when kitup runs as root.
- `sudo=True` runs command-backed operations as root.
- These opt-ins are intentionally non-idempotent: `image(pull="always")`,
  `build(force=True)`, `container(recreate=True)`, and `login(force=True)`.
- `build` and `container` do not detect content drift. Use `force=True` or
  `recreate=True` to apply changes.
