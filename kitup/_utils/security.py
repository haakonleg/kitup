"""Small Linux process-hardening helpers."""

import ctypes
import sys

_PR_SET_DUMPABLE = 4


def disable_same_uid_ptrace() -> None:
    """Make this process's /proc entries inaccessible to same-UID processes.

    ``PR_SET_DUMPABLE=0`` prevents same-UID processes from attaching with
    ptrace or reopening this process's file descriptors via
    ``/proc/<pid>/fd``. Root and processes with ``CAP_SYS_PTRACE`` are still
    able to inspect the process.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (AttributeError, OSError):
        pass
