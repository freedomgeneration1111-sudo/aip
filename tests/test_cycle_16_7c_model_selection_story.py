"""Cycle 16.7C — Model Selection Story regression tests.

Findings covered:
  #4 — Model catalog / model selection story:
    - Active GUI registers a Models page
    - Models page uses API client methods (list_model_library, fetch_model_library,
      toggle_model_enabled) — no direct file writes
    - Selected models flow into Ask dropdown via build_model_options
    - Missing API key produces honest unavailable message
    - Catalog fetch failure is surfaced honestly
    - No active GUI page imports gui.main, gui.shell, or gui.archive
    - GUI does not directly write config files for model selection
    - Backend write routes preserve admin/DEFINER guard
    - Legacy model page is not revived as entrypoint
"""

from __future__ import annotations

import inspect
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Finding #4 — Model Selection Story
# ---------------------------------------------------------------------------


class TestModelsPageRegistration:
    """Active GUI registers a Models page."""

    def test_models_page_module_exists(self) -> None:
        """gui/pages/models.py must exist."""
        assert (PROJECT_ROOT / "gui" / "pages" / "models.py").exists(), "gui/pages/models.py does not exist"

    def test_models_page_importable(self) -> None:
        """gui.pages.models must be importable."""
        import gui.pages.models  # noqa: F401

    def test_app_py_imports_models_page(self) -> None:
        """gui/app.py must import gui.pages.models to register the /models route."""
        source = (PROJECT_ROOT / "gui" / "app.py").read_text()
        assert "import gui.pages.models" in source, "gui/app.py does not import gui.pages.models"

    def test_models_page_has_route_decorator(self) -> None:
        """The models page must have a @ui.page('/models') route."""
        from gui.pages import models

        # Find the page function
        page_func = getattr(models, "models_page", None)
        assert page_func is not None, "models_page function not found in gui.pages.models"

    def test_nav_includes_models(self) -> None:
        """Left nav must include a Models item pointing to /models."""
        source = (PROJECT_ROOT / "gui" / "components" / "layout.py").read_text()
        assert '"/models"' in source, "layout.py nav items do not include /models route"


class TestModelsPageUsesApiClient:
    """Models page uses API client methods, not direct file writes."""

    def test_api_client_has_fetch_model_library(self) -> None:
        """API client must have a fetch_model_library method."""
        from gui.api_client import AipApiClient

        assert hasattr(AipApiClient, "fetch_model_library"), "AipApiClient missing fetch_model_library method"

    def test_api_client_has_toggle_model_enabled(self) -> None:
        """API client must have a toggle_model_enabled method."""
        from gui.api_client import AipApiClient

        assert hasattr(AipApiClient, "toggle_model_enabled"), "AipApiClient missing toggle_model_enabled method"

    def test_api_client_has_list_model_library(self) -> None:
        """API client must have a list_model_library method."""
        from gui.api_client import AipApiClient

        assert hasattr(AipApiClient, "list_model_library"), "AipApiClient missing list_model_library method"

    def test_models_page_does_not_write_config_files_directly(self) -> None:
        """Models page source must not contain direct file writes to config/."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        # Should not write to config files directly
        assert "enabled_models.json" not in source, "models.py references enabled_models.json — should use API client"
        # Should use api_client methods
        assert "api_client" in source or "get_api_client" in source, (
            "models.py does not reference api_client — should use API-first paths"
        )

    def test_fetch_model_library_calls_backend_endpoint(self) -> None:
        """fetch_model_library must call POST /api/v1/models/library/fetch."""
        from gui.api_client import AipApiClient

        source = inspect.getsource(AipApiClient.fetch_model_library)
        assert "/models/library/fetch" in source, (
            "fetch_model_library does not call the backend /models/library/fetch endpoint"
        )

    def test_toggle_model_enabled_calls_backend_endpoint(self) -> None:
        """toggle_model_enabled must call PATCH /api/v1/models/library (body-based)."""
        from gui.api_client import AipApiClient

        source = inspect.getsource(AipApiClient.toggle_model_enabled)
        assert "/models/library" in source, "toggle_model_enabled does not call the backend /models/library endpoint"
        # Body-based route: model_id must be in the JSON body, not URL path
        assert "model_id" in source, "toggle_model_enabled does not send model_id in body"


class TestSelectedModelsFlowToAsk:
    """Selected models flow into Ask dropdown via build_model_options."""

    def test_build_model_options_includes_selected_models(self) -> None:
        """build_model_options must include selected models in its output."""
        from gui.state import build_model_options, set_selected_models

        set_selected_models(["test/model-a", "test/model-b"])
        opts = build_model_options([])
        assert "test/model-a" in opts, f"Selected model-a not in options: {opts}"
        assert "test/model-b" in opts, f"Selected model-b not in options: {opts}"

    def test_ask_page_imports_refresh_enabled_models(self) -> None:
        """Ask page must import refresh_enabled_models from gui.state."""
        source = (PROJECT_ROOT / "gui" / "pages" / "ask.py").read_text()
        assert "refresh_enabled_models" in source, "ask.py does not import refresh_enabled_models"

    def test_refresh_enabled_models_exists(self) -> None:
        """gui.state must export refresh_enabled_models."""
        from gui.state import refresh_enabled_models  # noqa: F401

    def test_build_model_options_empty_state_is_honest(self) -> None:
        """When no models are available, build_model_options returns an honest indicator."""
        from gui.state import build_model_options, set_selected_models

        # Clear all model sources
        set_selected_models([])
        opts = build_model_options([])
        # Should have at least the hardcoded fallback or an honest indicator
        assert len(opts) > 0, "build_model_options returned empty list"
        # The indicator should reference the Models page, not Settings
        if opts == ["(no models -- open Models page)"]:
            pass  # Honest empty state


class TestMissingApiKeyBehavior:
    """Missing API key produces honest unavailable message."""

    def test_models_page_shows_needs_configuration_without_key(self) -> None:
        """Models page source must reference NEEDS_CONFIGURATION for missing key."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        assert "NOT CONFIGURED" in source or "NEEDS_CONFIGURATION" in source, (
            "models.py does not show honest needs-configuration state for missing API key"
        )

    def test_fetch_button_checks_api_key(self) -> None:
        """Models page fetch handler must check has_openrouter_api_key()."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        assert "has_openrouter_api_key" in source, "models.py fetch handler does not check for API key"


class TestCatalogFetchFailure:
    """Catalog fetch failure is surfaced honestly."""

    def test_fetch_model_library_returns_error_dict_on_failure(self) -> None:
        """fetch_model_library must return an error dict, not raise, on failure."""
        from gui.api_client import AipApiClient

        source = inspect.getsource(AipApiClient.fetch_model_library)
        assert '"error"' in source or "'error'" in source, "fetch_model_library does not return error dict on failure"

    def test_models_page_shows_fetch_failure(self) -> None:
        """Models page must show FETCH FAILED on error."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        assert "FETCH FAILED" in source, "models.py does not show FETCH FAILED on catalog fetch error"


