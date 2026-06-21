"""AIP Trace Panel — drawer/modal for displaying retrieval trace details.

Shows channels attempted, channels used, channel_details, lexical_only,
vector_contributed, degraded/failed channels, and warnings from the
retrieval trace. If no trace data is available, shows an honest
"unavailable" state.

Import boundary: this module imports ONLY from gui.* (theme, state, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_WARN_FG,
    F_MONO,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.trace_panel")


class TracePanel:
    """Manages a retrieval trace drawer/modal for the Ask Workbench.

    Usage:
        panel = TracePanel()
        # ... when user clicks "Show Trace":
        await panel.show_trace(session_id, api_client)
    """

    def __init__(self) -> None:
        self._drawer: Any = None

    async def show_trace(self, session_id: str, api_client: Any) -> None:
        """Open a right drawer showing the retrieval trace for a session.

        Fetches trace data from GET /api/v1/retrieval/traces/session/{session_id}.
        If the endpoint is unavailable or returns no trace, shows honest unavailable state.

        Args:
            session_id: The chat session ID to look up trace for
            api_client: AipApiClient instance for backend communication
        """
        # Close existing drawer if any
        self.close()

        with ui.right_drawer().style(
            f"background:{C_GROUND}; border-left:0.5px solid {C_INK40}; "
            f"width:420px; min-width:420px; padding:16px; overflow-y:auto;"
        ) as drawer:
            self._drawer = drawer

            # Header
            with (
                ui.row()
                .classes("w-full items-center")
                .style(f"border-bottom:0.5px solid {C_INK40}; padding-bottom:8px; margin-bottom:12px;")
            ):
                ui.label("Retrieval Trace").style(
                    f"font-size:13px; font-weight:700; color:{C_AMBER}; font-family:{F_MONO}; letter-spacing:0.5px;"
                )
                ui.space()
                ui.button("Close", on_click=self.close).props("dense flat size=sm").style(
                    f"color:{C_MUTED}; font-size:10px;"
                )

            # Fetch trace data
            trace_data = None
            try:
                trace_data = await api_client.get_retrieval_trace_by_session(session_id)
            except Exception as exc:
                log.warning("trace_fetch_failed: %s", exc)

            if not trace_data or trace_data.get("status") == "not_found" or not trace_data.get("trace"):
                ui.label("Retrieval trace unavailable for this answer.").style(
                    f"font-size:11px; color:{C_WARN_FG}; font-family:{F_MONO}; padding:16px; text-align:center;"
                )
                ui.label(
                    "The backend did not record a retrieval trace for this session. "
                    "This may occur in normal mode (no retrieval) or when the trace "
                    "store is not configured."
                ).style(f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; padding:8px 16px; line-height:1.4;")
                return

            trace = trace_data.get("trace", {})

            # ── Trace Overview ─────────────────────────────────
            self._render_section_label("OVERVIEW")

            query = trace.get("query", "unknown")
            total_ms = trace.get("total_elapsed_ms", 0)
            verdict = trace.get("verdict", "UNKNOWN")
            hits_before = trace.get("hits_before_fusion", 0)
            hits_after = trace.get("hits_after_fusion", 0)
            hits_gate = trace.get("hits_after_gate", 0)

            with (
                ui.card()
                .classes("w-full")
                .style(
                    f"background:{C_RAISED}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_MD}; padding:8px 12px; margin-bottom:8px;"
                )
            ):
                ui.label(f"Query: {query[:100]}").style(
                    f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO}; margin-bottom:4px;"
                )
                ui.label(f"Latency: {total_ms:.0f}ms | Verdict: {verdict}").style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; margin-bottom:4px;"
                )
                ui.label(
                    f"Hits: before_fusion={hits_before} → after_fusion={hits_after} → after_gate={hits_gate}"
                ).style(f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};")

            # ── Channel Flags ──────────────────────────────────
            lexical_only = trace.get("lexical_only", False)
            vector_contributed = trace.get("vector_contributed", False)

            self._render_section_label("RETRIEVAL FLAGS")
            with ui.row().classes("w-full").style("margin-bottom:8px; gap:8px;"):
                if lexical_only:
                    ui.label("LEXICAL ONLY").style(
                        f"font-size:9px; font-weight:700; color:{C_AMBER}; "
                        f"font-family:{F_MONO}; padding:2px 6px; "
                        f"background:{C_RAISED}; border-radius:{R_SM};"
                    )
                else:
                    ui.label("HYBRID").style(
                        f"font-size:9px; font-weight:700; color:{C_OK_FG}; "
                        f"font-family:{F_MONO}; padding:2px 6px; "
                        f"background:{C_RAISED}; border-radius:{R_SM};"
                    )

                if vector_contributed:
                    ui.label("VECTOR OK").style(
                        f"font-size:9px; font-weight:700; color:{C_OK_FG}; "
                        f"font-family:{F_MONO}; padding:2px 6px; "
                        f"background:{C_RAISED}; border-radius:{R_SM};"
                    )
                else:
                    ui.label("NO VECTOR").style(
                        f"font-size:9px; font-weight:700; color:{C_WARN_FG}; "
                        f"font-family:{F_MONO}; padding:2px 6px; "
                        f"background:{C_RAISED}; border-radius:{R_SM};"
                    )

            # ── Channels ───────────────────────────────────────
            channels_queried = trace.get("channels_queried", [])
            channels_used = trace.get("channels_used", [])
            channel_contributions = trace.get("channel_contributions", {})
            per_channel_ms = trace.get("per_channel_elapsed_ms", {})

            if channels_queried:
                self._render_section_label("CHANNELS")
                for ch in channels_queried:
                    used = ch in channels_used if channels_used else True
                    contrib = channel_contributions.get(ch, 0)
                    latency = per_channel_ms.get(ch, 0)

                    ch_color = C_OK_FG if used else C_INK60
                    ch_status = "used" if used else "skipped"

                    with ui.row().classes("w-full items-center").style("margin:2px 0;"):
                        dot = "●" if used else "○"
                        ui.label(dot).style(f"font-size:10px; color:{ch_color}; margin-right:4px;")
                        ui.label(f"{ch}: {ch_status} ({contrib} hits, {latency:.0f}ms)").style(
                            f"font-size:10px; font-family:{F_MONO}; color:{ch_color};"
                        )

            # ── Context Composition Visualizer (Phase 4.1) ─────
            # Shows how FTS5, vector, and entity resolution compose
            # the final context stack. This is the "Context Preparer
            # visualizer" — when a retrieval goes wrong, the DEFINER
            # can see which channel misfired, how RRF fused the hits,
            # and what the gating step kept vs dropped.
            self._render_context_composition(trace, hits_before, hits_after, hits_gate, channel_contributions)

            # ── Warnings ───────────────────────────────────────
            warnings = trace.get("degradation_warnings", [])
            if warnings:
                self._render_section_label("WARNINGS")
                for w in warnings[:10]:
                    ui.label(f"! {w}").style(f"font-size:10px; font-family:{F_MONO}; color:{C_WARN_FG};")

    def _render_section_label(self, text: str) -> None:
        """Render a section label."""
        ui.label(text).style(
            f"font-size:9px; font-weight:600; letter-spacing:1.5px; "
            f"color:{C_MUTED}; text-transform:uppercase; margin-bottom:4px; margin-top:8px;"
        )

    def _render_context_composition(
        self,
        trace: dict[str, Any],
        hits_before: int,
        hits_after: int,
        hits_gate: int,
        channel_contributions: dict[str, int],
    ) -> None:
        """Render the Context Composition visualizer section.

        Phase 4.1: Context Preparer visualizer. Shows how the retrieval
        channels compose the final context stack — the fusion flow:
          1. Per-channel contribution (raw hits per channel)
          2. RRF fusion step (hits before → after fusion)
          3. Gating step (hits after fusion → after gate)
          4. Final context summary (what made it into the prompt)

        This is the most powerful retrieval debugging tool in the system.
        When a retrieval goes wrong, the DEFINER can see which channel
        misfired, how RRF fused the hits, and what the gating step kept
        vs dropped — without reading the backend logs.
        """
        self._render_section_label("CONTEXT COMPOSITION")

        # ── Fusion flow diagram (text-based pipeline) ─────────
        with (
            ui.card()
            .classes("w-full")
            .style(
                f"background:{C_RAISED}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD}; padding:8px 12px; margin-bottom:8px;"
            )
        ):
            # Step 1: Per-channel raw hits
            if channel_contributions:
                ui.label("Step 1 — Channel Retrieval").style(
                    f"font-size:10px; font-weight:700; color:{C_AMBER}; font-family:{F_MONO}; margin-bottom:4px;"
                )
                for ch, hits in channel_contributions.items():
                    bar_len = min(int(hits) * 3, 30) if isinstance(hits, (int, float)) else 0
                    bar = "█" * bar_len if bar_len > 0 else "·"
                    ui.label(f"  {ch:20s} {bar} {hits} hits").style(
                        f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO}; line-height:1.4;"
                    )

            # Step 2: RRF Fusion
            ui.label("Step 2 — RRF Fusion").style(
                f"font-size:10px; font-weight:700; color:{C_AMBER}; "
                f"font-family:{F_MONO}; margin-top:6px; margin-bottom:4px;"
            )
            fusion_delta = hits_before - hits_after if hits_before >= hits_after else 0
            ui.label(f"  {hits_before} hits → {hits_after} fused ({fusion_delta} deduped)").style(
                f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO}; line-height:1.4;"
            )

            # Step 3: Gating
            ui.label("Step 3 — Gating").style(
                f"font-size:10px; font-weight:700; color:{C_AMBER}; "
                f"font-family:{F_MONO}; margin-top:6px; margin-bottom:4px;"
            )
            gate_delta = hits_after - hits_gate if hits_after >= hits_gate else 0
            gate_label = f"  {hits_after} fused → {hits_gate} gated ({gate_delta} filtered)"
            gate_color = C_OK_FG if hits_gate > 0 else C_WARN_FG
            ui.label(gate_label).style(f"font-size:10px; color:{gate_color}; font-family:{F_MONO}; line-height:1.4;")

            # Step 4: Final context
            ui.label("Step 4 — Final Context").style(
                f"font-size:10px; font-weight:700; color:{C_OK_FG}; "
                f"font-family:{F_MONO}; margin-top:6px; margin-bottom:4px;"
            )
            if hits_gate > 0:
                ui.label(f"  {hits_gate} source(s) injected into generative prompt").style(
                    f"font-size:10px; color:{C_OK_FG}; font-family:{F_MONO}; line-height:1.4;"
                )
            else:
                ui.label("  No sources passed gating — answer based on model knowledge only").style(
                    f"font-size:10px; color:{C_WARN_FG}; font-family:{F_MONO}; line-height:1.4;"
                )

        # ── Channel weight configuration (if available) ───────
        channel_weights = trace.get("channel_weights", {})
        if channel_weights:
            self._render_section_label("CHANNEL WEIGHTS")
            with ui.row().classes("w-full").style("margin-bottom:8px; gap:8px; flex-wrap:wrap;"):
                for ch, weight in channel_weights.items():
                    weight_str = f"{weight:.2f}" if isinstance(weight, (int, float)) else str(weight)
                    ui.label(f"{ch}={weight_str}").style(
                        f"font-size:9px; font-weight:600; color:{C_CREAM}; "
                        f"font-family:{F_MONO}; padding:2px 6px; "
                        f"background:{C_RAISED}; border-radius:{R_SM};"
                    )

        # ── Packed context preview (if available) ─────────────
        packed_context = trace.get("packed_context", "")
        if packed_context:
            self._render_section_label("PACKED CONTEXT PREVIEW")
            with (
                ui.expansion(
                    "Show packed context (what the model actually saw)",
                    icon="description",
                )
                .classes("w-full")
                .style(
                    f"margin-bottom:8px; padding:0 8px; font-family:{F_MONO}; "
                    f"background:{C_RAISED}; border-radius:{R_SM};"
                )
            ):
                preview = packed_context[:2000]
                if len(packed_context) > 2000:
                    preview += f"\n\n... ({len(packed_context) - 2000} more chars truncated)"
                ui.code(preview).style(f"font-size:9px; font-family:{F_MONO}; background:{C_GROUND}; color:{C_INK60};")

    def close(self) -> None:
        """Close the trace panel drawer."""
        if self._drawer is not None:
            try:
                self._drawer.close()
            except Exception:
                pass
            self._drawer = None
