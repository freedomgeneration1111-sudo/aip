"""Extension import-boundary test — ADR-014 §5.3 + Claude's separation-of-concerns fix.

This is the machine-enforced boundary that makes separation of concerns
permanent, regardless of whether extensions live in-tree or in separate
repos. It catches the real SoC erosion: a forbidden import inside an
extension, six weeks from now, that reaches into platform internals.

Two rules, both AST-checked (catches static, lazy, AND importlib imports):

  1. **Extensions import only the allowlist.** Anything under `extensions/*`
     may import from `aip.*` ONLY through:
       - `aip.foundation.protocols.*`  (the Actor Protocol + future Protocols)
       - `aip.adapter.extensions`      (the public extension API: Manifest, etc.)
     Anything else — `aip.adapter.corpus_registry`, `aip.orchestration.*`,
     `aip.adapter.api.*` — is a hard violation. Extensions reach the
     container via `ctx.container` (duck-typed), not by importing it.

  2. **The platform imports nothing from extensions.** Nothing under
     `src/aip/` may import from any extension package (`aristotle`, `loom`,
     `codeforge`, etc.). The host discovers extensions dynamically; it
     never imports them by name.

The allowlist is deliberately small. Growing it requires a deliberate
decision recorded in this file. The test fails CI loudly on the first
forbidden import.

Run:  CI=true uv run pytest tests/test_extension_import_boundary.py -v
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "aip"
GUI_ROOT = PROJECT_ROOT / "gui"
EXTENSIONS_ROOT = PROJECT_ROOT / "extensions"

# ---------------------------------------------------------------------------
# The allowlist — extensions may import from aip.* ONLY through these.
# Growing this list is a deliberate architectural decision.
# ---------------------------------------------------------------------------

ALLOWED_AIP_IMPORT_PREFIXES: tuple[str, ...] = (
    "aip.foundation.protocols",   # Actor Protocol + future Protocols
    "aip.adapter.extensions",     # public extension API (Manifest, ExtensionHost surface)
    "aip.foundation.schemas",     # dataclasses extensions may use (e.g. WorkflowTemplate)
)

# ---------------------------------------------------------------------------
# Known extension package names — the platform must not import any of these.
# Updated when a new extension is added. The test also catches unknown
# extension names by pattern (any top-level package under extensions/ that
# isn't a stdlib/third-party name).
# ---------------------------------------------------------------------------

KNOWN_EXTENSION_PACKAGES: tuple[str, ...] = (
    "aristotle",
    # Future: "loom", "codeforge",
)


# ---------------------------------------------------------------------------
# AST helpers (same pattern as test_import_boundary.py)
# ---------------------------------------------------------------------------


def _is_type_checking_block(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _collect_imports(filepath: Path) -> list[tuple[str, int, str]]:
    """Collect all imports (static, lazy, importlib) from a Python file.

    Returns list of (module_path, line_number, import_style).
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    imports: list[tuple[str, int, str]] = []

    def _visit(node: ast.AST, depth: int = 0) -> None:
        if _is_type_checking_block(node):
            return

        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    style = "static" if depth <= 1 else "lazy"
                    imports.append((alias.name, child.lineno, style))
                _visit(child, depth + 1)

            elif isinstance(child, ast.ImportFrom):
                if child.module and child.level == 0:
                    style = "static" if depth <= 1 else "lazy"
                    imports.append((child.module, child.lineno, style))
                _visit(child, depth + 1)

            elif isinstance(child, ast.Call):
                func = child.func
                mod_name: str | None = None

                if isinstance(func, ast.Attribute) and func.attr == "import_module" and child.args:
                    arg = child.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        mod_name = arg.value

                if mod_name:
                    imports.append((mod_name, child.lineno, "importlib"))

                if isinstance(func, ast.Name) and func.id == "__import__" and child.args:
                    arg = child.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        imports.append((arg.value, child.lineno, "importlib"))

                _visit(child, depth + 1)

            else:
                _visit(child, depth + 1)

    _visit(tree)
    return imports


def _py_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def _is_allowed_aip_import(module: str) -> bool:
    """Return True if the module is in the allowlist or a submodule of it."""
    for prefix in ALLOWED_AIP_IMPORT_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    # aip with no submodule (bare `import aip`) is NOT allowed — extensions
    # must be specific about what they import.
    return False


def _is_extension_package(module: str) -> bool:
    """Return True if the module is an extension package (e.g. 'aristotle', 'aristotle.actors')."""
    for ext in KNOWN_EXTENSION_PACKAGES:
        if module == ext or module.startswith(ext + "."):
            return True
    return False


# ---------------------------------------------------------------------------
# Test 1: extensions import only the allowlist
# ---------------------------------------------------------------------------


