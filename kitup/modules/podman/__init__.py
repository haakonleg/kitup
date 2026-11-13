"""Idempotent Podman images, containers, registries, and systemd units."""

from ...registry import module
from ._common import ContainerState, PodmanOption, PullPolicy
from ._containers import container
from ._images import build, image, tag
from ._quadlet import quadlet
from ._registries import login, logout, registry
from ._units import generate_systemd

module("podman")


__all__ = [
    "ContainerState",
    "PodmanOption",
    "PullPolicy",
    "build",
    "container",
    "generate_systemd",
    "image",
    "login",
    "logout",
    "quadlet",
    "registry",
    "tag",
]
