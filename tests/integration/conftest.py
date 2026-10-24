"""Gate the container integration suite behind an explicit opt-in."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("KITUP_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set KITUP_INTEGRATION=1 to run integration tests")
    for item in items:
        if Path(item.path).resolve().is_relative_to(INTEGRATION_DIR):
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def require_non_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("the integration suite must run as a non-root user")