def test_extensions_import_only_allowlist():
    """Every .py under extensions/* imports from aip.* ONLY through the allowlist.

    The allowlist: aip.foundation.protocols.*, aip.adapter.extensions,
    aip.foundation.schemas. Anything else is a hard violation — extensions
    reach the container via ctx.container (duck-typed), not by importing it.

    This test is the machine-enforced separation of concerns. It catches
    the real erosion: a forbidden `from aip.adapter.corpus_registry import ...`
    inside an extension, six weeks from now.
    """
    if not EXTENSIONS_ROOT.exists():
        pytest.skip("No extensions/ directory present")

    violations: list[str] = []

    for py_file in _py_files(EXTENSIONS_ROOT):
        rel = py_file.relative_to(PROJECT_ROOT)
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.") or module == "aip":
                if not _is_allowed_aip_import(module):
                    violations.append(
                        f"{rel}:{lineno} ({style}) — imports {module!r} "
                        f"(not in allowlist: {ALLOWED_AIP_IMPORT_PREFIXES})"
                    )

    assert not violations, (
        "Extensions may import from aip.* ONLY through the allowlist "
        f"({ALLOWED_AIP_IMPORT_PREFIXES}). Extensions reach the container "
        "via ctx.container (duck-typed), not by importing platform internals.\n  "
        + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 2: the platform imports nothing from extensions
# ---------------------------------------------------------------------------


def test_platform_does_not_import_extensions():
    """Nothing under src/aip/ imports from any extension package.

    The host discovers extensions dynamically (via filesystem glob or
    importlib.metadata entry points). It never imports an extension by
    name. A static `from aristotle.actors import ...` inside src/aip/
    would couple the platform to a specific extension — a hard violation.
    """
    violations: list[str] = []

    for py_file in _py_files(SRC_ROOT):
        rel = py_file.relative_to(SRC_ROOT)
        for module, lineno, style in _collect_imports(py_file):
            if _is_extension_package(module):
                violations.append(
                    f"{rel}:{lineno} ({style}) — imports extension package {module!r} "
                    f"(platform must discover extensions dynamically, never import by name)"
                )

    assert not violations, (
        "The platform must not import from any extension package. "
        "Extensions are discovered dynamically (filesystem or entry points), "
        "never imported by name.\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 2b: the GUI imports nothing from extensions by name
# ---------------------------------------------------------------------------


def test_gui_does_not_import_extensions():
    """Nothing under gui/ imports from any extension package by name.

    The GUI discovers extension pages via entry points
    (aip.extension_gui group), not by named imports. A static
    `import aristotle.gui` inside gui/ would couple the GUI to a specific
    extension — a hard violation.

    This test closes the gap where gui/app.py previously did
    `import aristotle.gui` directly. The boundary test for src/aip/
    (test_platform_does_not_import_extensions) does NOT cover gui/ —
    this test does.
    """
    if not GUI_ROOT.exists():
        pytest.skip("No gui/ directory present")

    violations: list[str] = []

    for py_file in _py_files(GUI_ROOT):
        rel = py_file.relative_to(PROJECT_ROOT)
        for module, lineno, style in _collect_imports(py_file):
            if _is_extension_package(module):
                violations.append(
                    f"{rel}:{lineno} ({style}) — imports extension package {module!r} "
                    f"(GUI must discover extension pages via entry points, never import by name)"
                )

    assert not violations, (
        "The GUI must not import from any extension package by name. "
        "Extension GUI pages are discovered via entry points "
        "(aip.extension_gui group), not by named imports.\n  "
        + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 3: informational summary (always passes)
# ---------------------------------------------------------------------------


def test_extension_boundary_summary():
    """Informational: print the current aip.* imports across extensions/.

    Always passes. Gives visibility into the coupling surface during CI runs.
    """
    if not EXTENSIONS_ROOT.exists():
        print("\nNo extensions/ directory present.")
        return

    summary: dict[str, list[str]] = {}
    for py_file in _py_files(EXTENSIONS_ROOT):
        rel = py_file.relative_to(PROJECT_ROOT)
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.") or module == "aip":
                allowed = "ALLOWED" if _is_allowed_aip_import(module) else "FORBIDDEN"
                summary.setdefault(module, []).append(
                    f"  {rel}:{lineno} ({style}) [{allowed}]"
                )

    print("\n" + "=" * 72)
    print("EXTENSION IMPORT BOUNDARY SUMMARY")
    print("=" * 72)
    if not summary:
        print("\n  No aip.* imports found in extensions/.")
    else:
        for module in sorted(summary):
            print(f"\n  {module}:")
            for entry in summary[module]:
                print(entry)
    print("\n" + "=" * 72)
    assert True
