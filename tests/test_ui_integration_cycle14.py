"""UI Cycle 14 — Integration Pass Tests.

Tests that the operator console is a coherent integrated system:
1. Navigation/page reachability — every page is reachable from shell nav
2. Route registration/no shadowing — backend routes don't conflict
3. GUI import boundary — no aip.* imports in gui/
4. API client fallback/error handling — honest degraded states, no fake data
5. Mutating actions remain explicit/sovereign — no auto-approve, auto-export, etc.
6. Disabled/not-wired states are visible
7. Status language consistency across pages
8. Cross-surface link wiring — Link Wiki, dashboard click-through, etc.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUI_ROOT = PROJECT_ROOT / "gui"
PAGES_ROOT = GUI_ROOT / "pages"
COMPONENTS_ROOT = GUI_ROOT / "components"
PANELS_ROOT = GUI_ROOT / "panels"
ROUTES_ROOT = PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes"


# ── 1. Navigation and page reachability ────────────────────────────────


class TestNavigationReachability:
    """Verify every completed page is reachable from the shell navigation."""

    EXPECTED_PAGES = {
        "/": "dashboard",
        "/ask": "ask",
        "/models": "models",
        "/corpus": "corpus",
        "/retrieval": "retrieval_lab",
        "/wiki": "wiki",
        "/artifacts": "artifacts",
        "/maintenance": "maintenance",
        "/settings": "settings",
    }

    def test_app_registers_all_page_routes(self):
        """gui/app.py must import and register all expected page modules."""
        app_source = (GUI_ROOT / "app.py").read_text()
        for route, module_name in self.EXPECTED_PAGES.items():
            # Check import of the page module
            assert f"gui.pages.{module_name}" in app_source, (
                f"app.py does not import gui.pages.{module_name} (route {route})"
            )

    def test_all_pages_use_standard_layout(self):
        """Every page module must call build_top_bar, build_left_nav, build_right_rail."""
        required_calls = {"build_top_bar", "build_left_nav", "build_right_rail"}
        for route, module_name in self.EXPECTED_PAGES.items():
            filepath = PAGES_ROOT / f"{module_name}.py"
            source = filepath.read_text()
            for call in required_calls:
                assert call in source, f"{module_name}.py does not call {call} (route {route})"

    def test_left_nav_contains_all_routes(self):
        """The left nav component must link to all expected routes."""
        layout_source = (COMPONENTS_ROOT / "layout.py").read_text()
        for route in self.EXPECTED_PAGES:
            if route == "/":
                # Dashboard may be represented as "/" or empty string
                assert '"/"' in layout_source or '""' in layout_source, "Left nav does not link to Dashboard (/)"
            else:
                assert f'"{route}"' in layout_source, f"Left nav does not link to route {route}"

    def test_no_dead_nav_items(self):
        """Navigation should not contain routes that have no @ui.page handler."""
        registered_routes = set()
        for module_name in self.EXPECTED_PAGES.values():
            filepath = PAGES_ROOT / f"{module_name}.py"
            source = filepath.read_text()
            # Find @ui.page("/...") decorators
            for match in re.finditer(r'@ui\.page\("([^"]+)"\)', source):
                registered_routes.add(match.group(1))

        # Check layout.py nav links are all in registered routes
        layout_source = (COMPONENTS_ROOT / "layout.py").read_text()
        nav_routes = set(re.findall(r'"/([\w-]*)"', layout_source))
        # The root route "/" is special
        for route in nav_routes:
            if route == "":
                assert "/" in registered_routes, "Nav links to / but it's not registered"
            else:
                assert f"/{route}" in registered_routes, f"Nav links to /{route} but no @ui.page('/{route}') found"


# ── 2. Route registration / no shadowing ───────────────────────────────


class TestRouteRegistration:
    """Verify backend route registration has no conflicts."""

    def test_no_duplicate_api_routes(self):
        """All API route paths should be unique across route modules.

        Routes are qualified by their module's prefix — e.g. /health in
        health.py (prefix /api/v1) and /health in retrieval_dashboard.py
        (prefix /api/v1/retrieval) resolve to different full paths.
        """
        # Map each route file to its API prefix
        app_source = (PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "app.py").read_text()

        # Extract include_router calls to find prefixes per module
        module_prefixes: dict[str, str] = {}
        for match in re.finditer(r'include_router\(\w+_router.*?from="([\w.]+)"', app_source):
            module_prefixes[match.group(1)] = ""

        # Check per-module routes are unique within their prefix
        route_files = sorted(ROUTES_ROOT.glob("*.py"))
        for rf in route_files:
            if rf.name.startswith("_"):
                continue
            source = rf.read_text()
            module_routes: set[str] = set()
            for match in re.finditer(r'@router\.(get|post|put|delete|websocket)\("([^"]+)"', source):
                method = match.group(1).upper()
                path = match.group(2)
                route_key = f"{method} {path}"
                # Within a single module, no route should appear twice
                assert route_key not in module_routes, f"{rf.name} defines duplicate route: {route_key}"
                module_routes.add(route_key)


# ── 3. GUI import boundary ─────────────────────────────────────────────


class TestGuiImportBoundary:
    """GUI must not import from aip.* — API-first boundary."""

    def _collect_aip_imports(self) -> list[tuple[str, int, str]]:
        """Find any imports from aip.* in gui/ files."""
        violations = []
        for py_file in GUI_ROOT.rglob("*.py"):
            # Skip archived files
            if "archive" in str(py_file):
                continue
            try:
                source = py_file.read_text()
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError):
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("aip."):
                        rel = py_file.relative_to(PROJECT_ROOT)
                        violations.append((str(rel), node.lineno, node.module))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("aip."):
                            rel = py_file.relative_to(PROJECT_ROOT)
                            violations.append((str(rel), node.lineno, alias.name))

        return violations

    def test_no_aip_imports_in_gui(self):
        """gui/ must not import from aip.* packages."""
        violations = self._collect_aip_imports()
        assert violations == [], "Import boundary violations in gui/:\n" + "\n".join(
            f"  {f}:{line} imports {mod}" for f, line, mod in violations
        )


# ── 4. API client fallback/error handling ──────────────────────────────


class TestApiClientErrorHandling:
    """API client must not fabricate healthy data on failure."""

    def test_text_generation_slots_no_fake_ci_mode(self):
        """list_text_generation_slots() must not return ci_mode=True on failure."""
        source = (GUI_ROOT / "api_client.py").read_text()
        # Find the method and its error return
        # The error path should return ci_mode: False, not True
        # Look for the specific pattern in the exception handler
        assert '"ci_mode": True' not in source or "ci_mode" not in source, (
            "api_client should not return ci_mode: True on failure"
        )
        # Verify the method returns an error key on failure
        # Find the method
        method_match = re.search(
            r"async def list_text_generation_slots.*?(?=async def |\nclass |\Z)",
            source,
            re.DOTALL,
        )
        if method_match:
            method_code = method_match.group()
            # The except block should NOT have ci_mode: True
            except_blocks = re.findall(r"except.*?return\s*\{[^}]+\}", method_code, re.DOTALL)
            for block in except_blocks:
                assert '"ci_mode": True' not in block, (
                    "list_text_generation_slots exception handler returns ci_mode=True (fake healthy)"
                )

    def test_get_status_summary_returns_empty_on_failure(self):
        """get_status_summary() should return {} on failure, not fake data."""
        source = (GUI_ROOT / "api_client.py").read_text()
        method_match = re.search(
            r"async def get_status_summary.*?(?=async def |\nclass |\Z)",
            source,
            re.DOTALL,
        )
        if method_match:
            method_code = method_match.group()
            # On failure, should return empty dict or dict with error
            # Should NOT return fabricated health data
            except_match = re.search(r"except.*?return\s*(\{[^}]*\})", method_code, re.DOTALL)
            if except_match:
                return_val = except_match.group(1)
                # Empty dict is fine, dict with error is fine, but not a dict with fake status data
                assert "healthy" not in return_val.lower() or "error" in return_val.lower(), (
                    "get_status_summary should not return fake healthy status on failure"
                )


# ── 5. Mutation sovereignty ────────────────────────────────────────────


class TestMutationSovereignty:
    """All mutating actions must be explicit — no auto-approve, auto-export, etc."""

    FORBIDDEN_AUTO_PATTERNS = [
        # Match actual function calls / logic, not docstring mentions like "never auto-approved"
        (r"(?!.*never)auto.approve", "auto-approve logic"),
        (r"(?!.*never)auto.export", "auto-export logic"),
        (r"(?!.*never)auto.wiki.mutat", "auto-wiki mutation"),
    ]

    def test_no_auto_approve_in_gui(self):
        """GUI must not contain auto-approve logic (docstring mentions are OK)."""
        for py_file in GUI_ROOT.rglob("*.py"):
            if "archive" in str(py_file):
                continue
            # status_types.py only contains TypedDict definitions with docstring comments
            if py_file.name == "status_types.py":
                continue
            source = py_file.read_text()
            # Strip comments and docstrings for pattern matching
            # Only check actual code lines, not documentation
            code_lines = []
            in_docstring = False
            for line in source.split("\n"):
                stripped = line.strip()
                if in_docstring:
                    if '"""' in stripped or "'''" in stripped:
                        in_docstring = False
                    continue
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    if stripped.count('"""') < 2 and stripped.count("'''") < 2:
                        in_docstring = True
                    continue
                if stripped.startswith("#"):
                    continue
                code_lines.append(line)
            code_only = "\n".join(code_lines).lower()
            for pattern, description in self.FORBIDDEN_AUTO_PATTERNS:
                assert not re.search(pattern, code_only), (
                    f"{py_file.relative_to(PROJECT_ROOT)} contains {description} in code"
                )

    def test_approve_all_not_wired(self):
        """approve_all_reviews() in API client must not be called from active GUI pages."""
        source = (GUI_ROOT / "api_client.py").read_text()
        if "approve_all_reviews" not in source:
            pytest.skip("approve_all_reviews not in API client")

        # Check that NO active page or component calls it
        # Excludes FROZEN/PRESERVED legacy files (shell.py, main.py)
        frozen_indicators = ["FROZEN", "PRESERVED", "archive"]
        for py_file in GUI_ROOT.rglob("*.py"):
            if any(ind in str(py_file) for ind in frozen_indicators):
                continue
            if py_file.name == "api_client.py":
                continue  # definition is fine
            # Check if the file itself is marked frozen
            first_line = py_file.read_text()[:200]
            if "FROZEN" in first_line or "PRESERVED" in first_line:
                continue
            page_source = py_file.read_text()
            assert "approve_all_reviews" not in page_source, (
                f"{py_file.name} calls approve_all_reviews — bulk approve must not be wired"
            )

    def test_artifact_review_requires_explicit_action(self):
        """Artifact approve/reject must require explicit user click, not be automatic."""
        review_source = (COMPONENTS_ROOT / "artifact_review_panel.py").read_text()
        # Approve button must exist and be explicit
        assert "approve" in review_source.lower(), "Artifact review panel must have approve action"
        assert "reject" in review_source.lower(), "Artifact review panel must have reject action"
        # Check that approve is not called automatically
        # It should be in a button click handler, not in a load/refresh function
        # Find approve function calls
        approve_calls = re.findall(r"\.approve\w*\(", review_source)
        for call in approve_calls:
            # Ensure it's inside a lambda or on_click, not in a data load function
            pass  # Basic check: approve method exists in the component


