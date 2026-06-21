"""AIP Artifacts Page — Route: /artifacts

Artifact Workbench v1 — inspect, review, approve, reject, mark needs-revision,
link, and export artifacts from the Operator Console.

Layout:
  - Left: Artifact list with tab filters (All, Generated, Needs Revision,
    Approved, Exported, Rejected, Overrides)
  - Right: Artifact detail panel (content preview, metadata, state badge,
    sources, review history, crosslinks, review actions)

The artifact lifecycle preserves DEFINER sovereignty:
  - No auto-approve
  - No auto-export
  - No silent state changes
  - Force-export is visibly exceptional and audited
  - Empty/unavailable states are honest

Import boundary: imports only gui.* (no aip.* imports).
"""

from __future__ import annotations

from typing import Any

from nicegui import context, ui

from gui.components.artifact_detail import render_artifact_detail
from gui.components.artifact_list import render_artifact_list
from gui.components.layout import build_left_nav, build_right_rail, build_top_bar
from gui.state import get_session_state
from gui.theme import (
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_MUTED,
    F_MONO,
    F_SANS,
)


@ui.page("/artifacts")
async def artifacts_page():
    """Artifact Workbench v1."""
    state = get_session_state()
    state.client = context.client

    # Refresh backend status before rendering layout
    await state.refresh_status_summary()

    build_top_bar(state)
    build_left_nav(state, active_page="/artifacts")
    build_right_rail(state)

    from gui.api_client import get_api_client

    api_client = get_api_client()

    # Refresh status summary for backend reachability check
    await state.refresh_status_summary()

    # Selected artifact state
    selected_artifact_id = {"id": None}

    with ui.row().classes("flex-1").style(f"background:{C_GROUND}; overflow:hidden; min-height:calc(100vh - 44px);"):
        # ── Left: Artifact List ──────────────────────────────
        with (
            ui.column()
            .classes("")
            .style(
                "width:280px; min-width:240px; max-width:320px; "
                f"border-right:0.5px solid {C_INK40}; overflow-y:auto; "
                f"padding:0; background:{C_GROUND};"
            )
        ):
            # Title
            with ui.row().classes("w-full items-center").style("padding:16px 16px 8px 16px;"):
                ui.label("Artifact Workbench").style(
                    f"font-family:{F_SANS}; font-size:18px; font-weight:700; color:{C_CREAM};"
                )

            # Dashboard summary
            with ui.row().classes("w-full").style("padding:0 16px 12px 16px;"):
                summary_label = ui.label("Loading...").style(f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};")

            # Artifact list component
            artifact_list_container = (
                ui.column().classes("w-full").style("padding:0 8px 8px 8px; flex:1; overflow-y:auto;")
            )

            # Render artifact list into container
            def on_select(artifact_id: str):
                """Handle artifact selection — show detail."""
                selected_artifact_id["id"] = artifact_id
                _render_detail(detail_area, selected_artifact_id, api_client, summary_label, state)

            def on_refresh():
                """Handle list refresh — also update summary."""
                _update_summary(summary_label, api_client, state)
                if selected_artifact_id["id"]:
                    _render_detail(detail_area, selected_artifact_id, api_client, summary_label, state)

            with artifact_list_container:
                render_artifact_list(
                    api_client,
                    on_select=on_select,
                    on_refresh=on_refresh,
                )

        # ── Right: Artifact Detail ───────────────────────────
        detail_area = ui.column().classes("flex-1").style(f"padding:24px; overflow-y:auto; background:{C_GROUND};")

        # Initial empty state
        with detail_area:
            ui.label("Select an artifact to view details").style(
                f"font-size:14px; color:{C_MUTED}; font-family:{F_SANS}; margin-top:48px;"
            )
            ui.label(
                "Use the list on the left to browse artifacts.\n"
                "Review, approve, reject, or export from the detail panel."
            ).style(
                f"font-size:11px; color:{C_INK40}; font-family:{F_MONO}; "
                f"margin-top:8px; white-space:pre-wrap; line-height:1.6;"
            )

    # Load summary
    _update_summary(summary_label, api_client, state)


def _render_detail(
    detail_area: ui.column,
    selected_artifact_id: dict,
    api_client: Any,
    summary_label: ui.label,
    state: Any,
) -> None:
    """Render artifact detail in the detail area."""
    artifact_id = selected_artifact_id.get("id")
    if not artifact_id:
        return

    detail_area.clear()

    with detail_area:
        render_artifact_detail(
            artifact_id,
            api_client,
            on_action_complete=lambda: _update_summary(summary_label, api_client, state),
        )


def _update_summary(summary_label: ui.label, api_client: Any, state: Any) -> None:
    """Update the dashboard summary label."""
    import asyncio

    async def _load():
        try:
            if not state.backend_reachable:
                summary_label.text = "UNAVAILABLE — backend unreachable"
                summary_label.style(f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};")
                return
            dashboard = await api_client.get_artifact_dashboard()
            counts = dashboard.get("counts", {})
            total = sum(counts.values())
            pending = dashboard.get("total_pending_review", 0)
            approved = counts.get("APPROVED", 0)
            summary_label.text = f"{total} artifacts | {pending} pending review | {approved} approved"
        except Exception:
            summary_label.text = "UNAVAILABLE — backend unreachable"
            summary_label.style(f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};")

    asyncio.ensure_future(_load())
