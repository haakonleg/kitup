"""Architecture rules for built-in modules and embedded worker sources."""

import ast
import sys
from pathlib import Path

from kitup import PACKAGE_NAME

PACKAGE_DIR = Path(__file__).parents[2] / PACKAGE_NAME
MODULES_DIR = PACKAGE_DIR / "modules"
DOCS_DIR = Path(__file__).parents[2] / "docs" / "modules"
FILE_PRIMITIVES = PACKAGE_DIR / "_utils" / "file_primitives.py"
PROTOCOL = PACKAGE_DIR / "_privileged" / "protocol.py"
WORKER = PACKAGE_DIR / "_privileged" / "worker.py"
CLIENT = PACKAGE_DIR / "_privileged" / "client.py"

EMBEDDED_SOURCES = (PROTOCOL, FILE_PRIMITIVES, WORKER)


def _sibling_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                prefix = f"{PACKAGE_NAME}.modules."
                if alias.name.startswith(prefix):
                    imported.add(alias.name.removeprefix(prefix).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                prefix = f"{PACKAGE_NAME}.modules"
                if node.module == prefix:
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif node.module and node.module.startswith(f"{prefix}."):
                    imported.add(node.module.removeprefix(f"{prefix}.").split(".")[0])
            elif node.level == 1:
                if node.module:
                    imported.add(node.module.split(".")[0])
                else:
                    imported.update(alias.name.split(".")[0] for alias in node.names)
            elif node.level == 2:
                if node.module == "modules":
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif node.module and node.module.startswith("modules."):
                    imported.add(node.module.removeprefix("modules.").split(".")[0])
                elif node.module:
                    imported.add(node.module.split(".")[0])
            elif node.level == 3:
                if node.module == "modules":
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif node.module and node.module.startswith("modules."):
                    imported.add(node.module.removeprefix("modules.").split(".")[0])

    return imported


def _module_names() -> set[str]:
    names = {path.stem for path in MODULES_DIR.glob("*.py") if path.stem != "__init__"}
    names.update(path.name for path in MODULES_DIR.iterdir() if path.is_dir() and (path / "__init__.py").is_file())
    return names


def _module_owner(path: Path) -> str:
    """Return the top-level module that owns a file under ``kitup.modules``."""
    first = path.relative_to(MODULES_DIR).parts[0]
    return Path(first).stem


def test_modules_do_not_import_each_other():
    module_names = _module_names()

    violations = [
        f"{path.relative_to(MODULES_DIR)} imports {imported}"
        for path in sorted(MODULES_DIR.rglob("*.py"))
        for imported in _sibling_imports(path)
        if imported in module_names - {_module_owner(path)}
    ]

    assert not violations, "modules must remain standalone: " + ", ".join(violations)


def test_modules_have_documentation():
    """Every built-in module has a matching page in ``docs/modules``."""
    module_names = _module_names()
    documented = {path.stem for path in DOCS_DIR.glob("*.md") if path.stem != "index"}

    missing = sorted(module_names - documented)
    orphaned = sorted(documented - module_names)

    assert not missing, "modules missing documentation: " + ", ".join(missing)
    assert not orphaned, "documentation without a module: " + ", ".join(orphaned)


class _EmbeddedVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []

    def visit_If(self, node: ast.If) -> None:
        # Type-checking-only imports are not executed in the embedded script.
        test = node.test
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level != 0:
            self.violations.append(f"{self.path.name}: relative import: {node.module or '<unknown>'}")
            return
        if node.module:
            self._check(node.module)

    def _check(self, name: str) -> None:
        top = name.split(".")[0]
        if name == PACKAGE_NAME or name.startswith(f"{PACKAGE_NAME}."):
            self.violations.append(f"{self.path.name}: imports {name}")
        elif top not in sys.stdlib_module_names:
            self.violations.append(f"{self.path.name}: non-stdlib import {name}")


def test_embedded_sources_are_standalone():
    """Embedded sources run via ``python -c`` and cannot import the package."""
    violations: list[str] = []
    for path in EMBEDDED_SOURCES:
        visitor = _EmbeddedVisitor(path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        violations.extend(visitor.violations)

    assert not violations, "embedded sources must use only the standard library: " + ", ".join(violations)


def _has_main_guard(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1:
            continue
        if not isinstance(test.ops[0], ast.Eq):
            continue
        left = test.left
        right = test.comparators[0]
        if (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        ):
            return True
    return False


def test_embedded_sources_have_no_main_guard():
    """``client.py`` calls ``main()`` explicitly after concatenating sources."""
    offenders = [path.name for path in EMBEDDED_SOURCES if _has_main_guard(path)]
    assert not offenders, "embedded sources must not run on import: " + ", ".join(offenders)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    return imported


def test_client_and_worker_do_not_import_json():
    """Only protocol.py may encode or decode the wire format."""
    for path in (CLIENT, WORKER):
        assert "json" not in _imported_modules(path), f"{path.name} must use protocol.py"
