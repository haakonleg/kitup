"""Helpers shared by the container integration tests."""

import subprocess
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


def sudo(*args: str) -> list[str]:
    """Return a non-interactive sudo command."""
    return ["sudo", "-n", *args]


def run(
    argv: list[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
    timeout: float = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=check,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def write_bundle(root: Path, name: str, source: str) -> Path:
    """Create a project root containing one auto-discovered bundle."""
    folder = root / "bundles" / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(
        "from kitup import Bundle\n\nBundle()\n",
        encoding="utf-8",
    )

    body = textwrap.dedent(source).strip()
    (folder / "tasks.py").write_text(
        f"from kitup import task\n\n{body}\n",
        encoding="utf-8",
    )
    return root


def run_kitup(command: str, root: Path, *args: str) -> int:
    """Run the CLI in-process against a temporary project root."""
    return main([command, str(root), "--no-color", *args])


def rpm_installed(name: str = TEST_PACKAGE) -> bool:
    return run(["rpm", "-q", name], check=False).returncode == 0


def remove_package(name: str = TEST_PACKAGE) -> None:
    if rpm_installed(name):
        run(sudo("dnf5", "remove", "-y", name))


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
