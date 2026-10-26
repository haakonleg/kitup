from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="PR_SET_DUMPABLE is Linux-specific",
)


def test_disable_same_uid_ptrace_sets_dumpable_zero():
    code = (
        "from kitup._utils.security import disable_same_uid_ptrace\n"
        "disable_same_uid_ptrace()\n"
        "import ctypes\n"
        "print(ctypes.CDLL(None).prctl(3))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "0"
