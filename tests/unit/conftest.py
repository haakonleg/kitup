"""Shared fixtures for the unit suite."""

import pytest

from ..helpers import purge_project_modules


@pytest.fixture
def clean_imports():
    """Keep modules imported from temporary configuration roots isolated."""
    purge_project_modules()
    yield
    purge_project_modules()
