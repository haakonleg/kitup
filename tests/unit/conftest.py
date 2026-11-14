"""Shared fixtures for the unit suite."""

import sys

import pytest

from kitup._utils import importer
from kitup.context import set_current_context
from kitup.registry import forget_module_prefix


@pytest.fixture(autouse=True)
def clear_ambient_context():
    """Run each test without a task context."""
    set_current_context(None)
    yield
    set_current_context(None)


@pytest.fixture(autouse=True)
def isolate_pycache(monkeypatch):
    """Keep project bytecode caches out of the real home directory."""
    monkeypatch.setattr(importer, "_pycache_disabled", True)


@pytest.fixture(autouse=True)
def purge_test_modules():
    """Drop temporary and project modules imported by tests."""
    yield
    for name in tuple(sys.modules):
        if name == "modules" or name.startswith(("pkg_", "modules.")):
            sys.modules.pop(name, None)
    forget_module_prefix("modules")
