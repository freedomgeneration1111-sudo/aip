"""Tests for Phase 4.1 features — provenance widget, context visualizer,
and Vigil consistency checker.

Feature 3: Real-time provenance feedback widget — inline collapsible
source strip on answer cards (answer_card.py::_render_provenance_strip).

Feature 4: Context Preparer visualizer — fusion flow diagram in the
trace panel (trace_panel.py::_render_context_composition).

Feature 5: Automated consistency-checker — Vigil 5th evaluation pass
(vigil.py::_run_consistency_check + _parse_consistency_response +
VigilConfig.consistency_check_* fields).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Feature 3: Provenance widget ────────────────────────────────────────


class TestProvenanceWidget:
    """The answer card has an inline provenance strip that shows sources
    without requiring a button click."""

    def test_render_provenance_strip_function_exists(self):
        """answer_card.py defines _render_provenance_strip."""
        source = (_REPO_ROOT / "gui" / "components" / "answer_card.py").read_text()
        assert "def _render_provenance_strip(" in source

    def test_answer_card_calls_provenance_strip(self):
        """add_answer_card calls _render_provenance_strip when sources exist."""
        source = (_REPO_ROOT / "gui" / "components" / "answer_card.py").read_text()
        assert "_render_provenance_strip(sources)" in source

    def test_provenance_strip_shows_source_count(self):
        """The strip shows the source count in the summary row."""
        source = (_REPO_ROOT / "gui" / "components" / "answer_card.py").read_text()
        assert "PROVENANCE:" in source

    def test_provenance_strip_has_collapsible_detail(self):
        """The strip has a collapsible expansion for source details."""
        source = (_REPO_ROOT / "gui" / "components" / "answer_card.py").read_text()
        assert "ui.expansion" in source
        assert "retrieved source" in source


# ── Feature 4: Context Preparer visualizer ──────────────────────────────


class TestContextPreparerVisualizer:
    """The trace panel has a Context Composition visualizer section."""

    def test_render_context_composition_method_exists(self):
        """trace_panel.py defines _render_context_composition."""
        source = (_REPO_ROOT / "gui" / "components" / "trace_panel.py").read_text()
        assert "def _render_context_composition(" in source

    def test_trace_panel_calls_context_composition(self):
        """show_trace calls _render_context_composition."""
        source = (_REPO_ROOT / "gui" / "components" / "trace_panel.py").read_text()
        assert "self._render_context_composition(" in source

    def test_context_composition_shows_fusion_flow(self):
        """The visualizer shows the RRF fusion flow (before → after → gate)."""
        source = (_REPO_ROOT / "gui" / "components" / "trace_panel.py").read_text()
        assert "RRF Fusion" in source
        assert "Gating" in source
        assert "Final Context" in source

    def test_context_composition_shows_channel_bars(self):
        """The visualizer shows per-channel hit bars."""
        source = (_REPO_ROOT / "gui" / "components" / "trace_panel.py").read_text()
        assert "Channel Retrieval" in source

    def test_context_composition_has_packed_context_preview(self):
        """The visualizer has a collapsible packed context preview."""
        source = (_REPO_ROOT / "gui" / "components" / "trace_panel.py").read_text()
        assert "packed_context" in source or "PACKED CONTEXT" in source


# ── Feature 5: Vigil consistency checker ────────────────────────────────


class TestVigilConsistencyCheckerConfig:
    """VigilConfig has the consistency check fields."""

    def test_consistency_check_enabled_field_exists(self):
        from aip.foundation.schemas.review import VigilConfig

        config = VigilConfig()
        assert hasattr(config, "consistency_check_enabled")
        assert config.consistency_check_enabled is True  # default-on

    def test_consistency_check_model_slot_field(self):
        from aip.foundation.schemas.review import VigilConfig

        config = VigilConfig()
        assert config.consistency_check_model_slot == "evaluation"

    def test_consistency_check_sample_size_field(self):
        from aip.foundation.schemas.review import VigilConfig

        config = VigilConfig()
        assert config.consistency_check_sample_size == 5

    def test_consistency_check_lookback_turns_field(self):
        from aip.foundation.schemas.review import VigilConfig

        config = VigilConfig()
        assert config.consistency_check_lookback_turns == 10


class TestVigilConsistencyCheckerMethods:
    """Vigil has the consistency check methods + system prompt."""

    def test_run_consistency_check_method_exists(self):
        source = (_REPO_ROOT / "src" / "aip" / "orchestration" / "actors" / "vigil.py").read_text()
        assert "async def _run_consistency_check(" in source

    def test_parse_consistency_response_method_exists(self):
        source = (_REPO_ROOT / "src" / "aip" / "orchestration" / "actors" / "vigil.py").read_text()
        assert "def _parse_consistency_response(" in source

    def test_consistency_system_prompt_exists(self):
        source = (_REPO_ROOT / "src" / "aip" / "orchestration" / "actors" / "vigil.py").read_text()
        assert "_CONSISTENCY_SYSTEM_PROMPT" in source
        assert "consistency_score" in source
        assert "contradictions" in source

    def test_run_cycle_calls_consistency_check(self):
        """run_cycle Step 6 calls _run_consistency_check."""
        source = (_REPO_ROOT / "src" / "aip" / "orchestration" / "actors" / "vigil.py").read_text()
        assert "Step 6: Cross-turn consistency check" in source
        assert "_run_consistency_check(flagged_turns)" in source

    def test_parse_consistency_response_valid_json(self):
        """_parse_consistency_response parses a valid JSON response."""
        from aip.orchestration.actors.vigil import Vigil

        content = json.dumps({
            "consistency_score": 0.9,
            "contradictions": [],
            "explanation": "No contradictions found."
        })
        result = Vigil._parse_consistency_response(content)
        assert result is not None
        assert result["consistency_score"] == 0.9

    def test_parse_consistency_response_markdown_fenced(self):
        """_parse_consistency_response strips markdown fences."""
        from aip.orchestration.actors.vigil import Vigil

        content = f"```json\n{json.dumps({'consistency_score': 0.5, 'contradictions': [{'topic': 'test'}], 'explanation': 'test'})}\n```"
        result = Vigil._parse_consistency_response(content)
        assert result is not None
        assert result["consistency_score"] == 0.5
        assert len(result["contradictions"]) == 1

    def test_parse_consistency_response_malformed(self):
        """_parse_consistency_response returns None on malformed JSON."""
        from aip.orchestration.actors.vigil import Vigil

        result = Vigil._parse_consistency_response("not json at all")
        assert result is None

    def test_parse_consistency_response_empty(self):
        """_parse_consistency_response returns None on empty content."""
        from aip.orchestration.actors.vigil import Vigil

        result = Vigil._parse_consistency_response("")
        assert result is None

    def test_metadata_fields_written(self):
        """The consistency check writes vigil_consistency_* metadata fields."""
        source = (_REPO_ROOT / "src" / "aip" / "orchestration" / "actors" / "vigil.py").read_text()
        assert "vigil_consistency_score" in source
        assert "vigil_consistency_contradictions" in source
        assert "vigil_consistency_explanation" in source
        assert "vigil_consistency_evaluated_at" in source