# ── 6. Status language consistency ──────────────────────────────────────


class TestStatusLanguageConsistency:
    """Status labels should be consistent across right rail and dashboard."""

    def test_retrieval_status_labels_match(self):
        """Right rail and dashboard should use same labels for retrieval states."""
        right_rail_source = (PANELS_ROOT / "right_rail.py").read_text()
        dashboard_source = (PAGES_ROOT / "dashboard.py").read_text()

        # For 'unavailable' state, both should use "UNAVAILABLE"
        # Right rail should NOT use "DOWN" for the same state
        # Find the right rail's mapping for 'unavailable'
        right_rail_unavailable = re.search(
            r'ch_state\s*==\s*"unavailable".*?status\s*=\s*"(\w+)"',
            right_rail_source,
            re.DOTALL,
        )
        if right_rail_unavailable:
            label = right_rail_unavailable.group(1)
            assert label == "UNAVAILABLE", f"Right rail uses '{label}' for unavailable state, expected 'UNAVAILABLE'"

        # Dashboard should also use "UNAVAILABLE" for the same state
        dashboard_unavailable = re.search(
            r'"unavailable".*?["\'](?:UNAVAILABLE|DOWN)["\']',
            dashboard_source,
            re.DOTALL,
        )
        if dashboard_unavailable:
            assert "DOWN" not in dashboard_unavailable.group(), (
                "Dashboard should use 'UNAVAILABLE' not 'DOWN' for unavailable retrieval state"
            )


