"""Command-line interface."""

import sys
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from . import PACKAGE_NAME, __version__
from ._utils.paths import expand_path
from ._utils.security import disable_same_uid_ptrace
from ._utils.validation import split_tags
from .args import Args, Command, Option, Parser
from .commands import CommandRunner
from .context import TaskContext
from .errors import KitupError, PrivilegeError, ProjectError, UsageError
from .facts import collect_facts
from .output import Output, bundle_label
from .presets import parse_with_presets
from .privileges import PrivilegeManager
from .project import BUNDLES_DIR, PRESETS_DIR, VARS_DIR, Project, load_project, on_project_path
from .registry import Bundle
from .resolver import BundlePlan, filter_plan, resolve
from .runner import Runner
from .vars import Vars, filter_var_paths, load_bundle_vars, load_vars

_NO_DEFAULT: Final = object()
_VERSION: Final = f"{PACKAGE_NAME} {__version__}"


_PROJECT_OPTION: Final = Option(
    flags=("project_dir",),
    dest="project_dir",
    default=None,
    metavar="PROJECT_DIR",
    help="project directory (default: current directory)",
    positional=True,
)
_PRESET_OPTION: Final = Option(
    flags=("-p", "--preset"),
    dest="preset",
    default=_NO_DEFAULT,
    kind="append",
    metavar="PRESET",
    help="read options from a preset file (relative to presets/, repeatable)",
)
_BUNDLE_PATH_OPTION: Final = Option(
    flags=("--bundle-path",),
    dest="bundle_path",
    default=(),
    kind="append",
    metavar="DIR",
    help="load bundles from an extra directory (repeatable)",
)
_VAR_FILE_OPTION: Final = Option(
    flags=("--var-file",),
    dest="var_file",
    default=(),
    kind="append",
    metavar="PATH",
    help="load an extra var file (repeatable)",
)
_SELECTION_OPTIONS: Final = (
    Option(
        flags=("-b", "--include-bundles"),
        dest="include_bundles",
        default=(),
        kind="append",
        metavar="LIST",
        help="only include these bundles and their dependencies",
    ),
    Option(
        flags=("-B", "--exclude-bundles"),
        dest="exclude_bundles",
        default=(),
        kind="append",
        metavar="LIST",
        help="exclude these bundles",
    ),
    Option(
        flags=("-i", "--include-vars"),
        dest="include_vars",
        default=(),
        kind="append",
        metavar="LIST",
        help="only include these vars",
    ),
    Option(
        flags=("-E", "--exclude-vars"),
        dest="exclude_vars",
        default=(),
        kind="append",
        metavar="LIST",
        help="exclude these vars",
    ),
)
_RUN_OPTIONS: Final = (
    Option(
        flags=("-n", "--dry-run"),
        dest="dry_run",
        default=False,
        kind="flag",
        help="report without mutating",
    ),
    Option(
        flags=("-d", "--diff"),
        dest="diff",
        default=False,
        kind="flag",
        help="show unified diffs",
    ),
    Option(
        flags=("-s", "--sudo"),
        dest="sudo",
        default=False,
        kind="flag",
        help="validate sudo credentials up front",
    ),
    Option(
        flags=("-t", "--tags"),
        dest="tags",
        default=(),
        kind="append",
        metavar="TAGS",
        help="only matching tags",
    ),
    Option(
        flags=("-x", "--skip-tags"),
        dest="skip_tags",
        default=(),
        kind="append",
        metavar="TAGS",
        help="skip matching tags",
    ),
)
_VERSION_OPTION: Final = Option(
    flags=("-V", "--version"),
    dest="version",
    default=_NO_DEFAULT,
    kind="version",
    version=_VERSION,
)
_COMMON_OPTIONS: Final = (
    Option(
        flags=("-v", "--verbose"),
        dest="verbose",
        default=False,
        kind="flag",
        help="verbose output",
    ),
    Option(
        flags=("-c", "--no-color"),
        dest="no_color",
        default=False,
        kind="flag",
        help="disable colors",
    ),
    _VERSION_OPTION,
)
_FORCE_OPTION: Final = Option(
    flags=("--force",),
    dest="force",
    default=False,
    kind="flag",
    help="overwrite existing project files",
)

_APPLY_OPTIONS: Final = (
    _PROJECT_OPTION,
    _PRESET_OPTION,
    _BUNDLE_PATH_OPTION,
    _VAR_FILE_OPTION,
    *_SELECTION_OPTIONS,
    *_RUN_OPTIONS,
    *_COMMON_OPTIONS,
)
_LIST_OPTIONS: Final = (
    _PROJECT_OPTION,
    _PRESET_OPTION,
    _BUNDLE_PATH_OPTION,
    _VAR_FILE_OPTION,
    *_SELECTION_OPTIONS,
    *_COMMON_OPTIONS,
)
_CHECK_OPTIONS: Final = _LIST_OPTIONS
_INIT_OPTIONS: Final = (_PROJECT_OPTION, _FORCE_OPTION, *_COMMON_OPTIONS)
_ALL_OPTIONS: Final = (*_APPLY_OPTIONS, *_LIST_OPTIONS, *_INIT_OPTIONS)

