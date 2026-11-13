"""Podman image pull, build, removal, and tagging."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from ..._utils.validation import (
    State,
    normalize_strings,
    require_bool,
    require_choice,
    require_collection,
    require_path,
    require_state,
    require_text,
    require_texts,
    require_timeout,
    unique_strings,
)
from ...context import TaskContext
from ...errors import TaskFailed
from ...results import CallResult
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
) -> CallResult:
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
    require_bool(sudo, "image sudo")
    require_timeout(timeout)
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
            data = {"image": name}
            if source is not None:
                data["source"] = source
            return ctx.changed(changed=False, summary=f"ensure image {name}", **data)
        if pull == "never":
            raise TaskFailed(f"image is not present locally: {name}")
        platform_args = ["--platform", platform] if platform is not None else []
        data = {"image": name}
        summary = f"pull {name}"
        if source is not None:
            data["source"] = source
            summary = f"pull {name} from {source}"
        call = CallResult(changed=True, summary=summary, **data)
        if ctx.dry_run:
            return ctx.changed(call)
        ctx.run(
            [podman_command, "pull", *platform_args, pull_source],
            timeout=timeout,
            sudo=sudo,
        )
        if source is not None and not image_exists(ctx, podman_command, name, sudo=sudo):
            raise TaskFailed(f"image is missing after pull: {name}")
        return ctx.changed(call)

    if not image_exists(ctx, podman_command, name, sudo=sudo):
        return ctx.changed(changed=False, summary=f"ensure image {name} is absent", image=name)
    call = CallResult(changed=True, summary=f"remove image {name}", image=name)
    if ctx.dry_run:
        return ctx.changed(call)
    force_args = ["--force"] if force else []
    ctx.run(
        [podman_command, "rmi", *force_args, name],
        timeout=timeout,
        sudo=sudo,
    )
    if image_exists(ctx, podman_command, name, sudo=sudo):
        raise TaskFailed(f"image is still present after removal: {name}")
    return ctx.changed(call)


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
) -> CallResult:
    """Build an image from a context directory.

    Relative paths resolve like other source paths. Absolute paths are used
    as-is. Content changes in the context are not detected. Use ``force=True``
    to rebuild an existing tag.
    """
    require_path(context, "build context")
    require_text(tag, "image tag")
    require_bool(pull, "build pull")
    require_bool(force, "build force")
    require_bool(no_cache, "build no_cache")
    require_bool(sudo, "build sudo")
    require_timeout(timeout)

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
        return ctx.changed(changed=False, summary=f"ensure image {tag}", image=tag, context=str(context_path))

    command = [podman_command, "build", "-t", tag]
    if containerfile_path is not None:
        command.extend(["-f", str(containerfile_path)])
    command.extend(argument_args)
    if pull:
        command.append("--pull=always")
    if no_cache:
        command.append("--no-cache")
    command.append(str(context_path))

    call = CallResult(changed=True, summary=f"build {tag} from {context_path}", image=tag, context=str(context_path))
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run(command, timeout=timeout, sudo=sudo)
    if not image_exists(ctx, podman_command, tag, sudo=sudo):
        raise TaskFailed(f"image is missing after build: {tag}")
    return ctx.changed(call)


def tag(ctx: TaskContext, image: str, targets: str | Iterable[str], *, sudo: bool = False) -> CallResult:
    """Add one or more target names to a local image."""
    require_text(image, "image name")
    wanted = unique_strings(normalize_strings(targets))
    require_collection(wanted, "image targets")
    require_texts(wanted, "image target")
    require_bool(sudo, "tag sudo")

    podman_command = podman(ctx)
    source_id = image_id(ctx, podman_command, image, sudo=sudo)
    changed = [target for target in wanted if image_id_or_none(ctx, podman_command, target, sudo=sudo) != source_id]
    if not changed:
        return ctx.changed(changed=False, summary=f"ensure tags for {image}", source=image, targets=[])

    call = CallResult(changed=True, summary=f"tag {image} as {', '.join(changed)}", source=image, targets=changed)
    if ctx.dry_run:
        return ctx.changed(call)
    ctx.run([podman_command, "tag", image, *changed], sudo=sudo)
    return ctx.changed(call)
