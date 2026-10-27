from bundles import base

from kitup.registry import Bundle

Bundle("Dotfiles", "Shell and editor configuration", depends_on=[base])

from . import dotfiles  # noqa: E402,F401