_DEFAULTS: Final[dict[str, Any]] = {
    option.dest: option.default for option in _ALL_OPTIONS if option.default is not _NO_DEFAULT
}


def build_parser() -> Parser:
    """Build the command-line argument parser.

    Defaults are suppressed so preset merging can distinguish an option
    that was supplied from one that was absent. ``_apply_defaults`` fills the
    final values from the option table.
    """
    return Parser(
        prog=PACKAGE_NAME,
        description="The simple python-driven system configuration tool.",
        commands=(
            Command(
                name="apply",
                help="apply a project",
                options=_APPLY_OPTIONS,
                epilog=_examples("kitup apply . --preset laptop", "kitup apply -b base,workstation -n"),
            ),
            Command(
                name="list",
                help="list bundles and tasks",
                options=_LIST_OPTIONS,
                epilog=_examples("kitup list", "kitup list my-machine -v"),
            ),
            Command(
                name="check",
                help="discover, resolve, and parse without running",
                options=_CHECK_OPTIONS,
                epilog=_examples("kitup check", "kitup check . --preset laptop"),
            ),
            Command(
                name="init",
                help="scaffold a new project",
                options=_INIT_OPTIONS,
                epilog=_examples("kitup init", "kitup init my-machine --force"),
            ),
        ),
        top_options=(_VERSION_OPTION,),
    )


