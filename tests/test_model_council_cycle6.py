"""Tests for UI Cycle 6 — Model Council / Multi-Model Comparison Reports.

Covers:
  - Model Council schema stability
  - POST /api/v1/beast/compare-models endpoint
  - Insufficient models state (honest when < 2 text-generation slots)
  - Multi-model execution via existing provider abstractions
  - Partial failure (one model fails → degraded report, not total failure)
  - No secret exposure in responses
  - save_as_artifact creates GENERATED artifact only (no auto-approve)
  - No auto-approve/export/wiki mutation/config mutation
  - GUI ModelCouncilPanel import without server start
  - Panel handles: insufficient models, partial report, successful report,
    failed report, save artifact result
  - Existing Beast Counsel tests still pass
  - Existing Ask Workbench tests still pass (answer card accepts callbacks)
  - GUI import-boundary tests (gui/ never imports from aip.orchestration)
  - Backend import-boundary tests (adapter routes never import from orchestration)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_mock_provider(slots: list[str], resolve_config=None, call_fn=None):
    """Create a mock model provider.

    Uses MagicMock (not AsyncMock) because list_slots() and _resolve_slot_config()
    are synchronous methods. Only call() is async.
    """
    provider = MagicMock()
    provider.list_slots.return_value = slots
    if resolve_config:
        provider._resolve_slot_config = resolve_config
    if call_fn:
        provider.call = AsyncMock(side_effect=call_fn)
    else:
        provider.call = AsyncMock(
            return_value={"content": "{}", "model": "test", "usage": {}, "latency_ms": 100, "error": False}
        )
    return provider


# ── 1. Schema stability tests ────────────────────────────────────────


class TestModelCouncilSchema:
    """Verify ModelCouncilRequest and ModelCouncilResponse schemas are stable."""

    def test_request_model_fields(self):
        """ModelCouncilRequest has all required fields."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test")
        assert hasattr(req, "prompt")
        assert hasattr(req, "turn_id")
        assert hasattr(req, "session_id")
        assert hasattr(req, "existing_answer")
        assert hasattr(req, "sources")
        assert hasattr(req, "selected_model_slots")
        assert hasattr(req, "save_as_artifact")

    def test_request_prompt_required(self):
        """ModelCouncilRequest requires prompt."""
        from pydantic import ValidationError

        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        with pytest.raises(ValidationError):
            ModelCouncilRequest()

    def test_request_save_as_artifact_default_false(self):
        """save_as_artifact defaults to False."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test")
        assert req.save_as_artifact is False

    def test_response_model_fields(self):
        """ModelCouncilResponse has all required fields."""
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse()
        fields = [
            "id",
            "status",
            "prompt",
            "turn_id",
            "session_id",
            "selected_models",
            "convergence",
            "disagreements",
            "unique_contributions",
            "risks",
            "beast_conclusion",
            "recommended_decision",
            "degraded_models",
            "failed_models",
            "artifact_id",
            "created_at",
            "advisory_only",
            "requires_DEFINER_approval",
            "error",
            "synthesis_status",
        ]
        for field in fields:
            assert hasattr(resp, field), f"Missing field: {field}"

    def test_response_status_values(self):
        """Status field supports all honest state values."""
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        for status in ["completed", "partial", "insufficient_models", "unavailable", "error"]:
            resp = ModelCouncilResponse(status=status)
            assert resp.status == status

    def test_response_advisory_only_defaults(self):
        """Response defaults to advisory_only=True and requires_DEFINER_approval=True."""
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse()
        assert resp.advisory_only is True
        assert resp.requires_DEFINER_approval is True

    def test_per_model_result_fields(self):
        """PerModelResult has all required fields."""
        from aip.adapter.api.routes.model_council import PerModelResult

        result = PerModelResult()
        fields = [
            "model_slot",
            "model_id",
            "provider",
            "status",
            "answer",
            "error",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_usd",
        ]
        for field in fields:
            assert hasattr(result, field), f"Missing field: {field}"

    def test_artifact_id_deterministic(self):
        """Artifact ID is deterministic based on turn_id + session_id."""
        from aip.adapter.api.routes.model_council import _council_artifact_id

        id1 = _council_artifact_id("turn-123", "sess-456")
        id2 = _council_artifact_id("turn-123", "sess-456")
        assert id1 == id2
        assert id1.startswith("council:report:")

    def test_artifact_id_different_turns(self):
        """Different turn_ids produce different artifact IDs."""
        from aip.adapter.api.routes.model_council import _council_artifact_id

        id1 = _council_artifact_id("turn-123", "sess-456")
        id2 = _council_artifact_id("turn-789", "sess-456")
        assert id1 != id2

    def test_excluded_slots_constant(self):
        """Embedding slot is in the excluded set."""
        from aip.adapter.api.routes.model_council import _EXCLUDED_SLOTS

        assert "embedding" in _EXCLUDED_SLOTS


# ── 2. Endpoint: Insufficient models ─────────────────────────────────


class TestInsufficientModels:
    """Test that endpoint returns insufficient_models honestly when < 2 text-generation slots."""

    @pytest.mark.asyncio
    async def test_insufficient_models_when_one_text_slot(self):
        """Returns insufficient_models when only one text-generation slot is configured."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        provider = _make_mock_provider(
            slots=["synthesis", "embedding"],
            resolve_config=lambda slot: {
                "synthesis": {
                    "provider": "openai_compatible",
                    "model": "gpt-4",
                    "base_url": "https://api.openai.com",
                    "api_key": "test-key",
                },
                "embedding": {
                    "provider": "openai_compatible",
                    "model": "text-embedding-3-small",
                    "base_url": "https://api.openai.com",
                    "api_key": "test-key",
                },
            }.get(slot, {}),
        )
        container.model_provider = provider
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        request = ModelCouncilRequest(prompt="What is dogfood mode?")
        result = await compare_models(request, container=container)

        assert result.status == "insufficient_models"
        assert "Insufficient" in result.error

    @pytest.mark.asyncio
    async def test_no_model_provider_returns_insufficient(self):
        """Returns insufficient_models when no model provider is configured."""
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

    @pytest.mark.asyncio
    async def test_embedding_only_returns_insufficient(self):
        """Returns insufficient_models when only embedding slot is configured."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        provider = _make_mock_provider(
            slots=["embedding"],
            resolve_config=lambda slot: {
                "embedding": {"provider": "openai_compatible", "model": "text-embedding-3-small"},
            }.get(slot, {}),
        )
        container.model_provider = provider
        container.artifact_store = AsyncMock()

        request = ModelCouncilRequest(prompt="Test prompt")
        result = await compare_models(request, container=container)

        assert result.status == "insufficient_models"


# ── 3. Endpoint: Multi-model execution ──────────────────────────────


class TestMultiModelExecution:
    """Test that endpoint runs multiple configured model slots."""

    @pytest.fixture
    def mock_container_multi(self):
        """Create a mock container with 3 text-generation slots."""
        from aip.adapter.api.dependencies import AipContainer

        def resolve_config(slot):
            return {
                "synthesis": {
                    "provider": "openai_compatible",
                    "model": "gpt-4",
                    "base_url": "https://api.openai.com",
                    "api_key": "test-key",
                },
                "evaluation": {
                    "provider": "openai_compatible",
                    "model": "claude-3-opus",
                    "base_url": "https://api.openai.com",
                    "api_key": "test-key",
                },
                "beast": {
                    "provider": "openai_compatible",
                    "model": "deepseek-chat",
                    "base_url": "https://api.openai.com",
                    "api_key": "test-key",
                },
                "embedding": {
                    "provider": "openai_compatible",
                    "model": "text-embedding-3-small",
                    "base_url": "https://api.openai.com",
                    "api_key": "test-key",
                },
            }.get(slot, {})

        async def mock_call(slot_name, messages, **kwargs):
            slot_answers = {
                "synthesis": {
                    "content": json.dumps({"answer": "Synthesis says dogfood means..."}),
                    "model": "gpt-4",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                    "latency_ms": 1200,
                    "cost_usd": 0.01,
                    "error": False,
                },
                "evaluation": {
                    "content": json.dumps({"answer": "Evaluation perspective..."}),
                    "model": "claude-3-opus",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 60, "total_tokens": 160},
                    "latency_ms": 1800,
                    "cost_usd": 0.02,
                    "error": False,
                },
                "beast": {
                    "content": json.dumps(
                        {
                            "convergence": "Models agree on core definition.",
                            "disagreements": "Synthesis emphasizes X, evaluation emphasizes Y.",
                            "unique_contributions": "Each adds nuance.",
                            "risks": "Over-reliance on single source.",
                            "beast_conclusion": "Combined view is well-rounded.",
                            "recommended_decision": "Synthesize both perspectives.",
                        }
                    ),
                    "model": "deepseek-chat",
                    "usage": {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500},
                    "latency_ms": 2000,
                    "cost_usd": 0.005,
                    "error": False,
                },
            }
            return slot_answers.get(
                slot_name, {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}
            )

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=resolve_config,
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        return container

    @pytest.mark.asyncio
    async def test_multi_model_comparison_succeeds(self, mock_container_multi):
        """Comparison with 3 text slots returns completed status."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="What is dogfood mode?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=mock_container_multi)

        assert result.status == "completed"
        assert len(result.selected_models) == 3  # synthesis, evaluation, beast (no embedding)
        assert result.synthesis_status == "completed"
        assert result.convergence != ""
        assert result.advisory_only is True
        assert result.requires_DEFINER_approval is True

    @pytest.mark.asyncio
    async def test_embedding_excluded_from_comparison(self, mock_container_multi):
        """Embedding slot is never included in comparison."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=mock_container_multi)

        model_slots = [m.model_slot for m in result.selected_models]
        assert "embedding" not in model_slots
        assert "synthesis" in model_slots
        assert "evaluation" in model_slots
        assert "beast" in model_slots

    @pytest.mark.asyncio
    async def test_per_model_results_populated(self, mock_container_multi):
        """Each model slot produces a result entry."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="What is dogfood mode?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=mock_container_multi)

        for pm in result.selected_models:
            assert pm.model_slot != ""
            assert pm.status in ("completed", "failed")
            if pm.status == "completed":
                assert pm.answer != ""


