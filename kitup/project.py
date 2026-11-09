"""Project layout discovery for bundles, variables, and preset files."""

import importlib
import keyword
import sys
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final

from ._utils.importer import load_package_module
from ._utils.paths import expand_path
from .errors import KitupError, PresetError, ProjectError
from .registry import Bundle, discover_task_modules

BUNDLES_DIR: Final = "bundles"
FILES_DIR: Final = "files"
VARS_DIR: Final = "vars"
PRESETS_DIR: Final = "presets"

_PROJECT_MODULES: Final = frozenset({"bundles", "vars"})
_PROJECT_MODULE_PREFIXES: Final = (
    "bundles.",
    "vars.",
    "_kitup_bundle_",
    "_kitup_task_",
    "_kitup_var_",
)


@dataclass(frozen=True, slots=True)
class Project:
    """A discovered project root and its bundles, variables, and presets.

    ``bundle_roots`` holds the resolved extra directories scanned through
    ``--bundle-path``.
    """

    root: Path
    bundles: tuple[Bundle, ...] = ()
    var_paths: tuple[Path, ...] = ()
    preset_paths: tuple[Path, ...] = ()
    bundle_roots: tuple[Path, ...] = ()


@contextmanager
def on_project_path(root: str | Path) -> Iterator[None]:
    """Temporarily make a project root importable as ``sys.path[0]``."""
    path = str(expand_path(root, error=ProjectError, resolve=True))
    old_path = list(sys.path)
    sys.path.insert(0, path)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path[:] = old_path
        importlib.invalidate_caches()


def load_project(
    root: str | Path | None = None,
    *,
    preset_paths: Sequence[Path] = (),
    bundle_paths: Sequence[str | Path] = (),
    extra_var_paths: Sequence[str | Path] = (),
) -> Project:
    """Discover a project rooted at ``root`` (default: the current directory)."""
    project_root = Path.cwd() if root is None else Path(root)
    project_root = expand_path(project_root, error=ProjectError, resolve=True)
    if not project_root.exists():
        raise ProjectError(f"project not found: {project_root} (run 'kitup init {project_root}' to create one)")
    if not project_root.is_dir():
        raise ProjectError(f"project root is not a directory: {project_root}")

    bundle_roots = _unique_roots(_resolve_project_path(project_root, path) for path in bundle_paths)
    resolved_var_paths = tuple(_resolve_project_path(project_root, path) for path in extra_var_paths)
    for path in resolved_var_paths:
        if not path.is_file():
            raise ProjectError(f"var file not found: {path}")
        if path.suffix != ".py":
            raise ProjectError(f"var file must end in .py: {path}")

    local_root = project_root / BUNDLES_DIR
    bundle_dirs = discover_bundle_dirs(project_root, extra_roots=bundle_roots)
    if bundle_dirs and (project_root / f"{BUNDLES_DIR}.py").is_file():
        raise ProjectError(
            f"cannot load bundles: {project_root / f'{BUNDLES_DIR}.py'} shadows the "
            f"{BUNDLES_DIR}/ directory; remove or rename it"
        )
    _reject_duplicate_bundle_ids(bundle_dirs)
    var_paths = (*discover_var_paths(project_root), *resolved_var_paths)
    _reject_duplicate_var_ids(var_paths)
    _purge_project_modules()
    with on_project_path(project_root):
        bundles = _load_bundles(bundle_dirs, local_root=local_root)
    return Project(
        root=project_root,
        bundles=bundles,
        var_paths=var_paths,
        preset_paths=tuple(_resolve_project_path(project_root, path) for path in preset_paths),
        bundle_roots=bundle_roots,
    )


def discover_bundle_dirs(
    root: str | Path,
    *,
    extra_roots: Sequence[Path] = (),
) -> tuple[Path, ...]:
    """Return bundle folders below ``<root>/bundles`` and extra roots in order."""
    bundle_dirs: list[Path] = []
    local_root = Path(root) / BUNDLES_DIR
    if local_root.is_dir():
        bundle_dirs.extend(_bundle_folders(local_root))
    for directory in _unique_roots(extra_roots):
        if not directory.is_dir():
            raise ProjectError(f"bundle path is not a directory: {directory}")
        bundle_dirs.extend(_bundle_folders(directory))
    return tuple(bundle_dirs)


def discover_var_paths(root: str | Path) -> tuple[Path, ...]:
    """Return direct ``*.py`` children of ``vars/`` in lexicographic order."""
    vars_dir = Path(root) / VARS_DIR
    if not vars_dir.is_dir():
        return ()
    try:
        entries = list(vars_dir.iterdir())
    except OSError as exc:
        raise ProjectError(f"cannot read var directory {vars_dir}: {exc}") from exc

    paths: list[Path] = []
    for path in sorted(entries, key=lambda item: item.name):
        if not path.is_file() or path.suffix != ".py" or _ignored(path.name):
            continue
        _require_identifier(path.stem, "var")
        paths.append(path)
    return tuple(paths)


