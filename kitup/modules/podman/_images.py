"""Podman image pull, build, removal, and tagging."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from ...context import TaskContext
from ...errors import TaskFailed
from ...validation import (
    State,
    normalize_strings,
    require_bool,
    require_choice,
    require_collection,
    require_path,
    require_state,
    require_text,
    require_texts,
    unique_strings,
)
from ._common import (
    PULL_POLICIES,
    PullPolicy,
    build_arg_args,
    image_exists,
    image_id,
    image_id_or_none,
    is_transport_reference,
    podman,
)


def image(
    ctx: TaskContext,
    name: str,
    *,
    source: str | None = None,
    state: State = "present",
    pull: PullPolicy = "missing",
    platform: str | None = None,
    force: bool = False,
    timeout: float = 600,
    sudo: bool = False,
) -> None:
    """Ensure an image is present locally or absent.

    ``name`` is the local image name. ``source`` optionally names a
    transport-qualified pull source such as ``oci-archive:``; the pull is
    verified against ``name`` afterwards. ``pull="always"`` re-pulls an
    existing image. ``force`` only affects removal. Pulls without ``source``
    are not verified afterwards because short-name resolution may store the
    image under a different local name.
    """
    require_text(name, "image name")
    if source is not None:
        require_text(source, "image source")
    require_state(state, "image")
    require_choice(pull, PULL_POLICIES, "image pull policy")
    require_bool(force, "image force")
    if platform is not None:
        require_text(platform, "image platform")
    if is_transport_reference(name):
        raise TaskFailed("name must be a local image name; use source for transport-qualified references")
    if source is not None and not is_transport_reference(source):
        raise TaskFailed("source must be a transport-qualified image reference")

    pull_source = source or name
    podman_command = podman(ctx)
    if state == "present":
        if pull != "always" and image_exists(ctx, podman_command, name, sudo=sudo):
            return
        if pull == "never":
            raise TaskFailed(f"image is not present locally: {name}")
        platform_args = ["--platform", platform] if platform is not None else []
        data = {"image": name, "action": "pull"}
        summary = f"pull {name}"
        if source is not None:
            data["source"] = source
            summary = f"pull {name} from {source}"
        ctx.changed(data, summary)
        ctx.run(
            [podman_command, "pull", *platform_args, pull_source],
            timeout=timeout,
            sudo=sudo,
            record=False,
        )
        if source is not None and not ctx.dry_run and not image_exists(ctx, podman_command, name, sudo=sudo):
            raise TaskFailed(f"image is missing after pull: {name}")
        return

    if not image_exists(ctx, podman_command, name, sudo=sudo):
        return
    ctx.changed({"image": name, "action": "remove"}, f"remove image {name}")
    force_args = ["--force"] if force else []
    ctx.run(
        [podman_command, "rmi", *force_args, name],
        timeout=timeout,
        sudo=sudo,
        record=False,
    )
    if not ctx.dry_run and image_exists(ctx, podman_command, name, sudo=sudo):
        raise TaskFailed(f"image is still present after removal: {name}")


def build(
    ctx: TaskContext,
    context: str | Path,
    *,
    tag: str,
    containerfile: str | Path | None = None,
    build_args: Mapping[str, str | int | float] | None = None,
    pull: bool = False,
    force: bool = False,
    no_cache: bool = False,
    timeout: float = 600,
    sudo: bool = False,
) -> None:
    """Build an image from a context directory.

    Relative paths resolve like other source paths: a bare name under the
    bundle's ``files`` directory, other relative paths against the bundle.
    Content changes in the context are not detected. Use ``force=True`` to
    rebuild an existing tag.
    """
    require_path(context, "build context")
    require_text(tag, "image tag")
    require_bool(pull, "build pull")
    require_bool(force, "build force")
    require_bool(no_cache, "build no_cache")

    context_path = ctx.resolve_source(context)
    if not context_path.is_dir():
        raise TaskFailed(f"build context is not a directory: {context_path}")
    containerfile_path = None
    if containerfile is not None:
        containerfile_path = ctx.resolve_source(containerfile)
        if not containerfile_path.is_file():
            raise TaskFailed(f"Containerfile is not a regular file: {containerfile_path}")

    argument_args = build_arg_args(build_args)
    podman_command = podman(ctx)
    if not force and image_exists(ctx, podman_command, tag, sudo=sudo):
        return

    command = [podman_command, "build", "-t", tag]
    if containerfile_path is not None:
        command.extend(["-f", str(containerfile_path)])
    command.extend(argument_args)
    if pull:
        command.append("--pull=always")
    if no_cache:
        command.append("--no-cache")
    command.append(str(context_path))

    ctx.changed(
        {"image": tag, "context": str(context_path), "action": "build"},
        f"build {tag} from {context_path}",
    )
    ctx.run(command, timeout=timeout, sudo=sudo, record=False)
    if not ctx.dry_run and not image_exists(ctx, podman_command, tag, sudo=sudo):
        raise TaskFailed(f"image is missing after build: {tag}")


def tag(
    ctx: TaskContext,
    image: str,
    targets: str | Iterable[str],
    *,
    sudo: bool = False,
) -> None:
    """Add one or more target names to a local image."""
    require_text(image, "image name")
    wanted = unique_strings(normalize_strings(targets))
    require_collection(wanted, "image targets")
    require_texts(wanted, "image target")

    podman_command = podman(ctx)
    source_id = image_id(ctx, podman_command, image, sudo=sudo)
    changed = [target for target in wanted if image_id_or_none(ctx, podman_command, target, sudo=sudo) != source_id]
    if not changed:
        return

    ctx.changed(
        {"source": image, "targets": changed, "action": "tag"},
        f"tag {image} as {', '.join(changed)}",
    )
    ctx.run([podman_command, "tag", image, *changed], sudo=sudo, record=False)
