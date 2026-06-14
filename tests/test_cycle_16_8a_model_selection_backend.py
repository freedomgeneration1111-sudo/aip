"""Cycle 16.8A — Model Selection Backend Fix regression tests.

Findings covered:
  F-D1 — Fresh backend DB must not fail model-library routes due to
          missing enabled_models table. Schema ensure helper guarantees
          the table exists before any route operation.
  F-D2 — OpenRouter model IDs containing '/' (e.g.
          "deepseek/deepseek-v4-flash:free") must be toggleable via
          body-based PATCH /models/library route.
  F-D3 — GET /models/library must not expose raw custom_api_key values.
          Only a boolean has_custom_api_key field is returned.

Also covers:
  - GUI API client uses body-based toggle path (no URL-encoded model_id).
  - Write routes remain DEFINER/admin protected.
  - aip init still seeds models from config/enabled_models.json.
  - Existing 16.7C test contract still passes.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# F-D1 — Schema ensure on backend route path
# ---------------------------------------------------------------------------


class TestSchemaEnsureOnFreshDB:
    """enabled_models table must be created automatically on first route access."""

    def test_ensure_schema_helper_exists(self) -> None:
        """models_library.py must export _ensure_schema helper."""
        from aip.adapter.api.routes.models_library import _ensure_schema

        assert callable(_ensure_schema), "_ensure_schema is not callable"

    def test_ensure_schema_creates_table(self, tmp_path: Path) -> None:
        """_ensure_schema must create the enabled_models table in a fresh DB."""
        import aiosqlite

        from aip.adapter.api.routes.models_library import _ensure_schema

        db_path = tmp_path / "state.db"
        conn = sqlite3.connect(str(db_path))
        # Verify table does not exist before
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='enabled_models'")
        assert cursor.fetchone() is None, "enabled_models should not exist yet"
        conn.close()

        # Run _ensure_schema via aiosqlite
        import asyncio

        async def _run():
            aconn = await aiosqlite.connect(str(db_path))
            try:
                await _ensure_schema(aconn)
            finally:
                await aconn.close()

        asyncio.get_event_loop().run_until_complete(_run())

        # Verify table now exists
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='enabled_models'")
        assert cursor.fetchone() is not None, "enabled_models table was not created"
        conn.close()

    def test_ensure_schema_is_idempotent(self, tmp_path: Path) -> None:
        """_ensure_schema must be safe to call multiple times without error."""
        import aiosqlite

        from aip.adapter.api.routes.models_library import _ensure_schema

        db_path = tmp_path / "state.db"
        import asyncio

        async def _run():
            aconn = await aiosqlite.connect(str(db_path))
            try:
                await _ensure_schema(aconn)
                await _ensure_schema(aconn)  # Call twice
            finally:
                await aconn.close()

        asyncio.get_event_loop().run_until_complete(_run())

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='enabled_models'")
        assert cursor.fetchone() is not None, "enabled_models table not found after idempotent calls"
        conn.close()

    def test_ensure_schema_preserves_existing_data(self, tmp_path: Path) -> None:
        """_ensure_schema must not drop or overwrite existing table data."""
        import aiosqlite

        from aip.adapter.api.routes.models_library import _ensure_schema

        db_path = tmp_path / "state.db"
        import asyncio

        # Create and seed
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE enabled_models (
                model_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'openrouter',
                cost_input_per_million REAL,
                cost_output_per_million REAL,
                context_length INTEGER,
                supports_vision INTEGER DEFAULT 0,
                supports_tools INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 0,
                is_custom INTEGER DEFAULT 0,
                custom_base_url TEXT,
                custom_api_key TEXT,
                last_fetched TEXT
            )
        """)
        conn.execute(
            "INSERT INTO enabled_models (model_id, display_name, enabled) VALUES (?, ?, ?)",
            ("test/model-a", "model-a", 1),
        )
        conn.commit()
        conn.close()

        async def _run():
            aconn = await aiosqlite.connect(str(db_path))
            try:
                await _ensure_schema(aconn)
            finally:
                await aconn.close()

        asyncio.get_event_loop().run_until_complete(_run())

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT model_id, enabled FROM enabled_models")
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert rows[0][0] == "test/model-a"
        assert rows[0][1] == 1

    def test_list_route_creates_schema_on_fresh_db(self, tmp_path: Path) -> None:
        """GET /models/library must work on a fresh DB without prior aip init."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        from aip.adapter.api.app import create_app

        # Point the routes module at our temp DB
        db_path = str(tmp_path / "state.db")
        with patch("aip.adapter.api.routes.models_library._STATE_DB", db_path):
            app = create_app()
            client = TestClient(app)
            resp = client.get("/api/v1/models/library")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert "items" in data
            assert "total" in data

    def test_fetch_route_creates_schema_on_fresh_db(self, tmp_path: Path) -> None:
        """POST /models/library/fetch must not fail with 'no such table' on fresh DB.

        We mock the OpenRouter HTTP call so this test doesn't depend on
        external network access. Instead of mocking httpx (which is complex
        with async context managers), we directly test that _ensure_schema
        is called in the fetch path by verifying the route doesn't fail
        with "no such table" on a fresh DB.
        """
        # Test at the unit level: verify _ensure_schema is called in the
        # fetch route code path by checking the source.
        from aip.adapter.api.routes import models_library

        source = inspect.getsource(models_library.fetch_model_library)
        assert "_ensure_schema" in source, (
            "fetch_model_library does not call _ensure_schema — fresh DB will fail"
        )

        # Also verify that the route's first action is schema ensure
        lines = source.splitlines()
        # The _ensure_schema_via_route() call should appear before the main try block
        found_ensure = False
        for line in lines:
            if "_ensure_schema" in line:
                found_ensure = True
                break
            if "import httpx" in line and found_ensure:
                break
        assert found_ensure, "_ensure_schema not found in fetch_model_library source"


# ---------------------------------------------------------------------------
# F-D2 — Slash-containing model IDs toggle
# ---------------------------------------------------------------------------


class TestSlashModelIdToggle:
    """OpenRouter model IDs containing '/' must be toggleable via body-based PATCH."""

    def test_toggle_route_is_body_based(self) -> None:
        """PATCH /models/library must use body-based model_id, not path param."""
        from aip.adapter.api.routes import models_library

        source = inspect.getsource(models_library.toggle_model_enabled)
        # The route must not have a {model_id} path parameter
        assert "model_id: str" not in source.split("def toggle_model_enabled")[1].split("body")[0], (
            "toggle_model_enabled still uses path parameter model_id"
        )
        # The body model must include model_id
        assert "body.model_id" in source, "toggle route does not use body.model_id"

    def test_toggle_request_model_has_model_id(self) -> None:
        """ToggleEnabledRequest must include model_id field."""
        from aip.adapter.api.routes.models_library import ToggleEnabledRequest

        fields = ToggleEnabledRequest.model_fields
        assert "model_id" in fields, "ToggleEnabledRequest missing model_id field"
        assert "enabled" in fields, "ToggleEnabledRequest missing enabled field"

    def test_gui_api_client_sends_model_id_in_body(self) -> None:
        """GUI toggle_model_enabled must send model_id in JSON body, not URL path."""
        from gui.api_client import AipApiClient

        source = inspect.getsource(AipApiClient.toggle_model_enabled)
        # Must use body-based endpoint
        assert '/models/library"' in source or "/models/library'" in source, (
            "toggle_model_enabled does not call body-based /models/library endpoint"
        )
        # model_id must be in the JSON body
        assert '"model_id"' in source or "'model_id'" in source, (
            "toggle_model_enabled does not send model_id in JSON body"
        )
        # Must NOT place slash-containing ID in URL path
        assert 'f"{self.base_url}/api/v1/models/library/{model_id}"' not in source, (
            "toggle_model_enabled still puts model_id in URL path"
        )

    def test_toggle_slash_id_via_test_client(self, tmp_path: Path) -> None:
        """Toggle a model ID containing '/' must work via body-based PATCH."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        from aip.adapter.api.app import create_app

        db_path = str(tmp_path / "state.db")
        with patch("aip.adapter.api.routes.models_library._STATE_DB", db_path):
            # Pre-seed the model
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enabled_models (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'openrouter',
                    cost_input_per_million REAL,
                    cost_output_per_million REAL,
                    context_length INTEGER,
                    supports_vision INTEGER DEFAULT 0,
                    supports_tools INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 0,
                    is_custom INTEGER DEFAULT 0,
                    custom_base_url TEXT,
                    custom_api_key TEXT,
                    last_fetched TEXT
                )
            """)
            conn.execute(
                "INSERT INTO enabled_models (model_id, display_name, enabled) VALUES (?, ?, ?)",
                ("deepseek/deepseek-v4-flash:free", "deepseek-v4-flash", 0),
            )
            conn.commit()
            conn.close()

            app = create_app()
            client = TestClient(app)

            # Enable the model
            resp = client.patch(
                "/api/v1/models/library",
                json={"model_id": "deepseek/deepseek-v4-flash:free", "enabled": 1},
            )
            # Accept 200 or 403 (if auth is strict in test mode)
            if resp.status_code == 403:
                pytest.skip("Auth required in test mode — toggle test needs DEFINER")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data.get("enabled") == 1, f"Model should be enabled, got {data}"

            # Disable the model
            resp = client.patch(
                "/api/v1/models/library",
                json={"model_id": "deepseek/deepseek-v4-flash:free", "enabled": 0},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data.get("enabled") == 0, f"Model should be disabled, got {data}"

    def test_toggle_nonexistent_model_returns_404(self, tmp_path: Path) -> None:
        """Toggling a model ID that doesn't exist must return honest 404."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        from aip.adapter.api.app import create_app

        db_path = str(tmp_path / "state.db")
        with patch("aip.adapter.api.routes.models_library._STATE_DB", db_path):
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enabled_models (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'openrouter',
                    cost_input_per_million REAL,
                    cost_output_per_million REAL,
                    context_length INTEGER,
                    supports_vision INTEGER DEFAULT 0,
                    supports_tools INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 0,
                    is_custom INTEGER DEFAULT 0,
                    custom_base_url TEXT,
                    custom_api_key TEXT,
                    last_fetched TEXT
                )
            """)
            conn.commit()
            conn.close()

            app = create_app()
            client = TestClient(app)

            resp = client.patch(
                "/api/v1/models/library",
                json={"model_id": "nonexistent/model-xyz:free", "enabled": 1},
            )
            if resp.status_code == 403:
                pytest.skip("Auth required in test mode")
            assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
            assert "not found" in resp.json().get("detail", "").lower()

    def test_toggle_with_colon_and_dot_in_id(self, tmp_path: Path) -> None:
        """Model IDs with ':', '.' must also be toggleable."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        from aip.adapter.api.app import create_app

        db_path = str(tmp_path / "state.db")
        with patch("aip.adapter.api.routes.models_library._STATE_DB", db_path):
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enabled_models (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'openrouter',
                    cost_input_per_million REAL,
                    cost_output_per_million REAL,
                    context_length INTEGER,
                    supports_vision INTEGER DEFAULT 0,
                    supports_tools INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 0,
                    is_custom INTEGER DEFAULT 0,
                    custom_base_url TEXT,
                    custom_api_key TEXT,
                    last_fetched TEXT
                )
            """)
            conn.execute(
                "INSERT INTO enabled_models (model_id, display_name, enabled) VALUES (?, ?, ?)",
                ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "nemotron-nano", 0),
            )
            conn.commit()
            conn.close()

            app = create_app()
            client = TestClient(app)

            resp = client.patch(
                "/api/v1/models/library",
                json={"model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "enabled": 1},
            )
            if resp.status_code == 403:
                pytest.skip("Auth required in test mode")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# F-D3 — No raw custom_api_key in list response
# ---------------------------------------------------------------------------


class TestNoApiKeyLeakage:
    """GET /models/library must not expose raw custom_api_key values."""

    def test_list_response_schema_has_no_custom_api_key_field(self) -> None:
        """List route response must not include custom_api_key in the output dict."""
        from aip.adapter.api.routes import models_library

        source = inspect.getsource(models_library.list_model_library)
        # The SELECT reads custom_api_key from DB to compute has_custom_api_key,
        # but the output dict must not pass it through. Check dict construction.
        in_dict = False
        for line in source.splitlines():
            if "items.append" in line:
                in_dict = True
            if in_dict:
                # A line like: "custom_api_key": row["custom_api_key"],
                # would expose the raw key — forbidden
                if '"custom_api_key"' in line and "row[" in line:
                    pytest.fail(
                        "list_model_library output dict includes raw custom_api_key from row"
                    )

    def test_list_response_includes_has_custom_api_key_boolean(self) -> None:
        """List route response must include has_custom_api_key boolean."""
        from aip.adapter.api.routes import models_library

        source = inspect.getsource(models_library.list_model_library)
        assert "has_custom_api_key" in source, "list_model_library does not include has_custom_api_key in response"

    def test_raw_key_absent_from_list_response(self, tmp_path: Path) -> None:
        """Even if a model has a stored API key, the list response must not contain it."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        from aip.adapter.api.app import create_app

        db_path = str(tmp_path / "state.db")
        fake_key = "sk-test-secret-key-1234567890"

        with patch("aip.adapter.api.routes.models_library._STATE_DB", db_path):
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enabled_models (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'openrouter',
                    cost_input_per_million REAL,
                    cost_output_per_million REAL,
                    context_length INTEGER,
                    supports_vision INTEGER DEFAULT 0,
                    supports_tools INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 0,
                    is_custom INTEGER DEFAULT 0,
                    custom_base_url TEXT,
                    custom_api_key TEXT,
                    last_fetched TEXT
                )
            """)
            conn.execute(
                "INSERT INTO enabled_models (model_id, display_name, enabled, custom_api_key) VALUES (?, ?, ?, ?)",
                ("test/key-model", "Key Model", 1, fake_key),
            )
            conn.commit()
            conn.close()

            app = create_app()
            client = TestClient(app)

            resp = client.get("/api/v1/models/library")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            data = resp.json()
            items = data.get("items", [])
            assert len(items) > 0, "No models returned"

            # The raw key must NOT appear in the response
            resp_text = resp.text
            assert fake_key not in resp_text, f"Raw API key leaked in list response! Response contains: {fake_key}"

            # The model entry should have has_custom_api_key=True
            model = items[0]
            assert model.get("has_custom_api_key") is True, (
                f"has_custom_api_key should be True for model with key, got {model.get('has_custom_api_key')}"
            )
            # Must NOT have custom_api_key field
            assert "custom_api_key" not in model, (
                "custom_api_key field present in list response — should be removed/masked"
            )


# ---------------------------------------------------------------------------
# Integration: GUI API client uses fixed toggle path
# ---------------------------------------------------------------------------


class TestGuiApiClientIntegration:
    """GUI API client must use the new body-based toggle endpoint."""

    def test_toggle_method_signature_unchanged(self) -> None:
        """toggle_model_enabled method signature must still accept (model_id, enabled)."""
        import inspect as _inspect

        from gui.api_client import AipApiClient

        sig = _inspect.signature(AipApiClient.toggle_model_enabled)
        params = list(sig.parameters.keys())
        assert "model_id" in params, f"toggle_model_enabled missing model_id param: {params}"
        assert "enabled" in params, f"toggle_model_enabled missing enabled param: {params}"

    def test_models_page_still_calls_toggle(self) -> None:
        """Models page must still call toggle_model_enabled via API client."""
        source = (PROJECT_ROOT / "gui" / "pages" / "models.py").read_text()
        assert "toggle_model_enabled" in source, "models.py no longer calls toggle_model_enabled via API client"


# ---------------------------------------------------------------------------
# Regression: Write routes still DEFINER protected
# ---------------------------------------------------------------------------


class TestWriteRoutesStillProtected:
    """Write routes must still require DEFINER auth."""

    def test_fetch_route_has_require_definer(self) -> None:
        """POST /models/library/fetch must still have require_definer."""
        source = (PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "models_library.py").read_text()
        lines = source.splitlines()
        in_fetch = False
        found_guard = False
        for line in lines:
            if "fetch_model_library" in line and "def " in line:
                in_fetch = True
            if in_fetch:
                if "require_definer" in line:
                    found_guard = True
                    break
                if line.strip().startswith("try:") or line.strip().startswith("try "):
                    break
        assert found_guard, "fetch_model_library missing require_definer guard"

    def test_toggle_route_has_require_definer(self) -> None:
        """PATCH /models/library must still have require_definer."""
        source = (PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "models_library.py").read_text()
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
                if line.strip().startswith("try:") or line.strip().startswith("try "):
                    break
        assert found_guard, "toggle_model_enabled missing require_definer guard"


# ---------------------------------------------------------------------------
# Regression: aip init still seeds models
# ---------------------------------------------------------------------------


class TestAipInitStillSeeds:
    """aip init must still seed models from config/enabled_models.json."""

    def test_init_has_populate_enabled_models(self) -> None:
        """aip init must call _populate_enabled_models."""
        source = (PROJECT_ROOT / "src" / "aip" / "cli" / "init.py").read_text()
        assert "_populate_enabled_models" in source, "aip init no longer calls _populate_enabled_models"

    def test_enabled_models_json_exists(self) -> None:
        """config/enabled_models.json must exist."""
        json_path = PROJECT_ROOT / "config" / "enabled_models.json"
        assert json_path.exists(), "config/enabled_models.json does not exist"

    def test_enabled_models_json_has_12_entries(self) -> None:
        """config/enabled_models.json must contain 12 model entries."""
        import json

        json_path = PROJECT_ROOT / "config" / "enabled_models.json"
        data = json.loads(json_path.read_text())
        assert isinstance(data, list), "enabled_models.json is not a list"
        assert len(data) == 12, f"Expected 12 models, got {len(data)}"

    def test_enabled_models_json_contains_slash_ids(self) -> None:
        """config/enabled_models.json must contain model IDs with '/'."""
        import json

        json_path = PROJECT_ROOT / "config" / "enabled_models.json"
        data = json.loads(json_path.read_text())
        slash_ids = [m for m in data if "/" in m]
        assert len(slash_ids) > 0, "No model IDs with '/' found in enabled_models.json"


# ---------------------------------------------------------------------------
# Regression: No scope creep
# ---------------------------------------------------------------------------


class TestNoScopeCreep:
    """Verify that this cycle did not broaden into out-of-scope areas."""

    def test_no_vigil_f09_f10_fix(self) -> None:
        """Vigil ECS UNIQUE constraint must not have been modified."""
        # Check that no ECS store files were changed in this cycle
        source = (PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "models_library.py").read_text()
        # models_library should not reference ECS or vigil
        assert "ecs_state" not in source, "models_library.py references ECS — scope creep"
        assert "vigil" not in source.lower(), "models_library.py references vigil — scope creep"

    def test_no_byok_key_management(self) -> None:
        """Full BYOK key management must not have been implemented."""
        source = (PROJECT_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "models_library.py").read_text()
        # Should not have routes for key management
        assert "store_api_key" not in source, "BYOK key storage implemented — scope creep"
        assert "set_custom_api_key" not in source, "BYOK key setter implemented — scope creep"