# ── 4. Endpoint: Partial failure ────────────────────────────────────


class TestPartialFailure:
    """One model failure yields partial/degraded report, not total failure."""

    @pytest.fixture
    def mock_container_partial(self):
        """Create a mock container where one model fails."""
        from aip.adapter.api.dependencies import AipContainer

        def resolve_config(slot):
            return {
                "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "key"},
                "evaluation": {"provider": "openai_compatible", "model": "claude-3-opus", "api_key": "key"},
                "beast": {"provider": "openai_compatible", "model": "deepseek-chat", "api_key": "key"},
                "embedding": {"provider": "openai_compatible", "model": "text-embedding-3-small", "api_key": "key"},
            }.get(slot, {})

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "evaluation":
                return {
                    "content": "",
                    "error": True,
                    "error_message": "Provider rate limited",
                    "model": "claude-3-opus",
                    "latency_ms": 500,
                }
            elif slot_name == "beast":
                return {
                    "content": json.dumps(
                        {
                            "convergence": "Partial agreement.",
                            "disagreements": "N/A - one model failed",
                            "unique_contributions": "Synthesis only.",
                            "risks": "Incomplete comparison.",
                            "beast_conclusion": "Comparison degraded.",
                            "recommended_decision": "Retry failed model.",
                        }
                    ),
                    "model": "deepseek-chat",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                    "latency_ms": 1500,
                    "error": False,
                }
            else:
                return {
                    "content": json.dumps({"answer": "Synthesis response..."}),
                    "model": "gpt-4",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                    "latency_ms": 1200,
                    "error": False,
                }

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=resolve_config,
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        return container

    @pytest.mark.asyncio
    async def test_partial_status_on_one_failure(self, mock_container_partial):
        """Returns partial status when one model fails."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=mock_container_partial)

        assert result.status == "partial"
        assert "evaluation" in result.failed_models

    @pytest.mark.asyncio
    async def test_partial_report_still_has_results(self, mock_container_partial):
        """Partial report still includes per-model results for successful models."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=mock_container_partial)

        completed = [m for m in result.selected_models if m.status == "completed"]
        failed = [m for m in result.selected_models if m.status == "failed"]
        assert len(completed) >= 1
        assert len(failed) >= 1

    @pytest.mark.asyncio
    async def test_all_models_fail_returns_error(self):
        """Returns error status when all models fail."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation"],
            resolve_config=lambda slot: {"provider": "test", "model": "test-model", "api_key": "key"},
            call_fn=lambda slot_name, messages, **kwargs: {
                "content": "",
                "error": True,
                "error_message": "All models down",
                "latency_ms": 0,
            },
        )
        container.artifact_store = AsyncMock()

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        assert result.status == "error"
        assert len(result.failed_models) == 2


# ── 5. No secret exposure tests ──────────────────────────────────────


class TestModelCouncilNoSecrets:
    """Verify Model Council endpoints never expose secrets."""

    def test_response_has_no_api_key(self):
        """ModelCouncilResponse never contains API keys."""
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse(status="completed")
        resp_dict = resp.model_dump()
        for key, value in resp_dict.items():
            if isinstance(value, str):
                assert "api_key" not in key.lower()
                assert "password" not in key.lower()
                assert "token" not in key.lower()
                assert "secret" not in key.lower()

    def test_per_model_result_no_secrets(self):
        """PerModelResult never contains API keys or secrets."""
        from aip.adapter.api.routes.model_council import PerModelResult

        result = PerModelResult(model_slot="synthesis", model_id="gpt-4", status="completed")
        result_dict = result.model_dump()
        serialized = json.dumps(result_dict)
        assert "api_key" not in serialized.lower()
        assert "password" not in serialized.lower()
        assert "sk-" not in serialized

    def test_response_no_secret_patterns(self):
        """Serialized response doesn't contain secret patterns."""
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse(status="completed", convergence="Models agree")
        serialized = json.dumps(resp.model_dump())
        assert "sk-" not in serialized
        assert "password" not in serialized.lower()
        assert "api_key" not in serialized.lower()


