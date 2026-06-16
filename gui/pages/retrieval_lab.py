"""AIP Retrieval Lab Page — Route: /retrieval

UI Cycle 11: Retrieval Lab v1.

Enables the DEFINER to test retrieval quality independently of answer
synthesis. Exposes per-channel retrieval results, fusion/ranking behavior,
latency, degraded channels, warnings, and selected context without
generating an answer.

Architecture requirements satisfied:
1. API-first — all data via gui.api_client, no orchestration imports.
2. No answer synthesis — retrieval diagnostic only.
3. No fake channel results — honest unavailable/not_configured states.
4. Vector fallback/degraded state visible.
5. Empty retrieval visible and not treated as success.
6. Channel failures visible.
7. No secrets exposed.
8. No corpus/wiki/artifact mutation from retrieval testing.
"""

from __future__ import annotations

from typing import Any

from nicegui import context, ui

from gui.api_client import get_api_client
from gui.components.layout import build_left_nav, build_top_bar
from gui.components.retrieval_channel_results import RetrievalChannelResults
from gui.components.retrieval_health_cards import RetrievalHealthCards
from gui.components.retrieval_query_panel import RetrievalQueryPanel
from gui.components.retrieval_ranked_context import RetrievalRankedContext
from gui.state import get_session_state
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    C_SURFACE,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)


