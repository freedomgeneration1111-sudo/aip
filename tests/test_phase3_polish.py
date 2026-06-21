"""Tests for Phase 3 polish — per-model attribution badges, stance
color-coding, dedicated [models.judge] slot, and GUI compress toggle.

Phase 3 deliverables:
  3a. Per-model attribution badges on unique_insights[] in ModelCouncilPanel
      + ask.py markdown (deterministic color per model label)
  3b. Per-model stance table color-coding on contradictions[] in
      ModelCouncilPanel + ask.py markdown
  3c. Dedicated [models.judge] TOML slot — _pick_fusion_engine prefers
      'judge' slot when configured (synthesis-only, never a panelist)
  3d. GUI toggle for compress_panel_outputs in Ask page header + state.py
      + api_client.py

Test coverage:
  1. _model_color() helper in model_council_panel.py is deterministic
  2. _model_color_markdown() helper in ask.py mirrors the panel palette
  3. ModelCouncilPanel._render_judge_analysis uses _model_color for
     unique_insights badges + contradictions stance color-coding
  4. ask.py _format_judge_analysis_markdown uses _model_color_markdown
     for the same sections (HTML spans)
  5. _pick_fusion_engine prefers 'judge' slot when configured (Phase 3c)
  6. _pick_fusion_engine falls back to beast slot when judge not configured
  7. 'judge' slot is in _EXCLUDED_SLOTS (never a panelist)
  8. config/aip.config.toml has the commented [models.judge] example
  9. GuiState has compress_panel_outputs field (default False)
  10. api_client.run_model_council forwards compress_panel_outputs
  11. ask.py _send_multicast passes compress_panel_outputs=state.compress_panel_outputs
  12. ask.py chat header has the Compress checkbox
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

# ── Path helpers ────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PANEL_PY = _REPO_ROOT / "gui" / "components" / "model_council_panel.py"
_ASK_PY = _REPO_ROOT / "gui" / "pages" / "ask.py"
_MODEL_COUNCIL_PY = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "model_council.py"
_CONFIG_TOML = _REPO_ROOT / "config" / "aip.config.toml"


def _read_panel_source() -> str:
    return _PANEL_PY.read_text(encoding="utf-8")


def _read_ask_source() -> str:
    return _ASK_PY.read_text(encoding="utf-8")


def _read_model_council_source() -> str:
    return _MODEL_COUNCIL_PY.read_text(encoding="utf-8")


def _read_config_source() -> str:
    return _CONFIG_TOML.read_text(encoding="utf-8")


# ── 3a + 3b: per-model color mapping ───────────────────────────────────


class TestModelColorHelpers:
    """The _model_color() helpers in panel + ask.py are deterministic
    and use the same palette (contract: change one, change both)."""

    def test_panel_model_color_is_deterministic(self):
        """_model_color() returns the same color for the same label."""
        from gui.components.model_council_panel import _model_color

        c1 = _model_color("synthesis")
        c2 = _model_color("synthesis")
        assert c1 == c2, "same label must produce same color"

    def test_panel_model_color_different_labels_may_differ(self):
        """Different labels CAN produce different colors (not required
        to always differ — collisions are acceptable)."""
        from gui.components.model_council_panel import _model_color

        c1 = _model_color("synthesis")
        c2 = _model_color("anthropic/claude-3-opus")
        # They MIGHT collide, but at least verify the function returns
        # valid hex strings for both
        assert c1.startswith("#")
        assert c2.startswith("#")

    def test_panel_model_color_returns_hex(self):
        from gui.components.model_council_panel import _model_color

        result = _model_color("test-model")
        assert result.startswith("#"), "color must be a hex string"
        assert len(result) == 7, "hex color must be 7 chars (#RRGGBB)"

    def test_panel_model_color_empty_label(self):
        from gui.components.model_council_panel import _model_color

        result = _model_color("")
        assert result.startswith("#"), "empty label must still return a color"

    def test_ask_model_color_markdown_mirrors_panel_palette(self):
        """ask.py _model_color_markdown uses the same palette as the
        panel's _model_color — same label → same color in both renderers."""
        # ask.py helper is module-level; import it
        import importlib

        from gui.components.model_council_panel import _model_color

        ask_module = importlib.import_module("gui.pages.ask")
        _model_color_markdown = ask_module._model_color_markdown

        # Same label must produce the same color in both helpers
        test_labels = ["synthesis", "beast", "anthropic/claude-3-opus", "deepseek/deepseek-v4"]
        for label in test_labels:
            panel_color = _model_color(label)
            markdown_color = _model_color_markdown(label)
            assert panel_color == markdown_color, (
                f"label '{label}': panel color {panel_color} != markdown color "
                f"{markdown_color} — the two palettes MUST be in sync "
                f"(contract: change one, change both)"
            )


