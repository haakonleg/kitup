from kitup.registry import Bundle

bundle = Bundle("base", "Base system configuration")

from . import dnf_config, packages  # noqa: F401
