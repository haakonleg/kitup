"""The simple python-driven system configuration tool."""

from typing import Final

__version__: Final = "3.0.0"

PACKAGE_NAME: Final = __name__

# PACKAGE_NAME and __version__ must exist before these imports: submodules such
# as ``output`` and ``_privileged.client`` read them from the package root.
from .context import TaskContext  # noqa: E402
from .registry import Bundle, Module, bundle, bundle_of, module, side_effect, task  # noqa: E402
from .results import CallResult  # noqa: E402

__all__: Final = [
    "PACKAGE_NAME",
    "Bundle",
    "CallResult",
    "Module",
    "TaskContext",
    "__version__",
    "bundle",
    "bundle_of",
    "module",
    "side_effect",
    "task",
]
