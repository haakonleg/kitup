"""Idempotent Podman images, containers, registries, and systemd units."""

from ...registry import module
from .common import ContainerState, PodmanOption, PullPolicy
from .containers import container
from .images import build, image, tag
from .quadlet import quadlet
from .registries import login, logout, registry
from .units import generate_systemd

module("podman", "Images, containers, registries, and systemd/Quadlet units.")


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