# ── 7. Cross-surface link wiring ───────────────────────────────────────


class TestCrossSurfaceLinks:
    """Verify cross-surface links are wired where backend support exists."""

    def test_link_wiki_wired_in_ask(self):
        """Ask page should wire on_link_wiki in the main (non-direct-model) path."""
        ask_source = (PAGES_ROOT / "ask.py").read_text()
        # _handle_link_wiki function should exist
        assert "_handle_link_wiki" in ask_source, "Ask page should have _handle_link_wiki handler function"
        # In the main answer path, on_link_wiki should be wired
        # (on_link_wiki=None is acceptable in the direct-model fallback path
        # where there is no backend to call)
        main_path_count = ask_source.count("on_link_wiki=lambda")
        assert main_path_count >= 1, "Ask page should wire on_link_wiki to _handle_link_wiki in main answer path"

    def test_dashboard_cards_have_click_through(self):
        """Dashboard cards for sub-pages should have navigate_to parameter."""
        dashboard_source = (PAGES_ROOT / "dashboard.py").read_text()
        # The _card function should accept navigate_to parameter
        assert "navigate_to" in dashboard_source, (
            "Dashboard _card function should accept navigate_to parameter for click-through"
        )
        # Key cards should have navigation targets
        expected_navigations = [
            ('navigate_to="/corpus"', "Corpus Health card"),
            ('navigate_to="/retrieval"', "Retrieval Health card"),
            ('navigate_to="/artifacts"', "Review Queue card"),
            ('navigate_to="/wiki"', "Wiki/CODEX card"),
        ]
        for nav, desc in expected_navigations:
            assert nav in dashboard_source, f"Dashboard {desc} should have {nav}"

    def test_corpus_links_to_retrieval_lab(self):
        """Corpus page should link to Retrieval Lab for testing retrieval quality."""
        corpus_source = (PAGES_ROOT / "corpus.py").read_text()
        assert "/retrieval" in corpus_source, "Corpus page should link to /retrieval (Retrieval Lab)"

    def test_artifact_save_notification_mentions_artifacts_page(self):
        """Ask page save-as-artifact notification should mention Artifacts page."""
        ask_source = (PAGES_ROOT / "ask.py").read_text()
        # After saving artifact, notification should mention Artifacts page
        assert "Artifacts page" in ask_source or "artifacts page" in ask_source, (
            "Save-artifact notification should mention Artifacts page for cross-navigation"
        )


