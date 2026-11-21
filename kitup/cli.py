"""Command-line interface."""

import inspect
import sys
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from . import PACKAGE_NAME, __version__
from ._utils.importer import configure_pycache
from ._utils.paths import expand_path
from ._utils.security import disable_same_uid_ptrace
from ._utils.validation import split_tags
from .args import Args, Command, Option, Parser
from .commands import CommandRunner
from .context import TaskContext
from .errors import KitupError, PrivilegeError, ProjectError, UsageError
from .host_facts import collect_facts
from .modules import discover_builtin_modules
from .output import Output
from .presets import parse_with_presets
from .privileges import PrivilegeManager
from .project import (
    BUNDLES_DIR,
    PRESETS_DIR,
    VARS_DIR,
    Project,
    discover_custom_modules,
    load_project,
    load_project_modules,
    on_project_path,
)
from .registry import MAIN_FILE, Bundle, Module
from .resolver import BundlePlan, filter_plan, resolve
from .runner import Runner
from .variables import Vars, filter_var_paths, load_bundle_vars, load_vars

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
_FILES_PATH_OPTION: Final = Option(
    flags=("--files-path",),
    dest="files_path",
    default=(),
    kind="append",
    metavar="DIR",
    help="load overlay files from an extra directory (repeatable)",
)
_MODULES_PATH_OPTION: Final = Option(
    flags=("--modules-path",),
    dest="modules_path",
    default=(),
    kind="append",
    metavar="DIR",
    help="load custom modules from an extra directory (repeatable)",
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
    Option(
        flags=("--no-pycache",),
        dest="no_pycache",
        default=False,
        kind="flag",
        help="do not write any bytecode cache for project code",
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
    _FILES_PATH_OPTION,
    _MODULES_PATH_OPTION,
    _VAR_FILE_OPTION,
    *_SELECTION_OPTIONS,
    *_RUN_OPTIONS,
    *_COMMON_OPTIONS,
)
_LIST_OPTIONS: Final = (
    _PROJECT_OPTION,
    _PRESET_OPTION,
    _BUNDLE_PATH_OPTION,
    _FILES_PATH_OPTION,
    _MODULES_PATH_OPTION,
    _VAR_FILE_OPTION,
    *_SELECTION_OPTIONS,
    *_COMMON_OPTIONS,
)
_CHECK_OPTIONS: Final = _LIST_OPTIONS
_INIT_OPTIONS: Final = (_PROJECT_OPTION, _FORCE_OPTION, *_COMMON_OPTIONS)
_LIST_MODULES_OPTIONS: Final = (_PROJECT_OPTION, _MODULES_PATH_OPTION, *_COMMON_OPTIONS)
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
                name="list-modules",
                help="list built-in and custom task modules",
                options=_LIST_MODULES_OPTIONS,
                epilog=_examples("kitup list-modules", "kitup list-modules my-machine -v"),
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
    output = Output()
    arguments = list(sys.argv[1:] if argv is None else argv)

    try:
        args, preset_paths = parse_with_presets(build_parser(), arguments, on_warning=output.warning)
        _apply_defaults(args)
        configure_pycache(disabled=args.no_pycache)
        output = Output(no_color=args.no_color, verbose=args.verbose, dry_run=args.dry_run)
        if args.command == "init":
            return _init(args, output)
        if args.command == "list-modules":
            return _list_modules(args, output)

        project = load_project(
            _project_root(args.project_dir),
            preset_paths=preset_paths,
            bundle_paths=args.bundle_path,
            files_paths=args.files_path,
            modules_paths=args.modules_path,
            extra_var_paths=args.var_file,
        )
        with on_project_path(project.root):
            return _run_project(args, project, output)
    except KitupError as exc:
        output.error(str(exc))
        return 2 if isinstance(exc, (UsageError, PrivilegeError)) else 1
    except KeyboardInterrupt:
        output.error("Interrupted.", prefix=False)
        return 130


def _apply_defaults(args: Args) -> None:
    for key, value in _DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)


def _project_root(value: str | Path | None) -> Path:
    return expand_path(value or ".", error=ProjectError, resolve=True)


def _list_modules(args: Args, output: Output) -> int:
    project = load_project_modules(_project_root(args.project_dir), modules_paths=args.modules_path)
    output.banner()
    output.write()
    output.write(f"project: {_project_label(project, verbose=args.verbose)}")
    _write_paths(output, "modules path", project.module_roots, verbose=args.verbose)
    _print_module_group("built-in modules", discover_builtin_modules(), output, verbose=args.verbose, show_path=False)
    _print_module_group(
        "custom modules", discover_custom_modules(project), output, verbose=args.verbose, show_path=True
    )
    return 0


def _run_project(args: Args, project: Project, output: Output) -> int:
    if not project.bundles and args.command in {"apply", "check"}:
        raise ProjectError(_missing_bundles_message(project))

    if args.command == "check":
        discover_custom_modules(project)

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
        if args.dry_run:
            output.dry_run_banner()
        return runner.run()
    finally:
        context.close()


def _missing_bundles_message(project: Project) -> str:
    locations = [project.root / BUNDLES_DIR, *project.bundle_roots]
    existing = [path for path in locations if path.exists()]
    if not existing:
        return f"no bundles found in {project.root}; run 'kitup init {project.root}' to create a project"
    return "no bundles found in " + ", ".join(str(path) for path in existing)