# ── 6. Save-as-artifact sovereignty tests ────────────────────────────


class TestSaveAsArtifactSovereignty:
    """save_as_artifact creates GENERATED artifact only — no auto-approve/export."""

    @pytest.fixture
    def mock_container_with_provider(self):
        """Create a mock container with full provider and stores."""
        from aip.adapter.api.dependencies import AipContainer

        def resolve_config(slot):
            return {
                "synthesis": {"provider": "test", "model": "gpt-4", "api_key": "key"},
                "evaluation": {"provider": "test", "model": "claude-3", "api_key": "key"},
                "beast": {"provider": "test", "model": "deepseek", "api_key": "key"},
                "embedding": {"provider": "test", "model": "emb-model", "api_key": "key"},
            }.get(slot, {})

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "beast":
                return {
                    "content": json.dumps(
                        {
                            "convergence": "Agree",
                            "disagreements": "None",
                            "unique_contributions": "Each unique",
                            "risks": "Low",
                            "beast_conclusion": "Solid",
                            "recommended_decision": "Proceed",
                        }
                    ),
                    "model": "deepseek",
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            return {
                "content": json.dumps({"answer": f"Response from {slot_name}"}),
                "model": f"model-{slot_name}",
                "usage": {"total_tokens": 100},
                "latency_ms": 800,
                "error": False,
            }

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=resolve_config,
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()
        return container

    @pytest.mark.asyncio
    async def test_save_creates_generated_artifact(self, mock_container_with_provider):
        """save_as_artifact=True creates a GENERATED artifact."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test", save_as_artifact=True)
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=mock_container_with_provider)

        assert result.artifact_id != ""
        mock_container_with_provider.artifact_store.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_does_not_auto_approve(self, mock_container_with_provider):
        """Saved artifact gets ECS GENERATED state, never APPROVED."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test", save_as_artifact=True)
        with patch("aip.adapter.api.routes.model_council.logger"):
            await compare_models(request, container=mock_container_with_provider)

        # Verify ECS transition was GENERATED, never APPROVED
        ecs_call = mock_container_with_provider.ecs_store.transition.call_args
        to_state = ecs_call.kwargs.get("to_state") or ecs_call[1].get("to_state")
        assert to_state == "GENERATED", f"ECS state was {to_state}, expected GENERATED"

    @pytest.mark.asyncio
    async def test_no_auto_approve_or_export(self, mock_container_with_provider):
        """No export/approve methods are called during comparison."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test", save_as_artifact=True)
        with patch("aip.adapter.api.routes.model_council.logger"):
            await compare_models(request, container=mock_container_with_provider)

        # No export/approve related calls on the container
        for attr in dir(mock_container_with_provider):
            if any(word in attr.lower() for word in ["export", "approve"]):
                if not attr.startswith("_"):
                    method = getattr(mock_container_with_provider, attr, None)
                    if callable(method) and hasattr(method, "assert_not_called"):
                        method.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_wiki_mutation(self, mock_container_with_provider):
        """Comparison does not call any wiki mutation methods."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test", save_as_artifact=True)
        with patch("aip.adapter.api.routes.model_council.logger"):
            await compare_models(request, container=mock_container_with_provider)

        # No wiki-related method should be called
        for attr in dir(mock_container_with_provider):
            if "wiki" in attr.lower() and not attr.startswith("_"):
                method = getattr(mock_container_with_provider, attr, None)
                if callable(method) and hasattr(method, "assert_not_called"):
                    method.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_false_does_not_create_artifact(self, mock_container_with_provider):
        """save_as_artifact=False does not create an artifact."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        request = ModelCouncilRequest(prompt="Test", save_as_artifact=False)
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=mock_container_with_provider)

        assert result.artifact_id == ""
        mock_container_with_provider.artifact_store.write.assert_not_called()


# ── 7. GUI ModelCouncilPanel import test ────────────────────────────


class TestModelCouncilPanelImport:
    """ModelCouncilPanel can be imported without starting a server."""

    def test_import_model_council_panel(self):
        """ModelCouncilPanel imports cleanly without NiceGUI server."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        assert panel is not None
        assert hasattr(panel, "show_council")
        assert hasattr(panel, "close")

    def test_panel_initial_state(self):
        """ModelCouncilPanel starts with no drawer and no report."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        assert panel._drawer is None
        assert panel._loading is False
        assert panel._last_report is None

    def test_panel_close_no_error(self):
        """ModelCouncilPanel.close() doesn't raise when no drawer is open."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        panel.close()  # Should not raise


