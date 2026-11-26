"""Project layout discovery for bundles, variables, and preset files."""

import importlib
import keyword
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final

from ._utils.importer import load_package_module, project_bytecode_cache
from ._utils.paths import expand_path
from .errors import KitupError, PresetError, ProjectError
from .registry import (
    MAIN_FILE,
    MAIN_STEM,
    Bundle,
    Module,
    discover_task_modules,
    forget_module_prefix,
    module_of,
    registered_modules,
)

BUNDLES_DIR: Final = "bundles"
FILES_DIR: Final = "files"
MODULES_DIR: Final = "modules"
VARS_DIR: Final = "vars"
PRESETS_DIR: Final = "presets"

_PROJECT_MODULES: Final = frozenset({"modules", "vars"})
_PROJECT_MODULE_PREFIXES: Final = (
    "modules.",
    "vars.",
    "_kitup_bundle_",
    "_kitup_task_",
    "_kitup_var_",
)

# A bundle folder may start with digits and an underscore, for example
# ``01_setup``. The prefix orders bundles and is stripped from the bundle id.
_BUNDLE_PREFIX: Final = re.compile(r"(?P<number>\d+)_(?P<id>.+)")


@dataclass(frozen=True, slots=True)
class Project:
    """A discovered project root and its bundles, variables, and presets.

    ``bundle_roots`` holds the resolved extra directories scanned through
    ``--bundle-path``. ``files_roots`` holds extra overlay directories from
    ``--files-path``. ``module_roots`` holds extra custom module directories
    from ``--modules-path``.
    """

    root: Path
    bundles: tuple[Bundle, ...] = ()
    var_paths: tuple[Path, ...] = ()
    preset_paths: tuple[Path, ...] = ()
    bundle_roots: tuple[Path, ...] = ()
    module_paths: tuple[Path, ...] = ()
    files_roots: tuple[Path, ...] = ()
    module_roots: tuple[Path, ...] = ()


@contextmanager
def on_project_path(root: str | Path) -> Iterator[None]:
    """Temporarily make a project root importable as ``sys.path[0]``.

    Project bytecode caches are redirected (or disabled) for the duration, so
    importing project code never creates ``__pycache__`` folders in the project.
    """
    path = str(expand_path(root, error=ProjectError, resolve=True))
    old_path = list(sys.path)
    sys.path.insert(0, path)
    importlib.invalidate_caches()
    try:
        with project_bytecode_cache():
            yield
    finally:
        sys.path[:] = old_path
        importlib.invalidate_caches()


def load_project(
    root: str | Path | None = None,
    *,
    preset_paths: Sequence[Path] = (),
    bundle_paths: Sequence[str | Path] = (),
    files_paths: Sequence[str | Path] = (),
    modules_paths: Sequence[str | Path] = (),
    extra_var_paths: Sequence[str | Path] = (),
) -> Project:
    """Discover a project rooted at ``root`` (default: the current directory)."""
    project_root = _resolve_project_root(root)

    bundle_roots = _resolve_roots(project_root, bundle_paths)
    files_roots = _resolve_roots(project_root, files_paths)
    module_roots = _resolve_roots(project_root, modules_paths)
    _require_directories(files_roots, label="files")

    resolved_var_paths = tuple(_resolve_against(project_root, path, error=ProjectError) for path in extra_var_paths)
    for path in resolved_var_paths:
        if not path.is_file():
            raise ProjectError(f"var file not found: {path}")
        if path.suffix != ".py":
            raise ProjectError(f"var file must end in .py: {path}")

    bundle_dirs = discover_bundle_dirs(project_root, extra_roots=bundle_roots)
    if bundle_dirs:
        _reject_shadowing_file(project_root, BUNDLES_DIR, label="bundles")
    _reject_shadowing_file(project_root, MODULES_DIR, label="modules")
    _reject_duplicate_bundle_ids(bundle_dirs)
    var_paths = (*discover_var_paths(project_root), *resolved_var_paths)
    _reject_duplicate_ids(var_paths, label="var")
    discovered_module_paths = _discover_project_modules(project_root, module_roots)
    with on_project_path(project_root):
        if module_roots:
            _install_modules_package(project_root, module_roots)
        bundles = _load_bundles(bundle_dirs)
    return Project(
        root=project_root,
        bundles=bundles,
        var_paths=var_paths,
        preset_paths=tuple(_resolve_against(project_root, path, error=ProjectError) for path in preset_paths),
        bundle_roots=bundle_roots,
        files_roots=files_roots,
        module_roots=module_roots,
        module_paths=discovered_module_paths,
    )


