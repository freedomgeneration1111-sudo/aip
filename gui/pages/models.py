"""AIP Models Page — Route: /models

Model Catalog — browse, search, and enable/disable models from the
OpenRouter catalog for use in the Ask dropdown.

This page replaces the legacy /models page from gui/main.py with an
active Operator Console page that uses API-first backend paths:
  - GET  /api/v1/models/library        — list enabled_models table
  - POST /api/v1/models/library/fetch  — fetch from OpenRouter (DEFINER-only)
  - PATCH /api/v1/models/library  — toggle enabled (DEFINER-only, body-based)

Honesty rules:
  - If OpenRouter API key is missing, show NEEDS_CONFIGURATION.
  - If catalog fetch fails, show the error honestly.
  - If no models are in the library, show empty/needs-fetch state.
  - Never pretend catalog models are available if fetch failed.

Import boundary: imports only gui.* (no aip.* imports).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_SURFACE,
    F_MONO,
    F_SANS,
    R_LG,
)

log = logging.getLogger("gui.pages.models")


@ui.page("/models")
async def models_page():
    """Model Catalog — browse, search, and enable models for Ask dropdown."""
    state = get_session_state()
    state.client = context.client
    api = get_api_client()

    await state.refresh_status_summary()

    build_top_bar(state)
    build_left_nav(state, active_page="/models")

    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:24px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Title
        ui.label("Models").style(f"font-family:{F_SANS}; font-size:28px; font-weight:700; color:{C_CREAM};")
        ui.label("Browse and select models for the Ask dropdown.").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:20px;"
        )

        # ── API Key Status ────────────────────────────────────────
        with _models_card("API Key"):
            _card_header("OPENROUTER API KEY")
            with ui.column().style("padding:16px;"):
                has_key = api.has_openrouter_api_key()
                if has_key:
                    ui.label("Configured").style(f"font-size:12px; color:{C_OK_FG}; font-family:{F_MONO};")
                else:
                    ui.label("NOT CONFIGURED").style(f"font-size:12px; color:{C_ERR_FG}; font-family:{F_MONO};")
                    ui.label(
                        "Set AIP_OPENAI_API_KEY in .env or enter it on the Ask page. Catalog fetch requires an API key."
                    ).style(f"font-size:11px; color:{C_MUTED}; margin-top:4px;")

        # ── Fetch Controls ────────────────────────────────────────
        with _models_card("Catalog Fetch"):
            _card_header("OPENROUTER CATALOG")
            with ui.column().style("padding:16px;"):
                fetch_status_label = ui.label("").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};")
                ui.button(
                    "Fetch from OpenRouter",
                    on_click=lambda: asyncio.create_task(
                        _handle_fetch(
                            api,
                            fetch_status_label,
                            models_container,
                            search_input,
                        ),
                    ),
                ).props("unelevated dense").style(f"background:{C_AMBER}; color:#000; font-size:11px; margin-top:8px;")

        # ── Search / Filter ───────────────────────────────────────
        with _models_card("Search"):
            _card_header("FILTER MODELS")
            with ui.column().style("padding:16px;"):
                search_input = (
                    ui.input(
                        placeholder="Search model name or ID...",
                        on_change=lambda: asyncio.create_task(_handle_search(api, search_input, models_container)),
                    )
                    .props("dense outlined dark")
                    .classes("w-full")
                    .style(f"font-family:{F_MONO}; font-size:12px; color:{C_CREAM};")
                )

        # ── Model List ────────────────────────────────────────────
        with _models_card("Model Library"):
            _card_header("MODELS")
            models_container = ui.column().classes("w-full").style("padding:16px;")
            with models_container:
                ui.label("Loading...").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};")

        # ── Link to Ask ───────────────────────────────────────────
        with ui.row().classes("w-full items-center").style("padding:16px 0;"):
            ui.label("Use selected models:").style(f"font-size:11px; color:{C_MUTED};")
            ui.link("Ask Page", "/ask").style(f"font-size:11px; color:{C_AMBER}; text-decoration:underline;")
            ui.label("|").style(f"font-size:11px; color:{C_INK60};")
            ui.link("Settings", "/settings").style(f"font-size:11px; color:{C_AMBER}; text-decoration:underline;")

    # ── Initial load ────────────────────────────────────────────
    await _load_models(api, models_container, search_input)


# ── Async handlers ──────────────────────────────────────────────────


async def _load_models(
    api: Any,
    container: ui.column,
    search_input: ui.input,
) -> None:
    """Load and render the model library list."""
    container.clear()
    with container:
        if not get_session_state().backend_reachable:
            ui.label("UNAVAILABLE — backend unreachable").style(
                f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};"
            )
            return

        try:
            models = await api.list_model_library(enabled_only=False)
        except Exception as exc:
            log.warning("models_page_load_failed: %s", exc)
            ui.label(f"Failed to load models: {exc}").style(f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};")
            return

        if not models:
            ui.label("No models in library. Fetch from OpenRouter to populate.").style(
                f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};"
            )
            return

        # Apply search filter if present
        filter_text = (search_input.value or "").strip().lower()
        if filter_text:
            models = [
                m
                for m in models
                if filter_text in m.get("model_id", "").lower() or filter_text in m.get("display_name", "").lower()
            ]

        enabled_count = sum(1 for m in models if m.get("enabled") == 1)
        ui.label(f"{enabled_count} enabled / {len(models)} total").style(
            f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; margin-bottom:8px;"
        )

        for m in models:
            _render_model_row(api, container, m)


def _render_model_row(api: Any, container: ui.column, model: dict) -> None:
    """Render a single model row with toggle checkbox."""
    with container:
        model_id = model.get("model_id", "?")
        display_name = model.get("display_name", model_id)
        enabled = model.get("enabled") == 1
        cost_in = model.get("cost_input_per_million")
        cost_out = model.get("cost_output_per_million")
        ctx_len = model.get("context_length")

        with (
            ui.row()
            .classes("w-full items-center")
            .style("padding:4px 0; border-bottom:0.5px solid rgba(255,255,255,0.05);")
        ):
            # Enable/disable checkbox
            ui.checkbox(
                value=enabled,
                on_change=lambda checked, mid=model_id: asyncio.create_task(
                    _handle_toggle(api, mid, checked),
                ),
            ).props("dense dark").style("margin-right:8px;")

            # Model info
            name_color = C_CREAM if enabled else C_MUTED
            ui.label(display_name).style(
                f"font-size:11px; font-weight:600; color:{name_color}; "
                f"font-family:{F_MONO}; min-width:200px; max-width:300px; "
                f"overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
            )
            ui.label(model_id).style(
                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; "
                f"flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
            )

            # Cost info
            if cost_in is not None or cost_out is not None:
                in_str = f"${cost_in:.3f}" if cost_in is not None else "?"
                out_str = f"${cost_out:.3f}" if cost_out is not None else "?"
                ui.label(f"${in_str}/${out_str}/M").style(
                    f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO}; min-width:90px; text-align:right;"
                )

            # Context length
            if ctx_len:
                ctx_str = f"{ctx_len // 1000}k" if ctx_len >= 1000 else str(ctx_len)
                ui.label(f"ctx:{ctx_str}").style(
                    f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO}; min-width:60px; text-align:right;"
                )


async def _handle_toggle(api: Any, model_id: str, enabled: bool) -> None:
    """Handle model enable/disable toggle."""
    result = await api.toggle_model_enabled(model_id, enabled)
    if "error" in result:
        log.error("toggle_model_failed: %s", result["error"])
        ui.notify(f"Failed to toggle {model_id}: {result['error']}", type="warning", position="top")
    else:
        status = "enabled" if enabled else "disabled"
        ui.notify(f"{model_id} {status}", type="positive", position="top")


async def _handle_fetch(
    api: Any,
    status_label: ui.label,
    container: ui.column,
    search_input: ui.input,
) -> None:
    """Handle fetch from OpenRouter button click."""
    if not api.has_openrouter_api_key():
        status_label.set_text("NEEDS_CONFIGURATION — Set API key first")
        status_label.style(f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};")
        ui.notify("OpenRouter API key not configured", type="warning", position="top")
        return

    status_label.set_text("Fetching from OpenRouter...")
    status_label.style(f"font-size:11px; color:{C_AMBER}; font-family:{F_MONO};")

    result = await api.fetch_model_library()

    if "error" in result:
        status_label.set_text(f"FETCH FAILED: {result['error']}")
        status_label.style(f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};")
        ui.notify(f"Fetch failed: {result['error']}", type="warning", position="top")
    else:
        fetched = result.get("fetched", 0)
        new = result.get("new_models_added", 0)
        status_label.set_text(f"Fetched {fetched} models, {new} new added")
        status_label.style(f"font-size:11px; color:{C_OK_FG}; font-family:{F_MONO};")
        ui.notify(f"Catalog updated: {new} new models", type="positive", position="top")

    # Reload the model list
    await _load_models(api, container, search_input)


async def _handle_search(
    api: Any,
    search_input: ui.input,
    container: ui.column,
) -> None:
    """Handle search/filter input change."""
    await _load_models(api, container, search_input)


# ── Card helpers (same pattern as settings.py) ──────────────────────


def _models_card(title: str):
    """Create a styled models card container."""
    return (
        ui.card()
        .classes("w-full")
        .style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_LG}; padding:0; margin-bottom:16px; "
            f"min-width:300px; max-width:800px;"
        )
    )


def _card_header(title: str):
    """Render a card header row."""
    with ui.row().classes("w-full items-center").style(f"padding:12px 16px; border-bottom:0.5px solid {C_INK40};"):
        ui.label(title).style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; color:{C_AMBER}; text-transform:uppercase;"
        )
