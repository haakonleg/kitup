"""Helpers shared by the unit and integration suites."""

import textwrap
from collections.abc import Mapping
from pathlib import Path


def write(path: Path, text: str) -> None:
    """Write dedented text, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def make_project(
    root: Path,
    *,
    bundles: Mapping[str, str | Mapping[str, str]] | None = None,
    var_files: Mapping[str, str] | None = None,
    vars: str | None = None,
    preset: Mapping[str, str] | None = None,
    modules: Mapping[str, str] | None = None,
) -> Path:
    """Build a project tree and return its root.

    A bundle value may be a mapping of files relative to the bundle folder, or
    a string used as an auto-discovered ``tasks.py``. ``vars`` is written to
    ``vars/base.py``; ``var_files`` adds further files under ``vars/``. Preset
    files are written under ``presets/``. ``modules`` adds files under
    ``modules/``.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    if bundles:
        (root / "bundles").mkdir(exist_ok=True)
        for name, value in bundles.items():
            folder = root / "bundles" / name
            if isinstance(value, str):
                write(folder / "__init__.py", "from kitup import bundle\n\nbundle()\n")
                write(folder / "tasks.py", value)
            else:
                for relative, text in value.items():
                    write(folder / relative, text)

    if vars is not None:
        write(root / "vars" / "base.py", vars)
    if var_files:
        for stem, text in var_files.items():
            write(root / "vars" / f"{stem}.py", text)
    if preset:
        for name, text in preset.items():
            write(root / "presets" / name, text)
    if modules:
        for relative, text in modules.items():
            write(root / "modules" / relative, text)

    return root
