from kitup.registry import Bundle

Bundle("Base system", "Core packages, services, and SSH hardening")

from . import packages, security, services  # noqa: E402,F401
