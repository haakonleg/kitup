"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from . import PACKAGE_NAME, __version__
from ._utils.security import disable_same_uid_ptrace
from .commands import CommandRunner
from .config import load_config
from .context import TaskContext
from .errors import KitupError, PrivilegeError, UsageError
from .facts import collect_facts
from .output import Output
from .privileges import PrivilegeManager
from .profiles import RunDefinition, load_profile
from .resolver import BundlePlan, resolve
from .runner import Runner
from .validation import split_tags


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PACKAGE_NAME,
        description="The simple python-driven system configuration tool.",
    )
    parser.add_argument("-V", "--version", action="version", version=_version())
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    apply_parser = subparsers.add_parser("apply", help="apply a profile")
    apply_parser.add_argument(
        "profile",
        nargs="?",
        metavar="PROFILE",
        help="profile file (default: ./kitup.py)",
    )
    _add_common_options(apply_parser)
    apply_parser.add_argument(
        "-n", "--dry-run", action="store_true", help="report without mutating"
    )
    apply_parser.add_argument("-d", "--diff", action="store_true", help="show unified diffs")
    apply_parser.add_argument(
        "-s", "--sudo", action="store_true", help="validate sudo credentials up front"
    )
    apply_parser.add_argument(
        "-t", "--tags", action="append", default=[], metavar="TAGS", help="only matching tags"
    )
    apply_parser.add_argument(
        "-x", "--skip-tags", action="append", default=[], metavar="TAGS", help="skip matching tags"
    )

    list_parser = subparsers.add_parser("list", help="list bundles and tasks")
    list_parser.add_argument(
        "profile",
        nargs="?",
        metavar="PROFILE",
        help="profile file (default: ./kitup.py)",
    )
    _add_common_options(list_parser)

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    parser.add_argument("-c", "--no-color", action="store_true", help="disable colors")
    parser.add_argument("-V", "--version", action="version", version=_version())


def _version() -> str:
    return f"{PACKAGE_NAME} {__version__}"


def main(argv: Sequence[str] | None = None) -> int:
    disable_same_uid_ptrace()
    args = build_parser().parse_args(argv)
    output = Output(no_color=args.no_color, verbose=args.verbose)

    try:
        profile_path = _resolve_profile(args.profile)
        old_path = list(sys.path)
        try:
            sys.path.insert(0, str(profile_path.parent))
            definition = load_profile(profile_path)
            config = load_config(definition.config)
            plan = resolve(definition.bundles)

            facts = collect_facts()
            output.banner(facts)
            if args.command == "list":
                _print_environment(
                    profile_path, definition, output, verbose=args.verbose
                )
                _print_plan(plan, output, verbose=args.verbose)
                return 0
            if args.verbose:
                _print_environment(
                    profile_path, definition, output, verbose=args.verbose
                )
            return _apply(args, profile_path, definition, plan, config, facts, output)
        finally:
            sys.path[:] = old_path
    except KitupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, (UsageError, PrivilegeError)) else 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


def _apply(
    args: argparse.Namespace,
    profile_path: Path,
    definition: RunDefinition,
    plan: BundlePlan,
    config: SimpleNamespace,
    facts: Mapping[str, Any],
    output: Output,
) -> int:
    tags = _merge_include_tags(definition.tags, _tag_filter(args.tags))
    skip_tags = _merge_skip_tags(definition.skip_tags, _tag_filter(args.skip_tags))

    sudo = PrivilegeManager()
    if args.sudo:
        sudo.validate()

    command = CommandRunner(sudo, output=output)
    context = TaskContext(
        dry_run=args.dry_run,
        diff=args.diff,
        verbose=args.verbose,
        config_dir=profile_path.parent,
        config=config,
        facts=facts,
        sudo=sudo,
        command=command,
        output=output,
    )
    try:
        runner = Runner(
            plan,
            context,
            output,
            tags=tags,
            skip_tags=skip_tags,
        )
        return runner.run()
    finally:
        context.close()


def _print_environment(
    profile_path: Path,
    definition: RunDefinition,
    output: Output,
    *,
    verbose: bool,
) -> None:
    output.write()
    output.write(f"profile: {_path_label(profile_path, verbose=verbose)}")
    if definition.description:
        output.write(f"description: {definition.description}")
    if definition.config:
        sources = ", ".join(
            _config_label(source, verbose=verbose) for source in definition.config
        )
        output.write(f"config: {sources}")


def _path_label(path: Path, *, verbose: bool) -> str:
    return str(path) if verbose else path.name


def _config_label(source: object, *, verbose: bool) -> str:
    if isinstance(source, Path):
        return _path_label(source, verbose=verbose)
    if isinstance(source, ModuleType):
        filename = getattr(source, "__file__", None)
        if filename:
            return _path_label(Path(filename).resolve(), verbose=verbose)
        return source.__name__
    return "<mapping>"


def _print_plan(plan: BundlePlan, output: Output, *, verbose: bool) -> None:
    for bundle in plan.bundles:
        output.write()
        output.write(f"bundle: {bundle.name}")
        if bundle.description:
            output.write(f"  {bundle.description}")
        if verbose:
            dependencies = ", ".join(
                dependency.name for dependency in bundle.depends_on
            )
            output.write(f"  depends:{f' {dependencies}' if dependencies else ''}")
            output.write("  tasks:")
        for task in bundle.tasks:
            tags = ", ".join(sorted(task.tags))
            if verbose:
                output.write(f"    - {task.name}")
                output.write(f"      id: {task.id}")
                output.write(f"      tags:{f' {tags}' if tags else ''}")
                if task.description:
                    output.write(f"      description: {task.description}")
            else:
                output.write(f"    - {task.name}{f' [{tags}]' if tags else ''}")


def _tag_filter(values: Sequence[str]) -> frozenset[str] | None:
    """Return an active tag filter, or None when the option was not supplied.

    An option supplied with no usable tags is still an active filter and
    therefore selects no tasks.
    """
    if not values:
        return None
    return frozenset(split_tags(values))


def _merge_include_tags(
    profile_tags: frozenset[str], cli_tags: frozenset[str] | None
) -> frozenset[str] | None:
    if cli_tags is None:
        return profile_tags or None
    if profile_tags:
        return cli_tags & profile_tags
    return cli_tags


def _merge_skip_tags(
    profile_tags: frozenset[str], cli_tags: frozenset[str] | None
) -> frozenset[str] | None:
    combined = profile_tags | (cli_tags or frozenset())
    return combined or None


def _resolve_profile(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return (Path.cwd() / "kitup.py").resolve()