def load_project_modules(
    root: str | Path | None = None,
    *,
    modules_paths: Sequence[str | Path] = (),
) -> Project:
    """Discover a project's root and custom module paths without loading bundles."""
    project_root = _resolve_project_root(root)
    module_roots = _resolve_roots(project_root, modules_paths)
    _reject_shadowing_file(project_root, MODULES_DIR, label="modules")
    discovered_module_paths = _discover_project_modules(project_root, module_roots)
    return Project(root=project_root, module_roots=module_roots, module_paths=discovered_module_paths)


def discover_custom_modules(project: Project) -> tuple[Module, ...]:
    """Import the project's custom modules and return their registrations.

    A module that does not call ``module(...)`` raises ``ProjectError``.
    """
    with on_project_path(project.root):
        if project.module_roots:
            _install_modules_package(project.root, project.module_roots)
        for path in project.module_paths:
            name = path.stem
            package = f"{MODULES_DIR}.{name}"
            try:
                loaded = importlib.import_module(package)
            except KitupError:
                raise
            except Exception as exc:
                raise ProjectError(f"failed to import module {name!r}: {exc}") from exc

            if module_of(loaded) is None:
                location = path.name if path.is_file() else f"{path.name}/__init__.py"
                raise ProjectError(f"module {name!r} must call module(...) in {location}")
    return registered_modules(MODULES_DIR)


def discover_bundle_dirs(root: str | Path, *, extra_roots: Sequence[Path] = ()) -> tuple[Path, ...]:
    """Return bundle folders below ``<root>/bundles`` and extra roots in order."""
    bundle_dirs: list[Path] = []
    local_root = Path(root) / BUNDLES_DIR
    if local_root.is_dir():
        bundle_dirs.extend(_bundle_folders(local_root))
    roots = _unique_roots(extra_roots)
    _require_directories(roots, label="bundle")
    for directory in roots:
        bundle_dirs.extend(_bundle_folders(directory))
    return tuple(bundle_dirs)


def discover_var_paths(root: str | Path) -> tuple[Path, ...]:
    """Return direct ``*.py`` children of ``vars/`` in lexicographic order."""
    vars_dir = Path(root) / VARS_DIR
    if not vars_dir.is_dir():
        return ()
    entries = _read_entries(vars_dir, label="var")

    paths: list[Path] = []
    for path in sorted(entries, key=lambda item: item.name):
        if not path.is_file() or path.suffix != ".py" or _ignored(path.name):
            continue
        _require_identifier(path.stem, "var")
        paths.append(path)
    return tuple(paths)


def discover_module_paths(root: str | Path, *, extra_roots: Sequence[Path] = ()) -> tuple[Path, ...]:
    """Return public custom module files and packages below ``<root>/modules`` and extra roots.

    Entries whose name starts with ``_`` or ``.`` are private and skipped. A
    public folder must be a package with an ``__init__.py`` that registers the
    module; a public file must be a ``.py`` file.
    """
    paths = _module_entries(Path(root) / MODULES_DIR)
    roots = _unique_roots(extra_roots)
    _require_directories(roots, label="modules")
    for directory in roots:
        paths.extend(_module_entries(directory))
    return tuple(paths)


def _module_entries(modules_dir: Path) -> list[Path]:
    """Return public custom module entries directly below ``modules_dir``."""
    if not modules_dir.is_dir():
        return []
    entries = _read_entries(modules_dir, label="module")

    paths: list[Path] = []
    for path in sorted(entries, key=lambda item: item.name):
        if _ignored(path.name):
            continue
        if path.is_file():
            if path.suffix != ".py":
                continue
            _require_identifier(path.stem, "module")
            paths.append(path)
        elif path.is_dir():
            _require_identifier(path.name, "module")
            if not (path / "__init__.py").is_file():
                raise ProjectError(
                    f"module package {path.name!r} is missing __init__.py; "
                    f'add one that calls module("{path.name}") or prefix the folder with "_"'
                )
            paths.append(path)
    return paths


def resolve_preset(root: str | Path, value: str | Path) -> Path:
    """Resolve a preset path, rooting relative names under ``presets/``."""
    path = expand_path(value, error=PresetError)
    if path.is_absolute():
        return path
    if ".." in path.parts:
        raise PresetError(f"preset path must stay inside {PRESETS_DIR}/: {value}")
    return Path(root) / PRESETS_DIR / path


