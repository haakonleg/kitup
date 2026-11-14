"""Shared fixtures for the unit suite."""

import sys

import pytest

from kitup.registry import forget_module_prefix


@pytest.fixture(autouse=True)
def purge_test_modules():
    """Drop temporary and project modules imported by tests."""
    yield
    for name in tuple(sys.modules):
        if name == "modules" or name.startswith(("pkg_", "modules.")):
            sys.modules.pop(name, None)
    forget_module_prefix("modules")
