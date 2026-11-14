"""Built-in, idempotent task modules.

Import modules from bundle task files, for example::

    from kitup.modules import dnf, files
"""

import importlib
from importlib import resources

from ..registry import Module, registered_modules


def discover_builtin_modules() -> tuple[Module, ...]:
    """Import every built-in module and return its registration by action prefix.

    Importing a module runs its ``module(...)`` call, which registers it.
    """
    for name in _module_names():
        importlib.import_module(f"{__name__}.{name}")
    return registered_modules(__name__)


def _module_names() -> tuple[str, ...]:
    """Return public module file stems and package names below this package."""
    names: list[str] = []
    for entry in resources.files(__name__).iterdir():
        if entry.name.startswith(("_", ".")):
            continue
        if entry.is_file() and entry.name.endswith(".py"):
            names.append(entry.name.removesuffix(".py"))
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            names.append(entry.name)
    return tuple(sorted(names))
