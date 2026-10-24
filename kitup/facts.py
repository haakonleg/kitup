"""System fact collection, performed once per run."""

from __future__ import annotations

import platform
import shlex
import socket
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

OS_RELEASE_PATH = Path("/etc/os-release")


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

    return MappingProxyType(facts)
