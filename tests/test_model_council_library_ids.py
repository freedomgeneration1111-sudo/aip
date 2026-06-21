"""Tests for the library-model-ID path added to Model Council.

Covers the bridge that lets Multi-Cast dispatch to OpenRouter models
selected via the Models page (the ``enabled_models`` SQLite library),
not just TOML-configured slots.

Specifically verifies:
  - ``ModelCouncilRequest.selected_model_ids`` field exists and defaults to []
  - ``PerModelResult.source`` field exists, defaults to "slot"
  - ``compare_models`` accepts ``selected_model_ids`` and routes them via
    ``_call_library_model_id`` (mocked — no real HTTP)
  - The ``insufficient_models`` gate counts slots + library IDs combined
  - Library results in the response carry ``source="library"`` and an
    empty ``model_slot``
  - Existing slot-only behavior is preserved when ``selected_model_ids``
    is empty (backward compat)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── 1. Schema additions ────────────────────────────────────────────────


class TestLibraryModelIdSchema:
    """Verify the new schema fields are present with correct defaults."""

    def test_request_has_selected_model_ids(self):
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test")
        assert hasattr(req, "selected_model_ids")
        assert req.selected_model_ids == []

    def test_request_accepts_selected_model_ids(self):
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(
            prompt="test",
            selected_model_ids=["deepseek/deepseek-v4-flash:free", "openai/gpt-4o"],
        )
        assert req.selected_model_ids == ["deepseek/deepseek-v4-flash:free", "openai/gpt-4o"]

    def test_per_model_result_has_source_field(self):
        from aip.adapter.api.routes.model_council import PerModelResult

        result = PerModelResult()
        assert hasattr(result, "source")
        # Default must be "slot" so existing callers continue to work
        assert result.source == "slot"

    def test_per_model_result_source_can_be_library(self):
        from aip.adapter.api.routes.model_council import PerModelResult

        result = PerModelResult(source="library")
        assert result.source == "library"


# ── 2. Combined gate: slots + library IDs ──────────────────────────────


class TestCombinedInsufficientGate:
    """The ≥2 usable gate must count slots + library IDs combined."""

    @pytest.mark.asyncio
    async def test_one_slot_plus_one_model_id_is_sufficient(self):
        """1 slot + 1 library ID = 2 usable → should NOT return insufficient_models."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "embedding"]
        provider._resolve_slot_config.return_value = {
            "provider": "openai_compatible",
            "model": "gpt-4",
            "base_url": "https://api.openai.com",
            "api_key": "test-key",
        }
        # Slot call returns a real answer
        provider.call = AsyncMock(
            return_value={"content": "slot answer", "model": "gpt-4", "usage": {}, "latency_ms": 10, "error": False}
        )
        container.model_provider = provider

        # Mock _call_library_model_id to return a real answer.
        # Accepts ``messages=`` (Phase 1 Fix D: the Fusion engine may be a
        # library model, which receives a full messages list for the
        # Judge/Synth system+user prompts).
        async def _fake_call_library_model_id(model_id, user_prompt=None, messages=None):
            return {
                "content": f"library answer from {model_id}",
                "model": model_id,
                "display_name": model_id,
                "usage": {},
                "latency_ms": 20,
                "cost_usd": 0.0,
                "error": False,
            }

        with patch(
            "aip.adapter.api.routes.model_council._call_library_model_id",
            new=_fake_call_library_model_id,
        ):
            request = ModelCouncilRequest(
                prompt="What is dogfood mode?",
                selected_model_slots=["synthesis"],
                selected_model_ids=["deepseek/deepseek-v4-flash:free"],
            )
            result = await compare_models(request, container=container)

        # Should NOT be insufficient_models — we have 2 usable
        assert result.status != "insufficient_models"
        # Should have 2 per-model results (1 slot + 1 library)
        assert len(result.selected_models) == 2
        sources = {pm.source for pm in result.selected_models}
        assert sources == {"slot", "library"}

    @pytest.mark.asyncio
    async def test_zero_slots_zero_model_ids_returns_insufficient(self):
        """0 slots + 0 library IDs → insufficient_models (no model_provider)."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        container.model_provider = None

        request = ModelCouncilRequest(prompt="Test prompt")
        result = await compare_models(request, container=container)

        assert result.status == "insufficient_models"
        assert "Insufficient" in result.error

    @pytest.mark.asyncio
    async def test_two_library_ids_no_provider_is_sufficient(self):
        """0 slots + 2 library IDs → should NOT return insufficient_models,
        even when model_provider is None (library path doesn't need it)."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        container.model_provider = None

        async def _fake_call_library_model_id(model_id, user_prompt=None, messages=None):
            return {
                "content": f"answer from {model_id}",
                "model": model_id,
                "display_name": model_id,
                "usage": {},
                "latency_ms": 30,
                "cost_usd": 0.0,
                "error": False,
            }

        with patch(
            "aip.adapter.api.routes.model_council._call_library_model_id",
            new=_fake_call_library_model_id,
        ):
            request = ModelCouncilRequest(
                prompt="Test prompt",
                selected_model_ids=[
                    "deepseek/deepseek-v4-flash:free",
                    "moonshotai/kimi-k2.6:free",
                ],
            )
            result = await compare_models(request, container=container)

        # Should NOT be insufficient — 2 library IDs is enough
        assert result.status != "insufficient_models"
        # Phase 1 Fix D: with 2 successful library IDs, the Fusion pipeline
        # now picks one of them as the Judge/Synth engine (previously this
        # returned ``unavailable`` because there was no ``beast`` slot —
        # but the engine fallback makes Fusion work with library-only
        # panels). The mock returns non-JSON content, so the Judge JSON
        # parse fails and the fallback path sets synthesis_status=
        # "completed" with fusion_answer set from the raw content.
        assert result.synthesis_status in ("completed", "failed")
        assert result.synthesis_status != "unavailable"
        # Both per-model results should be source="library"
        assert len(result.selected_models) == 2
        for pm in result.selected_models:
            assert pm.source == "library"
            assert pm.model_slot == ""  # library models have no slot


# ── 3. Library model lookup helper ─────────────────────────────────────


class TestLibraryModelLookup:
    """Test the _lookup_library_model helper directly."""

    @pytest.mark.asyncio
    async def test_lookup_returns_none_when_db_missing(self, tmp_path, monkeypatch):
        """Lookup returns None (not raises) when DB file doesn't exist."""
        from aip.adapter.api.routes import model_council

        # Point _STATE_DB at a nonexistent path
        monkeypatch.setattr(model_council, "_STATE_DB", str(tmp_path / "nonexistent.db"))

        result = await model_council._lookup_library_model("some/model:free")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_row_when_present(self, tmp_path, monkeypatch):
        """Lookup returns a dict when the model_id is in the table."""
        import aiosqlite

        from aip.adapter.api.routes import model_council

        db_path = str(tmp_path / "test_state.db")
        monkeypatch.setattr(model_council, "_STATE_DB", db_path)

        # Create the table and insert a row
        conn = await aiosqlite.connect(db_path)
        try:
            await conn.execute(
                """
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
                """
            )
            await conn.execute(
                """
                INSERT INTO enabled_models (model_id, display_name, enabled, is_custom)
                VALUES (?, ?, 1, 0)
                """,
                ("deepseek/deepseek-v4-flash:free", "DeepSeek V4 Flash"),
            )
            await conn.commit()
        finally:
            await conn.close()

        result = await model_council._lookup_library_model("deepseek/deepseek-v4-flash:free")
        assert result is not None
        assert result["model_id"] == "deepseek/deepseek-v4-flash:free"
        assert result["display_name"] == "DeepSeek V4 Flash"
        assert result["enabled"] == 1
        assert result["is_custom"] == 0


# ── 4. Backward compat: slot-only behavior preserved ───────────────────


class TestBackwardCompatSlotOnly:
    """Verify existing slot-only callers still work when selected_model_ids is empty."""

    @pytest.mark.asyncio
    async def test_slot_only_request_still_works(self):
        """A request with only selected_model_slots (no model_ids) should
        behave exactly as before — same slots, same source='slot'."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "evaluation", "beast", "embedding"]
        provider._resolve_slot_config.return_value = {
            "provider": "openai_compatible",
            "model": "gpt-4",
            "base_url": "https://api.openai.com",
            "api_key": "test-key",
        }
        provider.call = AsyncMock(
            return_value={"content": "{}", "model": "gpt-4", "usage": {}, "latency_ms": 10, "error": False}
        )
        container.model_provider = provider

        request = ModelCouncilRequest(
            prompt="Test",
            selected_model_slots=["synthesis", "evaluation", "beast"],
            # selected_model_ids intentionally omitted — defaults to []
        )
        result = await compare_models(request, container=container)

        # All 3 slots should be in the response
        assert len(result.selected_models) == 3
        for pm in result.selected_models:
            assert pm.source == "slot"
            assert pm.model_slot != ""

    @pytest.mark.asyncio
    async def test_per_model_result_source_field_in_response(self):
        """PerModelResult objects in the response carry the source field,
        and it serializes correctly through Pydantic."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "evaluation", "embedding"]
        provider._resolve_slot_config.return_value = {
            "provider": "openai_compatible",
            "model": "gpt-4",
            "base_url": "https://api.openai.com",
            "api_key": "test-key",
        }
        provider.call = AsyncMock(
            return_value={"content": "answer", "model": "gpt-4", "usage": {}, "latency_ms": 10, "error": False}
        )
        container.model_provider = provider

        async def _fake_call(model_id, user_prompt=None, messages=None):
            # Bug 1 fix: the panel dispatch now always passes messages=
            # (the full [system, user] list) instead of user_prompt=.
            # Accept both signatures for backward compat.
            return {
                "content": "library answer",
                "model": model_id,
                "display_name": model_id,
                "usage": {},
                "latency_ms": 20,
                "cost_usd": 0.0,
                "error": False,
            }

        with patch(
            "aip.adapter.api.routes.model_council._call_library_model_id",
            new=_fake_call,
        ):
            request = ModelCouncilRequest(
                prompt="Test",
                selected_model_slots=["synthesis"],
                selected_model_ids=["openai/gpt-4o"],
            )
            result = await compare_models(request, container=container)

        # Round-trip through model_dump to verify Pydantic serialization
        dumped = result.model_dump()
        sources = {pm["source"] for pm in dumped["selected_models"]}
        assert sources == {"slot", "library"}
