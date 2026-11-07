"""Gate the container integration suite behind an explicit opt-in."""

import os

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("KITUP_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set KITUP_INTEGRATION=1 to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def require_non_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("the integration suite must run as a non-root user")