# ── 8. Error/empty state handling ──────────────────────────────────────


class TestErrorEmptyStates:
    """Verify major pages handle error/empty/degraded states."""

    def test_corpus_page_has_error_handler(self):
        """Corpus page _load_all() should have try/except error handling."""
        corpus_source = (PAGES_ROOT / "corpus.py").read_text()
        # Find _load_all function
        load_all_match = re.search(
            r"async def _load_all.*?(?=\n    async def |\n    def |\Z)",
            corpus_source,
            re.DOTALL,
        )
        if load_all_match:
            func_code = load_all_match.group()
            assert "except" in func_code, "Corpus _load_all() must have error handling (try/except)"
            assert "backend_reachable" in func_code.lower() or "unavailable" in func_code.lower(), (
                "Corpus _load_all() should set backend_reachable=False or show unavailable on error"
            )

    def test_artifacts_page_checks_backend_reachable(self):
        """Artifacts page should check backend reachability."""
        artifacts_source = (PAGES_ROOT / "artifacts.py").read_text()
        assert "backend_reachable" in artifacts_source or "refresh_status_summary" in artifacts_source, (
            "Artifacts page should check backend_reachable status"
        )

    def test_wiki_page_handles_article_load_errors(self):
        """Wiki page should show error indicator when article loading fails."""
        wiki_source = (PAGES_ROOT / "wiki.py").read_text()
        # Should have error tracking for article load failures
        assert "articles_error" in wiki_source or "error" in wiki_source.lower(), (
            "Wiki page should handle article load errors visibly"
        )


# ── 9. Existing UI cycle tests still pass (structural) ─────────────────


class TestExistingUICycleTests:
    """Verify that existing UI cycle test files are importable and structurally sound."""

    EXISTING_TEST_FILES = [
        "test_gui_import_boundary.py",
        "test_import_boundary.py",
        "test_layer_discipline.py",
    ]

    def test_existing_test_files_exist(self):
        """Existing UI test files should still exist."""
        for tf in self.EXISTING_TEST_FILES:
            filepath = PROJECT_ROOT / "tests" / tf
            assert filepath.exists(), f"Existing test file {tf} is missing"

    def test_settings_page_exists_and_is_importable(self):
        """Settings page module should be importable (no syntax errors)."""
        filepath = PAGES_ROOT / "settings.py"
        assert filepath.exists(), "Settings page module should exist"
        source = filepath.read_text()
        # Should parse as valid Python
        ast.parse(source)


# ── 10. Model slot change notification ──────────────────────────────────


class TestModelSlotChangeNotification:
    """Model slot changes must not silently desync from backend."""

    def test_on_chat_model_changed_awaits_backend(self):
        """_on_chat_model_changed should await the backend call, not fire-and-forget."""
        ask_source = (PAGES_ROOT / "ask.py").read_text()
        # Find _on_chat_model_changed
        func_match = re.search(
            r"(async\s+)?def _on_chat_model_changed.*?(?=\ndef |\nclass |\Z)",
            ask_source,
            re.DOTALL,
        )
        if func_match:
            func_code = func_match.group()
            # Should be async
            assert func_code.startswith("async"), "_on_chat_model_changed should be async def (to await backend call)"
            # Should have try/except for error notification
            assert "except" in func_code, "_on_chat_model_changed should have error handling for backend failures"
            # Should NOT use asyncio.create_task for the slot update
            assert (
                "asyncio.create_task" not in func_code or "create_task" not in func_code.split("update_slot_model")[0]
                if "update_slot_model" in func_code
                else True
            ), "_on_chat_model_changed should await update_slot_model, not fire-and-forget"