def _resolve_project_root(root: str | Path | None) -> Path:
    project_root = Path.cwd() if root is None else Path(root)
    project_root = expand_path(project_root, error=ProjectError, resolve=True)
    if not project_root.exists():
        raise ProjectError(f"project not found: {project_root} (run 'kitup init {project_root}' to create one)")
    if not project_root.is_dir():
        raise ProjectError(f"project root is not a directory: {project_root}")
    return project_root


def _reject_shadowing_file(root: Path, directory: str, *, label: str) -> None:
    """Reject a ``<name>.py`` file that shadows the ``<name>/`` directory."""
    if (root / directory).is_dir() and (root / f"{directory}.py").is_file():
        raise ProjectError(
            f"cannot load {label}: {root / f'{directory}.py'} shadows the {directory}/ directory; remove or rename it"
        )


def _resolve_against(root: Path, value: str | Path, *, error: type[KitupError]) -> Path:
    path = expand_path(value, error=error)
    return path if path.is_absolute() else root / path


def _resolve_roots(root: Path, paths: Sequence[str | Path]) -> tuple[Path, ...]:
    """Resolve extra roots against ``root`` and drop duplicates."""
    return _unique_roots(_resolve_against(root, path, error=ProjectError) for path in paths)


def _require_directories(roots: Sequence[Path], *, label: str) -> None:
    """Reject extra roots that do not exist or are not directories."""
    for directory in roots:
        if not directory.is_dir():
            raise ProjectError(f"{label} path is not a directory: {directory}")


def _unique_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    """Return extra roots in order without repeating identical paths."""
    return tuple(dict.fromkeys(Path(root) for root in roots))


def _read_entries(directory: Path, *, label: str) -> list[Path]:
    """Return a directory's entries, wrapping read errors as ``ProjectError``."""
    try:
        return list(directory.iterdir())
    except OSError as exc:
        raise ProjectError(f"cannot read {label} directory {directory}: {exc}") from exc


def _bundle_folders(directory: Path) -> list[Path]:
    entries = _read_entries(directory, label="bundle")
    return sorted(
        (path for path in entries if path.is_dir() and not _ignored(path.name)),
        key=lambda path: (
            (prefix := _parse_bundle_folder(path.name)[0]) is None,
            prefix or 0,
            path.name,
        ),
    )


def _parse_bundle_folder(folder_name: str) -> tuple[int | None, str]:
    """Return the optional numeric prefix and bundle id for a folder name."""
    match = _BUNDLE_PREFIX.fullmatch(folder_name)
    if match is None:
        return None, folder_name
    return int(match.group("number")), match.group("id")


def _require_bundle_id(folder_name: str) -> str:
    """Validate a bundle folder name and return its bundle id."""
    _, bundle_id = _parse_bundle_folder(folder_name)
    if not bundle_id.isidentifier() or keyword.iskeyword(bundle_id):
        raise ProjectError(
            f"invalid bundle name {folder_name!r}: must be a valid, non-keyword Python identifier, "
            "optionally preceded by a numeric prefix such as '01_'"
        )
    return bundle_id


def _reject_duplicate_bundle_ids(bundle_dirs: Sequence[Path]) -> None:
    seen: dict[str, Path] = {}
    for folder in bundle_dirs:
        _, bundle_id = _parse_bundle_folder(folder.name)
        existing = seen.get(bundle_id)
        if existing is not None:
            raise ProjectError(f"duplicate bundle id {bundle_id!r}: {existing} and {folder}")
        seen[bundle_id] = folder


def _reject_duplicate_ids(paths: Sequence[Path], *, label: str) -> None:
    seen: dict[str, Path] = {}
    for path in paths:
        existing = seen.get(path.stem)
        if existing is not None:
            raise ProjectError(f"duplicate {label} id {path.stem!r}: {existing} and {path}")
        seen[path.stem] = path


def _discover_project_modules(project_root: Path, module_roots: Sequence[Path]) -> tuple[Path, ...]:
    """Discover custom modules, reject duplicate ids, and reset cached imports."""
    paths = discover_module_paths(project_root, extra_roots=module_roots)
    _reject_duplicate_ids(paths, label="module")
    _purge_project_modules()
    return paths


