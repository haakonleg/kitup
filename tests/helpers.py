"""Helpers shared by the unit and integration suites."""

import sys
import textwrap
from pathlib import Path

# Modules imported from temporary configuration roots that must not leak between tests.
_PROJECT_MODULES = frozenset(
    {
        "bundles",
        "config",
        "base_profile",
        "middle_profile",
        "child_profile",
        "empty_profile",
        "helper_profile",
        "left_profile",
        "right_profile",
        "laptop_profile",
    }
)


def write(path: Path, text: str) -> None:
    """Write dedented text, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def purge_project_modules() -> None:
    """Drop modules imported from temporary configuration roots."""
    for name in tuple(sys.modules):
        if name in _PROJECT_MODULES or name.startswith(("bundles.", "_kitup_profile_", "_kitup_config_")):
            sys.modules.pop(name, None)