# ── 8. GUI panel state handling tests ────────────────────────────────


class TestModelCouncilPanelStates:
    """ModelCouncilPanel handles all honest states (via render method signatures)."""

    def test_panel_has_render_methods(self):
        """ModelCouncilPanel has render methods for all states."""
        from gui.components.model_council_panel import ModelCouncilPanel

        panel = ModelCouncilPanel()
        assert hasattr(panel, "_render_insufficient_models")
        assert hasattr(panel, "_render_error")
        assert hasattr(panel, "_render_report")
        assert hasattr(panel, "_render_per_model_results")


# ── 9. API client method test ───────────────────────────────────────


class TestModelCouncilAPIClient:
    """API client has Model Council methods."""

    def test_client_has_run_model_council(self):
        """AipApiClient has run_model_council method."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "run_model_council")
        assert callable(client.run_model_council)

    def test_run_model_council_accepts_params(self):
        """run_model_council accepts all required parameters."""
        import inspect

        from gui.api_client import AipApiClient

        sig = inspect.signature(AipApiClient.run_model_council)
        params = sig.parameters
        assert "prompt" in params
        assert "turn_id" in params
        assert "session_id" in params
        assert "existing_answer" in params
        assert "sources" in params
        assert "selected_model_slots" in params
        assert "save_as_artifact" in params


# ── 10. TypedDict types test ────────────────────────────────────────


class TestModelCouncilTypes:
    """Model Council types exist in status_types module."""

    def test_model_council_response_type_exists(self):
        """ModelCouncilResponse TypedDict exists in status_types."""
        from gui.status_types import ModelCouncilResponse

        assert ModelCouncilResponse is not None

    def test_per_model_result_type_exists(self):
        """PerModelResult TypedDict exists in status_types."""
        from gui.status_types import PerModelResult

        assert PerModelResult is not None


# ── 11. Route registration test ──────────────────────────────────────


class TestModelCouncilRouteRegistration:
    """Model Council route is properly registered in the app."""

    def test_model_council_router_exists(self):
        """model_council module has a router attribute."""
        from aip.adapter.api.routes import model_council

        assert hasattr(model_council, "router")
        routes = [r.path for r in model_council.router.routes]
        assert "/beast/compare-models" in routes

    def test_model_council_route_importable(self):
        """model_council route module can be imported."""
        from aip.adapter.api.routes import model_council

        assert model_council is not None


# ── 12. Answer card Model Council action test ────────────────────────


class TestAnswerCardModelCouncil:
    """Answer card includes Model Council button wiring."""

    def test_add_answer_card_accepts_model_council_callback(self):
        """add_answer_card function accepts on_run_model_council parameter."""
        import inspect

        from gui.components.answer_card import add_answer_card

        sig = inspect.signature(add_answer_card)
        assert "on_run_model_council" in sig.parameters


# ── 13. GUI import boundary test ─────────────────────────────────────


class TestGUIImportBoundary:
    """GUI modules must never import from aip.orchestration."""

    GUI_MODULES = [
        "gui.components.beast_panel",
        "gui.components.model_council_panel",
        "gui.components.answer_card",
        "gui.components.source_panel",
        "gui.components.trace_panel",
        "gui.api_client",
        "gui.state",
        "gui.status_types",
    ]

    def test_model_council_panel_no_orchestration_imports(self):
        """model_council_panel.py does not import from aip.orchestration."""
        source = (PROJECT_ROOT / "gui/components/model_council_panel.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), f"Found orchestration import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip.orchestration"):
                    pytest.fail(f"Found orchestration import from: {node.module}")

    def test_all_gui_modules_no_orchestration_imports(self):
        """No GUI module imports from aip.orchestration."""
        for module_name in self.GUI_MODULES:
            try:
                source_path = PROJECT_ROOT / (module_name.replace(".", "/") + ".py")
                source = source_path.read_text()
            except FileNotFoundError:
                continue
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("aip.orchestration"), (
                            f"{module_name} imports from aip.orchestration: {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("aip.orchestration"):
                        pytest.fail(f"{module_name} imports from aip.orchestration: {node.module}")


# ── 14. Backend import boundary test ─────────────────────────────────


class TestBackendImportBoundary:
    """Model Council route must not import from orchestration."""

    def test_model_council_route_no_orchestration(self):
        """model_council.py does not import from aip.orchestration."""
        source = (PROJECT_ROOT / "src/aip/adapter/api/routes/model_council.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aip.orchestration"), f"Found orchestration import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("aip.orchestration"):
                    pytest.fail(f"Found orchestration import from: {node.module}")


# ── 15. Existing Beast Counsel tests still pass ──────────────────────


class TestExistingBeastCounselStillPasses:
    """Verify Beast Counsel functionality still works after Model Council changes."""

    def test_beast_commentary_importable(self):
        """Beast commentary route module can still be imported."""
        from aip.adapter.api.routes import beast_commentary

        assert beast_commentary is not None

    def test_beast_panel_importable(self):
        """BeastPanel can still be imported."""
        from gui.components.beast_panel import BeastPanel

        panel = BeastPanel()
        assert panel is not None

    def test_beast_panel_has_modes(self):
        """BeastPanel still has all modes."""
        from gui.components.beast_panel import BEAST_MODES

        assert len(BEAST_MODES) == 5
        assert "continuity" in BEAST_MODES
        assert "critique" in BEAST_MODES

    def test_beast_api_client_methods(self):
        """API client still has Beast commentary methods."""
        from gui.api_client import AipApiClient

        client = AipApiClient()
        assert hasattr(client, "get_beast_commentary")
        assert hasattr(client, "run_beast_commentary")


# ── 16. Beast synthesis unavailable test ──────────────────────────────


class TestBeastSynthesisUnavailable:
    """If Beast synthesis is unavailable, return per-model results with honest state."""

    @pytest.mark.asyncio
    async def test_synthesis_unavailable_when_beast_fails(self):
        """Phase 1 Fix D: when the ``beast`` slot fails in the panel, the
        Fusion engine falls back to another successful slot (synthesis or
        evaluation). The mock makes beast always return error, but
        synthesis/evaluation return valid JSON — so the Fusion pipeline
        runs on the fallback engine and ``synthesis_status`` is
        ``"completed"`` (not ``"failed"`` as in the pre-Fix-D behavior).

        The per-model results still show beast as ``"failed"`` so the
        human can see which panelist didn't respond.
        """
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "beast":
                return {
                    "content": "",
                    "error": True,
                    "error_message": "Beast provider down",
                    "model": "beast-model",
                    "latency_ms": 100,
                }
            return {
                "content": json.dumps({"answer": f"Response from {slot_name}"}),
                "model": f"model-{slot_name}",
                "usage": {"total_tokens": 100},
                "latency_ms": 800,
                "error": False,
            }

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        # Fix D: beast failed in the panel, but synthesis+evaluation
        # succeeded, so the Fusion engine falls back to synthesis (or
        # evaluation). The mock returns valid JSON for those slots, so
        # the Judge+Synth stages succeed → synthesis_status="completed".
        assert result.synthesis_status == "completed"
        # Per-model results: synthesis + evaluation completed, beast failed
        completed = [m for m in result.selected_models if m.status == "completed"]
        assert len(completed) >= 2  # synthesis + evaluation
        failed = [m for m in result.selected_models if m.status == "failed"]
        assert any(m.model_slot == "beast" for m in failed)

    @pytest.mark.asyncio
    async def test_synthesis_unavailable_when_only_one_succeeds(self):
        """Returns synthesis_status='unavailable' when only one model succeeds."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "evaluation":
                return {"content": "", "error": True, "error_message": "Failed", "latency_ms": 100}
            elif slot_name == "beast":
                return {"content": "", "error": True, "error_message": "Down", "latency_ms": 0}
            return {
                "content": "Only synthesis answered",
                "model": "gpt-4",
                "usage": {"total_tokens": 50},
                "latency_ms": 500,
                "error": False,
            }

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        # Only one model succeeded — can't compare
        assert result.synthesis_status == "unavailable"
        assert "only one" in result.beast_conclusion.lower() or "Only one" in result.beast_conclusion


