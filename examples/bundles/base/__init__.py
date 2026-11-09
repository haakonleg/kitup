from kitup import bundle

from . import packages, security, services

bundle(
    "Base system",
    "Core packages, services, and SSH hardening",
    modules=[packages, security, services],
)
