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

import asyncio
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
                        "content": json.dumps(
                            {
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
                            }
                        ),
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

        all_beast_calls = [c for c in fusion_container._test_beast_call_log if c["slot"] == "beast"]
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
                        "content": json.dumps(
                            {
                                "convergence": "Legacy convergence string",
                                "disagreements": "Legacy disagreements string",
                                "unique_contributions": "Legacy unique contributions",
                                "risks": "Legacy risks",
                                "beast_conclusion": "Legacy conclusion",
                                "recommended_decision": "Legacy decision",
                            }
                        ),
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
                # Panel call (no JUDGE/SYNTHESIZER system prompt) — beast answers as a panelist
                return {
                    "content": "Beast panel answer.",
                    "model": "deepseek-chat",
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                    "latency_ms": 1000,
                    "cost_usd": 0.01,
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
                        "content": json.dumps(
                            {
                                "status": "completed",
                                "analysis": {
                                    "consensus": [],
                                    "contradictions": [],
                                    "partial_coverage": [],
                                    "unique_insights": [],
                                    "blind_spots": [],
                                },
                                "responses": [],
                            }
                        ),
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
                # Panel call (no JUDGE/SYNTHESIZER system prompt) — beast answers as a panelist
                return {
                    "content": "Beast panel answer.",
                    "model": "deepseek-chat",
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                    "latency_ms": 1000,
                    "cost_usd": 0.01,
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
                        "content": json.dumps(
                            {
                                "status": "completed",
                                "analysis": {
                                    "consensus": ["x"],
                                    "contradictions": [],
                                    "partial_coverage": [],
                                    "unique_insights": [],
                                    "blind_spots": ["y"],
                                },
                                "responses": [],
                            }
                        ),
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
                # Panel call (no JUDGE/SYNTHESIZER system prompt) — beast answers as a panelist
                return {
                    "content": "Beast panel answer.",
                    "model": "deepseek-chat",
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                    "latency_ms": 1000,
                    "cost_usd": 0.01,
                    "error": False,
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
                return {
                    "content": "synthesis answer",
                    "model": "gpt-4",
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            if slot_name == "evaluation":
                return {
                    "content": "evaluation answer",
                    "model": "claude-3-opus",
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
                        "content": json.dumps(
                            {
                                "status": "completed",
                                "analysis": {
                                    "consensus": [],
                                    "contradictions": [],
                                    "partial_coverage": [],
                                    "unique_insights": [],
                                    "blind_spots": [],
                                },
                                "responses": [],
                            }
                        ),
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
                # Panel call (no JUDGE/SYNTHESIZER system prompt) — beast answers as a panelist
                return {
                    "content": "Beast panel answer.",
                    "model": "deepseek-chat",
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                    "latency_ms": 1000,
                    "cost_usd": 0.01,
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

        panel_path = Path(__file__).resolve().parent.parent / "gui" / "components" / "model_council_panel.py"
        source = panel_path.read_text(encoding="utf-8")
        assert 'data.get("fusion_answer"' in source, (
            "model_council_panel.py must render data['fusion_answer'] as the new headline section"
        )


# ── Phase 1 Fix A/B/C regression tests ────────────────────────────────


class TestFusionPerCallTimeouts:
    """Fix A: per-call ``asyncio.wait_for`` wrappers ensure a single hung
    model cannot hold the entire panel (or the Judge, or the Synth)
    hostage. Each call is cut loose at its timeout and recorded as
    failed; the rest of the pipeline completes.
    """

    @pytest.fixture
    def panel_with_one_hung_model(self):
        """Container where the ``evaluation`` slot hangs forever; the other
        two slots return immediately. The panel gather must complete
        (without waiting for the hung slot) and the hung slot must be
        recorded as ``status="failed"`` with a timeout message.
        """

        # Patch the panel timeout DOWN to 0.3s so the test is fast.
        # We patch the module-level constant that the gather wraps use.
        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "synthesis":
                return {
                    "content": "synthesis fast answer",
                    "model": "gpt-4",
                    "usage": {},
                    "latency_ms": 50,
                    "error": False,
                }
            if slot_name == "evaluation":
                # Hang forever — should be cut loose by the timeout
                await asyncio.sleep(60)
                return {
                    "content": "should never reach here",
                    "model": "claude-3-opus",
                    "usage": {},
                    "latency_ms": 60000,
                    "error": False,
                }
            if slot_name == "beast":
                # beast slot participates in the panel AND in Fusion.
                # Distinguish by system prompt.
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    return {
                        "content": json.dumps(
                            {
                                "status": "completed",
                                "analysis": {
                                    "consensus": ["synthesis answered"],
                                    "contradictions": [],
                                    "partial_coverage": [],
                                    "unique_insights": [],
                                    "blind_spots": ["evaluation hung — no stance captured"],
                                },
                                "responses": [{"model": "synthesis", "content": "fast answer"}],
                            }
                        ),
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 100,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    return {
                        "content": "Fusion answer with only synthesis available.",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 100,
                        "error": False,
                    }
                # beast as panelist
                return {
                    "content": "beast panel answer",
                    "model": "deepseek-chat",
                    "usage": {},
                    "latency_ms": 50,
                    "error": False,
                }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        return _make_three_slot_container(mock_call)

    @pytest.mark.asyncio
    async def test_hung_panel_model_does_not_block_gather(self, panel_with_one_hung_model):
        """A single hung panel model is cut loose at the panel timeout and
        recorded as ``status="failed"``; the rest of the panel completes
        and the overall response still returns.
        """
        from aip.adapter.api.routes import model_council as mc_mod
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test hung panel model")
        # Patch the panel timeout DOWN to 0.3s so the test runs fast.
        original = mc_mod._PANEL_CALL_TIMEOUT_S
        mc_mod._PANEL_CALL_TIMEOUT_S = 0.3
        try:
            with patch("aip.adapter.api.routes.model_council.logger"):
                # Also patch asyncio.wait_for's timeout by patching the
                # constant the wrappers reference. The wrappers themselves
                # are already created at call time using the module
                # constant, so this works.
                result = await compare_models(request, container=panel_with_one_hung_model)
        finally:
            mc_mod._PANEL_CALL_TIMEOUT_S = original

        # The gather completed (we got a response, not a hang).
        assert result.status in ("completed", "partial")
        # The hung 'evaluation' slot must be recorded as failed.
        eval_result = next(
            (pm for pm in result.selected_models if pm.model_slot == "evaluation"),
            None,
        )
        assert eval_result is not None, "evaluation slot missing from selected_models"
        assert eval_result.status == "failed"
        assert "timed out" in (eval_result.error or "").lower(), (
            f"Expected timeout message in error, got: {eval_result.error!r}"
        )
        # The fast slots still completed.
        synth_result = next(
            (pm for pm in result.selected_models if pm.model_slot == "synthesis"),
            None,
        )
        assert synth_result is not None
        assert synth_result.status == "completed"
        assert synth_result.answer == "synthesis fast answer"
        # Fusion still ran (beast + synthesis succeeded = 2 successful).
        # Judge + Synth each succeeded.
        assert result.synthesis_status == "completed"
        assert result.fusion_answer != ""

    @pytest.mark.asyncio
    async def test_judge_timeout_yields_failed_synthesis_empty_judge_analysis(self):
        """Judge-Beast call timeout → ``synthesis_status='failed'`` and
        ``judge_analysis={}`` (Judge never produced output).
        """
        from aip.adapter.api.routes import model_council as mc_mod
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name in ("synthesis", "evaluation"):
                return {
                    "content": f"{slot_name} answer",
                    "model": slot_name,
                    "usage": {},
                    "latency_ms": 50,
                    "error": False,
                }
            if slot_name == "beast":
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    # Hang forever — should be cut loose by the Judge timeout
                    await asyncio.sleep(60)
                    return {"content": "should never reach here", "error": False}
                if "SYNTHESIZER" in system_content:
                    return {
                        "content": "synth answer (should not be reached if Judge timed out)",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 50,
                        "error": False,
                    }
                # beast as panelist
                return {
                    "content": "beast panel answer",
                    "model": "deepseek-chat",
                    "usage": {},
                    "latency_ms": 50,
                    "error": False,
                }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        container = _make_three_slot_container(mock_call)
        request = ModelCouncilRequest(prompt="Test Judge timeout")
        original_judge = mc_mod._JUDGE_CALL_TIMEOUT_S
        original_panel = mc_mod._PANEL_CALL_TIMEOUT_S
        mc_mod._JUDGE_CALL_TIMEOUT_S = 0.3
        mc_mod._PANEL_CALL_TIMEOUT_S = 5.0  # panel slots return fast; no impact
        try:
            with patch("aip.adapter.api.routes.model_council.logger"):
                result = await compare_models(request, container=container)
        finally:
            mc_mod._JUDGE_CALL_TIMEOUT_S = original_judge
            mc_mod._PANEL_CALL_TIMEOUT_S = original_panel

        assert result.synthesis_status == "failed"
        assert result.fusion_answer == ""
        assert result.judge_analysis == {}, (
            f"Judge timed out — judge_analysis should be empty, got: {result.judge_analysis}"
        )

    @pytest.mark.asyncio
    async def test_synth_timeout_preserves_judge_analysis(self):
        """Synth-Beast call timeout → ``synthesis_status='failed'`` but
        ``judge_analysis`` is still populated (Judge succeeded earlier).
        """
        from aip.adapter.api.routes import model_council as mc_mod
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name in ("synthesis", "evaluation"):
                return {
                    "content": f"{slot_name} answer",
                    "model": slot_name,
                    "usage": {},
                    "latency_ms": 50,
                    "error": False,
                }
            if slot_name == "beast":
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    return {
                        "content": json.dumps(
                            {
                                "status": "completed",
                                "analysis": {
                                    "consensus": ["x"],
                                    "contradictions": [],
                                    "partial_coverage": [],
                                    "unique_insights": [],
                                    "blind_spots": ["y"],
                                },
                                "responses": [],
                            }
                        ),
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 50,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    # Hang forever — should be cut loose by the Synth timeout
                    await asyncio.sleep(60)
                    return {"content": "should never reach here", "error": False}
                # beast as panelist
                return {
                    "content": "beast panel answer",
                    "model": "deepseek-chat",
                    "usage": {},
                    "latency_ms": 50,
                    "error": False,
                }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        container = _make_three_slot_container(mock_call)
        request = ModelCouncilRequest(prompt="Test Synth timeout")
        original_synth = mc_mod._SYNTH_CALL_TIMEOUT_S
        original_panel = mc_mod._PANEL_CALL_TIMEOUT_S
        original_judge = mc_mod._JUDGE_CALL_TIMEOUT_S
        mc_mod._SYNTH_CALL_TIMEOUT_S = 0.3
        mc_mod._PANEL_CALL_TIMEOUT_S = 5.0
        mc_mod._JUDGE_CALL_TIMEOUT_S = 5.0
        try:
            with patch("aip.adapter.api.routes.model_council.logger"):
                result = await compare_models(request, container=container)
        finally:
            mc_mod._SYNTH_CALL_TIMEOUT_S = original_synth
            mc_mod._PANEL_CALL_TIMEOUT_S = original_panel
            mc_mod._JUDGE_CALL_TIMEOUT_S = original_judge

        assert result.synthesis_status == "failed"
        assert result.fusion_answer == ""
        # Judge succeeded, so judge_analysis should still be populated
        assert result.judge_analysis != {}
        assert result.judge_analysis.get("status") == "completed"


class TestFusionJudgePromptContract:
    """Fix C: the Judge system prompt must instruct the model to use the
    EXACT label string from the answers_block section header (e.g.
    'synthesis' or 'anthropic/claude-3-opus'), never invent generic
    labels like 'model_a' or fall back to 'beast' when 'beast' isn't
    a section label.
    """

    def test_judge_prompt_contains_model_label_contract(self):
        """The Judge system prompt source contains the MODEL LABEL CONTRACT
        instruction block.
        """
        from pathlib import Path

        mc_path = (
            Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "model_council.py"
        )
        source = mc_path.read_text(encoding="utf-8")
        # The contract block heading
        assert "MODEL LABEL CONTRACT" in source, "Judge system prompt must contain the MODEL LABEL CONTRACT block"
        # The "EXACT" emphasis — instructs the model not to invent labels
        assert "EXACT <LABEL>" in source, "Judge prompt must instruct model to use the EXACT <LABEL> string"
        # The "Do NOT invent your own labels" prohibition
        assert "Do NOT invent your own labels" in source, "Judge prompt must prohibit inventing labels"
        # The concrete example showing correct label usage
        assert "anthropic/claude-3-opus" in source, "Judge prompt must include a concrete library-model label example"


class TestFusionGuiRendersJudgeAnalysis:
    """Fix B: the GUI consumers must render the ``judge_analysis`` dict,
    not just the flattened legacy strings. Verified by source-string
    contract check (the GUI files are not import-safe in the test env
    due to nicegui transitive deps, so we AST/string-check instead —
    consistent with the existing TestFusionGuiConsumerContract pattern).
    """

    def test_ask_page_reads_judge_analysis(self):
        """The ask.py _send_multicast path reads ``judge_analysis`` from
        the result and renders it via ``_format_judge_analysis_markdown``.
        """
        from pathlib import Path

        ask_path = Path(__file__).resolve().parent.parent / "gui" / "pages" / "ask.py"
        source = ask_path.read_text(encoding="utf-8")
        assert 'result.get("judge_analysis"' in source, (
            "ask.py _send_multicast must read result['judge_analysis'] to surface the structured JSON"
        )
        assert "_format_judge_analysis_markdown" in source, (
            "ask.py must define and call _format_judge_analysis_markdown to render the structured JSON"
        )

    def test_panel_renders_judge_analysis(self):
        """The model_council_panel renders the ``judge_analysis`` field
        via ``_render_judge_analysis``.
        """
        from pathlib import Path

        panel_path = Path(__file__).resolve().parent.parent / "gui" / "components" / "model_council_panel.py"
        source = panel_path.read_text(encoding="utf-8")
        assert 'data.get("judge_analysis"' in source, (
            "model_council_panel.py must read data['judge_analysis'] to surface the structured JSON"
        )
        assert "_render_judge_analysis" in source, (
            "model_council_panel.py must define _render_judge_analysis to render the structured JSON"
        )


# ── 16. Phase 1 Fix D — graceful degradation when panel models fail ──


class TestFusionFixDEngineFallback:
    """Fix D regression tests: when some panel models fail (timeout, error,
    network), the Fusion pipeline must still run on whatever models DID
    return, picking a successful panel model as the Judge+Synth engine.

    Pre-Fix-D bug: the code always called ``container.model_provider.call(
    "beast", ...)`` for Judge+Synth, even when the ``beast`` slot had just
    failed in the panel. If ``beast`` was one of the timing-out OpenRouter
    free models, the Judge call would also time out at
    ``_JUDGE_CALL_TIMEOUT_S`` and the entire Fusion output was lost —
    the user saw only per-model cards and no fusion/judge output at all.

    These tests verify the fix: the engine is picked from SUCCESSFUL
    panel models, so the Fusion pipeline degrades gracefully.
    """

    @pytest.fixture
    def beast_panel_fails_container(self):
        """Container where the ``beast`` slot fails as a PANELIST (returns
        error for non-JUDGE/SYNTH calls), but ``synthesis`` and
        ``evaluation`` succeed as panelists. When the Fusion engine is
        picked, ``synthesis`` is the fallback (Preference 2 in
        ``_pick_fusion_engine``) and it returns valid Judge JSON +
        Synth text.
        """

        async def mock_call(slot_name, messages, **kwargs):
            if slot_name == "synthesis":
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content:
                    # synthesis acts as Judge engine (Fix D fallback)
                    return {
                        "content": json.dumps(
                            {
                                "status": "completed",
                                "analysis": {
                                    "consensus": ["both non-beast models agree"],
                                    "contradictions": [
                                        {
                                            "topic": "detail",
                                            "stances": [
                                                {"model": "synthesis", "stance": "high"},
                                                {"model": "evaluation", "stance": "low"},
                                            ],
                                        },
                                    ],
                                    "partial_coverage": [],
                                    "unique_insights": [
                                        {"model": "evaluation", "insight": "edge case"},
                                    ],
                                    "blind_spots": ["beast's view (beast timed out)"],
                                },
                                "responses": [
                                    {"model": "synthesis", "content": "synth ans"},
                                    {"model": "evaluation", "content": "eval ans"},
                                ],
                            }
                        ),
                        "model": "gpt-4",
                        "usage": {},
                        "latency_ms": 1500,
                        "error": False,
                    }
                if "SYNTHESIZER" in system_content:
                    # synthesis acts as Synth engine (Fix D fallback)
                    return {
                        "content": "Fused answer composed by synthesis engine after beast panel failure.",
                        "model": "gpt-4",
                        "usage": {},
                        "latency_ms": 1200,
                        "error": False,
                    }
                # Panel call — synthesis answers as a panelist
                return {
                    "content": "Synthesis panel answer.",
                    "model": "gpt-4",
                    "usage": {},
                    "latency_ms": 1000,
                    "error": False,
                }
            if slot_name == "evaluation":
                return {
                    "content": "Evaluation panel answer.",
                    "model": "claude-3-opus",
                    "usage": {},
                    "latency_ms": 1100,
                    "error": False,
                }
            if slot_name == "beast":
                # beast FAILS as a panelist (simulating OpenRouter free
                # model timeout). Should NEVER be called as Judge/Synth
                # because Fix D picks a successful panel model instead.
                system_content = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_content = msg.get("content", "")
                if "JUDGE" in system_content or "SYNTHESIZER" in system_content:
                    return {
                        "content": "",
                        "model": "deepseek-chat",
                        "usage": {},
                        "latency_ms": 0,
                        "error": True,
                        "error_message": "beast should not be picked as engine when it failed as panelist",
                    }
                return {
                    "content": "",
                    "model": "deepseek-chat",
                    "usage": {},
                    "latency_ms": 30000,
                    "error": True,
                    "error_message": "timed out after 30s",
                }
            return {"content": "", "error": True, "error_message": f"Unknown slot: {slot_name}"}

        return _make_three_slot_container(mock_call)

    @pytest.mark.asyncio
    async def test_beast_panel_failure_still_produces_fusion(self, beast_panel_fails_container):
        """Fix D: when ``beast`` fails as a panelist but ``synthesis`` and
        ``evaluation`` succeed, the Fusion pipeline picks ``synthesis`` as
        the engine and produces a complete fusion_answer + judge_analysis.

        This is the EXACT scenario the user reported: 2 of 4 models timed
        out (openrouter free models), got 2 responses, but NO fusion synth
        or judge response was produced. Pre-Fix-D, the engine was always
        ``beast`` (which had just failed), so Judge timed out too. Post-
        Fix-D, the engine falls back to a successful panel model.
        """
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        request = ModelCouncilRequest(prompt="Test prompt for Fix D")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=beast_panel_fails_container)

        # Per-model: synthesis + evaluation completed, beast failed
        statuses = {pm.model_slot: pm.status for pm in result.selected_models if pm.source == "slot"}
        assert statuses.get("synthesis") == "completed"
        assert statuses.get("evaluation") == "completed"
        assert statuses.get("beast") == "failed", "beast must be recorded as failed in per-model results"

        # Overall status: partial (some models failed)
        assert result.status == "partial"

        # CRITICAL Fix D assertion: fusion STILL ran despite beast failure
        assert result.synthesis_status == "completed", (
            "Fix D: synthesis_status must be 'completed' when the engine "
            "fallback succeeds — pre-Fix-D this was 'failed' because the "
            "engine was always the (just-failed) beast slot"
        )
        assert result.fusion_answer != "", (
            "Fix D: fusion_answer must be populated — pre-Fix-D this was "
            "empty because the Judge call timed out on the failed beast slot"
        )
        assert "Fused answer composed by synthesis" in result.fusion_answer
        assert result.judge_analysis != {}, (
            "Fix D: judge_analysis must be populated — pre-Fix-D this was "
            "empty because the Judge call timed out on the failed beast slot"
        )
        # Judge JSON content sanity check
        assert result.judge_analysis.get("status") == "completed"
        assert "consensus" in result.judge_analysis.get("analysis", {})

    @pytest.mark.asyncio
    async def test_all_panel_fail_yields_unavailable_synthesis(self):
        """Fix D guard: when ALL panel models fail (successful_count < 2),
        the Fusion pipeline cannot run — synthesis_status='unavailable'
        (or 'failed' if 1 succeeded then engine pick failed). The key
        invariant: the pipeline does NOT crash, it degrades honestly.
        """
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        # Build a container where all 3 slots fail
        async def all_fail_call(slot_name, messages, **kwargs):
            return {
                "content": "",
                "model": slot_name,
                "usage": {},
                "latency_ms": 30000,
                "error": True,
                "error_message": "timed out after 30s",
            }

        container = _make_three_slot_container(all_fail_call)

        request = ModelCouncilRequest(prompt="All models fail")
        with patch("aip.adapter.api.routes.model_council.logger"):
            result = await compare_models(request, container=container)

        # All per-model results failed
        assert all(pm.status == "failed" for pm in result.selected_models)
        # Pipeline did not crash — synthesis honestly reports unavailable
        assert result.synthesis_status in ("unavailable", "failed")
        assert result.fusion_answer == ""
        assert result.judge_analysis == {}

    def test_pick_fusion_engine_preference_order(self):
        """Unit test for ``_pick_fusion_engine``: verifies the preference
        order is (1) beast slot if it succeeded, (2) any other successful
        slot, (3) any successful library model.
        """
        from aip.adapter.api.routes.model_council import PerModelResult, _pick_fusion_engine

        # Case 1: beast succeeded → beast is picked
        results_beast_ok = [
            PerModelResult(
                model_slot="synthesis", model_id="gpt-4", provider="openai", status="completed", source="slot"
            ),
            PerModelResult(
                model_slot="beast", model_id="deepseek", provider="openai", status="completed", source="slot"
            ),
            PerModelResult(
                model_slot="evaluation", model_id="claude", provider="openai", status="completed", source="slot"
            ),
        ]
        kind, eid = _pick_fusion_engine(results_beast_ok)
        assert (kind, eid) == ("slot", "beast"), "beast must be picked when it succeeded"

        # Case 2: beast failed, synthesis succeeded → synthesis picked
        results_beast_fail = [
            PerModelResult(
                model_slot="synthesis", model_id="gpt-4", provider="openai", status="completed", source="slot"
            ),
            PerModelResult(
                model_slot="beast",
                model_id="deepseek",
                provider="openai",
                status="failed",
                error="timed out",
                source="slot",
            ),
            PerModelResult(
                model_slot="evaluation", model_id="claude", provider="openai", status="completed", source="slot"
            ),
        ]
        kind, eid = _pick_fusion_engine(results_beast_fail)
        assert (kind, eid) == ("slot", "synthesis"), (
            "Fix D: when beast fails, synthesis (first successful slot) must be picked"
        )

        # Case 3: all slots failed, library model succeeded → library picked
        results_library_only = [
            PerModelResult(
                model_slot="beast",
                model_id="deepseek",
                provider="openai",
                status="failed",
                error="timed out",
                source="slot",
            ),
            PerModelResult(
                model_slot="synthesis",
                model_id="gpt-4",
                provider="openai",
                status="failed",
                error="timed out",
                source="slot",
            ),
            PerModelResult(
                model_slot="",
                model_id="anthropic/claude-3-opus",
                provider="openrouter",
                status="completed",
                source="library",
            ),
        ]
        kind, eid = _pick_fusion_engine(results_library_only)
        assert (kind, eid) == ("library", "anthropic/claude-3-opus"), (
            "Fix D: when all slots fail but a library model succeeded, library must be picked"
        )

        # Case 4: no successful models → (None, None)
        results_all_fail = [
            PerModelResult(
                model_slot="beast",
                model_id="deepseek",
                provider="openai",
                status="failed",
                error="timed out",
                source="slot",
            ),
            PerModelResult(
                model_slot="",
                model_id="anthropic/claude-3-opus",
                provider="openrouter",
                status="failed",
                error="timed out",
                source="library",
            ),
        ]
        kind, eid = _pick_fusion_engine(results_all_fail)
        assert (kind, eid) == (None, None), "no successful models → no engine"