# ── 3a: unique_insights badges ──────────────────────────────────────────


class TestUniqueInsightsBadges:
    """Phase 3a: unique_insights[] renders model labels as colored badges."""

    def test_panel_uses_model_color_for_unique_insights(self):
        """The panel source calls _model_color() in the unique_insights
        rendering section."""
        source = _read_panel_source()
        # Find the unique_insights section
        ui_idx = source.find("Unique insights")
        assert ui_idx != -1, "unique_insights section not found in panel"
        # Look at the next 600 chars
        section = source[ui_idx : ui_idx + 800]
        assert "_model_color" in section, (
            "Phase 3a: the unique_insights section must call _model_color() "
            "to render the model label as a colored badge"
        )

    def test_ask_markdown_uses_model_color_for_unique_insights(self):
        """The ask.py markdown renderer uses _model_color_markdown() in
        the unique_insights section."""
        source = _read_ask_source()
        # Find the unique_insights section in _format_judge_analysis_markdown
        ui_idx = source.find("Unique insights")
        assert ui_idx != -1
        section = source[ui_idx : ui_idx + 800]
        assert "_model_color_markdown" in section, (
            "Phase 3a: the ask.py markdown unique_insights section must call "
            "_model_color_markdown() to render the model badge"
        )

    def test_ask_markdown_unique_insights_has_html_span_badge(self):
        """The ask.py markdown unique_insights section renders an HTML
        <span> badge with a background color."""
        source = _read_ask_source()
        ui_idx = source.find("Unique insights")
        section = source[ui_idx : ui_idx + 1200]
        # Must contain an HTML span with background style
        assert "background:" in section or "background-color:" in section, (
            "Phase 3a: the unique_insights badge must use a background color "
            "(HTML span) so it renders as a colored badge in markdown"
        )
        assert "<span" in section, "badge must be an HTML <span>"


# ── 3b: contradictions stance color-coding ──────────────────────────────


class TestContradictionsStanceColorCoding:
    """Phase 3b: contradictions[] stance table color-codes model labels."""

    def test_panel_uses_model_color_for_contradictions(self):
        """The panel source calls _model_color() in the contradictions
        stance rendering section."""
        source = _read_panel_source()
        # Find the contradictions section
        con_idx = source.find("Contradictions stance table")
        assert con_idx != -1, "contradictions section not found in panel"
        # Use a larger window — the _model_color call is deeper in the section
        section = source[con_idx : con_idx + 2000]
        assert "_model_color" in section, (
            "Phase 3b: the contradictions stance section must call _model_color() to color-code the model label"
        )

    def test_ask_markdown_uses_model_color_for_contradictions(self):
        """The ask.py markdown renderer uses _model_color_markdown() in
        the contradictions stance table."""
        source = _read_ask_source()
        con_idx = source.find("Contradictions stance table")
        assert con_idx != -1
        # Use a larger window — the _model_color_markdown call is deeper
        section = source[con_idx : con_idx + 2000]
        assert "_model_color_markdown" in section, (
            "Phase 3b: the ask.py markdown contradictions section must call "
            "_model_color_markdown() to color-code the model label"
        )

    def test_ask_markdown_contradictions_has_html_span(self):
        """The ask.py markdown contradictions stance table renders an
        HTML <span> with color for the model label."""
        source = _read_ask_source()
        con_idx = source.find("Contradictions stance table")
        section = source[con_idx : con_idx + 1500]
        assert "<span" in section, "Phase 3b: the contradictions stance model label must be an HTML <span>"
        assert "border-left" in section, (
            "Phase 3b: the stance model label must have a colored border-left (visual marker for the model's stance)"
        )


# ── 3c: dedicated [models.judge] slot ───────────────────────────────────


