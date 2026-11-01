"""The simple python-driven system configuration tool."""

from typing import Final

__version__: Final = "2.0.0"

PACKAGE_NAME: Final = __name__

# PACKAGE_NAME must exist before importing profiles.
from .profiles import RunDefinition, profile  # noqa: E402

__all__: Final = ["PACKAGE_NAME", "RunDefinition", "__version__", "profile"]
