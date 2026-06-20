"""AIP Graph Page — Route: /graph

Knowledge Graph visualization in the Operator Console.
Shows graph stats from the backend and embeds the backend /graph-viz
page in an iframe for interactive Cytoscape.js exploration.

If the graph has zero nodes, shows empty-state guidance.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import context, ui

from gui.api_client import get_api_client
from gui.components.layout import build_left_nav, build_top_bar, build_right_rail
from gui.state import get_session_state
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_BG,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    C_SURFACE,
    C_WARN_BG,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.pages.graph")


@ui.page("/graph")
async def graph_page():
    """Knowledge Graph — explore entities and relationships."""
    try:
        await _graph_page_impl()
    except Exception as exc:
        log.exception("graph_page_crash: %s", exc)
        try:
            state = get_session_state()
            build_top_bar(state)
            build_left_nav(state, active_page="/graph")
    build_right_rail(state)
            with (
                ui.card()
                .style(
                    f"background:{C_ERR_BG}; border:1px solid {C_ERR_FG}; "
                    f"border-radius:{R_SM}; padding:16px; margin:24px;"
                )
            ):
                ui.label("Graph Page — Fatal Error").style(
                    f"font-size:16px; font-weight:700; color:{C_ERR_FG}; font-family:{F_SANS};"
                )
                ui.label(f"The Graph page crashed: {exc}").style(
                    f"font-size:12px; color:{C_CREAM}; font-family:{F_MONO}; margin-top:8px;"
                )
                ui.label("Check that the backend is running and the graph store is configured.").style(
                    f"font-size:11px; color:{C_MUTED}; margin-top:4px;"
                )
        except Exception:
            ui.label("Graph page failed to load. Check console logs.").style("color:red; padding:24px;")


async def _graph_page_impl():
    """Inner implementation of graph page, wrapped by crash boundary."""
    state = get_session_state()
    state.client = context.client
    api = get_api_client()

    # Refresh backend status before rendering layout
    try:
        await state.refresh_status_summary()
    except Exception as exc:
        log.warning("Graph page: status summary refresh failed: %s", exc)

    build_top_bar(state)
    build_left_nav(state, active_page="/graph")
    build_right_rail(state)

    # ── Fetch graph stats ───────────────────────────────────────
    graph_stats: dict[str, Any] = {}
    stats_error: str | None = None
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{api.base_url}/api/v1/graph/stats", timeout=8.0)
            if resp.status_code == 200:
                graph_stats = resp.json()
            else:
                stats_error = f"Backend returned status {resp.status_code}"
    except Exception as exc:
        log.warning("Failed to fetch graph stats: %s", exc)
        stats_error = str(exc)

    # ── Layout ──────────────────────────────────────────────────
    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:24px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Title
        ui.label("Knowledge Graph").style(
            f"font-family:{F_SANS}; font-size:24px; font-weight:700; color:{C_CREAM};"
        )
        ui.label("Explore entities, relationships, and domain bridges in the knowledge graph.").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:16px;"
        )

        # Stats error warning
        if stats_error and not state.backend_reachable:
            with (
                ui.card()
                .style(
                    f"background:{C_WARN_BG}; border:1px solid {C_WARN_FG}; "
                    f"border-radius:{R_SM}; padding:12px; margin-bottom:12px;"
                )
            ):
                ui.label("Backend unreachable — graph stats unavailable.").style(
                    f"font-size:12px; color:{C_WARN_FG}; font-family:{F_SANS};"
                )
                ui.label(f"Error: {stats_error}").style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                )

        # Stats cards
        with ui.row().classes("w-full").style("gap:12px; margin-bottom:16px;"):
            _stat_card("Nodes", graph_stats.get("nodes", 0))
            _stat_card("Edges", graph_stats.get("edges", 0))

            # Show type breakdown if available
            by_type = graph_stats.get("nodes_by_type", {})
            if by_type:
                _stat_card("Entity Types", len(by_type))
            else:
                _stat_card("Entity Types", 0)

        # Type breakdown
        if by_type:
            with (
                ui.card()
                .style(
                    f"background:{C_SURFACE}; border-radius:{R_MD}; padding:16px; "
                    f"margin-bottom:16px; border:0.5px solid {C_INK40};"
                )
            ):
                ui.label("Nodes by Type").style(
                    f"font-size:13px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS}; margin-bottom:8px;"
                )
                with ui.row().style("gap:8px; flex-wrap:wrap;"):
                    for entity_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
                        ui.chip(f"{entity_type}: {count}", color="amber", outline=True).props("dense").style(
                            f"font-family:{F_MONO}; font-size:11px;"
                        )

        # Relationship breakdown
        by_rel = graph_stats.get("edges_by_relationship", {})
        if by_rel:
            with (
                ui.card()
                .style(
                    f"background:{C_SURFACE}; border-radius:{R_MD}; padding:16px; "
                    f"margin-bottom:16px; border:0.5px solid {C_INK40};"
                )
            ):
                ui.label("Edges by Relationship").style(
                    f"font-size:13px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS}; margin-bottom:8px;"
                )
                with ui.row().style("gap:8px; flex-wrap:wrap;"):
                    for rel_type, count in sorted(by_rel.items(), key=lambda x: -x[1]):
                        ui.chip(f"{rel_type}: {count}", color="blue", outline=True).props("dense").style(
                            f"font-family:{F_MONO}; font-size:11px;"
                        )

        # Graph visualization iframe
        node_count = graph_stats.get("nodes", 0)
        if node_count == 0:
            # Empty state
            with (
                ui.card()
                .style(
                    f"background:{C_SURFACE}; border-radius:{R_MD}; padding:24px; "
                    f"border:0.5px solid {C_INK40}; text-align:center; width:100%;"
                )
            ):
                ui.icon("hub", size="48px").style(f"color:{C_MUTED}; margin-bottom:12px;")
                ui.label("No graph data yet").style(
                    f"font-size:16px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS}; margin-bottom:8px;"
                )
                ui.label(
                    "The knowledge graph is empty. Graph nodes are created by:\n"
                    "  - Running the seed bootstrap on first install\n"
                    "  - Sexton extracting entities from corpus turns\n"
                    "  - Manual graph node creation via the API"
                ).style(f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO}; white-space:pre-line;")
        else:
            # Embed the graph-viz iframe
            ui.label("Interactive Graph Visualization").style(
                f"font-size:13px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS}; margin-bottom:8px;"
            )
            viz_url = f"{api.base_url}/graph-viz"
            # Use ui.html with explicit width/height and allow-scripts for Cytoscape.js
            ui.html(
                f'<iframe src="{viz_url}" '
                f'width="100%" height="600" '
                f'style="width:100%; min-height:600px; height:600px; '
                f'border:1px solid {C_INK40}; border-radius:{R_MD}; background:#0f0f0f; display:block;" '
                f'sandbox="allow-scripts allow-same-origin allow-popups" '
                f'loading="lazy"></iframe>'
            )
            # Fallback: direct link if iframe blocked or blank
            with (
                ui.card()
                .style(
                    f"background:{C_SURFACE}; border-radius:{R_SM}; padding:12px; "
                    f"margin-top:8px; border:0.5px solid {C_INK40}; width:100%;"
                )
            ):
                ui.label("If the embedded visualization doesn't render above:").style(
                    f"font-size:10px; color:{C_MUTED};"
                )
                with ui.row().style("gap:8px; margin-top:4px;"):
                    ui.link("Open Graph Visualization (new tab)", viz_url, new_tab=True).style(
                        f"font-size:11px; color:{C_AMBER}; text-decoration:underline; font-weight:600;"
                    )
                    ui.label("|").style(f"font-size:11px; color:{C_INK40};")
                    ui.link("Graph Data API", f"{api.base_url}/api/v1/graph/data", new_tab=True).style(
                        f"font-size:11px; color:{C_AMBER}; text-decoration:underline;"
                    )

        # Links
        with ui.row().classes("w-full items-center").style("padding:8px 16px; gap:16px;"):
            ui.link("Corpus Workbench", "/corpus").style(
                f"font-size:10px; color:{C_AMBER}; text-decoration:underline;"
            )
            ui.link("Maintenance Center", "/maintenance").style(
                f"font-size:10px; color:{C_AMBER}; text-decoration:underline;"
            )


def _stat_card(label: str, value: Any) -> None:
    """Render a small stat card."""
    with (
        ui.card()
        .style(
            f"background:{C_SURFACE}; border-radius:{R_MD}; padding:12px 16px; "
            f"min-width:100px; border:0.5px solid {C_INK40};"
        )
    ):
        ui.label(str(value)).style(
            f"font-size:20px; font-weight:700; color:{C_OK_FG}; font-family:{F_MONO};"
        )
        ui.label(label).style(
            f"font-size:10px; color:{C_MUTED}; font-family:{F_SANS}; text-transform:uppercase; "
            f"letter-spacing:0.5px;"
        )