class TestDedicatedJudgeSlot:
    """Phase 3c: _pick_fusion_engine prefers 'judge' slot when configured."""

    def test_judge_slot_in_excluded_slots(self):
        """'judge' is in _EXCLUDED_SLOTS so it never becomes a panelist."""
        from aip.adapter.api.routes.model_council import _EXCLUDED_SLOTS

        assert "judge" in _EXCLUDED_SLOTS, (
            "Phase 3c: 'judge' must be in _EXCLUDED_SLOTS — it's a dedicated synthesis-only slot, never a panelist"
        )

    def test_pick_fusion_engine_prefers_judge_slot_when_configured(self):
        """When the model_provider has a configured 'judge' slot,
        _pick_fusion_engine returns ('slot', 'judge') — preference 0."""
        from aip.adapter.api.routes.model_council import PerModelResult, _pick_fusion_engine

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "beast", "judge"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek", "api_key": "k"},
            "judge": {"provider": "openai_compatible", "model": "claude-3-opus", "api_key": "k"},
        }.get(slot, {})

        # Even when beast succeeded in the panel, the judge slot is preferred
        per_model_results = [
            PerModelResult(
                model_slot="synthesis", model_id="gpt-4", provider="openai", status="completed", source="slot"
            ),
            PerModelResult(
                model_slot="beast", model_id="deepseek", provider="openai", status="completed", source="slot"
            ),
        ]
        kind, eid = _pick_fusion_engine(per_model_results, model_provider=provider)
        assert (kind, eid) == ("slot", "judge"), (
            f"Phase 3c: when 'judge' slot is configured, it must be picked as the Fusion engine — got ({kind}, {eid})"
        )

    def test_pick_fusion_engine_falls_back_when_judge_not_configured(self):
        """When the model_provider does NOT have a 'judge' slot,
        _pick_fusion_engine falls back to the beast slot (preference 1)."""
        from aip.adapter.api.routes.model_council import PerModelResult, _pick_fusion_engine

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "beast"]  # no judge
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek", "api_key": "k"},
        }.get(slot, {})

        per_model_results = [
            PerModelResult(
                model_slot="synthesis", model_id="gpt-4", provider="openai", status="completed", source="slot"
            ),
            PerModelResult(
                model_slot="beast", model_id="deepseek", provider="openai", status="completed", source="slot"
            ),
        ]
        kind, eid = _pick_fusion_engine(per_model_results, model_provider=provider)
        assert (kind, eid) == ("slot", "beast"), (
            f"Phase 3c: when 'judge' slot is NOT configured, must fall back to beast slot — got ({kind}, {eid})"
        )

    def test_pick_fusion_engine_judge_slot_with_placeholder_model_skipped(self):
        """When the 'judge' slot exists but has a placeholder model
        (e.g. '<judge>'), it's treated as unconfigured — fall through
        to beast slot."""
        from aip.adapter.api.routes.model_council import PerModelResult, _pick_fusion_engine

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "beast", "judge"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek", "api_key": "k"},
            "judge": {"provider": "openai_compatible", "model": "<judge>", "api_key": "k"},  # placeholder
        }.get(slot, {})

        per_model_results = [
            PerModelResult(
                model_slot="beast", model_id="deepseek", provider="openai", status="completed", source="slot"
            ),
        ]
        kind, eid = _pick_fusion_engine(per_model_results, model_provider=provider)
        assert (kind, eid) == ("slot", "beast"), (
            f"Phase 3c: when 'judge' slot has a placeholder model, must fall through to beast — got ({kind}, {eid})"
        )

    def test_pick_fusion_engine_backward_compat_no_model_provider_arg(self):
        """When model_provider is None (not passed), _pick_fusion_engine
        still works — falls through to the panel-based picks. Backward
        compat with callers that don't pass the new arg."""
        from aip.adapter.api.routes.model_council import PerModelResult, _pick_fusion_engine

        per_model_results = [
            PerModelResult(
                model_slot="beast", model_id="deepseek", provider="openai", status="completed", source="slot"
            ),
        ]
        # No model_provider arg — backward compat
        kind, eid = _pick_fusion_engine(per_model_results)
        assert (kind, eid) == ("slot", "beast"), (
            "Phase 3c: when model_provider is None (not passed), must "
            f"fall through to panel-based picks — got ({kind}, {eid})"
        )

    def test_config_has_commented_judge_slot_example(self):
        """config/aip.config.toml has a commented [models.judge] example
        so users know how to configure the dedicated Judge slot."""
        source = _read_config_source()
        # The commented example must exist
        assert "[models.judge]" in source, (
            "Phase 3c: config/aip.config.toml must have a commented [models.judge] example"
        )
        # Must mention AIP_JUDGE_API_KEY env var
        assert "AIP_JUDGE_API_KEY" in source, "Phase 3c: config must document the AIP_JUDGE_API_KEY env var override"


# ── 3d: GUI compress_panel_outputs toggle ───────────────────────────────