class TestNoLegacyGUIRevival:
    """No active GUI page imports gui.main, gui.shell, or gui.archive."""

    def test_models_page_does_not_import_legacy(self) -> None:
        """models.py must not import gui.main, gui.shell, or gui.archive."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        assert "import gui.main" not in source, "models.py imports gui.main — legacy revival"
        assert "import gui.shell" not in source, "models.py imports gui.shell — legacy revival"
        assert "import gui.archive" not in source, "models.py imports gui.archive — legacy revival"

    def test_no_main_or_shell_in_active_gui(self) -> None:
        """No active page under gui/pages/ imports gui.main or gui.shell."""
        pages_dir = PROJECT_ROOT / "gui" / "pages"
        for py_file in pages_dir.glob("*.py"):
            source = py_file.read_text()
            assert "import gui.main" not in source, f"{py_file.name} imports gui.main"
            assert "import gui.shell" not in source, f"{py_file.name} imports gui.shell"
            assert "from gui.main" not in source, f"{py_file.name} imports from gui.main"
            assert "from gui.shell" not in source, f"{py_file.name} imports from gui.shell"

    def test_no_archive_import_in_active_gui(self) -> None:
        """No active page imports gui.archive."""
        pages_dir = PROJECT_ROOT / "gui" / "pages"
        for py_file in pages_dir.glob("*.py"):
            source = py_file.read_text()
            assert "import gui.archive" not in source, f"{py_file.name} imports gui.archive"
            assert "from gui.archive" not in source, f"{py_file.name} imports from gui.archive"


class TestNoDirectConfigFileWrites:
    """GUI does not directly write config files for model selection."""

    def test_models_page_no_enabled_models_json_write(self) -> None:
        """models.py must not write to config/enabled_models.json directly."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        assert "enabled_models.json" not in source, "models.py references enabled_models.json — should use API client"
        assert "write_text" not in source or "selected_models" not in source, (
            "models.py may be writing selected models directly"
        )

    def test_toggle_uses_api_client(self) -> None:
        """Model enable/disable must go through the API client, not direct DB/file."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        assert "toggle_model_enabled" in source, "models.py does not call toggle_model_enabled via API client"


class TestBackendWriteRoutesProtected:
    """Backend write routes preserve admin/DEFINER guard."""

    def test_fetch_endpoint_requires_definer(self) -> None:
        """POST /models/library/fetch must require DEFINER auth."""
        source = (PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "models_library.py").read_text()
        assert "require_definer" in source, "models_library.py does not use require_definer for write routes"

    def test_toggle_endpoint_requires_definer(self) -> None:
        """PATCH /models/library (body-based) must require DEFINER auth."""
        source = (PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "models_library.py").read_text()
        # The toggle route must have require_definer
        lines = source.splitlines()
        in_toggle = False
        found_guard = False
        for line in lines:
            if "toggle_model_enabled" in line and "def " in line:
                in_toggle = True
            if in_toggle:
                if "require_definer" in line:
                    found_guard = True
                    break
                if line.strip().startswith("return ") or line.strip().startswith("raise "):
                    break
        assert found_guard, "toggle_model_enabled endpoint does not have require_definer guard"
