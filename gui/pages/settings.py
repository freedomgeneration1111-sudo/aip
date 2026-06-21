"""AIP Settings Page — Route: /settings

Settings Workbench v1 — UI Cycle 14.

Shows honest system configuration:
  - Dogfood mode
  - Model slots with provider status
  - API key status (configured / not configured — never values)
  - Link to maintenance for system health

All values are honest — "Not configured" if not configured,
"Unavailable" if backend is down. No fake data.

Import boundary: imports only gui.* (no aip.* imports).
"""

from __future__ import annotations

import asyncio
import logging

from nicegui import context, ui

from gui.api_client import get_api_client
from gui.components.layout import build_left_nav, build_top_bar
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
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_LG,
)

log = logging.getLogger("gui.pages.settings")


@ui.page("/settings")
async def settings_page():
    """Settings Workbench v1 — model slots, API keys, dogfood mode."""
    state = get_session_state()
    state.client = context.client

    # Refresh status summary for backend reachability check
    await state.refresh_status_summary()

    build_top_bar(state)
    build_left_nav(state, active_page="/settings")

    api_client = get_api_client()

    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:24px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Title
        ui.label("Settings").style(f"font-family:{F_SANS}; font-size:28px; font-weight:700; color:{C_CREAM};")
        ui.label("Model slots, API keys, and dogfood status.").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:20px;"
        )

        # ── Backend Status ──────────────────────────────────────
        with _settings_card("Backend Status"):
            _card_header("BACKEND STATUS")
            with ui.column().style("padding:16px;"):
                if not state.backend_reachable:
                    ui.label("UNAVAILABLE — backend unreachable").style(
                        f"font-size:12px; color:{C_ERR_FG}; font-family:{F_MONO};"
                    )
                else:
                    ui.label("REACHABLE").style(f"font-size:12px; color:{C_OK_FG}; font-family:{F_MONO};")

        # ── Dogfood Mode ────────────────────────────────────────
        with _settings_card("Dogfood Mode"):
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
                ui.label(mode).style(f"font-size:18px; font-weight:700; color:{mc}; font-family:{F_MONO};")
                if mode == "DIRECT MODEL ONLY":
                    ui.label("No retrieval. No corpus. No actors. No artifact lifecycle.").style(
                        f"font-size:11px; color:{C_ERR_FG};"
                    )
                elif mode == "BARE":
                    ui.label("Backend up — actors and retrieval status vary.").style(
                        f"font-size:11px; color:{C_WARN_FG};"
                    )
                elif mode == "DEGRADED":
                    ui.label("Some subsystems down.").style(f"font-size:11px; color:{C_WARN_FG};")
                else:
                    ui.label("All subsystems operational.").style(f"font-size:11px; color:{C_OK_FG};")

        # ── Model Slots ─────────────────────────────────────────
        with _settings_card("Model Slots"):
            _card_header("MODEL SLOTS")
            slots_container = ui.column().classes("w-full").style("padding:16px;")
            with slots_container:
                ui.label("Loading...").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};")

        # ── API Key Status ──────────────────────────────────────
        with _settings_card("API Key Status"):
            _card_header("API KEY STATUS")
            with ui.column().style("padding:16px;"):
                # OpenRouter key
                has_key = api_client.has_openrouter_api_key()
                key_status = "Configured" if has_key else "Not configured"
                key_color = C_OK_FG if has_key else C_ERR_FG
                ui.label(f"OpenRouter: {key_status}").style(f"font-size:11px; color:{key_color}; font-family:{F_MONO};")

        # ── Navigation Link ─────────────────────────────────────
        with ui.row().classes("w-full items-center").style("padding:16px 0;"):
            ui.label("Model selection:").style(f"font-size:11px; color:{C_MUTED};")
            ui.link("Models Page", "/models").style(f"font-size:11px; color:{C_AMBER}; text-decoration:underline;")
            ui.label("|").style(f"font-size:11px; color:{C_INK40};")
            ui.label("System health:").style(f"font-size:11px; color:{C_MUTED};")
            ui.link("Maintenance Page", "/maintenance").style(
                f"font-size:11px; color:{C_AMBER}; text-decoration:underline;"
            )
            ui.label("|").style(f"font-size:11px; color:{C_INK40};")
            ui.link("Dashboard", "/").style(f"font-size:11px; color:{C_AMBER}; text-decoration:underline;")

    # ── Load model slots data ────────────────────────────────────
    async def _load_model_slots():
        """Fetch and render model slot data."""
        slots_container.clear()
        with slots_container:
            if not state.backend_reachable:
                ui.label("UNAVAILABLE — backend unreachable").style(
                    f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};"
                )
                return

            try:
                # Try the text-generation-slots endpoint
                slots_data = await api_client.list_text_generation_slots()
                slots = slots_data.get("slots", [])
                ci_mode = slots_data.get("ci_mode", False)

                if ci_mode:
                    ui.label("CI mode: ON (stub providers)").style(
                        f"font-size:10px; color:{C_AMBER}; font-family:{F_MONO}; margin-bottom:8px;"
                    )

                if not slots:
                    ui.label("No model slots configured").style(
                        f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};"
                    )
                    return

                for slot in slots:
                    if isinstance(slot, dict):
                        name = slot.get("slot_name", slot.get("name", "?"))
                        model = slot.get("model", "not set")
                        provider = slot.get("provider", "unknown")
                        active = slot.get("active", False)

                        # Status indicator
                        if model and model != "not set":
                            dot = "●"
                            color = C_OK_FG if active else C_WARN_FG
                        else:
                            dot = "○"
                            color = C_MUTED

                        with ui.row().classes("w-full items-center").style("margin:2px 0;"):
                            ui.label(dot).style(f"font-size:10px; color:{color}; margin-right:6px;")
                            ui.label(f"{name}").style(
                                f"font-size:11px; font-weight:600; color:{C_CREAM}; "
                                f"font-family:{F_MONO}; min-width:100px;"
                            )
                            ui.label(f"{model}").style(f"font-size:11px; color:{color}; font-family:{F_MONO}; flex:1;")
                            ui.label(f"({provider})").style(f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};")

            except Exception as exc:
                log.warning("settings_model_slots_load_failed: %s", exc)
                ui.label(f"Failed to load model slots: {exc}").style(
                    f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};"
                )

    asyncio.create_task(_load_model_slots())


# ── Card helpers ──────────────────────────────────────────────────────


def _settings_card(title: str):
    """Create a styled settings card container."""
    return (
        ui.card()
        .classes("w-full")
        .style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_LG}; padding:0; margin-bottom:16px; "
            f"min-width:300px; max-width:600px;"
        )
    )


def _card_header(title: str):
    """Render a card header row."""
    with ui.row().classes("w-full items-center").style(f"padding:12px 16px; border-bottom:0.5px solid {C_INK40};"):
        ui.label(title).style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; color:{C_AMBER}; text-transform:uppercase;"
        )