def _print_environment(project: Project, var_paths: Sequence[Path], output: Output, *, verbose: bool) -> None:
    output.write()
    output.write(f"project: {_project_label(project, verbose=verbose)}")
    _write_paths(output, "bundle path", project.bundle_roots, verbose=verbose)
    _write_paths(output, "files path", project.files_roots, verbose=verbose)
    _write_paths(output, "modules path", project.module_roots, verbose=verbose)
    _write_paths(output, "preset", project.preset_paths, verbose=verbose)
    _write_paths(output, "vars", var_paths, verbose=verbose)


def _project_label(project: Project, *, verbose: bool) -> str:
    return str(project.root) if verbose else (project.root.name or str(project.root))


def _path_label(path: Path, *, verbose: bool) -> str:
    return str(path) if verbose else path.name


def _write_paths(output: Output, label: str, paths: Sequence[Path], *, verbose: bool) -> None:
    if paths:
        labels = ", ".join(_path_label(path, verbose=verbose) for path in paths)
        output.write(f"{label}: {labels}")


def _print_plan(plan: BundlePlan, output: Output, *, verbose: bool) -> None:
    for bundle in plan.bundles:
        output.bundle_plan_header(bundle, verbose=verbose)
        if verbose:
            dependencies = ", ".join(_dependency_id(dependency) for dependency in bundle.depends_on)
            output.write(f"  depends:{f' {dependencies}' if dependencies else ''}")
            if bundle.var_paths:
                var_labels = ", ".join(_path_label(path, verbose=verbose) for path in bundle.var_paths)
                output.write(f"  vars: {var_labels}")
            output.write("  tasks:")
        for task in bundle.tasks:
            output.write(f"    - {task.name}")
            if not verbose:
                continue
            tags = ", ".join(sorted(map(str, task.tags)))
            output.write(f"      id: {task.id}")
            output.write(f"      tags:{f' {tags}' if tags else ''}")
            if task.description:
                description = task.description.replace("\n", "\n      ")
                output.write(f"      description: {description}")

    bundles = len(plan.bundles)
    tasks = sum(len(bundle.tasks) for bundle in plan.bundles)
    output.write()
    output.write(f"{bundles} {_plural(bundles, 'bundle')}, {tasks} {_plural(tasks, 'task')}")


def _plural(count: int, noun: str) -> str:
    """Return ``noun`` with a plural suffix unless ``count`` is one."""
    return noun if count == 1 else f"{noun}s"


def _dependency_id(dependency: str | Bundle) -> str:
    if isinstance(dependency, str):
        return dependency
    return dependency.id


def _print_module_group(
    label: str, modules: Sequence[Module], output: Output, *, verbose: bool, show_path: bool
) -> None:
    output.write()
    output.write(f"{label}:")
    if not modules:
        output.write("  (none)")
        return
    label_width = max(len(entry.name) for entry in modules) + 1
    for index, entry in enumerate(modules):
        if verbose and index:
            output.write()
        if verbose:
            output.write(f"  - {entry.name}")
            _print_module_details(entry, output, show_path=show_path)
        elif entry.description:
            description = entry.description.replace("\n", "\n      ")
            label_text = f"{entry.name}:"
            output.write(f"  - {label_text:<{label_width}}  {output.dim(description)}")
        else:
            output.write(f"  - {entry.name}")


def _print_module_details(entry: Module, output: Output, *, show_path: bool) -> None:
    if entry.description:
        description = entry.description.replace("\n", "\n      ")
        output.write(f"      description: {description}")
    module = sys.modules.get(entry.module_name)
    if show_path:
        path = getattr(module, "__file__", None)
        if path:
            output.write(f"      path: {path}")
    functions = _module_functions(module) if module is not None else []
    if not functions:
        output.write("      functions: (none)")
        return
    output.write("      functions:")
    for name, description in functions:
        suffix = f": {description}" if description else ""
        output.write(f"        - {name}{suffix}")


def _module_functions(module: ModuleType) -> list[tuple[str, str]]:
    """Return public functions with the first line of their docstring."""
    exported = getattr(module, "__all__", None)
    if exported is None:
        candidates = [
            (name, value)
            for name, value in vars(module).items()
            if not name.startswith("_") and _declared_in(value, module.__name__)
        ]
    else:
        candidates = [(name, getattr(module, name, None)) for name in exported if isinstance(name, str)]
    return [
        (name, _first_line(getattr(value, "__doc__", None))) for name, value in candidates if inspect.isroutine(value)
    ]


def _declared_in(value: object, module_name: str) -> bool:
    """Return whether ``value`` was defined by ``module_name`` or its submodules."""
    owner = getattr(value, "__module__", "")
    return owner == module_name or owner.startswith(f"{module_name}.")


def _first_line(doc: str | None) -> str:
    """Return the first non-empty line of a docstring."""
    if not doc:
        return ""
    return next((line.strip() for line in doc.splitlines() if line.strip()), "")


def _warn_empty_bundles(plan: BundlePlan, output: Output) -> None:
    empty = [bundle.id for bundle in plan.bundles if not bundle.tasks]
    if not empty:
        return
    output.write()
    for bundle_id in empty:
        output.warning(f"bundle {bundle_id!r} has no tasks", to_stderr=False)


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
    output.write(output.dim("Next steps:"))
    output.write("  kitup check")
    output.write("  kitup apply -n")
    return 0


def _init_files(root: Path) -> dict[Path, str]:
    bundle = root / BUNDLES_DIR / "example"
    return {
        bundle / MAIN_FILE: textwrap.dedent(
            """
            from kitup import bundle

            bundle("Example bundle")
            """
        ).lstrip(),
        bundle / "tasks.py": textwrap.dedent(
            """
            from kitup import ctx, task


            @task
            def hello():
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
