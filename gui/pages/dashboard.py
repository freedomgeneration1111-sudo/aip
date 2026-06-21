"""AIP Dashboard Page — Route: /

'Can I trust AIP right now?'

Shows honest system health cards powered by the consolidated
GET /api/v1/status/summary endpoint. No fake healthy data.
Live data if available, 'unavailable' / 'not_wired' if not.
"""

from __future__ import annotations

import logging

from nicegui import context, ui

from gui.components.layout import build_left_nav, build_right_rail, build_top_bar
from gui.state import GuiState, get_session_state
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_LG,
)

log = logging.getLogger("gui.pages.dashboard")


@ui.page("/")
async def dashboard_page():
    """Dashboard — 'Can I trust AIP right now?'"""
    state = get_session_state()
    state.client = context.client

    # Single-call status refresh using the consolidated endpoint
    await state.refresh_status_summary()

    # Build layout
    build_top_bar(state)
    build_left_nav(state, active_page="/")
    build_right_rail(state)

    # Main content
    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:24px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Heading
        ui.label("Can I trust AIP right now?").style(
            f"font-family:{F_SANS}; font-size:28px; font-weight:700; color:{C_CREAM}; margin-bottom:4px;"
        )
        ui.label("Honest system status — no fake healthy indicators.").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:24px;"
        )

        # Row 1: Dogfood Mode | Backend Health | Corpus Health | Retrieval Health
        with ui.row().classes("w-full gap-4").style("flex-wrap:wrap;"):
            _dogfood_card(state)
            _backend_health_card(state)
            _corpus_health_card(state)
            _retrieval_health_card(state)

        # Row 2: Actor Health | Embedding/Backfill | Review Queue | Wiki/CODEX
        with ui.row().classes("w-full gap-4").style("flex-wrap:wrap; margin-top:16px;"):
            _actor_health_card(state)
            _embedding_backfill_card(state)
            _review_queue_card(state)
            _wiki_codex_card(state)

        # Row 3: Model Slots (full width)
        with ui.row().classes("w-full gap-4").style("flex-wrap:wrap; margin-top:16px;"):
            _model_slots_card(state)

        # Row 4: Warnings | Recent Activity
        with ui.row().classes("w-full gap-4").style("flex-wrap:wrap; margin-top:16px;"):
            _warnings_card(state)
            _recent_activity_card(state)


# ── Card helpers ───────────────────────────────────────────────────────


def _card(title: str, navigate_to: str | None = None):
    """Create a styled card container with optional click-through navigation."""
    card = (
        ui.card()
        .classes("w-full")
        .style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_LG}; padding:0; min-width:260px; max-width:400px; flex:1;"
            + ("cursor:pointer;" if navigate_to else "")
        )
    )
    if navigate_to:
        card.on("click", lambda: ui.navigate.to(navigate_to))
    return card


def _card_header(title: str):
    """Render a card header row."""
    with ui.row().classes("w-full items-center").style(f"padding:12px 16px; border-bottom:0.5px solid {C_INK40};"):
        ui.label(title).style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; color:{C_AMBER}; text-transform:uppercase;"
        )


def _unavailable_label(msg: str = "UNAVAILABLE — backend unreachable"):
    """Render an unavailable label with error color."""
    ui.label(msg).style(f"font-size:11px; color:{C_ERR_FG};")


def _status_dot(ok: bool) -> str:
    """Return a colored dot character."""
    return "●" if ok else "○"


def _status_color(ok: bool) -> str:
    """Return a color for a boolean status."""
    return C_OK_FG if ok else C_ERR_FG


# ── Individual dashboard cards ─────────────────────────────────────────


