"""AIP Right Rail — persistent right-side panel with system status.

Shows live data from the consolidated GET /api/v1/status/summary endpoint.
If backend is unreachable, shows "UNAVAILABLE" honestly.
Never fakes healthy data.

UI Cycle 3: Now consumes state.status_summary for all sections,
falling back to individual endpoint data when summary is unavailable.
"""

from __future__ import annotations

import logging

from nicegui import ui

from gui.state import GuiState
from gui.theme import (
    C_AMBER,
    C_DOGFOOD_BARE,
    C_DOGFOOD_DEGRADED,
    C_DOGFOOD_FULL,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    C_WARN_FG,
    F_MONO,
)

log = logging.getLogger("gui.panels.right_rail")


def build_right_rail(state: GuiState) -> None:
    """Build the full right rail with live data from status_summary.

    Sections:
      1. DOGFOOD MODE — mode + color coding
      2. ACTOR STATUS — Beast/Vigil/Sexton with active/idle/degraded/failed
      3. RETRIEVAL HEALTH — channels with per-channel state
      4. PENDING GATES — count of pending reviews
      5. WARNINGS — list of active warnings

    All data is derived from state.status_summary when available,
    falling back to individual endpoint data otherwise.
    """
    with ui.right_drawer().style(
        f"background:{C_GROUND}; border-left:0.5px solid {C_INK40}; "
        f"width:260px; min-width:260px; padding:12px; overflow-y:auto;"
    ):
        # Section 1: DOGFOOD MODE
        _section_label("DOGFOOD MODE")
        _dogfood_section(state)

        _sep()

        # Section 2: ACTOR STATUS
        _section_label("ACTOR STATUS")
        _actor_section(state)

        _sep()

        # Section 3: RETRIEVAL HEALTH
        _section_label("RETRIEVAL HEALTH")
        _retrieval_section(state)

        _sep()

        # Section 4: PENDING GATES
        _section_label("PENDING GATES")
        _gates_section(state)

        _sep()

        # Section 5: WARNINGS
        _section_label("WARNINGS")
        _warnings_section(state)


def _section_label(text: str) -> None:
    """Render a section label."""
    ui.label(text).style(
        f"font-size:9px; font-weight:600; letter-spacing:1.5px; "
        f"color:{C_MUTED}; text-transform:uppercase; margin-bottom:4px;"
    )


def _sep() -> None:
    """Render a section separator."""
    ui.separator().style(f"background:{C_INK40}; margin:8px 0;")


def _dogfood_section(state: GuiState) -> None:
    """Render dogfood mode section."""
    mode = state.dogfood_mode
    mode_colors = {
        "FULL": C_DOGFOOD_FULL,
        "DIAGNOSTIC": C_DOGFOOD_FULL,
        "DEGRADED": C_DOGFOOD_DEGRADED,
        "BARE": C_DOGFOOD_BARE,
        "DIRECT MODEL ONLY": C_DOGFOOD_BARE,
    }
    mc = mode_colors.get(mode, C_MUTED)
    ui.label(mode).style(f"font-size:12px; font-family:{F_MONO}; color:{mc}; font-weight:600;")
    if mode == "DIRECT MODEL ONLY":
        ui.label("No retrieval. No corpus. No actors.").style(
            f"font-size:9px; color:{C_DOGFOOD_BARE}; font-family:{F_MONO};"
        )
    elif mode == "BARE":
        if state.backend_reachable:
            ui.label("Backend reachable — no actors or retrieval active.").style(
                f"font-size:9px; color:{C_AMBER}; font-family:{F_MONO};"
            )
        else:
            ui.label("No status data fetched yet.").style(
                f"font-size:9px; color:{C_AMBER}; font-family:{F_MONO};"
            )


