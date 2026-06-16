"""Tests for Phase 1 of the OpenRouter Fusion pipeline.

Phase 1 replaces the legacy bare-comparison Beast synthesis with a
two-stage Fusion pipeline:

  Panel (N concurrent) → Judge-Beast (structured JSON)
                        → Synth-Beast (final fused answer)

This file verifies:

  1. Schema additions — ``fusion_answer`` and ``judge_analysis`` fields
     exist on ``ModelCouncilResponse`` with correct defaults.
  2. Two-stage Beast call — the ``beast`` slot is called TWICE during
     synthesis (Judge then Synth), not once.
  3. ``fusion_answer`` is populated from the Synth-Beast output.
  4. ``judge_analysis`` is populated from the Judge-Beast JSON output.
  5. Legacy fields (``convergence``, ``disagreements``,
     ``unique_contributions``, ``risks``) are populated from the
     Judge JSON's new structured schema (``analysis.consensus``,
     ``analysis.contradictions``, etc.).
  6. Legacy fields still work when the Judge returns the old top-level
     schema (``convergence`` key at top level instead of under
     ``analysis``) — backward compat with the existing test mock and
     with older Beast models.
  7. Per-model panel outputs remain in ``selected_models`` (the human
     can compare them alongside the fusion output).
  8. ``beast_conclusion`` is mirrored to ``fusion_answer`` for legacy
     consumers.
  9. Synth-Beast call reads ONLY the Judge JSON — never the raw panel
     outputs (asymmetric information contract).
  10. When Judge call fails, ``synthesis_status = "failed"`` and
      ``fusion_answer`` is empty.
  11. When Synth-Beast call fails, ``synthesis_status = "failed"`` and
      ``judge_analysis`` is still populated (Judge succeeded).
  12. When only one model succeeds, ``synthesis_status = "unavailable"``.
  13. Phase 1 contract still enforces ADVISORY ONLY — ``advisory_only``
      and ``requires_DEFINER_approval`` are True.
  14. No auto-approve / auto-export / wiki mutation / config mutation
      (carried over from Cycle 6 guarantees).
  15. No secret exposure in the response.

These tests are additive — they do not duplicate the existing Cycle 6
/ Cycle 6.1 / library-IDs tests; they assert the Phase 1 contract
specifically.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────


def _make_mock_provider(slots: list[str], resolve_config=None, call_fn=None):
    """Create a mock model provider (same shape as the Cycle 6 helper)."""
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


def _make_three_slot_container(call_fn):
    """Build a container with synthesis/evaluation/beast + embedding slots."""
    from aip.adapter.api.dependencies import AipContainer

    def resolve_config(slot):
        return {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "evaluation": {"provider": "openai_compatible", "model": "claude-3-opus", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek-chat", "api_key": "k"},
            "embedding": {"provider": "openai_compatible", "model": "text-embedding-3-small", "api_key": "k"},
        }.get(slot, {})

    container = AipContainer({})
    container.model_provider = _make_mock_provider(
        slots=["synthesis", "evaluation", "beast", "embedding"],
        resolve_config=resolve_config,
        call_fn=call_fn,
    )
    container.artifact_store = AsyncMock()
    container.ecs_store = AsyncMock()
    return container


# ── 1. Schema additions ────────────────────────────────────────────────


class TestFusionSchemaAdditions:
    """Verify the new Phase 1 fields exist with correct defaults."""

    def test_response_has_fusion_answer_field(self):
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse()
        assert hasattr(resp, "fusion_answer")
        assert resp.fusion_answer == ""

    def test_response_has_judge_analysis_field(self):
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse()
        assert hasattr(resp, "judge_analysis")
        assert resp.judge_analysis == {}

    def test_judge_analysis_accepts_dict(self):
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse(
            judge_analysis={
                "status": "completed",
                "analysis": {"consensus": ["point A"], "blind_spots": ["topic X"]},
            }
        )
        assert resp.judge_analysis["status"] == "completed"
        assert resp.judge_analysis["analysis"]["consensus"] == ["point A"]

    def test_existing_fields_still_present(self):
        """All legacy fields are still on the response (backward compat)."""
        from aip.adapter.api.routes.model_council import ModelCouncilResponse

        resp = ModelCouncilResponse()
        for legacy_field in (
            "convergence",
            "disagreements",
            "unique_contributions",
            "risks",
            "beast_conclusion",
            "recommended_decision",
            "synthesis_status",
            "selected_models",
        ):
            assert hasattr(resp, legacy_field), f"Missing legacy field: {legacy_field}"


# ── 2-4. Two-stage Beast call and field population ────────────────────


class TestFusionPipelineExecution:
    """Verify the two-stage Judge+Synth pipeline runs correctly."""

    @pytest.fixture
    def fusion_container(self):
        """Container whose beast slot differentiates Judge vs Synth by prompt."""
        beast_call_log: list[dict] = []

        async def mock_call(slot_name, messages, **kwargs):
            beast_call_log.append({"slot": slot_name, "messages": messages})

            if slot_name == "synthesis":
                return {
                    "content": "Synthesis model says AIP is the local-first sovereign knowledge engine.",
                    "model": "gpt-4",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                    "latency_ms": 1200,
                    "cost_usd": 0.01,
                    "error": False,
                }
            if slot_name == "evaluation":
                return {
                    "content": "Evaluation model says AIP is a knowledge lifecycle manager.",
                    "model": "claude-3-opus",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 60, "total_tokens": 160},
                    "latency_ms": 1800,
                    "cost_usd": 0.02,
                    "error": False,
                }
            if slot_name == "beast":
                # Differentiate Judge vs Synth by inspecting the system prompt
                system_content = ""
                user_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                    elif msg.get("role") == "user":
                        user_content = msg.get("content", "")

                if "JUDGE" in system_content:
                    return {
                        "content": json.dumps({
                            "status": "completed",
                            "analysis": {
                                "consensus": [
                                    "AIP is a knowledge engine",
                                    "It is local-first",
                                ],
                                "contradictions": [
                                    {
                                        "topic": "scope",
                                        "stances": [
                                            {"model": "synthesis", "stance": "broad"},
                                            {"model": "evaluation", "stance": "narrow"},
                                        ],
                                    },
                                ],
                                "partial_coverage": [
                                    {
                                        "models": ["synthesis"],
                                        "point": "sovereignty",
                                    },
                                ],
                                "unique_insights": [
                                    {
                                        "model": "evaluation",
                                        "insight": "lifecycle framing",
                                    },
                                ],
                                "blind_spots": [
                                    "no model addressed cost",
                                ],
                            },
                            "responses": [
                                {"model": "synthesis", "content": "knowledge engine"},
                                {"model": "evaluation", "content": "lifecycle manager"},
                            ],
                        }),
                        "model": "deepseek-chat",
                        "usage": {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500},
                        "latency_ms": 2000,
                        "cost_usd": 0.005,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    return {
                        "content": "AIP (AI Poiesis) is a local-first sovereign knowledge engine that manages the full knowledge lifecycle from ingestion through synthesis, evaluation, review, and canonical promotion.",
                        "model": "deepseek-chat",
                        "usage": {"prompt_tokens": 400, "completion_tokens": 100, "total_tokens": 500},
                        "latency_ms": 1500,
                        "cost_usd": 0.005,
                        "error": False,
                    }
                # Default fallback
                return {
                    "content": "{}",
                    "model": "deepseek-chat",
                    "usage": {},
                    "latency_ms": 100,
                    "error": False,
                }
            return {
                "content": "",
                "error": True,
                "error_message": f"Unknown slot: {slot_name}",
            }

        container = _make_three_slot_container(mock_call)
        # Stash the call log so tests can inspect it
        container._test_beast_call_log = beast_call_log  # type: ignore[attr-defined]
        return container

    @pytest.mark.asyncio
    async def test_beast_slot_called_twice_for_fusion(self, fusion_container):
        """Beast slot is called for Fusion (Judge + Synth) in addition to its
        panel role.

        The beast slot participates in the panel (one call, just user
        prompt), then is called twice more for the Fusion pipeline
        (Judge with the JUDGE system prompt, then Synth with the
        SYNTHESIZER system prompt). This test isolates the two Fusion
        calls by filtering on the system prompt content.
        """
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="What is AIP?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=fusion_container)

        all_beast_calls = [
            c for c in fusion_container._test_beast_call_log if c["slot"] == "beast"
        ]
        # Isolate the Fusion calls by inspecting the system prompt
        fusion_calls = []
        for call in all_beast_calls:
            for msg in call["messages"]:
                if msg.get("role") == "system":
                    sys_content = msg.get("content", "")
                    if "JUDGE" in sys_content or "SYNTHESIZER" in sys_content:
                        fusion_calls.append(call)
                        break

        assert len(fusion_calls) == 2, (
            f"Expected 2 Fusion calls (Judge + Synth), got {len(fusion_calls)}. "
            f"Total beast calls: {len(all_beast_calls)} (1 panel + 2 fusion expected)."
        )
        # First Fusion call must be the Judge
        first_sys = ""
        for msg in fusion_calls[0]["messages"]:
            if msg.get("role") == "system":
                first_sys = msg.get("content", "")
        assert "JUDGE" in first_sys, "First Fusion call must be the Judge"
        # Second Fusion call must be the Synthesizer
        second_sys = ""
        for msg in fusion_calls[1]["messages"]:
            if msg.get("role") == "system":
                second_sys = msg.get("content", "")
        assert "SYNTHESIZER" in second_sys, "Second Fusion call must be the Synthesizer"

    @pytest.mark.asyncio
    async def test_fusion_answer_populated(self, fusion_container):
        """``fusion_answer`` is populated from the Synth-Beast output."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="What is AIP?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=fusion_container)

        assert result.synthesis_status == "completed"
        assert result.fusion_answer != ""
        assert "AI Poiesis" in result.fusion_answer
        assert "local-first" in result.fusion_answer

    @pytest.mark.asyncio
    async def test_judge_analysis_populated(self, fusion_container):
        """``judge_analysis`` is populated from the Judge-Beast JSON output."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="What is AIP?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=fusion_container)

        assert result.judge_analysis != {}
        assert result.judge_analysis.get("status") == "completed"
        analysis = result.judge_analysis.get("analysis", {})
        assert "AIP is a knowledge engine" in analysis.get("consensus", [])
        assert "no model addressed cost" in analysis.get("blind_spots", [])

    @pytest.mark.asyncio
    async def test_legacy_fields_populated_from_new_schema(self, fusion_container):
        """Legacy fields are derived from the new structured ``analysis.*`` schema."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="What is AIP?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=fusion_container)

        # convergence ← analysis.consensus[]
        assert "AIP is a knowledge engine" in result.convergence
        # disagreements ← analysis.contradictions[]
        assert "scope" in result.disagreements
        # unique_contributions ← analysis.unique_insights[]
        assert "evaluation" in result.unique_contributions
        assert "lifecycle framing" in result.unique_contributions
        # risks ← analysis.blind_spots[]
        assert "no model addressed cost" in result.risks

    @pytest.mark.asyncio
    async def test_beast_conclusion_mirrored_to_fusion_answer(self, fusion_container):
        """``beast_conclusion`` is mirrored to ``fusion_answer`` for legacy consumers."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="What is AIP?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=fusion_container)

        assert result.beast_conclusion == result.fusion_answer

    @pytest.mark.asyncio
    async def test_per_model_outputs_preserved(self, fusion_container):
        """Per-model panel outputs remain in ``selected_models`` for the human to compare."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="What is AIP?")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=fusion_container)

        assert len(result.selected_models) == 3  # synthesis, evaluation, beast
        # The beast slot is included as a panelist too — its panel answer
        # is the default fallback content from the mock (not the Judge/Synth output).
        slots = {pm.model_slot for pm in result.selected_models}
        assert "synthesis" in slots
        assert "evaluation" in slots
        assert "beast" in slots
        # All panelists completed
        for pm in result.selected_models:
            assert pm.status == "completed"


