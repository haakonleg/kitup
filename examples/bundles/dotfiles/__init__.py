from kitup.registry import Bundle

bundle = Bundle("dotfiles", "Home directory dotfiles", depends_on=("base",))

from . import dotfiles  # noqa: F401