def resolve_preset(root: str | Path, value: str | Path) -> Path:
    """Resolve a preset path, rooting relative names under ``presets/``."""
    path = expand_path(value, error=PresetError)
    if path.is_absolute():
        return path
    if ".." in path.parts:
        raise PresetError(f"preset path must stay inside {PRESETS_DIR}/: {value}")
    return Path(root) / PRESETS_DIR / path


def _resolve_project_path(root: Path, value: str | Path) -> Path:
    return _resolve_against(root, value, error=ProjectError)


def _resolve_against(root: Path, value: str | Path, *, error: type[KitupError]) -> Path:
    path = expand_path(value, error=error)
    return path if path.is_absolute() else root / path


def _unique_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    """Return extra roots in order without repeating identical paths."""
    return tuple(dict.fromkeys(Path(root) for root in roots))


def _bundle_folders(directory: Path) -> list[Path]:
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise ProjectError(f"cannot read bundle directory {directory}: {exc}") from exc
    return sorted(
        (path for path in entries if path.is_dir() and not _ignored(path.name)),
        key=lambda path: path.name,
    )


def _reject_duplicate_bundle_ids(bundle_dirs: Sequence[Path]) -> None:
    seen: dict[str, Path] = {}
    for folder in bundle_dirs:
        existing = seen.get(folder.name)
        if existing is not None:
            raise ProjectError(f"duplicate bundle id {folder.name!r}: {existing} and {folder}")
        seen[folder.name] = folder


def _reject_duplicate_var_ids(var_paths: Sequence[Path]) -> None:
    seen: dict[str, Path] = {}
    for path in var_paths:
        existing = seen.get(path.stem)
        if existing is not None:
            raise ProjectError(f"duplicate var id {path.stem!r}: {existing} and {path}")
        seen[path.stem] = path


def _load_bundles(bundle_dirs: Sequence[Path], *, local_root: Path) -> tuple[Bundle, ...]:
    bundles: list[Bundle] = []
    for folder in bundle_dirs:
        _require_identifier(folder.name, "bundle")
        is_local = folder.parent == local_root
        package = f"{BUNDLES_DIR}.{folder.name}" if is_local else f"_kitup_bundle_{folder.name}"
        try:
            module = importlib.import_module(package) if is_local else _load_external_bundle(folder, package)
        except KitupError:
            raise
        except Exception as exc:
            raise ProjectError(f"failed to import bundle {folder.name!r}: {exc}") from exc

        bundle = getattr(module, "__kitup_bundle__", None)
        if bundle is None:
            modules = discover_task_modules(package, folder)
            bundle = Bundle(path=folder, modules=modules)
        elif not isinstance(bundle, Bundle):
            raise ProjectError(f"bundle {folder.name!r} has a non-Bundle __kitup_bundle__")

        var_paths = _discover_bundle_var_paths(bundle, folder, bundle_id=folder.name)
        bundle._finalize(
            id=folder.name,
            name=bundle.name or folder.name,
            var_paths=var_paths,
        )
        bundles.append(bundle)
    return tuple(bundles)


def _discover_bundle_var_paths(bundle: Bundle, folder: Path, *, bundle_id: str) -> tuple[Path, ...]:
    """Discover a bundle's optional ``vars/`` files."""
    root = bundle.path if bundle.path is not None else folder
    try:
        return discover_var_paths(root)
    except ProjectError as exc:
        raise ProjectError(f"bundle {bundle_id!r}: {exc}") from exc


def _load_external_bundle(folder: Path, package_name: str) -> ModuleType:
    """Load a bundle outside ``bundles/`` under a synthetic package name."""
    init = folder / "__init__.py"
    if init.is_file():
        return load_package_module(init, package_name)

    module = ModuleType(package_name)
    module.__path__ = [str(folder)]
    module.__package__ = package_name
    module.__file__ = None
    sys.modules[package_name] = module
    return module


def _ignored(name: str) -> bool:
    return name.startswith(("_", "."))


def _purge_project_modules() -> None:
    """Drop cached modules that could come from another project root."""
    for name in tuple(sys.modules):
        if name in _PROJECT_MODULES or name.startswith(_PROJECT_MODULE_PREFIXES):
            sys.modules.pop(name, None)


def _require_identifier(value: str, label: str) -> None:
    if not value.isidentifier() or keyword.iskeyword(value):
        raise ProjectError(f"invalid {label} name {value!r}: must be a valid, non-keyword Python identifier")