def _load_bundles(bundle_dirs: Sequence[Path]) -> tuple[Bundle, ...]:
    """Import and finalize bundles, returning them in run order."""
    loaded: list[tuple[int | None, Bundle]] = []
    for folder in bundle_dirs:
        bundle_id = _require_bundle_id(folder.name)
        package = f"_kitup_bundle_{folder.name}"
        _synthetic_package(package, (folder,))
        try:
            module = _import_bundle_main(folder, package=package)
        except KitupError:
            raise
        except Exception as exc:
            raise ProjectError(f"failed to import bundle {folder.name!r}: {exc}") from exc

        bundle = _bundle_from_main(module, folder, package=package, bundle_id=bundle_id)
        var_paths = _discover_bundle_var_paths(bundle, folder, bundle_id=bundle_id)
        bundle._finalize(
            id=bundle_id,
            name=bundle.name or bundle_id,
            var_paths=var_paths,
        )
        loaded.append((_parse_bundle_folder(folder.name)[0], bundle))
    # Negating the order sorts higher values first; the negative-infinity
    # default then places bundles without an order after every explicit one.
    loaded.sort(key=lambda entry: (entry[0] is None, entry[0] or 0, -entry[1].order))
    return tuple(bundle for _, bundle in loaded)


def _import_bundle_main(folder: Path, *, package: str) -> ModuleType | None:
    """Import a bundle's optional ``main.py`` entry point."""
    if not (folder / MAIN_FILE).is_file():
        return None
    return importlib.import_module(f"{package}.{MAIN_STEM}")


def _bundle_from_main(module: ModuleType | None, folder: Path, *, package: str, bundle_id: str) -> Bundle:
    """Return the bundle declared in ``main.py`` or synthesize one from task modules."""
    bundle = getattr(module, "__kitup_bundle__", None) if module is not None else None
    if bundle is not None:
        if isinstance(bundle, Bundle):
            return bundle
        raise ProjectError(f"bundle {bundle_id!r} has a non-Bundle __kitup_bundle__")
    return Bundle(path=folder, modules=discover_task_modules(package, folder))


def _discover_bundle_var_paths(bundle: Bundle, folder: Path, *, bundle_id: str) -> tuple[Path, ...]:
    """Discover a bundle's optional ``vars/`` files."""
    root = bundle.path if bundle.path is not None else folder
    try:
        return discover_var_paths(root)
    except ProjectError as exc:
        raise ProjectError(f"bundle {bundle_id!r}: {exc}") from exc


def _synthetic_package(name: str, roots: Sequence[Path]) -> ModuleType:
    """Register a synthetic package whose import search path is ``roots``."""
    package = ModuleType(name)
    package.__path__ = [str(root) for root in roots]
    package.__package__ = name
    package.__file__ = None
    sys.modules[name] = package
    return package


def _install_modules_package(root: Path, extra_roots: Sequence[Path]) -> None:
    """Make the project's and extra module directories importable as ``modules``."""
    roots = [path for path in (root / MODULES_DIR, *extra_roots) if path.is_dir()]
    if not roots:
        return

    existing = sys.modules.get(MODULES_DIR)
    if existing is not None and hasattr(existing, "__path__"):
        _extend_package_path(existing, roots)
        return

    init = root / MODULES_DIR / "__init__.py"
    if init.is_file():
        try:
            package = load_package_module(init, MODULES_DIR)
        except KitupError:
            raise
        except Exception as exc:
            raise ProjectError(f"failed to import modules package {init}: {exc}") from exc
    else:
        package = _synthetic_package(MODULES_DIR, roots)
    _extend_package_path(package, roots)


def _extend_package_path(package: ModuleType, roots: Sequence[Path]) -> None:
    """Append missing directories to a package's import search path."""
    path = list(getattr(package, "__path__", ()))
    for root in roots:
        value = str(root)
        if value not in path:
            path.append(value)
    package.__path__ = path


def _ignored(name: str) -> bool:
    return name.startswith(("_", "."))


def _purge_project_modules() -> None:
    """Drop cached modules that could come from another project root."""
    for name in tuple(sys.modules):
        if name in _PROJECT_MODULES or name.startswith(_PROJECT_MODULE_PREFIXES):
            sys.modules.pop(name, None)
    forget_module_prefix(MODULES_DIR)


def _require_identifier(value: str, label: str) -> None:
    if not value.isidentifier() or keyword.iskeyword(value):
        raise ProjectError(f"invalid {label} name {value!r}: must be a valid, non-keyword Python identifier")
