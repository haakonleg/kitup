"""Shared fixtures for the unit suite."""

import sys

import pytest


@pytest.fixture(autouse=True)
def purge_test_packages():
    """Drop temporary package modules imported by registry tests."""
    yield
    for name in tuple(sys.modules):
        if name.startswith("pkg_"):
            sys.modules.pop(name, None)
