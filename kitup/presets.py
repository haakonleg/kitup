"""Read plain-text preset files from the project and merge them into CLI arguments."""

import argparse
import contextlib
import io
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from ._utils.paths import expand_path
from .errors import PresetError
from .project import resolve_preset

_IGNORED_OPTIONS: Final = frozenset({"-h", "--help", "-V", "--version"})


def read_preset_tokens(path: Path) -> list[str]:
    """Read a preset file and return its shell-split token stream."""
    preset_path = Path(path)
    if not preset_path.exists():
        raise PresetError(f"preset not found: {preset_path}")
    if not preset_path.is_file():
        raise PresetError(f"preset is not a file: {preset_path}")

    try:
        text = preset_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PresetError(f"cannot read preset {preset_path}: {exc}") from exc

    tokens: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        try:
            tokens.extend(shlex.split(line, comments=False, posix=True))
        except ValueError as exc:
            raise PresetError(f"invalid quoting in preset {preset_path}:{number}: {exc}") from exc
    return tokens


def parse_with_presets(
    parser: argparse.ArgumentParser,
    argv: Sequence[str],
    *,
    root: Path | None = None,
) -> tuple[argparse.Namespace, tuple[Path, ...]]:
    """Parse ``argv`` and overlay any ``--preset`` files before the real CLI.

    Relative preset names resolve under the project's ``presets/`` directory;
    absolute paths are used as-is. ``parser`` must have been built with
    suppressed defaults so an option that is absent from one source does not
    erase a value from another. Returns the merged namespace and the resolved
    preset paths.
    """
    real = parser.parse_args(list(argv))
    command = real.command

    if root is None:
        project_dir = getattr(real, "project_dir", None)
        root = expand_path(project_dir or ".", error=PresetError, resolve=True)
    else:
        root = expand_path(root, error=PresetError, resolve=True)

    preset_paths = tuple(resolve_preset(root, value) for value in getattr(real, "preset", ()))
    merged = argparse.Namespace()
    for path in preset_paths:
        tokens = read_preset_tokens(path)
        namespace = _parse_preset_tokens(parser, command, tokens, path)
        _overlay(merged, namespace)
    _overlay(merged, real)
    return merged, preset_paths


def _parse_preset_tokens(
    parser: argparse.ArgumentParser,
    command: str,
    tokens: Sequence[str],
    path: Path,
) -> argparse.Namespace:
    filtered = _filter_preset_tokens(tokens)
    stderr = io.StringIO()
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            namespace, _ = parser.parse_known_args([command, *filtered])
    except SystemExit as exc:
        message = stderr.getvalue().strip() or f"invalid options in {path}"
        raise PresetError(f"invalid preset {path}: {message}") from exc

    for key in ("command", "project_dir", "preset"):
        if hasattr(namespace, key):
            delattr(namespace, key)
    return namespace


def _filter_preset_tokens(tokens: Sequence[str]) -> list[str]:
    """Drop options that are not valid in a preset."""
    filtered: list[str] = []
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            if not token.startswith("-"):
                continue
        if token in _IGNORED_OPTIONS:
            continue
        if token in {"-p", "--preset"}:
            skip_value = True
            continue
        if token.startswith(("--preset=", "-p=")):
            continue
        filtered.append(token)
    return filtered


def _overlay(target: argparse.Namespace, source: argparse.Namespace) -> None:
    for key, value in vars(source).items():
        setattr(target, key, value)
