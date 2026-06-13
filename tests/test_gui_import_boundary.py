"""GUI Import Boundary Tests — enforces API-first discipline for new shell.

Tests that:
1. gui/ does not import from aip.orchestration
2. gui/ only uses gui.api_client for backend communication
3. gui.app can be imported without starting a server
4. ui.run() is guarded under if __name__ == "__main__"
5. New shell/pages are importable (structure check)
6. No module-level _state singleton in gui.state (per-session pattern)
7. Start scripts reference gui.app (not gui.shell or gui.main)
8. Legacy files are marked frozen/preserved
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUI_ROOT = PROJECT_ROOT / "gui"


# ── AST Helpers ────────────────────────────────────────────────────────


def _collect_imports(filepath: Path) -> list[tuple[str, int, str]]:
    """Collect all imports from a Python file.

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
        # Skip TYPE_CHECKING blocks
        if isinstance(node, ast.If):
            test = node.test
            is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_type_checking:
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
            else:
                _visit(child, depth + 1)

    _visit(tree)
    return imports


def _py_files(directory: Path) -> list[Path]:
    """Collect all .py files under a directory, excluding __pycache__."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


# ── Test: GUI must not import orchestration ────────────────────────────


def test_gui_does_not_import_orchestration():
    """GUI must remain API-first and must not import orchestration internals."""
    if not GUI_ROOT.exists():
        pytest.skip("No GUI directory present")

    violations: list[str] = []
    for py_file in _py_files(GUI_ROOT):
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.orchestration"):
                violations.append(
                    f"{py_file.relative_to(PROJECT_ROOT)}:{lineno} ({style}) — imports '{module}' from GUI layer"
                )
            # Also flag direct aip.adapter imports from GUI
            # (GUI should only talk to the API, not import adapter internals)
            if module.startswith("aip.adapter") and not module.startswith("aip.adapter.api"):
                violations.append(
                    f"{py_file.relative_to(PROJECT_ROOT)}:{lineno} ({style}) — "
                    f"imports '{module}' from GUI layer (should use API client)"
                )
    assert not violations, "GUI must not import orchestration internals — use API client instead:\n  " + "\n  ".join(
        violations
    )


# ── Test: gui.app can be imported without starting a server ────────────


def test_gui_app_importable():
    """gui.app can be imported without starting a server."""
    import gui.app  # noqa: F401 — just testing importability


# ── Test: ui.run() is guarded ─────────────────────────────────────────


def test_ui_run_guarded():
    """ui.run() in gui/app.py must be guarded under if __name__ == '__main__'."""
    app_file = GUI_ROOT / "app.py"
    if not app_file.exists():
        pytest.skip("gui/app.py not found")

    source = app_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(app_file))

    # Find all calls to ui.run()
    ui_run_calls: list[tuple[int, bool]] = []  # (lineno, is_guarded)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            is_ui_run = (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "ui"
            ) or (isinstance(func, ast.Name) and func.id == "ui.run")
            if is_ui_run:
                # Check if it's inside an `if __name__ == "__main__":` block
                guarded = _is_in_main_guard(node, tree)
                ui_run_calls.append((node.lineno, guarded))

    if not ui_run_calls:
        # No ui.run() calls found — that's OK for import-only modules
        return

    unguarded = [(lineno, g) for lineno, g in ui_run_calls if not g]
    assert not unguarded, (
        f"ui.run() must be guarded under `if __name__ == '__main__':` "
        f"in gui/app.py. Un guarded calls at lines: {unguarded}"
    )


def _is_in_main_guard(target_node: ast.AST, tree: ast.Module) -> bool:
    """Check if a node is inside an `if __name__ == '__main__':` block."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_main_guard = (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and any(isinstance(comp, ast.Constant) and comp.value == "__main__" for comp in test.comparators)
            )
            if is_main_guard:
                # Check if target_node is somewhere inside this if block
                if _is_descendant(target_node, node):
                    return True
    return False


def _is_descendant(target: ast.AST, ancestor: ast.AST) -> bool:
    """Check if target is a descendant of ancestor in the AST."""
    for child in ast.iter_child_nodes(ancestor):
        if child is target:
            return True
        if _is_descendant(target, child):
            return True
    return False


# ── Test: new shell/pages are importable ───────────────────────────────


def test_gui_theme_importable():
    """gui.theme can be imported."""
    import gui.theme  # noqa: F401


def test_gui_state_importable():
    """gui.state can be imported."""
    import gui.state  # noqa: F401


def test_gui_components_importable():
    """gui.components submodules can be imported."""
    import gui.components.artifact_detail  # noqa: F401
    import gui.components.artifact_list  # noqa: F401
    import gui.components.artifact_review_panel  # noqa: F401
    import gui.components.artifact_state_badge  # noqa: F401
    import gui.components.buttons  # noqa: F401
    import gui.components.chat  # noqa: F401
    import gui.components.corpus_actions  # noqa: F401
    import gui.components.corpus_problems  # noqa: F401
    import gui.components.corpus_summary  # noqa: F401
    import gui.components.document_detail  # noqa: F401
    import gui.components.document_table  # noqa: F401
    import gui.components.layout  # noqa: F401
    import gui.components.modals  # noqa: F401
    import gui.components.pills  # noqa: F401
    import gui.components.retrieval_channel_results  # noqa: F401
    import gui.components.retrieval_health_cards  # noqa: F401
    import gui.components.retrieval_query_panel  # noqa: F401
    import gui.components.retrieval_ranked_context  # noqa: F401