class TestGuiCompressToggle:
    """Phase 3d: GUI state + api_client + ask.py wiring for
    compress_panel_outputs toggle."""

    def test_state_has_compress_panel_outputs_field(self):
        """GuiState has the compress_panel_outputs field (default False)."""
        from gui.state import GuiState

        state = GuiState()
        assert hasattr(state, "compress_panel_outputs"), "Phase 3d: GuiState must have a compress_panel_outputs field"
        assert state.compress_panel_outputs is False, "Phase 3d: compress_panel_outputs must default to False (opt-in)"

    def test_api_client_run_model_council_accepts_compress_panel_outputs(self):
        """api_client.run_model_council accepts the compress_panel_outputs param."""
        import inspect

        from gui.api_client import AipApiClient

        sig = inspect.signature(AipApiClient.run_model_council)
        assert "compress_panel_outputs" in sig.parameters, (
            "Phase 3d: run_model_council must accept compress_panel_outputs param"
        )
        param = sig.parameters["compress_panel_outputs"]
        assert param.default is False, "Phase 3d: compress_panel_outputs must default to False"

    def test_api_client_payload_includes_compress_panel_outputs(self):
        """The POST payload includes compress_panel_outputs."""
        source = _read_ask_source()
        # The api_client is in a different file; read it directly
        api_client_source = (_REPO_ROOT / "gui" / "api_client.py").read_text(encoding="utf-8")
        # Find the run_model_council method
        match = re.search(
            r"async\s+def\s+run_model_council.*?(?=\n    async\s+def\s+\w+|\n    def\s+\w+|\Z)",
            api_client_source,
            re.DOTALL,
        )
        assert match is not None
        method_body = match.group()
        assert '"compress_panel_outputs"' in method_body, (
            "Phase 3d: run_model_council payload must include 'compress_panel_outputs' key"
        )

    def test_ask_send_multicast_passes_compress_panel_outputs(self):
        """_send_multicast passes compress_panel_outputs=state.compress_panel_outputs."""
        source = _read_ask_source()
        # Find _send_multicast
        sm_idx = source.find("async def _send_multicast")
        assert sm_idx != -1, "_send_multicast not found"
        # Find the run_model_council call within _send_multicast
        rmc_idx = source.find("run_model_council", sm_idx)
        assert rmc_idx != -1
        # Use a larger window — the compress_panel_outputs= line is at the end
        call_section = source[rmc_idx : rmc_idx + 900]
        assert "compress_panel_outputs=" in call_section, (
            "Phase 3d: _send_multicast must pass compress_panel_outputs=... to run_model_council"
        )
        assert "state.compress_panel_outputs" in call_section, (
            "Phase 3d: _send_multicast must read state.compress_panel_outputs so the GUI toggle controls the flag"
        )

    def test_ask_header_has_compress_checkbox(self):
        """The Ask page chat header has a 'Compress' checkbox."""
        source = _read_ask_source()
        # Find the Compress checkbox in the header
        assert '"Compress"' in source, "Phase 3d: the Ask page header must have a 'Compress' checkbox"
        # Verify it's bound to state.compress_panel_outputs
        assert "state.compress_panel_outputs" in source, (
            "Phase 3d: the Compress checkbox must be bound to state.compress_panel_outputs"
        )

    def test_ask_header_compress_checkbox_has_tooltip(self):
        """The Compress checkbox has a tooltip explaining what it does."""
        source = _read_ask_source()
        # Find the Compress checkbox
        compress_idx = source.find('"Compress"')
        assert compress_idx != -1
        # Look at the next 400 chars for the tooltip
        section = source[compress_idx : compress_idx + 500]
        assert "tooltip" in section, (
            "Phase 3d: the Compress checkbox must have a tooltip explaining "
            "what compression does (so the user knows when to enable it)"
        )


# ── End-to-end payload contract ──────────────────────────────────────────


class TestEndToEndPayloadContractPhase3:
    """The GUI's POST payload keys match the backend's ModelCouncilRequest
    field names — including the new compress_panel_outputs (Phase 3d)."""

    def test_compress_panel_outputs_payload_key_matches_backend_field(self):
        """The payload key 'compress_panel_outputs' matches the
        ModelCouncilRequest.compress_panel_outputs field name."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        backend_fields = set(ModelCouncilRequest.model_fields.keys())
        assert "compress_panel_outputs" in backend_fields, (
            "Phase 3d: ModelCouncilRequest must have compress_panel_outputs field"
        )

        # The GUI payload must include the key
        api_client_source = (_REPO_ROOT / "gui" / "api_client.py").read_text(encoding="utf-8")
        method_match = re.search(
            r"async\s+def\s+run_model_council.*?(?=\n    async\s+def\s+\w+|\n    def\s+\w+|\Z)",
            api_client_source,
            re.DOTALL,
        )
        assert method_match is not None
        method_body = method_match.group()
        payload_match = re.search(
            r"payload:\s*dict\[str,\s*Any\]\s*=\s*\{([^}]+)\}",
            method_body,
            re.DOTALL,
        )
        assert payload_match is not None
        payload_keys = set(re.findall(r'"(\w+)":\s', payload_match.group(1)))
        missing = payload_keys - backend_fields
        assert not missing, (
            f"GUI payload keys {missing} are NOT fields on ModelCouncilRequest. The bug is always in the gap."
        )
