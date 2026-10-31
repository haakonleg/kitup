"""Helpers shared by the container integration tests."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from kitup.cli import main

TEST_PACKAGE = "kitup-test"
TEST_REPO = Path("/srv/kitup-repo")
TEST_RPM = TEST_REPO / "kitup-test-1.0-1.noarch.rpm"
FLATPAK_REPO = Path("/srv/flatpak-repo")
FLATPAK_REMOTE = "kitup-test"
FLATPAK_USER_REMOTE = "kitup-test-user"
FLATPAK_APP = "org.kitup.TestApp"
FLATPAK_RUNTIME = "org.kitup.TestPlatform"
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
    """Create a configuration root with an imported bundle and a profile."""
    bundles = root / "bundles"
    folder = bundles / name
    folder.mkdir(parents=True)
    (bundles / "__init__.py").write_text("", encoding="utf-8")
    (folder / "__init__.py").write_text(
        "from __future__ import annotations\n\n"
        "from kitup.registry import Bundle\n\n"
        "from . import tasks\n\n"
        f"Bundle({name!r}, 'integration test bundle', modules=[tasks])\n",
        encoding="utf-8",
    )

    body = textwrap.dedent(source).strip()
    (folder / "tasks.py").write_text(
        "from __future__ import annotations\n\n"
        "from kitup.registry import task\n\n"
        f"{body}\n",
        encoding="utf-8",
    )

    (root / "kitup.py").write_text(
        "from bundles import " + name + "\n\n"
        "from kitup import profile\n\n"
        f"profile(bundles=[{name}])\n",
        encoding="utf-8",
    )
    return root


def run_kitup(command: str, root: Path, *args: str) -> int:
    """Run the CLI in-process against a temporary configuration root."""
    _purge_profile_modules()
    try:
        return main(
            [command, str(root / "kitup.py"), "--no-color", *args]
        )
    finally:
        _purge_profile_modules()


def _purge_profile_modules() -> None:
    """Keep separate configuration roots isolated inside one pytest process."""
    for name in tuple(sys.modules):
        if (
            name == "bundles"
            or name.startswith("bundles.")
            or name in {"base_profile", "middle_profile", "child_profile"}
        ):
            sys.modules.pop(name, None)


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


def flatpak_remotes(scope: str = "system") -> list[str]:
    result = run(
        ["flatpak", "remotes", f"--{scope}", "--columns=name"],
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def flatpak_installed(scope: str = "system") -> list[str]:
    result = run(
        ["flatpak", "list", f"--{scope}", "--columns=application"],
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]