def _examples(*lines: str) -> str:
    return "examples:\n" + "\n".join(f"  {line}" for line in lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return an exit code."""
    disable_same_uid_ptrace()
    arguments = list(sys.argv[1:] if argv is None else argv)

    try:
        args, preset_paths = parse_with_presets(build_parser(), arguments)
        _apply_defaults(args)
        output = Output(no_color=args.no_color, verbose=args.verbose)
        if args.command == "init":
            return _init(args, output)

        project = load_project(
            _project_root(args.project_dir),
            preset_paths=preset_paths,
            bundle_paths=args.bundle_path,
            extra_var_paths=args.var_file,
        )
        with on_project_path(project.root):
            return _run_project(args, project, output)
    except KitupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, (UsageError, PrivilegeError)) else 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


def _apply_defaults(args: Args) -> None:
    for key, value in _DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)


def _project_root(value: str | Path | None) -> Path:
    return expand_path(value or ".", error=ProjectError, resolve=True)


def _run_project(args: Args, project: Project, output: Output) -> int:
    if not project.bundles and args.command in {"apply", "check"}:
        raise ProjectError(_missing_bundles_message(project))

    registry = {bundle.id: bundle for bundle in project.bundles}
    plan = resolve(project.bundles, registry=registry)
    plan = filter_plan(
        plan,
        include=_value_filter(args.include_bundles),
        exclude=_value_filter(args.exclude_bundles),
        registry=registry,
    )
    var_paths = filter_var_paths(
        project.var_paths,
        include=_value_filter(args.include_vars),
        exclude=_value_filter(args.exclude_vars),
    )

    if args.command in {"list", "check"}:
        output.banner()
        if args.command == "check":
            values = load_vars(var_paths)
            load_bundle_vars(values, plan.bundles)
        _print_environment(project, var_paths, output, verbose=args.verbose)
        _print_plan(plan, output, verbose=args.verbose)
        if args.command == "check":
            _warn_empty_bundles(plan, output)
        return 0

    facts = collect_facts()
    output.banner(facts)
    values = load_vars(var_paths)
    bundle_values = load_bundle_vars(values, plan.bundles)

    if args.verbose:
        _print_environment(project, var_paths, output, verbose=args.verbose)
    return _apply(args, project, plan, values, facts, output, bundle_vars=bundle_values)


def _apply(
    args: Args,
    project: Project,
    plan: BundlePlan,
    values: Vars,
    facts: Mapping[str, Any],
    output: Output,
    *,
    bundle_vars: Mapping[Bundle, Vars],
) -> int:
    sudo = PrivilegeManager()
    if args.sudo:
        sudo.validate()

    command = CommandRunner(sudo, output=output)
    context = TaskContext(
        dry_run=args.dry_run,
        diff=args.diff,
        verbose=args.verbose,
        root=project.root,
        project=project,
        vars=values,
        facts=facts,
        sudo=sudo,
        command=command,
        bundle_vars=bundle_vars,
        output=output,
    )
    try:
        runner = Runner(
            plan,
            context,
            output,
            tags=_value_filter(args.tags),
            skip_tags=_value_filter(args.skip_tags),
        )
        return runner.run()
    finally:
        context.close()


def _missing_bundles_message(project: Project) -> str:
    locations = [project.root / BUNDLES_DIR, *project.bundle_roots]
    existing = [path for path in locations if path.exists()]
    if not existing:
        return f"no bundles found in {project.root}; run 'kitup init {project.root}' to create a project"
    return "no bundles found in " + ", ".join(str(path) for path in existing)


def _print_environment(
    project: Project,
    var_paths: Sequence[Path],
    output: Output,
    *,
    verbose: bool,
) -> None:
    output.write()
    project_label = str(project.root) if verbose else (project.root.name or str(project.root))
    output.write(f"project: {project_label}")
    if project.bundle_roots:
        bundle_paths = ", ".join(_path_label(path, verbose=verbose) for path in project.bundle_roots)
        output.write(f"bundle path: {bundle_paths}")
    if project.preset_paths:
        preset_labels = ", ".join(_path_label(path, verbose=verbose) for path in project.preset_paths)
        output.write(f"preset: {preset_labels}")
    if var_paths:
        var_labels = ", ".join(_path_label(path, verbose=verbose) for path in var_paths)
        output.write(f"vars: {var_labels}")


def _path_label(path: Path, *, verbose: bool) -> str:
    return str(path) if verbose else path.name


def _print_plan(plan: BundlePlan, output: Output, *, verbose: bool) -> None:
    for bundle in plan.bundles:
        output.write()
        output.write(f"bundle: {bundle_label(bundle)}")
        if verbose:
            if bundle.description:
                description = bundle.description.replace("\n", "\n  ")
                output.write(f"  {description}")
            dependencies = ", ".join(_dependency_id(dependency) for dependency in bundle.depends_on)
            output.write(f"  depends:{f' {dependencies}' if dependencies else ''}")
            if bundle.var_paths:
                var_labels = ", ".join(_path_label(path, verbose=verbose) for path in bundle.var_paths)
                output.write(f"  vars: {var_labels}")
            output.write("  tasks:")
        for task in bundle.tasks:
            if verbose:
                tags = ", ".join(sorted(map(str, task.tags)))
                output.write(f"    - {task.name}")
                output.write(f"      id: {task.id}")
                output.write(f"      tags:{f' {tags}' if tags else ''}")
                if task.description:
                    description = task.description.replace("\n", "\n      ")
                    output.write(f"      description: {description}")
            else:
                output.write(f"    - {task.name}")


def _dependency_id(dependency: str | Bundle) -> str:
    if isinstance(dependency, str):
        return dependency
    return dependency.id


def _warn_empty_bundles(plan: BundlePlan, output: Output) -> None:
    empty = [bundle.id for bundle in plan.bundles if not bundle.tasks]
    if not empty:
        return
    output.write()
    for bundle_id in empty:
        output.write(f"warning: bundle {bundle_id!r} has no tasks")


def _value_filter(values: Sequence[str]) -> frozenset[str] | None:
    """Return an active filter, or None when the option was not supplied.

    An option supplied with no usable values is still an active filter and
    therefore selects nothing.
    """
    if not values:
        return None
    return frozenset(split_tags(values))


def _init(args: Args, output: Output) -> int:
    root = _project_root(args.project_dir)
    files = _init_files(root)
    existing = [path for path in files if path.exists()]
    if existing and not args.force:
        names = ", ".join(str(path.relative_to(root)) for path in existing)
        raise ProjectError(f"refusing to overwrite existing files in {root}: {names} (use --force)")

    for path, content in files.items():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ProjectError(f"cannot create {path}: {exc}") from exc

    output.write(f"Initialized kitup project in {root}")
    output.write("Next steps:")
    output.write("  kitup check")
    output.write("  kitup apply -n")
    return 0


def _init_files(root: Path) -> dict[Path, str]:
    bundle = root / BUNDLES_DIR / "example"
    return {
        bundle / "__init__.py": textwrap.dedent(
            """
            from kitup import Bundle

            Bundle("Example bundle")
            """
        ).lstrip(),
        bundle / "tasks.py": textwrap.dedent(
            """
            from kitup import task


            @task
            def hello(ctx):
                \"\"\"Say hello from the example bundle.\"\"\"
                ctx.note("Hello from the example bundle")
            """
        ).lstrip(),
        root / VARS_DIR / "base.py": textwrap.dedent(
            """
            PACKAGES = ["git"]
            """
        ).lstrip(),
        root / VARS_DIR / "example.py": textwrap.dedent(
            """
            # Machine-specific values for the example vars.
            EXAMPLE_VALUE = "change me"
            """
        ).lstrip(),
        root / PRESETS_DIR / "laptop": textwrap.dedent(
            """
            # Example preset. Run it with: kitup apply --preset laptop
            # Each line is split like a shell command line; options only.
            # --include-bundles example
            # -t example
            """
        ).lstrip(),
    }
