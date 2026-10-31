from bundles import base

from kitup.registry import Bundle

from . import dotfiles

Bundle(
    "Dotfiles",
    "Shell and editor configuration",
    depends_on=[base],
    modules=[dotfiles],
)