def _dogfood_card(state: GuiState) -> None:
    """Dogfood Mode card — the primary trust indicator."""
    with _card("Dogfood Mode"):
        _card_header("DOGFOOD MODE")
        with ui.column().style("padding:16px;"):
            mode = state.dogfood_mode
            mode_colors = {
                "FULL": C_OK_FG,
                "DIAGNOSTIC": C_OK_FG,
                "DEGRADED": C_WARN_FG,
                "BARE": C_AMBER,
                "DIRECT MODEL ONLY": C_ERR_FG,
            }
            mc = mode_colors.get(mode, C_MUTED)
            ui.label(mode).style(f"font-size:22px; font-weight:700; color:{mc}; font-family:{F_MONO};")
            if mode == "DIRECT MODEL ONLY":
                ui.label("Backend unreachable. No retrieval. No corpus. No actors. No artifact lifecycle.").style(
                    f"font-size:11px; color:{C_ERR_FG}; margin-top:4px;"
                )
            elif mode == "BARE":
                ui.label("Backend reachable but no actors or retrieval active.").style(
                    f"font-size:11px; color:{C_WARN_FG}; margin-top:4px;"
                )
            elif mode == "DEGRADED":
                ui.label("Some subsystems down. Check details below.").style(
                    f"font-size:11px; color:{C_WARN_FG}; margin-top:4px;"
                )
            elif mode == "DIAGNOSTIC":
                ui.label("Running in diagnostic mode. Full subsystem access.").style(
                    f"font-size:11px; color:{C_OK_FG}; margin-top:4px;"
                )
            else:
                ui.label("All subsystems operational. Full dogfood active.").style(
                    f"font-size:11px; color:{C_OK_FG}; margin-top:4px;"
                )


def _backend_health_card(state: GuiState) -> None:
    """Backend/API Health card."""
    with _card("Backend Health"):
        _card_header("BACKEND / API HEALTH")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            bh = summary.get("backend_health", {})
            if not bh:
                ui.label("No backend health data.").style(f"font-size:11px; color:{C_MUTED};")
                return

            status = bh.get("status", "unknown")
            status_colors = {"ok": C_OK_FG, "degraded": C_WARN_FG, "unhealthy": C_ERR_FG}
            sc = status_colors.get(status, C_MUTED)
            ui.label(f"Status: {status.upper()}").style(
                f"font-size:14px; font-weight:600; color:{sc}; font-family:{F_MONO};"
            )

            uptime = bh.get("uptime_seconds", 0)
            if uptime > 0:
                hours, remainder = divmod(uptime, 3600)
                minutes, _ = divmod(remainder, 60)
                ui.label(f"Uptime: {int(hours)}h {int(minutes)}m").style(
                    f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};"
                )

            db_ok = bh.get("db_writable", None)
            if db_ok is not None:
                label = "DB: WRITABLE" if db_ok else "DB: READ-ONLY"
                color = C_OK_FG if db_ok else C_ERR_FG
                ui.label(label).style(f"font-size:11px; color:{color}; font-family:{F_MONO};")

            ci = bh.get("ci_mode", None)
            if ci:
                ui.label("CI mode: ON (stub providers)").style(
                    f"font-size:11px; color:{C_AMBER}; font-family:{F_MONO};"
                )


def _corpus_health_card(state: GuiState) -> None:
    """Corpus Health card — turns, tagged, embedded, coverage %."""
    with _card("Corpus Health", navigate_to="/corpus"):
        _card_header("CORPUS HEALTH")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            cs = summary.get("corpus_summary", {})
            if not cs:
                # Fall back to separate API call
                ui.label("No corpus data in summary.").style(f"font-size:11px; color:{C_MUTED};")
                return

            total = cs.get("total_turns", 0)
            tagged = cs.get("tagged", 0)
            embedded = cs.get("embedded", 0)
            untagged = cs.get("untagged", 0)
            unembedded = cs.get("unembedded", total - embedded)

            ui.label(f"Turns: {total:,}").style(f"font-size:12px; color:{C_CREAM}; font-family:{F_MONO};")
            ui.label(f"Tagged: {tagged:,}  |  Untagged: {untagged:,}").style(
                f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};"
            )
            ui.label(f"Embedded: {embedded:,}  |  Unembedded: {unembedded:,}").style(
                f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};"
            )

            # Embedding coverage percentage
            if total > 0:
                pct = round(embedded / total * 100, 1)
                pct_color = C_OK_FG if pct >= 50 else C_WARN_FG if pct >= 10 else C_ERR_FG
                ui.label(f"Coverage: {pct}%").style(
                    f"font-size:12px; font-weight:600; color:{pct_color}; font-family:{F_MONO};"
                )
                if pct < 50:
                    ui.label("Low coverage — vector retrieval limited.").style(f"font-size:10px; color:{C_WARN_FG};")


