"""Helpers shared by the container integration tests."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from kitup.cli import main

TEST_PACKAGE = "kitup-test"
TEST_REPO = Path("/srv/kitup-repo")
TEST_RPM = TEST_REPO / "kitup-test-1.0-1.noarch.rpm"
SCHEMA = "org.kitup.IntegrationTest"
SCHEMA_KEY = "example-string"
SCHEMA_DEFAULT = "'default-value'"


def run(
    argv: list[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=check,
        cwd=cwd,
        text=True,
        capture_output=True,
    )


def write_bundle(root: Path, name: str, source: str) -> Path:
    """Create a configuration root containing one single-purpose bundle."""
    folder = root / "bundles" / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(
        "from __future__ import annotations\n\n"
        "from kitup.registry import Bundle\n\n"
        f"bundle = Bundle({name!r}, 'integration test bundle')\n\n"
        f"{textwrap.dedent(source).strip()}\n",
        encoding="utf-8",
    )
    return root


def run_kitup(command: str, root: Path, *args: str) -> int:
    """Run the CLI in-process against a temporary configuration root."""
    return main([command, "-D", str(root), "--no-color", *args])


def rpm_installed(name: str = TEST_PACKAGE) -> bool:
    return run(["rpm", "-q", name], check=False).returncode == 0


def remove_package(name: str = TEST_PACKAGE) -> None:
    if rpm_installed(name):
        run(["sudo", "-n", "dnf5", "remove", "-y", name])


def dnf_reposdir() -> Path:
    result = run(["dnf5", "--dump-main-config"])
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "reposdir":
            first = value.split(",", 1)[0].strip()
            if first:
                return Path(first)
    return Path("/etc/yum.repos.d")


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return run(["systemctl", *args], check=False)