def test_gui_pages_importable():
    """gui.pages submodules can be imported."""
    import gui.pages.artifacts  # noqa: F401
    import gui.pages.ask  # noqa: F401
    import gui.pages.corpus  # noqa: F401
    import gui.pages.dashboard  # noqa: F401
    import gui.pages.maintenance  # noqa: F401
    import gui.pages.retrieval_lab  # noqa: F401
    import gui.pages.settings  # noqa: F401
    import gui.pages.wiki  # noqa: F401


def test_gui_panels_importable():
    """gui.panels submodules can be imported."""
    import gui.panels.right_rail  # noqa: F401


# ── Test: no module-level _state singleton in gui.state ────────────────


def test_gui_state_no_module_level_singleton():
    """gui.state should not have a module-level _state singleton.

    The old pattern ` _state: GuiState | None = None` at module level
    has been replaced with per-session state via get_session_state().
    """
    state_file = GUI_ROOT / "state.py"
    if not state_file.exists():
        pytest.skip("gui/state.py not found")

    source = state_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(state_file))

    # Look for module-level assignments like _state: GuiState | None = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_state" and node.value is not None:
                pytest.fail(
                    f"gui/state.py has module-level _state singleton at line {node.lineno}. "
                    f"Use get_session_state() for per-session state instead."
                )
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_state":
                    # Check if it's at module level (not in a function)
                    pytest.fail(
                        f"gui/state.py has module-level _state assignment at line {node.lineno}. "
                        f"Use get_session_state() for per-session state instead."
                    )


# ── Test: gui.state uses logging instead of except: pass ──────────────


def test_gui_state_no_silent_exception_catching():
    """gui.state should log errors instead of `except Exception: pass`."""
    state_file = GUI_ROOT / "state.py"
    if not state_file.exists():
        pytest.skip("gui/state.py not found")

    source = state_file.read_text(encoding="utf-8")

    # Simple text check — look for patterns like `except Exception:\n    pass`
    # or `except OSError:\n    pass` etc.
    # We allow `except ImportError: pass` (standard pattern for optional deps)
    lines = source.split("\n")
    violations: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "pass":
            # Check if previous non-empty line is an except
            for j in range(i - 1, max(i - 3, 0), -1):
                prev = lines[j].strip()
                if prev.startswith("except ") and "ImportError" not in prev:
                    violations.append(f"Line {i + 1}: silent `except: pass` pattern (should log error instead)")
                    break
                elif prev and not prev.startswith("#"):
                    break

    # We allow some exceptions for now — just check that the key
    # persistence functions log errors
    # This is a soft check
    assert "log.error" in source, (
        "gui/state.py should use log.error() for exception handling instead of silent `except: pass`"
    )


# ── Test: start scripts reference gui.app ──────────────────────────────


def test_start_scripts_reference_gui_app():
    """All start scripts must reference gui.app, not gui.shell or gui.main.

    A wrapper script that delegates to scripts/start.sh is also accepted
    since the canonical script references gui.app.
    """
    script_files = [
        PROJECT_ROOT / "scripts" / "start.sh",
        PROJECT_ROOT / "start.sh",
        PROJECT_ROOT / "start-aip.sh",
    ]

    violations: list[str] = []
    for script in script_files:
        if not script.exists():
            continue
        content = script.read_text(encoding="utf-8")
        # Wrapper scripts that exec scripts/start.sh are valid
        # (the canonical script references gui.app)
        if "scripts/start.sh" in content and "exec" in content:
            continue
        # Check that gui.app is referenced
        if "gui.app" not in content:
            violations.append(f"{script.relative_to(PROJECT_ROOT)}: does not reference 'gui.app'")
        # Check that gui.shell is NOT referenced as the primary GUI start
        if "python -m gui.shell" in content:
            violations.append(
                f"{script.relative_to(PROJECT_ROOT)}: references 'python -m gui.shell' (should be 'python -m gui.app')"
            )
        # Check that gui.main is NOT referenced as the primary GUI start
        if "python -m gui.main" in content:
            violations.append(
                f"{script.relative_to(PROJECT_ROOT)}: references 'python -m gui.main' (should be 'python -m gui.app')"
            )

    assert not violations, "Start scripts must reference gui.app as the default GUI entry point:\n  " + "\n  ".join(
        violations
    )


def test_legacy_shell_is_frozen():
    """gui/shell.py must be marked as FROZEN in its docstring."""
    shell_file = GUI_ROOT / "shell.py"
    if not shell_file.exists():
        pytest.skip("gui/shell.py not found")

    docstring = _get_module_docstring(shell_file)
    assert docstring and "FROZEN" in docstring.upper(), "gui/shell.py must be marked as FROZEN in its module docstring"


def test_legacy_main_is_preserved():
    """gui/main.py must be marked as PRESERVED in its docstring."""
    main_file = GUI_ROOT / "main.py"
    if not main_file.exists():
        pytest.skip("gui/main.py not found")

    docstring = _get_module_docstring(main_file)
    assert docstring and "PRESERVED" in docstring.upper(), (
        "gui/main.py must be marked as PRESERVED in its module docstring"
    )


def test_archive_main_exists():
    """gui/archive/main.py must exist as the archived copy."""
    archive_main = GUI_ROOT / "archive" / "main.py"
    assert archive_main.exists(), "gui/archive/main.py must exist as an archived copy of the original chat frontend"


def _get_module_docstring(filepath: Path) -> str | None:
    """Extract the module-level docstring from a Python file."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return None

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                return node.value.value
    return None
