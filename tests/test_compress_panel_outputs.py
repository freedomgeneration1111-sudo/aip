"""Tests for Phase 2 Step 2-D — per-model compression pass.

When ``request.compress_panel_outputs`` is True, the endpoint runs a
per-panelist compression pass BEFORE the Judge reads the panel outputs.
Each successful panelist's answer is summarized to 5-8 key claims via
the picked Fusion engine. The compressed claims replace the raw answers
in the ``answers_block`` passed to the Judge.

Test coverage:
  1. ``ModelCouncilRequest.compress_panel_outputs`` field exists (default False)
  2. ``_compress_panel_outputs`` helper exists and is async
  3. When ``compress_panel_outputs=True``, the compression pass runs and
     the Judge's answers_block contains compressed claims (not raw answers)
  4. When ``compress_panel_outputs=False`` (default), the compression pass
     does NOT run — the Judge reads raw panel outputs (backward compat)
  5. On per-model compression failure, the raw answer is kept (graceful degrade)
  6. The Synth stage is unaffected — it still reads ONLY the Judge JSON
     (compression does NOT leak into the Synth prompt)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────


def _make_three_slot_container(call_fn):
    """Build a container with synthesis/evaluation/beast slots."""
    from aip.adapter.api.dependencies import AipContainer

    def resolve_config(slot):
        return {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "evaluation": {"provider": "openai_compatible", "model": "claude-3-opus", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek-chat", "api_key": "k"},
        }.get(slot, {})

    provider = MagicMock()
    provider.list_slots.return_value = ["synthesis", "evaluation", "beast"]
    provider._resolve_slot_config = resolve_config
    provider.call = AsyncMock(side_effect=call_fn)

    container = AipContainer({})
    container.model_provider = provider
    container.artifact_store = AsyncMock()
    container.ecs_store = AsyncMock()
    return container


def _valid_judge_json() -> str:
    return json.dumps({
        "status": "completed",
        "analysis": {
            "consensus": ["AIP stands for AI Poiesis"],
            "contradictions": [],
            "partial_coverage": [],
            "unique_insights": [],
            "blind_spots": [],
        },
        "responses": [],
    })


def _valid_compression_json(claims: list[str]) -> str:
    return json.dumps({"claims": claims})


# ── 1. Field exists + helper exists ─────────────────────────────────────


class TestCompressPanelOutputsField:
    """``ModelCouncilRequest.compress_panel_outputs`` field exists with
    the correct default."""

    def test_field_exists(self):
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        assert "compress_panel_outputs" in ModelCouncilRequest.model_fields, (
            "ModelCouncilRequest must have a 'compress_panel_outputs' field — "
            "Phase 2 Step 2-D: opt-in flag for the compression pass."
        )

    def test_field_defaults_false(self):
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test")
        assert req.compress_panel_outputs is False, (
            "compress_panel_outputs must default to False — backward compat "
            "(Judge reads raw panel outputs when the flag is off)."
        )

    def test_field_can_be_true(self):
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test", compress_panel_outputs=True)
        assert req.compress_panel_outputs is True


class TestCompressPanelOutputsHelper:
    """The ``_compress_panel_outputs`` helper exists and is async."""

    def test_helper_exists_and_is_async(self):
        import inspect
        from aip.adapter.api.routes.model_council import _compress_panel_outputs

        assert inspect.iscoroutinefunction(_compress_panel_outputs), (
            "_compress_panel_outputs must be an async function — it makes "
            "concurrent model calls via asyncio.gather."
        )

    def test_compress_system_prompt_exists(self):
        """The compression system prompt constant exists and is behavioral-only."""
        from aip.adapter.api.routes import model_council

        assert hasattr(model_council, "_COMPRESS_SYSTEM_PROMPT"), (
            "_COMPRESS_SYSTEM_PROMPT constant must exist — the compression "
            "pass needs a dedicated system prompt."
        )
        prompt = model_council._COMPRESS_SYSTEM_PROMPT
        # Must ask for 5-8 claims in JSON format
        assert "claims" in prompt
        assert "JSON" in prompt
        # Must NOT contain task content (it's behavioral-only)
        assert "Analyze the prompt below" not in prompt


# ── 2. Compression pass runs when flag is True ──────────────────────────


class TestCompressionPassRuns:
    """When ``compress_panel_outputs=True``, the compression pass runs
    and the Judge's answers_block contains compressed claims (not raw
    answers)."""

    @pytest.mark.asyncio
    async def test_compressed_claims_appear_in_judge_answers_block(self):
        """Verify that when compress_panel_outputs=True:
        - the compression pass runs (one call per successful panelist)
        - the Judge's answers_block contains '[Compressed — N key claims]'
          headers + the claim bullets (NOT the raw answer text)
        """
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def panel_call(slot_name, messages, **kwargs):
            return {
                "content": f"Raw long answer from {slot_name} with lots of detail " * 50,
                "model": slot_name,
                "usage": {"total_tokens": 500},
                "latency_ms": 50,
                "cost_usd": 0.0,
                "error": False,
            }

        container = _make_three_slot_container(panel_call)

        # Track ALL fusion engine calls (compression + judge + synth)
        engine_calls = []
        async def fake_fusion_engine(kind, engine_id, messages, container, timeout):
            engine_calls.append(list(messages))
            # Detect compression calls by the system prompt
            sys_msg = messages[0].get("content", "") if messages else ""
            if "compression engine" in sys_msg.lower():
                # Return compressed claims
                return {
                    "content": _valid_compression_json([
                        "Claim 1 from this model",
                        "Claim 2 from this model",
                        "Claim 3 from this model",
                    ]),
                    "model": "fake-compressor",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }
            elif "JUDGE" in sys_msg.upper():
                # Judge call — return valid judge JSON
                return {
                    "content": _valid_judge_json(),
                    "model": "fake-judge",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }
            else:
                # Synth call
                return {
                    "content": "Fused synthesis answer",
                    "model": "fake-synth",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }

        with (
            patch(
                "aip.adapter.api.routes.model_council._call_fusion_engine",
                new=AsyncMock(side_effect=fake_fusion_engine),
            ),
            patch(
                "aip.adapter.api.routes.model_council._pick_fusion_engine",
                return_value=("slot", "beast"),
            ),
        ):
            request = ModelCouncilRequest(
                prompt="test",
                compress_panel_outputs=True,
            )
            result = await compare_models(request, container=container)

        # Compression calls happened (3 panelists → 3 compression calls)
        compression_calls = [
            c for c in engine_calls
            if "compression engine" in (c[0].get("content", "") if c else "").lower()
        ]
        assert len(compression_calls) == 3, (
            f"Expected 3 compression calls (one per panelist), got {len(compression_calls)}"
        )

        # The Judge call's user message must contain compressed claims
        # (NOT the raw answer text)
        judge_calls = [
            c for c in engine_calls
            if "ACTING AS THE JUDGE" in (c[0].get("content", "") if c else "").upper()
        ]
        assert len(judge_calls) == 1, "Expected exactly 1 Judge call"
        judge_user_msg = judge_calls[0][-1]["content"]  # last message = user
        # Must contain the '[Compressed — N key claims]' header
        assert "[Compressed —" in judge_user_msg, (
            "Judge's answers_block must contain '[Compressed — N key claims]' "
            "headers when compress_panel_outputs=True"
        )
        # Must contain the claim bullets
        assert "Claim 1 from this model" in judge_user_msg
        assert "Claim 2 from this model" in judge_user_msg
        # Must NOT contain the raw answer text (which was 50 repetitions)
        assert "Raw long answer from" not in judge_user_msg, (
            "Judge's answers_block must NOT contain the raw panel answer "
            "text when compression ran — only the compressed claims."
        )


# ── 3. Compression pass does NOT run when flag is False (default) ───────


class TestCompressionDisabledByDefault:
    """When ``compress_panel_outputs=False`` (default), the compression
    pass does NOT run — the Judge reads raw panel outputs (backward compat)."""

    @pytest.mark.asyncio
    async def test_no_compression_calls_when_flag_false(self):
        """When compress_panel_outputs=False (default), no compression
        calls are made. The Judge's answers_block contains the raw
        panel answer text."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def panel_call(slot_name, messages, **kwargs):
            return {
                "content": f"Raw answer from {slot_name}",
                "model": slot_name,
                "usage": {"total_tokens": 100},
                "latency_ms": 50,
                "cost_usd": 0.0,
                "error": False,
            }

        container = _make_three_slot_container(panel_call)

        engine_calls = []
        async def fake_fusion_engine(kind, engine_id, messages, container, timeout):
            engine_calls.append(list(messages))
            sys_msg = messages[0].get("content", "") if messages else ""
            if "JUDGE" in sys_msg.upper():
                return {
                    "content": _valid_judge_json(),
                    "model": "fake-judge",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }
            return {
                "content": "Fused synthesis answer",
                "model": "fake-synth",
                "usage": {},
                "latency_ms": 10,
                "cost_usd": 0.0,
                "error": False,
            }

        with (
            patch(
                "aip.adapter.api.routes.model_council._call_fusion_engine",
                new=AsyncMock(side_effect=fake_fusion_engine),
            ),
            patch(
                "aip.adapter.api.routes.model_council._pick_fusion_engine",
                return_value=("slot", "beast"),
            ),
        ):
            # Default: compress_panel_outputs=False
            request = ModelCouncilRequest(prompt="test")
            result = await compare_models(request, container=container)

        # NO compression calls happened
        compression_calls = [
            c for c in engine_calls
            if "compression engine" in (c[0].get("content", "") if c else "").lower()
        ]
        assert len(compression_calls) == 0, (
            "No compression calls should happen when compress_panel_outputs=False"
        )

        # The Judge's answers_block must contain the raw answer text
        judge_calls = [
            c for c in engine_calls
            if "ACTING AS THE JUDGE" in (c[0].get("content", "") if c else "").upper()
        ]
        assert len(judge_calls) == 1
        judge_user_msg = judge_calls[0][-1]["content"]
        assert "Raw answer from synthesis" in judge_user_msg
        assert "Raw answer from evaluation" in judge_user_msg
        assert "Raw answer from beast" in judge_user_msg
        # Must NOT contain the '[Compressed —' header
        assert "[Compressed —" not in judge_user_msg


