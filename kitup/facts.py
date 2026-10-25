"""System fact collection, performed once per run."""

from __future__ import annotations

import os
import platform
import re
import shlex
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

OS_RELEASE_PATH = Path("/etc/os-release")
MOUNTINFO_PATH = Path("/proc/self/mountinfo")


def parse_os_release(text: str) -> dict[str, str]:
    """Parse ``os-release``-style ``KEY=value`` lines."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        try:
            parsed = shlex.split(raw_value, comments=False, posix=True)
            value = parsed[0] if parsed else ""
        except ValueError:
            value = raw_value.strip().strip("'\"")
        values[key] = value
    return values


def read_os_release(path: Path = OS_RELEASE_PATH) -> dict[str, str]:
    """Read and parse an os-release file, returning an empty dict if absent."""
    try:
        return parse_os_release(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def parse_mountinfo(text: str) -> list[dict[str, Any]]:
    """Parse ``mountinfo`` entries into mount attribute mappings.

    Mountpoint, device, filesystem type, and mount options are unescaped.
    Entries with malformed lines are skipped.
    """
    mounts: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        fields = raw_line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) < separator + 3:
            continue

        mount = _unescape_mount_field(fields[4])
        # Per-mount options come before the separator; filesystem/superblock
        # options (e.g. btrfs ``compress=`` and ``subvol=``) come after it.
        super_options = fields[separator + 3] if len(fields) > separator + 3 else ""
        options = _parse_options(fields[5], super_options)
        mounts.append(
            {
                "mount": mount,
                "device": _unescape_mount_field(fields[separator + 2]),
                "fstype": _unescape_mount_field(fields[separator + 1]),
                "options": options,
            }
        )
    return mounts


def collect_mounts(
    mountinfo_path: Path = MOUNTINFO_PATH,
    statvfs: Callable[[str], os.statvfs_result] = os.statvfs,
) -> tuple[Mapping[str, Any], ...]:
    """Collect mounted filesystems with their capacity, omitting unreadable ones."""
    try:
        text = mountinfo_path.read_text(encoding="utf-8")
    except OSError:
        return ()

    mounts: list[Mapping[str, Any]] = []
    for mount in parse_mountinfo(text):
        try:
            stats = statvfs(mount["mount"])
        except OSError:
            continue
        block_size = stats.f_frsize or stats.f_bsize
        total = stats.f_blocks * block_size
        free = stats.f_bfree * block_size
        mount["bytes_total"] = total
        mount["bytes_free"] = free
        mount["bytes_used"] = total - free
        mounts.append(MappingProxyType(mount))
    return tuple(mounts)


def _parse_options(*values: str) -> tuple[str, ...]:
    options = [
        _unescape_mount_field(option)
        for value in values
        for option in value.split(",")
        if option
    ]
    # The two option sets overlap (e.g. ``rw``), so keep the first occurrence.
    return tuple(dict.fromkeys(options))


_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape_mount_field(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def collect_facts(os_release_path: Path = OS_RELEASE_PATH) -> Mapping[str, Any]:
    """Collect a read-only snapshot of system facts.

    Optional facts whose source is unavailable are omitted rather than causing
    a failure. Values are snapshots; tasks needing live state must query it.
    """
    facts: dict[str, Any] = {}

    try:
        facts["hostname"] = socket.gethostname()
    except OSError:
        pass

    os_release = read_os_release(os_release_path)
    if os_release:
        facts["os_release"] = MappingProxyType(os_release)
        if "ID" in os_release:
            facts["distro_id"] = os_release["ID"]
        if "VERSION_ID" in os_release:
            facts["version_id"] = os_release["VERSION_ID"]

    if machine := platform.machine():
        facts["arch"] = machine
    if kernel := platform.release():
        facts["kernel"] = kernel

    mounts = collect_mounts()
    if mounts:
        facts["mounts"] = mounts

    return MappingProxyType(facts)
