"""The simple python-driven system configuration tool."""

__version__ = "2.0.0"

PACKAGE_NAME = __name__

# PACKAGE_NAME must exist before importing profiles.
from .profiles import RunDefinition, profile  # noqa: E402

__all__ = ["PACKAGE_NAME", "RunDefinition", "__version__", "profile"]
