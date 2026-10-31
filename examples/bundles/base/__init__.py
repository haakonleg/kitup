from kitup.registry import Bundle

from . import packages, security, services

Bundle(
    "Base system",
    "Core packages, services, and SSH hardening",
    modules=[packages, security, services],
)