# ── 17. Custom slot selection test ───────────────────────────────────


class TestCustomSlotSelection:
    """Test that caller can specify which model slots to compare."""

    @pytest.mark.asyncio
    async def test_selected_model_slots_filter(self):
        """Caller can specify which slots to use for comparison."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "beast":
                return {
                    "content": json.dumps(
                        {
                            "convergence": "Agree",
                            "disagreements": "None",
                            "unique_contributions": "Both unique",
                            "risks": "Low",
                            "beast_conclusion": "Good",
                            "recommended_decision": "Accept",
                        }
                    ),
                    "model": "beast-model",
                    "usage": {},
                    "latency_ms": 500,
                    "error": False,
                }
            return {
                "content": f"Response from {slot_name}",
                "model": f"model-{slot_name}",
                "usage": {"total_tokens": 50},
                "latency_ms": 300,
                "error": False,
            }

        container = AipContainer({})
        container.model_provider = _make_mock_provider(
            slots=["synthesis", "evaluation", "beast", "embedding"],
            resolve_config=lambda slot: {"provider": "test", "model": f"model-{slot}", "api_key": "key"},
            call_fn=mock_call,
        )
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        # Request comparison with only synthesis and evaluation
        request = ModelCouncilRequest(
            prompt="Test",
            selected_model_slots=["synthesis", "evaluation"],
        )
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        # Should use synthesis and evaluation for comparison
        model_slots = [m.model_slot for m in result.selected_models]
        assert "synthesis" in model_slots
        assert "evaluation" in model_slots
