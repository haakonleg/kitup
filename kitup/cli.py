"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import __version__
from .commands import CommandRunner
from .config import load_config
from .context import TaskContext
from .discovery import bundles_dir, discover_bundles
from .errors import KitupError, PrivilegeError, UsageError
from .facts import collect_facts
from .output import Output
from .privileges import PrivilegeManager
from .resolver import BundlePlan, resolve
from .runner import Runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kitup",
        description="The simple python-driven system configuration tool.",
    )
    parser.add_argument("-V", "--version", action="version", version=_version())
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    apply_parser = subparsers.add_parser("apply", help="apply bundles")
    apply_parser.add_argument("bundles", nargs="*", metavar="BUNDLE")
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
    list_parser.add_argument("bundles", nargs="*", metavar="BUNDLE")
    _add_common_options(list_parser)

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-D", "--dir", metavar="PATH", help="configuration root")
    parser.add_argument("-C", "--config", metavar="PATH", help="override config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    parser.add_argument("-c", "--no-color", action="store_true", help="disable colors")
    parser.add_argument("-V", "--version", action="version", version=_version())


def _version() -> str:
    return f"kitup {__version__}"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Output(no_color=args.no_color, verbose=args.verbose)

    try:
        config_dir = _resolve_config_dir(args.dir)
        override = _resolve_override(args.config)
        config = load_config(config_dir, override)
        bundles = discover_bundles(config_dir)
        plan = resolve(args.bundles or None, bundles)

        facts = collect_facts()
        output.banner(facts)
        if args.verbose:
            _print_environment(config_dir, override, output)

        if args.command == "list":
            _print_plan(plan, output, verbose=args.verbose)
            return 0

        return _apply(args, plan, config_dir, config, facts, output)
    except KitupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, (UsageError, PrivilegeError)) else 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


def _apply(
    args: argparse.Namespace,
    plan: BundlePlan,
    config_dir: Path,
    config: SimpleNamespace,
    facts: Mapping[str, Any],
    output: Output,
) -> int:
    tags = _tag_filter(args.tags)
    skip_tags = _tag_filter(args.skip_tags)

    sudo = PrivilegeManager()
    if args.sudo:
        sudo.validate()

    command = CommandRunner(sudo, output=output)
    context = TaskContext(
        dry_run=args.dry_run,
        diff=args.diff,
        verbose=args.verbose,
        config_dir=config_dir,
        config=config,
        facts=facts,
        sudo=sudo,
        command=command,
        output=output,
    )
    runner = Runner(
        plan,
        context,
        output,
        tags=tags,
        skip_tags=skip_tags,
    )
    return runner.run()


def _print_environment(config_dir: Path, override: Path | None, output: Output) -> None:
    output.write()
    output.write(f"using bundle dir: {bundles_dir(config_dir)}")
    config = config_dir / "config.py"
    if config.is_file():
        output.write(f"using config: {config}")
    if override is not None:
        output.write(f"using config: {override}")


def _print_plan(plan: BundlePlan, output: Output, *, verbose: bool) -> None:
    for bundle in plan.bundles:
        output.write()
        output.write(f"bundle: {bundle.name}")
        if bundle.description:
            output.write(f"  {bundle.description}")
        if verbose:
            dependencies = ", ".join(bundle.depends_on)
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
    return frozenset(_split_tags(values))


def _split_tags(values: Sequence[str]) -> list[str]:
    return [
        tag.strip()
        for value in values
        for tag in value.split(",")
        if tag.strip()
    ]


def _resolve_config_dir(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def _resolve_override(value: str | None) -> Path | None:
    return Path(value).expanduser().resolve() if value else None
