"""Architecture rules for built-in modules and embedded worker sources."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).parents[2] / "kitup"
MODULES_DIR = PACKAGE_DIR / "modules"
FILE_HELPER = PACKAGE_DIR / "_utils" / "file_helper.py"
PROTOCOL = PACKAGE_DIR / "_privileged" / "protocol.py"
WORKER = PACKAGE_DIR / "_privileged" / "worker.py"
CLIENT = PACKAGE_DIR / "_privileged" / "client.py"

EMBEDDED_SOURCES = (PROTOCOL, FILE_HELPER, WORKER)


def _sibling_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("kitup.modules."):
                    imported.add(alias.name.removeprefix("kitup.modules."))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                if node.module:
                    imported.add(node.module)
                else:
                    imported.update(alias.name for alias in node.names)
            elif node.level == 2 and node.module == "modules":
                imported.update(alias.name for alias in node.names)
            elif node.level == 2 and node.module and node.module.startswith("modules."):
                imported.add(node.module.removeprefix("modules."))
            elif node.level == 0 and node.module == "kitup.modules":
                imported.update(alias.name for alias in node.names)
            elif node.level == 0 and node.module and node.module.startswith("kitup.modules."):
                imported.add(node.module.removeprefix("kitup.modules."))

    return imported


def test_modules_do_not_import_each_other():
    module_names = {path.stem for path in MODULES_DIR.glob("*.py") if path.stem != "__init__"}

    violations = []
    for path in sorted(MODULES_DIR.glob("*.py")):
        for imported in _sibling_imports(path):
            if imported in module_names - {path.stem}:
                violations.append(f"{path.name} imports {imported}")

    assert not violations, "modules must remain standalone: " + ", ".join(violations)


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
            self.violations.append(
                f"{self.path.name}: relative import: {node.module or '<unknown>'}"
            )
            return
        if node.module:
            self._check(node.module)

    def _check(self, name: str) -> None:
        top = name.split(".")[0]
        if name == "kitup" or name.startswith("kitup."):
            self.violations.append(f"{self.path.name}: imports {name}")
        elif top not in sys.stdlib_module_names:
            self.violations.append(f"{self.path.name}: non-stdlib import {name}")


def test_embedded_sources_are_standalone():
    """Embedded sources run via ``python -c`` and cannot import kitup."""
    violations: list[str] = []
    for path in EMBEDDED_SOURCES:
        visitor = _EmbeddedVisitor(path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        violations.extend(visitor.violations)

    assert not violations, "embedded sources must use only the standard library: " + ", ".join(
        violations
    )


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
    assert not offenders, "embedded sources must not run on import: " + ", ".join(
        offenders
    )


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
        ):
            imported.add(node.module.split(".")[0])
    return imported


def test_client_and_worker_do_not_import_json():
    """Only protocol.py may encode or decode the wire format."""
    for path in (CLIENT, WORKER):
        assert "json" not in _imported_modules(path), f"{path.name} must use protocol.py"