def _actor_section(state: GuiState) -> None:
    """Render actor status section from status_summary data."""
    if not state.backend_reachable:
        ui.label("UNAVAILABLE — backend unreachable").style(f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};")
        return

    # Prefer actor data from consolidated summary
    summary = state.status_summary
    actor_summary = summary.get("actor_status_summary", state.actor_status)

    for actor_name in ("beast", "vigil", "sexton"):
        info = actor_summary.get(actor_name, {})
        if isinstance(info, dict):
            initialized = info.get("initialized", False)
            actor_state = info.get("state", "")

            if not initialized:
                dot = "○"
                color = C_MUTED
                status = "not configured"
            elif actor_state in ("active", "instantiated"):
                dot = "●"
                color = C_OK_FG
                status = "active"
            elif actor_state in ("degraded",):
                dot = "●"
                color = C_WARN_FG
                status = "degraded"
            elif actor_state in ("failed", "error"):
                dot = "●"
                color = C_ERR_FG
                status = "failed"
            elif actor_state == "not_configured":
                dot = "○"
                color = C_AMBER
                status = "not configured"
            elif initialized and not actor_state:
                dot = "●"
                color = C_OK_FG
                status = "active"
            else:
                dot = "○"
                color = C_MUTED
                status = actor_state if actor_state else "unknown"

            # Show last cycle time if available
            last_cycle = info.get("last_cycle_time")
            cycle_info = ""
            if last_cycle:
                try:
                    import datetime

                    if isinstance(last_cycle, (int, float)):
                        ts = datetime.datetime.fromtimestamp(last_cycle).strftime("%H:%M")
                        cycle_info = f" (last: {ts})"
                    elif isinstance(last_cycle, str):
                        cycle_info = f" (last: {last_cycle[11:16]})"
                except Exception:
                    pass

            with ui.row().classes("w-full items-center").style("margin:2px 0;"):
                ui.label(dot).style(f"font-size:10px; color:{color}; margin-right:4px;")
                ui.label(f"{actor_name.capitalize()}: {status}{cycle_info}").style(
                    f"font-size:11px; font-family:{F_MONO}; color:{color};"
                )
        else:
            ui.label(f"{actor_name.capitalize()}: UNKNOWN").style(
                f"font-size:11px; font-family:{F_MONO}; color:{C_MUTED};"
            )


def _retrieval_section(state: GuiState) -> None:
    """Render retrieval health section from status_summary data."""
    if not state.backend_reachable:
        for ch in ("Lexical", "Vector", "Graph", "CODEX", "Procedural"):
            ui.label(f"  {ch}: UNAVAILABLE").style(f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};")
        return

    # Prefer retrieval data from consolidated summary
    summary = state.status_summary
    retrieval_summary = summary.get("retrieval_health_summary", state.retrieval_health)

    if retrieval_summary:
        # Map channel names to display names
        channel_display = {
            "fts": "Lexical",
            "vector": "Vector",
            "graph": "Graph",
            "wiki": "CODEX",
            "corpus": "Corpus",
            "procedural": "Procedural",
            "codex": "CODEX",
        }

        for ch_key, ch_data in retrieval_summary.items():
            display_name = channel_display.get(ch_key, ch_key.capitalize())

            if isinstance(ch_data, dict):
                ch_state = ch_data.get("state", "unknown")
                latency = ch_data.get("latency_ms", None)

                if ch_state in ("available", "active"):
                    status = "OK"
                    color = C_OK_FG
                elif ch_state in ("not_configured", "not_wired"):
                    status = "NOT CONFIGURED"
                    color = C_AMBER
                elif ch_state == "degraded":
                    status = "DEGRADED"
                    color = C_WARN_FG
                elif ch_state == "unavailable":
                    status = "UNAVAILABLE"
                    color = C_ERR_FG
                elif ch_state == "empty":
                    status = "EMPTY"
                    color = C_MUTED
                else:
                    status = ch_state.upper()
                    color = C_MUTED

                latency_info = f" ({latency}ms)" if latency is not None else ""
                ui.label(f"  {display_name}: {status}{latency_info}").style(
                    f"font-size:10px; font-family:{F_MONO}; color:{color};"
                )
            else:
                ui.label(f"  {display_name}: NO DATA").style(f"font-size:10px; font-family:{F_MONO}; color:{C_MUTED};")
    else:
        for ch in ("Lexical", "Vector", "Graph", "CODEX", "Procedural"):
            ui.label(f"  {ch}: NOT WIRED").style(f"font-size:10px; font-family:{F_MONO}; color:{C_MUTED};")


def _gates_section(state: GuiState) -> None:
    """Render pending gates count."""
    count = state.pending_gates_count
    color = C_AMBER if count > 0 else C_MUTED
    ui.label(f"{count} pending review{'s' if count != 1 else ''}").style(
        f"font-size:11px; font-family:{F_MONO}; color:{color};"
    )


def _warnings_section(state: GuiState) -> None:
    """Render active warnings."""
    if state.warnings:
        for w in state.warnings[:8]:
            ui.label(f"! {w}").style(f"font-size:10px; font-family:{F_MONO}; color:{C_DOGFOOD_BARE};")
    else:
        ui.label("None").style(f"font-size:10px; font-family:{F_MONO}; color:{C_MUTED};")