# ── 4. Graceful degrade on compression failure ──────────────────────────


class TestCompressionGracefulDegrade:
    """When a per-model compression call fails, the raw answer is kept
    for that model (graceful degrade). The Judge sees the raw text for
    the failed model and compressed claims for the successful ones."""

    @pytest.mark.asyncio
    async def test_compression_failure_falls_back_to_raw(self):
        """When the compression call fails for one model, the Judge's
        answers_block contains the raw answer for that model and
        compressed claims for the others."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def panel_call(slot_name, messages, **kwargs):
            return {
                "content": f"Raw answer from {slot_name}",
                "model": slot_name,
                "usage": {"total_tokens": 100},
                "latency_ms": 50,
                "cost_usd": 0.0,
                "error": False,
            }

        container = _make_three_slot_container(panel_call)

        engine_calls = []
        async def fake_fusion_engine(kind, engine_id, messages, container, timeout):
            engine_calls.append(list(messages))
            sys_msg = messages[0].get("content", "") if messages else ""
            user_msg = messages[-1].get("content", "") if messages else ""
            if "compression engine" in sys_msg.lower():
                # Fail compression for the "evaluation" model
                if "evaluation" in user_msg:
                    return {
                        "content": "",
                        "model": "fake-compressor",
                        "usage": {},
                        "latency_ms": 10,
                        "cost_usd": 0.0,
                        "error": True,
                        "error_message": "simulated compression failure for evaluation",
                    }
                # Succeed for synthesis + beast
                return {
                    "content": _valid_compression_json([
                        f"Compressed claim from {user_msg.split('Model: ')[1].split('\\n')[0] if 'Model: ' in user_msg else 'unknown'}",
                    ]),
                    "model": "fake-compressor",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }
            elif "JUDGE" in sys_msg.upper():
                return {
                    "content": _valid_judge_json(),
                    "model": "fake-judge",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }
            else:
                return {
                    "content": "Fused synthesis answer",
                    "model": "fake-synth",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }

        with (
            patch(
                "aip.adapter.api.routes.model_council._call_fusion_engine",
                new=AsyncMock(side_effect=fake_fusion_engine),
            ),
            patch(
                "aip.adapter.api.routes.model_council._pick_fusion_engine",
                return_value=("slot", "beast"),
            ),
        ):
            request = ModelCouncilRequest(
                prompt="test",
                compress_panel_outputs=True,
            )
            result = await compare_models(request, container=container)

        # The Judge's answers_block must contain:
        # - compressed claims for synthesis + beast
        # - raw answer for evaluation (compression failed)
        judge_calls = [
            c for c in engine_calls
            if "ACTING AS THE JUDGE" in (c[0].get("content", "") if c else "").upper()
        ]
        assert len(judge_calls) == 1
        judge_user_msg = judge_calls[0][-1]["content"]
        # synthesis + beast have compressed claims
        assert "[Compressed —" in judge_user_msg
        # evaluation has the raw answer (compression failed → fallback)
        assert "Raw answer from evaluation" in judge_user_msg, (
            "When compression fails for evaluation, the Judge must see the "
            "raw answer for that model (graceful degrade)."
        )


# ── 5. Synth stage is unaffected by compression ────────────────────────


class TestSynthUnaffectedByCompression:
    """The Synth stage reads ONLY the Judge JSON — compression does NOT
    leak into the Synth prompt. The Synth never sees the raw panel
    outputs OR the compressed claims."""

    @pytest.mark.asyncio
    async def test_synth_receives_only_judge_json_with_compression_on(self):
        """When compress_panel_outputs=True, the Synth call's user
        message contains ONLY the Judge JSON — not the compressed claims
        and not the raw panel outputs."""
        from aip.adapter.api.routes.model_council import (
            ModelCouncilRequest,
            compare_models,
        )

        async def panel_call(slot_name, messages, **kwargs):
            return {
                "content": f"Raw answer from {slot_name}",
                "model": slot_name,
                "usage": {"total_tokens": 100},
                "latency_ms": 50,
                "cost_usd": 0.0,
                "error": False,
            }

        container = _make_three_slot_container(panel_call)

        engine_calls = []
        async def fake_fusion_engine(kind, engine_id, messages, container, timeout):
            engine_calls.append({
                "system": messages[0].get("content", "") if messages else "",
                "user": messages[-1].get("content", "") if messages else "",
            })
            sys_msg = messages[0].get("content", "") if messages else ""
            if "compression engine" in sys_msg.lower():
                return {
                    "content": _valid_compression_json(["Compressed claim"]),
                    "model": "fake-compressor",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }
            elif "JUDGE" in sys_msg.upper():
                return {
                    "content": _valid_judge_json(),
                    "model": "fake-judge",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }
            else:
                # Synth call
                return {
                    "content": "Fused synthesis answer",
                    "model": "fake-synth",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }

        with (
            patch(
                "aip.adapter.api.routes.model_council._call_fusion_engine",
                new=AsyncMock(side_effect=fake_fusion_engine),
            ),
            patch(
                "aip.adapter.api.routes.model_council._pick_fusion_engine",
                return_value=("slot", "beast"),
            ),
        ):
            request = ModelCouncilRequest(
                prompt="test",
                compress_panel_outputs=True,
            )
            result = await compare_models(request, container=container)

        # Find the Synth call
        synth_calls = [c for c in engine_calls if "ACTING AS THE SYNTHESIZER" in c["system"].upper()]
        assert len(synth_calls) == 1, "Expected exactly 1 Synth call"
        synth_user_msg = synth_calls[0]["user"]
        # The Synth must NOT see compressed claims or raw panel outputs
        assert "[Compressed —" not in synth_user_msg, (
            "Synth must NOT see compressed claims — it reads ONLY the Judge JSON."
        )
        assert "Raw answer from" not in synth_user_msg, (
            "Synth must NOT see raw panel outputs — it reads ONLY the Judge JSON."
        )
        # The Synth MUST see the Judge JSON
        assert "consensus" in synth_user_msg or "analysis" in synth_user_msg or "judge" in synth_user_msg.lower(), (
            "Synth must receive the Judge JSON in its user message."
        )
