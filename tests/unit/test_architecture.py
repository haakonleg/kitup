"""Architecture rules for built-in modules."""

from __future__ import annotations

import ast
from pathlib import Path

MODULES_DIR = Path(__file__).parents[2] / "kitup" / "modules"


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