@ui.page("/retrieval")
async def retrieval_lab_page():
    """Retrieval Lab — test retrieval quality without answer synthesis."""
    state = get_session_state()
    state.client = context.client
    api = get_api_client()

    # Refresh backend status before rendering layout
    await state.refresh_status_summary()

    build_top_bar(state)
    build_left_nav(state, active_page="/retrieval")

    # ── State ──────────────────────────────────────────────────────────
    health_data: dict[str, Any] = {}
    test_result: dict[str, Any] = {}
    backend_available: bool = False

    # ── Component instances ────────────────────────────────────────────
    health_cards = RetrievalHealthCards()
    channel_results = RetrievalChannelResults()
    ranked_context = RetrievalRankedContext()

    # ── Layout ─────────────────────────────────────────────────────────
    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:24px 32px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Page title
        with ui.row().classes("items-center gap-3"):
            ui.label("Retrieval Lab").style(f"font-family:{F_SANS}; font-size:24px; font-weight:700; color:{C_CREAM};")
            ui.label("v1").style(
                f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; "
                f"border:0.5px solid {C_INK40}; border-radius:2px; padding:1px 4px;"
            )

        ui.label(
            "Test retrieval quality independently of answer synthesis. No model dispatch, no corpus mutation."
        ).style(f"font-size:12px; color:{C_MUTED}; font-family:{F_SANS}; margin-bottom:12px;")

        # Backend availability status
        backend_status_label = ui.label("").style(f"font-size:10px; font-family:{F_MONO}; margin-bottom:8px;")

        # ── Health Cards Section ───────────────────────────────────────
        with (
            ui.expansion("Channel Health", value=True)
            .classes("w-full mb-2")
            .style(
                f"font-family:{F_SANS}; font-size:13px; font-weight:600; color:{C_CREAM}; "
                f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD};"
            )
        ):
            health_cards._container = ui.column().classes("w-full gap-1")
            # Will be populated by _load_health

        # ── Query Panel ────────────────────────────────────────────────
        query_panel = RetrievalQueryPanel(on_run=lambda q, ch, lim, tr: _handle_run_test(q, ch, lim, tr))
        query_panel.render()

        # ── Test Results Area ──────────────────────────────────────────
        ui.column().classes("w-full mt-4 gap-4")

        # Warnings bar (for degraded/unavailable states)
        warnings_container = ui.column().classes("w-full gap-1")

        # Channel results section
        with (
            ui.expansion("Per-Channel Results", value=True)
            .classes("w-full")
            .style(
                f"font-family:{F_SANS}; font-size:13px; font-weight:600; color:{C_CREAM}; "
                f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD};"
            )
        ):
            channel_results._container = ui.column().classes("w-full gap-1 p-2")

        # Ranked context section
        with (
            ui.expansion("Ranked Context (Fusion Results)", value=True)
            .classes("w-full")
            .style(
                f"font-family:{F_SANS}; font-size:13px; font-weight:600; color:{C_CREAM}; "
                f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD};"
            )
        ):
            ranked_context._container = ui.column().classes("w-full gap-1 p-2")

        # No-results-yet placeholder
        no_results_placeholder = ui.label("Enter a query and click Run Test to see retrieval results.").style(
            f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO}; text-align:center; padding:32px;"
        )

        # Trace detail (collapsible)
        trace_container = ui.column().classes("w-full")

    # ── Data Loading ───────────────────────────────────────────────────

    async def _load_health() -> None:
        """Load channel health data from the backend."""
        nonlocal health_data, backend_available
        try:
            health_data = await api.retrieval_health()
            backend_available = health_data.get("status") not in ("error", "unavailable")
            health_cards.render(health_data)

            if backend_available:
                summary = health_data.get("summary", {})
                active = summary.get("active", 0)
                total = summary.get("total_channels", 0)
                backend_status_label.text = f"Backend: connected | {active}/{total} channels active"
                backend_status_label.style(f"font-size:10px; color:{C_OK_FG}; font-family:{F_MONO}; margin-bottom:8px;")
            else:
                msg = health_data.get("message", "Backend unavailable")
                backend_status_label.text = f"Backend: unavailable — {msg}"
                backend_status_label.style(
                    f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO}; margin-bottom:8px;"
                )
        except Exception as exc:
            backend_available = False
            backend_status_label.text = f"Backend: unreachable — {exc}"
            backend_status_label.style(f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO}; margin-bottom:8px;")

    async def _handle_run_test(
        query: str,
        selected_channels: list[str],
        limit: int,
        include_trace: bool,
    ) -> None:
        """Handle the Run Test button: execute retrieval test and display results."""
        nonlocal test_result

        # Hide placeholder
        no_results_placeholder.set_visibility(False)

        # Clear previous results
        warnings_container.clear()
        trace_container.clear()

        if not query:
            with warnings_container:
                ui.label("Query is required — enter a test query and try again.").style(
                    f"font-size:11px; color:{C_AMBER}; font-family:{F_MONO};"
                )
            # Render empty state
            test_result = {
                "status": "error",
                "query": "",
                "channel_results": {},
                "channel_health": {},
                "fusion_results": [],
                "selected_context": [],
                "degraded_channels": [],
                "failed_channels": [],
                "warnings": ["query is required"],
                "lexical_only": False,
                "vector_contributed": False,
            }
            channel_results.render(test_result)
            ranked_context.render(test_result)
            return

        # Execute retrieval test
        try:
            test_result = await api.retrieval_test(
                query=query,
                selected_channels=selected_channels,
                limit=limit,
                include_trace=include_trace,
            )
        except Exception as exc:
            test_result = {
                "status": "error",
                "message": f"Retrieval test failed: {exc}",
                "query": query,
                "selected_channels": selected_channels,
                "channel_results": {},
                "channel_health": {},
                "latency_ms": 0,
                "fusion_results": [],
                "selected_context": [],
                "degraded_channels": [],
                "failed_channels": list(selected_channels),
                "warnings": [f"Backend error: {exc}"],
                "lexical_only": True,
                "vector_contributed": False,
            }

        # Render results
        channel_results.render(test_result)
        ranked_context.render(test_result)

        # Show warnings
        warnings = test_result.get("warnings", [])
        degraded = test_result.get("degraded_channels", [])
        failed = test_result.get("failed_channels", [])

        with warnings_container:
            if degraded:
                ui.label(f"Degraded channels: {', '.join(degraded)}").style(
                    f"font-size:10px; color:{C_AMBER}; font-family:{F_MONO};"
                )
            if failed:
                ui.label(f"Failed/unavailable channels: {', '.join(failed)}").style(
                    f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};"
                )
            for w in warnings:
                ui.label(f"! {w[:200]}").style(f"font-size:9px; color:{C_AMBER}; font-family:{F_MONO};")

            # Honesty warnings
            status = test_result.get("status", "")
            if status == "unavailable":
                ui.label("Retrieval pipeline is not available — orchestration not wired.").style(
                    f"font-size:11px; color:{C_ERR_FG}; font-family:{F_SANS};"
                )
            elif status == "error":
                msg = test_result.get("message", "Unknown error")
                ui.label(f"Retrieval test error: {msg}").style(
                    f"font-size:11px; color:{C_ERR_FG}; font-family:{F_SANS};"
                )

        # Show trace detail if available
        trace_data = test_result.get("trace")
        if include_trace and trace_data:
            with trace_container:
                with (
                    ui.expansion("Trace Detail", value=False)
                    .classes("w-full")
                    .style(
                        f"font-family:{F_MONO}; font-size:11px; color:{C_CREAM}; "
                        f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                        f"border-radius:{R_SM};"
                    )
                ):
                    # Show key trace fields
                    trace_fields = [
                        ("Query", trace_data.get("query", "")),
                        ("Verdict", trace_data.get("verdict", "")),
                        ("Channels Queried", ", ".join(trace_data.get("channels_queried", []))),
                        ("Hits Before Fusion", str(trace_data.get("hits_before_fusion", 0))),
                        ("Hits After Fusion", str(trace_data.get("hits_after_fusion", 0))),
                        ("Hits After Gate", str(trace_data.get("hits_after_quality_gate", 0))),
                        ("Lexical Only", str(trace_data.get("lexical_only", False))),
                        ("Vector Contributed", str(trace_data.get("vector_contributed", False))),
                        ("Total Elapsed", f"{trace_data.get('total_elapsed_ms', 0):.0f}ms"),
                        ("Degradation Summary", trace_data.get("degradation_summary", "")[:300]),
                    ]
                    for field_label, field_value in trace_fields:
                        with ui.row().classes("gap-2"):
                            ui.label(f"{field_label}:").style(
                                f"font-size:10px; font-weight:600; color:{C_CREAM}; "
                                f"font-family:{F_MONO}; min-width:140px;"
                            )
                            ui.label(str(field_value)[:200]).style(
                                f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                            )

    # ── Initial Load ───────────────────────────────────────────────────
    await _load_health()