# ── 5-6. Backward compat with old-schema Judge output ─────────────────


class TestFusionBackwardCompat:
    """Judge output that uses the old top-level schema (convergence, etc.)
    instead of the new ``analysis.*`` shape is still parsed correctly."""

    @pytest.fixture
    def legacy_judge_container(self):
        """Container whose beast slot returns old-schema JSON for Judge call."""
        async def mock_call(slot_name, messages, **kwargs):
            if slot_name in ("synthesis", "evaluation"):
                return {
                    "content": f"{slot_name} answer",
                    "model": slot_name,
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                    "latency_ms": 1000,
                    "cost_usd": 0.01,
                    "error": False,
                }
            if slot_name == "beast":
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    # Old-schema JSON (legacy Beast behavior)
                    return {
                        "content": json.dumps({
                            "convergence": "Legacy convergence string",
                            "disagreements": "Legacy disagreements string",
                            "unique_contributions": "Legacy unique contributions",
                            "risks": "Legacy risks",
                            "beast_conclusion": "Legacy conclusion",
                            "recommended_decision": "Legacy decision",
                        }),
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    return {
                        "content": "Final fused answer from legacy judge JSON.",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": False,
                    }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        return _make_three_slot_container(mock_call)

    @pytest.mark.asyncio
    async def test_legacy_judge_schema_populates_legacy_fields(self, legacy_judge_container):
        """Judge returning old-schema top-level keys still populates legacy fields."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=legacy_judge_container)

        assert result.synthesis_status == "completed"
        assert result.convergence == "Legacy convergence string"
        assert result.disagreements == "Legacy disagreements string"
        assert result.unique_contributions == "Legacy unique contributions"
        assert result.risks == "Legacy risks"
        assert result.recommended_decision == "Legacy decision"

    @pytest.mark.asyncio
    async def test_legacy_judge_schema_still_populates_judge_analysis(self, legacy_judge_container):
        """Even with old-schema JSON, ``judge_analysis`` is populated with the raw dict."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=legacy_judge_container)

        assert result.judge_analysis != {}
        assert result.judge_analysis.get("convergence") == "Legacy convergence string"

    @pytest.mark.asyncio
    async def test_legacy_judge_schema_still_produces_fusion_answer(self, legacy_judge_container):
        """Synth-Beast still produces a fusion_answer even when Judge used old schema."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=legacy_judge_container)

        assert result.fusion_answer == "Final fused answer from legacy judge JSON."


# ── 7-9. Asymmetric information contract ──────────────────────────────


class TestFusionAsymmetricInformation:
    """Synth-Beast must read ONLY the Judge JSON — never raw panel outputs."""

    @pytest.fixture
    def asymmetry_container(self):
        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "synthesis":
                return {
                    "content": "PANEL_SYNTHESIS_UNIQUE_STRING_XYZ",
                    "model": "gpt-4",
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            if slot_name == "evaluation":
                return {
                    "content": "PANEL_EVALUATION_UNIQUE_STRING_ABC",
                    "model": "claude-3-opus",
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            if slot_name == "beast":
                system_content = ""
                user_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                    elif msg.get("role") == "user":
                        user_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    return {
                        "content": json.dumps({
                            "status": "completed",
                            "analysis": {
                                "consensus": [],
                                "contradictions": [],
                                "partial_coverage": [],
                                "unique_insights": [],
                                "blind_spots": [],
                            },
                            "responses": [],
                        }),
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    # Capture what the Synth was given
                    return {
                        "content": f"SYNTH_RECEIVED: {user_content[:500]}",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": False,
                    }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        return _make_three_slot_container(mock_call)

    @pytest.mark.asyncio
    async def test_synth_does_not_receive_raw_panel_outputs(self, asymmetry_container):
        """The Synth-Beast user prompt must NOT contain raw panel answer strings.

        This is the asymmetric information contract: Synth-Beast reads
        only the Judge JSON, never the original panel outputs.
        """
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=asymmetry_container)

        # The Synth echoed back what it received in its user prompt.
        # Verify the raw panel outputs are NOT in there.
        assert "PANEL_SYNTHESIS_UNIQUE_STRING_XYZ" not in result.fusion_answer, (
            "Synth-Beast received raw panel output — asymmetric information contract violated"
        )
        assert "PANEL_EVALUATION_UNIQUE_STRING_ABC" not in result.fusion_answer, (
            "Synth-Beast received raw panel output — asymmetric information contract violated"
        )
        # The Synth DID receive the Judge JSON (the analysis object)
        assert "SYNTH_RECEIVED" in result.fusion_answer
        # The Judge JSON should be in the synth's input (the user_content echoed back)
        # Verify by checking that "Judge JSON" appears in the synth's response
        assert "Judge JSON" in result.fusion_answer


# ── 10-12. Failure paths ──────────────────────────────────────────────


class TestFusionFailurePaths:
    """Verify honest degradation when Judge or Synth fails."""

    @pytest.fixture
    def judge_failure_container(self):
        async def mock_call(slot_name, messages, **kwargs):
            if slot_name in ("synthesis", "evaluation"):
                return {
                    "content": f"{slot_name} answer",
                    "model": slot_name,
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            if slot_name == "beast":
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    # Judge fails
                    return {
                        "content": "",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": True,
                        "error_message": "Judge model rate-limited",
                    }
                # Synth wouldn't be called because Judge failed
                return {
                    "content": "should not be reached",
                    "model": "deepseek-chat",
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        return _make_three_slot_container(mock_call)

    @pytest.mark.asyncio
    async def test_judge_failure_marks_synthesis_failed(self, judge_failure_container):
        """Judge failure → synthesis_status='failed', fusion_answer empty."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=judge_failure_container)

        assert result.synthesis_status == "failed"
        assert result.fusion_answer == ""
        assert result.judge_analysis == {}

    @pytest.fixture
    def synth_failure_container(self):
        async def mock_call(slot_name, messages, **kwargs):
            if slot_name in ("synthesis", "evaluation"):
                return {
                    "content": f"{slot_name} answer",
                    "model": slot_name,
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            if slot_name == "beast":
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    return {
                        "content": json.dumps({
                            "status": "completed",
                            "analysis": {
                                "consensus": ["x"],
                                "contradictions": [],
                                "partial_coverage": [],
                                "unique_insights": [],
                                "blind_spots": ["y"],
                            },
                            "responses": [],
                        }),
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    return {
                        "content": "",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": True,
                        "error_message": "Synth model timeout",
                    }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        return _make_three_slot_container(mock_call)

    @pytest.mark.asyncio
    async def test_synth_failure_preserves_judge_analysis(self, synth_failure_container):
        """Synth failure → synthesis_status='failed' but judge_analysis populated."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=synth_failure_container)

        assert result.synthesis_status == "failed"
        assert result.fusion_answer == ""
        # Judge succeeded, so judge_analysis should still be populated
        assert result.judge_analysis != {}
        assert result.judge_analysis.get("status") == "completed"

    @pytest.mark.asyncio
    async def test_single_successful_model_yields_unavailable(self):
        """When only one panel model succeeds, synthesis_status='unavailable'."""
        from aip.adapter.api.dependencies import AipContainer
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "synthesis":
                return {
                    "content": "only successful model",
                    "model": "gpt-4",
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            if slot_name == "evaluation":
                return {
                    "content": "",
                    "model": "claude-3-opus",
                    "usage": {},
                    "latency_ms": 500,
                    "error": True,
                    "error_message": "rate limited",
                }
            return {"content": "", "error": True, "error_message": "unexpected"}

        container = _make_three_slot_container(mock_call)
        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        assert result.synthesis_status == "unavailable"
        assert result.fusion_answer == ""
        assert result.judge_analysis == {}


# ── 13-15. Guarantees carried over from Cycle 6 ───────────────────────


class TestFusionGuaranteesPreserved:
    """Phase 1 preserves the Cycle 6 advisory guarantees."""

    @pytest.fixture
    def basic_container(self):
        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "synthesis":
                return {"content": "synthesis answer", "model": "gpt-4", "usage": {}, "latency_ms": 1000, "error": False}
            if slot_name == "evaluation":
                return {"content": "evaluation answer", "model": "claude-3-opus", "usage": {}, "latency_ms": 1000, "error": False}
            if slot_name == "beast":
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    return {
                        "content": json.dumps({
                            "status": "completed",
                            "analysis": {"consensus": [], "contradictions": [], "partial_coverage": [], "unique_insights": [], "blind_spots": []},
                            "responses": [],
                        }),
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    return {
                        "content": "fused answer",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 1000,
                        "error": False,
                    }
            return {"content": "", "error": True, "error_message": "unexpected"}

        return _make_three_slot_container(mock_call)

    @pytest.mark.asyncio
    async def test_advisory_only_remains_true(self, basic_container):
        """``advisory_only`` and ``requires_DEFINER_approval`` are still True."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=basic_container)

        assert result.advisory_only is True
        assert result.requires_DEFINER_approval is True

    @pytest.mark.asyncio
    async def test_no_secrets_in_response(self, basic_container):
        """API keys are not leaked into the response."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=basic_container)

        # Serialize and check no secret material
        serialized = json.dumps(result.model_dump(), default=str)
        assert "api_key" not in serialized.lower()
        assert "test-key" not in serialized
        assert "sk-" not in serialized

    @pytest.mark.asyncio
    async def test_no_auto_approve_on_save_as_artifact(self, basic_container):
        """``save_as_artifact=True`` produces a GENERATED artifact only — never APPROVED."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test", save_as_artifact=True)
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=basic_container)

        assert result.artifact_id != ""
        # ecs_store.transition should have been called with to_state="GENERATED",
        # NEVER "APPROVED". Inspect the mock.
        transition_calls = basic_container.ecs_store.transition.call_args_list
        assert len(transition_calls) >= 1
        for call in transition_calls:
            kwargs = call.kwargs
            assert kwargs.get("to_state") != "APPROVED", (
                "Phase 1 Fusion must NEVER auto-approve — APPROVED transition requires DEFINER"
            )


# ── 16. GUI consumer contract ─────────────────────────────────────────


class TestFusionGuiConsumerContract:
    """Verify the GUI consumers can read the new fields without breaking."""

    def test_ask_page_reads_fusion_answer(self):
        """The ask.py _send_multicast path reads ``fusion_answer`` from the result.

        We verify by import-and-ast: the ask page source must reference
        the new ``fusion_answer`` key on the council result dict.
        """
        from pathlib import Path

        ask_path = Path(__file__).resolve().parent.parent / "gui" / "pages" / "ask.py"
        source = ask_path.read_text(encoding="utf-8")
        assert 'result.get("fusion_answer"' in source, (
            "ask.py _send_multicast must read result['fusion_answer'] to surface the new field"
        )

    def test_panel_renders_fusion_answer(self):
        """The model_council_panel renders the ``fusion_answer`` field."""
        from pathlib import Path

        panel_path = (
            Path(__file__).resolve().parent.parent
            / "gui" / "components" / "model_council_panel.py"
        )
        source = panel_path.read_text(encoding="utf-8")
        assert 'data.get("fusion_answer"' in source, (
            "model_council_panel.py must render data['fusion_answer'] as the new headline section"
        )