def _retrieval_health_card(state: GuiState) -> None:
    """Retrieval Health card — per-channel status from consolidated summary."""
    with _card("Retrieval Health", navigate_to="/retrieval"):
        _card_header("RETRIEVAL HEALTH")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            rhs = summary.get("retrieval_health_summary", {})
            if not rhs:
                ui.label("No retrieval health data.").style(f"font-size:11px; color:{C_MUTED};")
                return

            available_count = 0
            total_count = 0
            for ch_name, ch_data in rhs.items():
                total_count += 1
                if not isinstance(ch_data, dict):
                    continue
                ch_state = ch_data.get("state", "unknown")
                if ch_state in ("available", "active"):
                    status_text = "OK"
                    color = C_OK_FG
                    available_count += 1
                elif ch_state in ("not_configured", "not_wired"):
                    status_text = "NOT CONFIGURED"
                    color = C_AMBER
                elif ch_state in ("degraded",):
                    status_text = "DEGRADED"
                    color = C_WARN_FG
                    available_count += 0.5
                elif ch_state in ("unavailable",):
                    status_text = "UNAVAILABLE"
                    color = C_ERR_FG
                else:
                    status_text = ch_state.upper()
                    color = C_MUTED
                ui.label(f"{ch_name}: {status_text}").style(f"font-size:11px; color:{color}; font-family:{F_MONO};")

            if total_count > 0:
                ui.label(f"{available_count:.0f}/{total_count} channels OK").style(
                    f"font-size:11px; font-weight:600; color:{C_CREAM}; font-family:{F_MONO}; margin-top:4px;"
                )


def _actor_health_card(state: GuiState) -> None:
    """Actor Health card — Beast, Vigil, Sexton status."""
    with _card("Actor Health"):
        _card_header("ACTOR HEALTH")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            actor_summary = summary.get("actor_status_summary", state.actor_status)

            for actor_name in ("beast", "vigil", "sexton"):
                info = actor_summary.get(actor_name, {})
                if isinstance(info, dict):
                    initialized = info.get("initialized", False)
                    actor_state = info.get("state", "")
                    if not initialized:
                        status = "NOT CONFIGURED"
                        color = C_MUTED
                    elif actor_state in ("active", "instantiated"):
                        status = "ACTIVE"
                        color = C_OK_FG
                    elif actor_state in ("degraded",):
                        status = "DEGRADED"
                        color = C_WARN_FG
                    elif actor_state in ("failed", "error"):
                        status = "FAILED"
                        color = C_ERR_FG
                    elif actor_state == "not_configured":
                        status = "NOT CONFIGURED"
                        color = C_AMBER
                    elif initialized and not actor_state:
                        status = "ACTIVE"
                        color = C_OK_FG
                    else:
                        status = actor_state.upper() if actor_state else "UNKNOWN"
                        color = C_MUTED

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

                    ui.label(f"{actor_name.capitalize()}: {status}{cycle_info}").style(
                        f"font-size:11px; color:{color}; font-family:{F_MONO};"
                    )
                else:
                    ui.label(f"{actor_name.capitalize()}: UNKNOWN").style(
                        f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};"
                    )


def _embedding_backfill_card(state: GuiState) -> None:
    """Embedding / Backfill status card."""
    with _card("Embedding / Backfill", navigate_to="/corpus"):
        _card_header("EMBEDDING / BACKFILL")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            eb = summary.get("embedding_backfill_summary", {})
            if not eb:
                ui.label("No embedding backfill data.").style(f"font-size:11px; color:{C_MUTED};")
                return

            backfill_state = eb.get("state", eb.get("backfill_state", "unknown"))
            state_colors = {
                "embedded": C_OK_FG,
                "backfill_running": C_OK_FG,
                "configured_idle": C_AMBER,
                "backfill_pending": C_AMBER,
                "partially_embedded": C_WARN_FG,
                "degraded": C_WARN_FG,
                "not_configured": C_ERR_FG,
                "failed": C_ERR_FG,
            }
            sc = state_colors.get(backfill_state, C_MUTED)
            ui.label(f"State: {backfill_state}").style(
                f"font-size:12px; font-weight:600; color:{sc}; font-family:{F_MONO};"
            )

            pct = eb.get("percentage")
            if pct is not None:
                ui.label(f"Coverage: {pct}%").style(f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};")


