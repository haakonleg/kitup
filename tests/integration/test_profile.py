"""Integration test for composed profiles and imported dependencies."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from .helpers import run_kitup

pytestmark = pytest.mark.integration


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_composed_profiles_merge_config_and_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "bundles" / "__init__.py", "")
    write(
        tmp_path / "bundles" / "base" / "__init__.py",
        """
        from kitup.registry import Bundle

        from . import tasks

        Bundle("Base", modules=[tasks])
        """,
    )
    write(
        tmp_path / "bundles" / "base" / "tasks.py",
        """
        from kitup.registry import task

        @task("show merged config")
        def show(ctx):
            value = f"{ctx.config.BASE}/{ctx.config.CHILD}"
            ctx.changed({"value": value}, value)
        """,
    )
    write(
        tmp_path / "bundles" / "extra" / "__init__.py",
        """
        from bundles import base

        from kitup.registry import Bundle

        from . import tasks

        Bundle("Extra", depends_on=[base], modules=[tasks])
        """,
    )
    write(
        tmp_path / "bundles" / "extra" / "tasks.py",
        """
        from kitup.registry import task

        @task("extra")
        def extra(ctx):
            ctx.changed({}, "extra")
        """,
    )
    write(
        tmp_path / "base_profile.py",
        """
        from pathlib import Path

        from bundles import base

        from kitup import profile

        profile(bundles=[base], config=[Path("base.py")], description="Base")
        """,
    )
    write(tmp_path / "base.py", "BASE = 'base'\n")
    write(tmp_path / "child.py", "CHILD = 'child'\n")
    write(
        tmp_path / "kitup.py",
        """
        import base_profile
        from pathlib import Path

        from bundles import extra

        from kitup import profile

        profile.extend(base_profile, bundles=[extra], config=[Path("child.py")])
        """,
    )

    assert run_kitup("apply", tmp_path, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "base/child" in out
    assert out.index("bundle: Base") < out.index("bundle: Extra")
