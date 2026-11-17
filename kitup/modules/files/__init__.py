"""Idempotent filesystem and text-file management, including Jinja2 templates."""

from ...registry import module
from .block import block
from .files import copy, directory, file, move, symlink
from .ini import ini
from .line import line
from .template_render import template
from .toml import toml

module("files", "Filesystem and text-file management, including Jinja2 templates.")

__all__ = [
    "block",
    "copy",
    "directory",
    "file",
    "ini",
    "line",
    "move",
    "symlink",
    "template",
    "toml",
]