def _review_queue_card(state: GuiState) -> None:
    """Review Queue card."""
    with _card("Review Queue", navigate_to="/artifacts"):
        _card_header("REVIEW QUEUE")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            rq = summary.get("review_queue_summary", {})
            if rq and "count" in rq:
                count = rq["count"]
                rq_state = rq.get("state", "")
                color = C_AMBER if count > 0 else C_OK_FG
                ui.label(f"{count} pending review{'s' if count != 1 else ''}").style(
                    f"font-size:14px; font-weight:600; color:{color}; font-family:{F_MONO};"
                )
                if rq_state:
                    ui.label(f"Queue state: {rq_state}").style(
                        f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};"
                    )
            else:
                count = state.pending_gates_count
                color = C_AMBER if count > 0 else C_OK_FG
                ui.label(f"{count} pending review{'s' if count != 1 else ''}").style(
                    f"font-size:14px; font-weight:600; color:{color}; font-family:{F_MONO};"
                )


def _wiki_codex_card(state: GuiState) -> None:
    """Wiki / CODEX Health card."""
    with _card("Wiki / CODEX", navigate_to="/wiki"):
        _card_header("WIKI / CODEX")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            ws = summary.get("wiki_summary", {})
            if not ws:
                ui.label("No wiki data available.").style(f"font-size:11px; color:{C_MUTED};")
                return

            total = ws.get("total", 0)
            approved = ws.get("approved", 0)
            generated = ws.get("generated", 0)
            wiki_state = ws.get("state", "")

            ui.label(f"Total articles: {total}").style(f"font-size:12px; color:{C_CREAM}; font-family:{F_MONO};")
            ui.label(f"Approved: {approved}  |  Generated: {generated}").style(
                f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};"
            )
            if wiki_state:
                ui.label(f"State: {wiki_state}").style(f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};")


def _model_slots_card(state: GuiState) -> None:
    """Model Slots card — shows configured slots with API key status (not values)."""
    with _card("Model Slots", navigate_to="/settings"):
        _card_header("MODEL SLOTS")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            slots = summary.get("model_slot_summary", [])
            if not slots:
                ui.label("No model slot data.").style(f"font-size:11px; color:{C_MUTED};")
                return

            for slot in slots:
                if isinstance(slot, dict):
                    name = slot.get("slot_name", "?")
                    model = slot.get("model", "not set")
                    provider = slot.get("provider", "")
                    api_key_status = slot.get("api_key", "unknown")

                    # Show "configured" or "missing" for API key — never the actual key
                    ui.label(f"{name}: {model} ({provider}) key:{api_key_status}").style(
                        f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};"
                    )


def _warnings_card(state: GuiState) -> None:
    """Warnings card — shows current warnings from the summary."""
    with _card("Warnings", navigate_to="/maintenance"):
        _card_header("CURRENT WARNINGS")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            if state.warnings:
                for w in state.warnings[:8]:
                    ui.label(f"! {w}").style(f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};")
            else:
                ui.label("None").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};")


def _recent_activity_card(state: GuiState) -> None:
    """Recent Activity card — shows recent activity from the summary."""
    with _card("Recent Activity"):
        _card_header("RECENT ACTIVITY")
        with ui.column().style("padding:16px;"):
            if not state.backend_reachable:
                _unavailable_label()
                return

            summary = state.status_summary
            activity = summary.get("recent_activity", [])
            if activity and isinstance(activity, list):
                for entry in activity[:10]:
                    if isinstance(entry, dict):
                        text = entry.get("description", entry.get("text", str(entry)))
                    else:
                        text = str(entry)
                    ui.label(f"- {text}").style(f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};")
            else:
                ui.label("No recent activity data.").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};")
