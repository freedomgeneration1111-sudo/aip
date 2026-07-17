"""AIP Ask Page — Route: /ask

THE MOST IMPORTANT PAGE — the Ask Workbench.

UI Cycle 4 upgrades the migrated Ask page into the Full Dogfood Ask Workbench.
Every assistant answer is now inspectable, source-grounded, and linkable, with
visible retrieval health and degraded/direct-model warnings.

Multi-Model selection (current cycle): the chat header now uses a SINGLE
multi-select checkbox dropdown for picking N models from the unified
"available models" pool (OpenRouter library IDs + slot model IDs).
The send handler auto-routes based on count — no separate "Multi-Cast"
button is required:
  - 0 selected  → notify "pick a model" and bail
  - 1 selected  → normal single-model chat (WS route, uses the
    synthesis slot's configured model)
  - ≥2 selected → Multi-Cast Fusion (POST /beast/compare-models with
    ``skip_default_slots=True``). The selected models are sent as
    ``selected_model_ids`` (OpenRouter IDs); ``selected_model_slots``
    is always ``[]`` so the backend does NOT auto-add the default
    TOML slots. The ``beast`` slot is used ONLY for the Judge+Synth
    synthesis stages, not as a panel model. Models are NOT tied to
    actor slots/roles — the user picks N models from the unified
    dropdown, and the backend calls those N models directly via
    OpenRouter.
This restores the original "checkbox dropdown → auto-trigger synthesis"
UX. The separate "Multi-Cast: ON/OFF" button and the second row of
slot/library checkboxes were removed.

Flow:
  1. API key check on load
  2. Backend health check with 4s timeout
  3. Model slot loading from /api/v1/models/slots
  4. Session creation via POST /api/v1/sessions
  5. WebSocket chat via ws://backend/api/v1/chat/session_id
     (or Multi-Cast via POST /beast/compare-models when ≥2 models
     are selected in the multi-select dropdown)
  6. Message types: message, response, gate, error, pong
  7. Gate handling: approve/reject buttons for DEFINER gates
  8. Auto-save toggle with session update
  9. Direct OpenRouter fallback when backend unreachable — MUST show
     "DIRECT MODEL ONLY — NOT DOGFOOD" banner
  10. Per-answer status strip: retrieval healthy / degraded / lexical only /
      no sources / direct model only / trace unavailable
  11. Per-answer actions: Show Sources, Show Trace, Save as Artifact,
      Link Wiki, Beast Counsel, Model Council
      (Beast Counsel + Link Wiki require turn_id from the WS response —
      the backend now echoes turn_id back so these actions work)
  12. Source panel: drawer with source title/path, snippet, score, channel
  13. Trace panel: drawer with channels attempted/used, degradation, warnings
  14. Beast Counsel + Model Council panels open as centered modal dialogs
      (ui.dialog) — no longer ui.right_drawer (per "no right sidebar" rule)

CRITICAL RULES:
  - Direct model fallback must be labeled
    "DIRECT MODEL ONLY — NOT DOGFOOD — No retrieval. No corpus.
     No actors. No artifact lifecycle."
  - If retrieval trace/source data is unavailable, show unavailable honestly.
  - Do not create fake traces.
  - Do not silently save artifacts, mutate wiki, approve gates, or export.
  - Beast Counsel and Model Council are ADVISORY ONLY.
  - Models are NOT tied to actor slots/roles. Only the multimodel
    synthesis (Judge+Synth stages) is tied to the ``beast`` slot.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from nicegui import context, ui

from gui.components.answer_card import add_answer_card
from gui.components.beast_panel import BeastPanel
from gui.components.chat import add_message, add_system_message, build_chat_input
from gui.components.layout import build_left_nav, build_right_rail, build_top_bar, clear_active_extension, set_active_extension
from gui.components.modals import show_api_key_prompt
from gui.components.model_council_panel import ModelCouncilPanel
from gui.components.source_panel import SourcePanel
from gui.components.trace_panel import TracePanel
from gui.state import (
    GuiState,
    build_model_options,
    get_role_model,
    get_selected_models,
    get_session_state,
    refresh_enabled_models,
    set_role_model,
    set_selected_models,
)
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_DOGFOOD_BARE,
    C_ERR_BG,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_SURFACE,
    C_WARN_BG,
    C_WARN_FG,
    F_MONO,
    R_SM,
    btn_primary,
    btn_secondary,
)

log = logging.getLogger("gui.pages.ask")


@ui.page("/ask")
async def ask_page(extension: str = "", debug: str = "", concept: str = ""):
    """Ask Workbench — chat interface with backend or direct model fallback.

    Also serves as the ARISTOTLE tutoring interface when loaded with
    ?extension=aristotle. In ARISTOTLE mode the student sees only
    SOCRATES — no model selector, no state labels, no internal terminology.

    Query params (auto-injected by FastAPI):
      extension: "aristotle" → ARISTOTLE tutoring mode
      concept:   "<id>"       → pre-selected concept from concept map click
      debug:     "true"       → show debug panel in ARISTOTLE mode
    """
    # Detect ARISTOTLE mode from query param (FastAPI injects ?extension=… as kwarg)
    is_aristotle = extension == "aristotle"
    is_debug = debug == "true"

    try:
        if is_aristotle:
            await _ask_page_aristotle(concept_from_url=concept, is_debug=is_debug)
        else:
            await _ask_page_impl()
    except Exception as exc:
        log.exception("ask_page_crash: %s", exc)
        # Render minimal shell so the user sees something instead of blank white
        try:
            state = get_session_state()
            build_top_bar(state)
            build_left_nav(state, active_page="/ask")
            build_right_rail(state)
            with ui.card().style(
                f"background:{C_ERR_BG}; border:1px solid {C_ERR_FG}; border-radius:{R_SM}; padding:16px; margin:24px;"
            ):
                ui.label("Ask Workbench — Fatal Error").style(
                    f"font-size:16px; font-weight:700; color:{C_ERR_FG}; font-family:{F_SANS};"
                )
                ui.label(f"The Ask page crashed during initialization: {exc}").style(
                    f"font-size:12px; color:{C_CREAM}; font-family:{F_MONO}; margin-top:8px;"
                )
                ui.label("Check the console logs for details. The backend may be down or misconfigured.").style(
                    f"font-size:11px; color:{C_MUTED}; margin-top:4px;"
                )
        except Exception:
            # Last resort — if even the crash UI fails
            ui.label("Ask page failed to load. Check console logs.").style("color:red; padding:24px;")


async def _ask_page_impl():
    """Inner implementation of ask page, wrapped by crash boundary."""
    state = get_session_state()
    state.client = context.client

    # ── Initialize panels ────────────────────────────────────────
    source_panel = SourcePanel()
    trace_panel = TracePanel()
    beast_panel = BeastPanel()
    model_council_panel = ModelCouncilPanel()

    # ── Crash-safe initialization: render shell first, then do network calls ──
    _init_error: str | None = None
    _api_key_missing: bool = False

    # ── BUILD LAYOUT FIRST (before any blocking network calls) ──
    # This ensures the shell (top bar, nav, chat area) is visible
    # even if backend/API calls fail or block.

    # ── Backend Health Check (non-blocking with timeout) ────────
    try:
        await _check_backend_health(state)
    except Exception as exc:
        log.warning("Backend health check failed: %s", exc)
        state.backend_reachable = False
        _init_error = f"Backend health check failed: {exc}"

    # ── Load Model Slots ──────────────────────────────────────
    slots: list[dict[str, Any]] = []
    try:
        slots = await _load_model_slots(state)
    except AttributeError as exc:
        log.error("Model slot loading failed (missing API client method): %s", exc)
        _init_error = f"API client method missing: {exc}"
    except Exception as exc:
        log.warning("Model slot loading failed: %s", exc)
        _init_error = f"Model slot loading failed: {exc}"

    # Populate role model assignments from backend slot config
    for s in slots:
        sn = s.get("slot_name")
        m = s.get("model")
        if sn and m and not str(m).startswith("<"):
            set_role_model(sn, m)

    # ── Build Model Options ───────────────────────────────────
    # Refresh enabled models from backend library so the dropdown
    # includes models toggled on via the Models page.
    try:
        await refresh_enabled_models()
    except Exception as exc:
        log.warning("Enabled models refresh failed: %s", exc)
    all_model_options = build_model_options(state.available_slots)

    # Determine current chat model
    current_chat_model = get_role_model("synthesis")
    if not current_chat_model or current_chat_model not in all_model_options:
        for s in slots:
            if s.get("slot_name") == "synthesis" and s.get("model", "") and not s["model"].startswith("<"):
                current_chat_model = s["model"]
                break
    if not current_chat_model or current_chat_model not in all_model_options:
        current_chat_model = all_model_options[0] if all_model_options else ""

    # Refresh dogfood mode from status summary
    try:
        await state.refresh_status_summary()
    except Exception as exc:
        log.warning("Status summary refresh failed: %s", exc)
        if not _init_error:
            _init_error = f"Status summary refresh failed: {exc}"

    # ── API Key Check (deferred — do not block page render) ──
    try:
        if not state.api_client.has_openrouter_api_key():
            _api_key_missing = True
    except Exception as exc:
        log.warning("API key check failed: %s", exc)
        if not _init_error:
            _init_error = f"API key check failed: {exc}"

    # ── BUILD LAYOUT ──────────────────────────────────────────
    build_top_bar(state)
    build_left_nav(state, active_page="/ask")
    build_right_rail(state)

    # ── Show degraded card if initialization had errors ────────
    if _init_error or _api_key_missing:
        with ui.card().style(
            f"background:{C_WARN_BG}; border:1px solid {C_WARN_FG}; "
            f"border-radius:{R_SM}; padding:16px; margin:12px 16px;"
        ):
            if _api_key_missing:
                ui.label("API Key Not Configured").style(
                    f"font-size:14px; font-weight:700; color:{C_DOGFOOD_BARE}; font-family:{F_SANS};"
                )
                ui.label(
                    "No OpenRouter API key is set. Chat and embedding will not work. "
                    "Set your key via the Settings page or the OPENROUTER_API_KEY environment variable."
                ).style(f"font-size:12px; color:{C_CREAM}; font-family:{F_MONO}; margin-top:4px;")
                with ui.row().style("margin-top:8px; gap:8px;"):

                    async def _prompt_key():
                        try:
                            key = await show_api_key_prompt()
                            if key:
                                state.api_client.set_openrouter_api_key(key)
                                ui.notify(
                                    "API key saved! Refresh the page to use chat.", color="positive", position="top"
                                )
                        except Exception as exc:
                            log.warning("API key prompt failed: %s", exc)

                    ui.button("Enter API Key", on_click=lambda: asyncio.create_task(_prompt_key())).props(
                        "dense"
                    ).style(f"font-family:{F_SANS};")
                    ui.link("Settings", "/settings").style(
                        f"font-size:11px; color:{C_AMBER}; text-decoration:underline; align-self:center;"
                    )
            if _init_error:
                ui.label("Ask Workbench — Degraded").style(
                    f"font-size:14px; font-weight:700; color:{C_WARN_FG}; font-family:{F_SANS};"
                )
                ui.label(_init_error).style(f"font-size:12px; color:{C_CREAM}; font-family:{F_MONO}; margin-top:4px;")
                ui.label(
                    "The Ask page loaded with errors. Some features may not work. "
                    "Check that the backend is running and an API key is set."
                ).style(f"font-size:11px; color:{C_MUTED}; margin-top:4px;")
            log.error("ask_page_init_error: %s (api_key_missing=%s)", _init_error, _api_key_missing)

    # Main content
    with (
        ui.column()
        .classes("flex-1")
        .style(
            f"background:{C_GROUND}; overflow-y:auto; min-height:calc(100vh - 44px); "
            f"display:flex; flex-direction:column;"
        )
    ):
        # ── Chat header bar ───────────────────────────────────────
        # The chat header now uses a SINGLE multi-select checkbox
        # dropdown for picking N models from the unified "available
        # models" pool. The send handler auto-routes based on count:
        #   - 0 or 1 selected → normal single-model chat (WS route)
        #   - ≥2 selected → Multi-Cast Fusion (POST /beast/compare-models)
        #     — the ``beast`` slot is used ONLY for the Judge+Synth
        #     synthesis stages, not as a panel model. Models are NOT
        #     tied to actor slots/roles.
        # This restores the original "checkbox dropdown → auto-trigger
        # synthesis" UX. The separate "Multi-Cast: ON/OFF" button and
        # the second row of slot/library checkboxes were removed.
        with (
            ui.row()
            .classes("w-full items-center")
            .style(f"padding:8px 16px; background:{C_SURFACE}; border-bottom:0.5px solid {C_INK40};")
        ):
            # Multi-select model dropdown (checkboxes via multiple=True)
            ui.label("Models").style(
                f"font-size:10px; font-weight:600; color:{C_AMBER}; letter-spacing:0.5px; margin-right:8px;"
            )
            # Pre-populate the selection: if the user had a single chat
            # model picked previously, carry it over as the sole
            # selection. Otherwise default to the first available model
            # (so single-model chat still works out of the box).
            if not state.multicast_selected_model_ids:
                if current_chat_model and current_chat_model in all_model_options:
                    state.multicast_selected_model_ids = [current_chat_model]
                elif all_model_options and not all_model_options[0].startswith("("):
                    state.multicast_selected_model_ids = [all_model_options[0]]
                # else: leave empty — the user will pick from the dropdown
            # Derive multicast_enabled from the count for any legacy
            # consumers that still read the flag.
            state.multicast_enabled = len(state.multicast_selected_model_ids) >= 2

            (
                ui.select(
                    all_model_options,
                    value=list(state.multicast_selected_model_ids),
                    multiple=True,
                    on_change=lambda e: asyncio.create_task(
                        _on_chat_models_changed(e.value, state, multicast_count_label)
                    ),
                )
                .props("dense dark use-chips")
                .classes("min-w-[260px]")
                .style("font-size:11px;")
            )

            # Inline count label — tells the user whether the next send
            # will be single-model or Multi-Cast Fusion. This replaces
            # the old "Multi-Cast: ON/OFF" button.
            _n = len(state.multicast_selected_model_ids)
            _count_text = (
                f"{_n} selected · Multi-Cast Fusion"
                if _n >= 2
                else f"{_n} selected · Single-model"
                if _n == 1
                else "0 selected · pick a model"
            )
            _count_color = C_AMBER if _n >= 2 else C_INK60
            multicast_count_label = ui.label(_count_text).style(
                f"font-size:10px; font-weight:600; color:{_count_color}; "
                f"letter-spacing:0.5px; margin-left:8px; font-family:{F_MONO};"
            )

            ui.separator().props("vertical").style(f"margin:0 12px; color:{C_INK40};")

            # Mode toggle
            mode_label = ui.label("Normal" if state.current_mode == "normal" else "Augmented").style(
                f"font-size:10px; font-weight:600; color:{C_CREAM}; letter-spacing:0.5px;"
            )
            ui.button("Normal", on_click=lambda: _set_mode("normal", state, mode_label)).props("dense flat").style(
                f"color:{C_MUTED if state.current_mode == 'normal' else C_INK60}; font-size:10px;"
            )
            ui.button("Augmented", on_click=lambda: _set_mode("augmented", state, mode_label)).props(
                "dense flat"
            ).style(f"color:{C_AMBER if state.current_mode == 'augmented' else C_INK60}; font-size:10px;")

            ui.space()

            # Auto-save toggle
            ui.checkbox(
                "Auto-save",
                value=state.auto_save,
                on_change=lambda e: asyncio.create_task(_on_auto_save_toggled(e.value, state)),
            ).style(f"color:{C_MUTED}; font-size:10px;")

            ui.separator().props("vertical").style(f"margin:0 8px; color:{C_INK40};")

            # Phase 3d: per-model compression pass toggle. When ON, the
            # Multi-Cast send handler passes ``compress_panel_outputs=True``
            # to run_model_council. The backend summarizes each panelist's
            # answer to 5-8 key claims BEFORE the Judge reads them —
            # reduces the Judge's context window pressure on long panel
            # outputs. Only applies when ≥2 models are selected. Default
            # OFF (opt-in). See state.py + api_client.run_model_council.
            ui.checkbox(
                "Compress",
                value=state.compress_panel_outputs,
                on_change=lambda e: setattr(state, "compress_panel_outputs", e.value),
            ).style(f"color:{C_MUTED}; font-size:10px;").tooltip(
                "When ON, each panelist's answer is summarized to 5-8 key "
                "claims before the Judge reads them. Reduces Judge context "
                "pressure on long panel outputs. Only applies to Multi-Cast "
                "(≥2 models selected)."
            )

        # ── Direct model fallback banner ──────────────────────────
        if not state.backend_reachable:
            with (
                ui.row()
                .classes("w-full items-center justify-center")
                .style(f"padding:8px 16px; background:{C_ERR_BG}; border-bottom:1px solid {C_ERR_FG};")
            ):
                ui.label(
                    "DIRECT MODEL ONLY — NOT DOGFOOD — No retrieval. No corpus. No actors. No artifact lifecycle."
                ).style(
                    f"font-size:11px; font-weight:700; color:{C_DOGFOOD_BARE}; "
                    f"font-family:{F_MONO}; letter-spacing:0.5px;"
                )

        # ── Chat container ────────────────────────────────────────
        chat_container = (
            ui.column().classes("w-full flex-1").style("padding:16px; overflow-y:auto; flex:1; min-height:300px;")
        )

        # ── Connection status ─────────────────────────────────────
        if not state.backend_reachable:
            with chat_container:
                ui.label(
                    "AIP Backend not reachable — chat will use direct OpenRouter API "
                    "(no auto-save, no actors, no augmented mode)."
                ).style(
                    f"color:{C_WARN_FG}; font-size:12px; padding:12px; background:{C_WARN_BG}; border-radius:{R_SM};"
                )
                ui.label(
                    "For full features, start the backend: uvicorn aip.adapter.api.app:create_app --factory --port 8000"
                ).style(f"color:{C_MUTED}; font-size:10px; padding:4px 12px;")
        else:
            with chat_container:
                key_status = "API key: Set" if state.api_client.has_openrouter_api_key() else "API key: MISSING"
                ui.label(f"Connected to AIP Backend. {len(slots)} slot(s). {key_status}.").style(
                    f"color:{C_OK_FG if state.api_client.has_openrouter_api_key() else C_WARN_FG}; "
                    f"font-size:11px; padding:8px 12px;"
                )

        # ── Chat input ────────────────────────────────────────────
        # The send handler auto-routes based on the count of selected
        # models in the multi-select dropdown:
        #   - 0 selected  → notify "pick a model" and bail
        #   - 1 selected  → normal single-model _send_prompt (WS route,
        #     uses the synthesis slot's configured model)
        #   - ≥2 selected → Multi-Cast Fusion _send_multicast (POST
        #     /beast/compare-models with skip_default_slots=True so
        #     the ``beast`` slot is used ONLY for Judge+Synth, not as
        #     a panel model).
        input_field = build_chat_input(
            state,
            chat_container,
            send_fn=lambda: _dispatch_send(
                state,
                chat_container,
                input_field,
                source_panel,
                trace_panel,
                beast_panel,
                model_council_panel,
            ),
        )


# ── HELPER FUNCTIONS ───────────────────────────────────────────────────


async def _check_backend_health(state: GuiState) -> str:
    """Check backend health with 4s timeout. Returns status string."""
    try:
        health = await asyncio.wait_for(state.api_client.check_health(), timeout=4.0)
        state.backend_reachable = True
        slots = health.get("model_slots", [])
        return f"Backend: OK (slots: {', '.join(slots)})"
    except asyncio.TimeoutError:
        state.backend_reachable = False
        return "Backend: TIMEOUT (>4s)"
    except Exception as exc:
        state.backend_reachable = False
        return f"Backend: UNREACHABLE — {exc}"


async def _load_model_slots(state: GuiState) -> list[dict[str, Any]]:
    """Fetch model slots from backend."""
    try:
        slots = await state.api_client.list_model_slots()
        state.available_slots = slots
        state.backend_reachable = True
        return slots
    except Exception:
        state.backend_reachable = False
        return []


async def _on_chat_model_changed(model_id: str, state: GuiState) -> None:
    """Handle a single-model chat selection change — awaits backend confirmation.

    Back-compat shim: the chat header now uses a multi-select dropdown
    (see ``_on_chat_models_changed``). This function is preserved so the
    back-compat test ``test_on_chat_model_changed_awaits_backend`` and
    any external callers that still pass a single ``model_id`` string
    continue to work. It sets the synthesis slot's model on the backend
    so the WS chat route uses it for single-model chat.
    """
    state.current_role = None
    set_role_model("synthesis", model_id)
    # Track in selected models
    selected = get_selected_models()
    if model_id not in selected:
        selected.insert(0, model_id)
        set_selected_models(selected)
    state.reset_session()
    try:
        await state.api_client.update_slot_model(
            "synthesis", model_id, api_key=state.api_client.get_openrouter_api_key()
        )
        ui.notify(f"Chat model -> {model_id}", color="info")
    except Exception as exc:
        log.warning("model_slot_update_failed: %s", exc)
        ui.notify("Model slot change failed — backend may not have updated", color="warning")


async def _on_chat_models_changed(
    models: list[str] | str | None,
    state: GuiState,
    count_label: ui.label | None = None,
) -> None:
    """Handle the multi-select model dropdown change.

    The dropdown is the single source of truth for which model(s) the
    next send will use. The send handler auto-routes based on count:
      - 0 selected  → notify user, fall back to first available
      - 1 selected  → set the synthesis slot's model on the backend
        (so WS chat route uses it) and notify "Single-model mode"
      - ≥2 selected → Multi-Cast Fusion. No backend slot update is
        needed — the multi-cast path sends ``selected_model_ids``
        directly to ``POST /beast/compare-models`` with
        ``skip_default_slots=True`` so the ``beast`` slot is used ONLY
        for the Judge+Synth synthesis stages, not as a panel model.

    ``models`` may be a list (the normal multi-select case) or a single
    string (back-compat with callers that pass a scalar). ``None`` is
    treated as "no selection".
    """
    # Normalize input — defensive against NiceGUI returning a scalar
    # when ``multiple=True`` but the user clears the selection.
    if models is None:
        models_list: list[str] = []
    elif isinstance(models, str):
        models_list = [models]
    else:
        models_list = [m for m in models if isinstance(m, str) and m]

    state.multicast_selected_model_ids = models_list
    # Derive the legacy ``multicast_enabled`` flag for any consumer
    # that still reads it.
    state.multicast_enabled = len(models_list) >= 2
    state.reset_session()

    n = len(models_list)
    # Update the inline count label so the user sees whether the next
    # send will be single-model or Multi-Cast Fusion.
    if count_label is not None:
        if n >= 2:
            count_label.text = f"{n} selected · Multi-Cast Fusion"
            count_label.style(
                f"color:{C_AMBER}; font-size:10px; font-weight:600; letter-spacing:0.5px; font-family:{F_MONO};"
            )
        elif n == 1:
            count_label.text = f"{n} selected · Single-model"
            count_label.style(
                f"color:{C_INK60}; font-size:10px; font-weight:600; letter-spacing:0.5px; font-family:{F_MONO};"
            )
        else:
            count_label.text = "0 selected · pick a model"
            count_label.style(
                f"color:{C_INK60}; font-size:10px; font-weight:600; letter-spacing:0.5px; font-family:{F_MONO};"
            )

    # Single-model branch: keep the synthesis slot's configured model
    # in sync with the dropdown so the WS chat route uses it. We reuse
    # ``_on_chat_model_changed`` to get the backend update + notify +
    # selected_models persistence for free.
    if n == 1:
        await _on_chat_model_changed(models_list[0], state)
        return

    if n >= 2:
        ui.notify(
            f"{n} models selected — next send will run Multi-Cast Fusion",
            color="positive",
        )
        return

    # n == 0: no model selected.
    ui.notify(
        "No model selected — pick at least one model from the dropdown",
        color="warning",
    )


def _set_mode(mode: str, state: GuiState, label: ui.label) -> None:
    """Set the chat mode (normal or augmented)."""
    state.current_mode = mode
    state.reset_session()
    label.text = "Normal" if mode == "normal" else "Augmented"


async def _on_auto_save_toggled(enabled: bool, state: GuiState) -> None:
    """Handle auto-save checkbox toggle."""
    state.auto_save = enabled
    if state.session_id is not None:
        try:
            await state.api_client.update_session(state.session_id, {"auto_save": enabled})
            status = "enabled" if enabled else "disabled"
            ui.notify(f"Auto-save {status}", color="positive" if enabled else "warning")
        except Exception as exc:
            ui.notify(f"Failed to update auto-save: {exc}", color="negative")
    else:
        status = "enabled" if enabled else "disabled"
        ui.notify(f"Auto-save will be {status} for next session", color="info")


# ── Multi-Model send routing ───────────────────────────────────────────


async def _dispatch_send(
    state: GuiState,
    chat_container,
    input_field: ui.input,
    source_panel: SourcePanel,
    trace_panel: TracePanel,
    beast_panel: BeastPanel,
    model_council_panel: ModelCouncilPanel,
) -> None:
    """Dispatch the send action based on the number of selected models.

    The multi-select dropdown is the single source of truth. The send
    handler auto-routes — no separate "Multi-Cast" button click is
    required:
      - 0 selected  → notify user, bail
      - 1 selected  → normal single-model _send_prompt (WS chat route,
        uses the synthesis slot's configured model)
      - ≥2 selected → Multi-Cast Fusion _send_multicast. Sends
        ``selected_model_ids`` to ``POST /beast/compare-models`` with
        ``selected_model_slots=[]`` and ``skip_default_slots=True`` so
        the ``beast`` slot is used ONLY for the Judge+Synth synthesis
        stages, not as a panel model. Models are NOT tied to actor
        slots/roles — the user picks N models from the unified
        "available models" pool, and the backend calls those N models
        directly via OpenRouter.
    """
    selected_models = list(state.multicast_selected_model_ids)
    n_selected = len(selected_models)

    if n_selected == 0:
        ui.notify(
            "No model selected — pick at least one model from the dropdown",
            color="warning",
        )
        return

    if n_selected >= 2:
        if not state.backend_reachable:
            ui.notify(
                "Multi-Cast requires a live backend (POST /beast/compare-models)",
                color="negative",
            )
            return
        await _send_multicast(
            state,
            chat_container,
            input_field,
            source_panel,
            trace_panel,
            beast_panel,
            model_council_panel,
        )
        return

    # n_selected == 1: normal single-model chat path.
    await _send_prompt(
        state,
        chat_container,
        input_field,
        source_panel,
        trace_panel,
        beast_panel,
        model_council_panel,
    )


def _model_color_markdown(model_label: str) -> str:
    """Return a deterministic hex color for a model label (Phase 3).

    Mirrors ``gui.components.model_council_panel._model_color`` so the
    same model gets the same color in both the panel renderer (NiceGUI
    widgets) and the markdown answer card (HTML spans). The palette and
    hash function are identical to the panel's — keeping them in sync is
    a contract: if you change one, change both.

    Phase 3a + 3b: per-model attribution badges (unique_insights) and
    per-model stance color-coding (contradictions) use this helper so
    the human can visually track a model's contributions across Judge
    analysis sections without reading the label every time.
    """
    if not model_label:
        return "#B8935A"  # C_AMBER equivalent
    palette = [
        "#B8935A",  # C_AMBER
        "#4A9B8E",  # slate-teal
        "#9B6B4A",  # warm copper
        "#6B8E9B",  # steel blue
        "#9B4A6B",  # muted rose
        "#4A6B9B",  # dusty blue
        "#8E9B4A",  # olive
        "#6B4A9B",  # violet
    ]
    h = sum(ord(c) for c in str(model_label))
    return palette[h % len(palette)]


def _format_judge_analysis_markdown(judge_analysis: dict[str, Any]) -> str:
    """Format the structured Judge JSON as markdown for the Multi-Cast card.

    Phase 1 Fix B: the synthesis card previously rendered only the
    flattened legacy strings (``convergence``, ``disagreements``, etc.)
    which lose per-model attribution. This helper appends the full
    structured Judge analysis as markdown sections:

      - ``### Judge · Consensus`` — bulleted list
      - ``### Judge · Contradictions`` — per-topic stance table
      - ``### Judge · Partial Coverage`` — per-model-attributed bullets
      - ``### Judge · Unique Insights`` — per-model-attributed bullets
      - ``### Judge · Blind Spots`` — italicized bullets (the gaps)
      - A fenced ``json`` block with the full raw JSON for audit

    Returns empty string if ``judge_analysis`` is empty/missing — caller
    should skip appending in that case.
    """
    if not judge_analysis or not isinstance(judge_analysis, dict):
        return ""

    analysis = judge_analysis.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}

    lines: list[str] = []

    # Consensus
    consensus = analysis.get("consensus", [])
    if isinstance(consensus, list) and consensus:
        lines.append("### Judge · Consensus (all models agree)")
        for point in consensus:
            lines.append(f"- {point}")

    # Contradictions stance table
    contradictions = analysis.get("contradictions", [])
    if isinstance(contradictions, list) and contradictions:
        lines.append("### Judge · Contradictions (per-model stances)")
        lines.append("")
        lines.append("| Topic | Model | Stance |")
        lines.append("|---|---|---|")
        for c in contradictions:
            if not isinstance(c, dict):
                continue
            topic = str(c.get("topic", "?")).replace("|", "\\|")
            stances = c.get("stances", [])
            if not isinstance(stances, list) or not stances:
                lines.append(f"| {topic} | _none_ | _none_ |")
                continue
            for s in stances:
                if not isinstance(s, dict):
                    continue
                model = str(s.get("model", "?")).replace("|", "\\|")
                stance = str(s.get("stance", "?")).replace("|", "\\|").replace("\n", " ")
                # Phase 3b: per-model stance color-coding via HTML span.
                # The model label gets a deterministic color (same as the
                # panel renderer) so the human can visually track the same
                # model's stance across contradiction topics.
                model_clr = _model_color_markdown(model)
                model_badge = (
                    f'<span style="color:{model_clr};font-weight:600;'
                    f'border-left:2px solid {model_clr};padding-left:4px;">{model}</span>'
                )
                lines.append(f"| {topic} | {model_badge} | {stance} |")
        lines.append("")

    # Partial coverage
    partial = analysis.get("partial_coverage", [])
    if isinstance(partial, list) and partial:
        lines.append("### Judge · Partial Coverage (some models only)")
        for p in partial:
            if not isinstance(p, dict):
                continue
            models = p.get("models", [])
            point = p.get("point", "?")
            models_str = ", ".join(str(m) for m in models) if isinstance(models, list) else str(models)
            lines.append(f"- **[{models_str}]** {point}")

    # Unique insights
    unique = analysis.get("unique_insights", [])
    if isinstance(unique, list) and unique:
        lines.append("### Judge · Unique Insights (per-model)")
        for u in unique:
            if not isinstance(u, dict):
                continue
            model = u.get("model", "?")
            insight = u.get("insight", "?")
            # Phase 3a: per-model attribution badge via HTML span.
            # The model label renders as a colored badge (deterministic
            # color matching the panel renderer) so the human can visually
            # track which model contributed each unique insight.
            model_clr = _model_color_markdown(str(model))
            model_badge = (
                f'<span style="background:{model_clr};color:#0E0E0F;'
                f"font-family:'Courier New',monospace;font-size:9px;"
                f"font-weight:700;padding:1px 6px;border-radius:3px;"
                f'letter-spacing:0.3px;">{model}</span>'
            )
            lines.append(f"- {model_badge} {insight}")

    # Blind spots
    blind = analysis.get("blind_spots", [])
    if isinstance(blind, list) and blind:
        lines.append("### Judge · Blind Spots (no model addressed)")
        for b in blind:
            lines.append(f"- *{b}*")

    # Collapsible raw JSON (HTML <details> renders in markdown)
    try:
        raw_json = json.dumps(judge_analysis, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        raw_json = str(judge_analysis)
    lines.append("")
    lines.append("<details><summary>Judge Analysis (raw JSON)</summary>")
    lines.append("")
    lines.append("```json")
    lines.append(raw_json)
    lines.append("```")
    lines.append("")
    lines.append("</details>")

    return "\n".join(lines)


async def _send_multicast(
    state: GuiState,
    chat_container,
    input_field: ui.input,
    source_panel: SourcePanel,
    trace_panel: TracePanel,
    beast_panel: BeastPanel,
    model_council_panel: ModelCouncilPanel,
) -> None:
    """Send the prompt to every selected model via POST /beast/compare-models.

    The backend calls each selected model concurrently (all via direct
    OpenRouter calls — they're sent as ``selected_model_ids`` from the
    unified multi-select dropdown, NOT as TOML slot names), then runs
    the two-stage Beast Fusion pipeline (Judge-Beast → Synth-Beast)
    on the per-model responses. We render each per-model answer as its
    own answer card, then render a final Beast Fusion synthesis card.

    Per the "models not tied to actor slots/roles" rule:
      - ``selected_model_slots=[]`` (always empty — no TOML slot names)
      - ``skip_default_slots=True`` (so the backend does NOT auto-add
        the default ``_DEFAULT_COMPARISON_SLOTS`` = synthesis/evaluation/
        beast — the panel is built ONLY from the user's selection)
      - ``selected_model_ids`` carries the N OpenRouter IDs picked in
        the multi-select dropdown (could be 2, 5, any count)
      - The ``beast`` slot is used ONLY for the Judge+Synth synthesis
        stages (via ``_pick_fusion_engine``), NOT as a panel model.

    The synthesis is ADVISORY ONLY — never auto-approved.
    """
    prompt = input_field.value.strip()
    if not prompt:
        return

    # Models come from the unified multi-select dropdown — they are
    # OpenRouter IDs, not TOML slot names. We send them as
    # ``selected_model_ids`` only; ``selected_model_slots`` is always []
    # and ``skip_default_slots=True`` prevents the backend from
    # auto-adding the default TOML slots (synthesis/evaluation/beast).
    selected_model_ids = list(state.multicast_selected_model_ids)
    total_selected = len(selected_model_ids)
    log.info(
        "send_multicast: model_ids=%s backend_reachable=%s prompt_len=%d skip_default_slots=True",
        selected_model_ids,
        state.backend_reachable,
        len(prompt),
    )

    add_message(chat_container, "user", prompt)
    input_field.value = ""

    with chat_container:
        thinking_label = ui.label(f"Multi-Casting to {total_selected} models... (this may take 30-90s)").style(
            f"color:{C_MUTED}; font-size:11px;"
        )

    try:
        session_id = await state.ensure_session()
    except Exception as exc:
        log.warning("send_multicast: ensure_session failed: %s", exc)
        thinking_label.delete()
        add_system_message(chat_container, f"Session creation failed: {exc}")
        return

    try:
        # Phase 1 retrieval bridge (Step 2-B): pass a real, non-empty
        # turn_id + the assemble_augmented_context flag so the backend
        # calls the shared ``_augmented_context.assemble_augmented_context()``
        # helper and prepends corpus/wiki/graph/definer context to each
        # panel call's user prompt.
        #
        # turn_id: the helper uses ``session_id`` (not turn_id) for
        # session_meta lookup — turn_id only (a) gates the helper call
        # (must be non-empty) and (b) computes the council artifact_id.
        # We pass ``session_id`` as the turn_id signal so the gate
        # passes when augmented mode is on. This is layer-discipline-
        # compliant (GUI can't import ``make_turn_id`` from foundation)
        # and gives a per-session-deterministic artifact_id. A future
        # step can add a backend endpoint that returns a per-turn
        # turn_id if per-send artifact uniqueness becomes needed.
        #
        # assemble_augmented_context: True when state.current_mode ==
        # 'augmented' — the backend will run retrieval (corpus turns +
        # wiki + graph + definer profile) and prepend the result to
        # each panel call. When False (normal mode), the panel calls
        # proceed with the bare prompt (existing behavior).
        is_augmented = state.current_mode == "augmented"
        result = await state.api_client.run_model_council(
            prompt=prompt,
            turn_id=session_id if is_augmented else "",  # non-empty signals "run retrieval"
            session_id=session_id,
            existing_answer="",  # Pre-send mode: no existing answer to compare
            sources=[],
            selected_model_slots=[],  # Models NOT tied to actor slots/roles
            selected_model_ids=selected_model_ids,
            skip_default_slots=True,  # Don't auto-add default TOML slots
            assemble_augmented_context=is_augmented,  # Phase 1 retrieval bridge
            compress_panel_outputs=state.compress_panel_outputs,  # Phase 3d
        )
    except Exception as exc:
        log.exception("send_multicast: run_model_council failed: %s", exc)
        thinking_label.delete()
        add_system_message(chat_container, f"Multi-Cast failed: {exc}")
        ui.notify(f"Multi-Cast failed: {exc}", color="negative")
        return

    thinking_label.delete()

    status = result.get("status", "error")
    if status == "error":
        err = result.get("error", "Unknown error")
        add_system_message(chat_container, f"Multi-Cast error: {err}")
        ui.notify(f"Multi-Cast error: {err}", color="negative")
        return

    if status == "insufficient_models":
        err = result.get("error", "Insufficient models selected")
        add_system_message(chat_container, f"Multi-Cast unavailable: {err}")
        ui.notify(
            "Multi-Cast needs ≥2 selected models — pick more in the dropdown",
            color="warning",
            timeout=8000,
        )
        return

    per_model = result.get("selected_models", [])
    # Render one answer card per model that completed.
    # NOTE: in the new "models not tied to actor slots/roles" mode,
    # every panelist comes via ``selected_model_ids`` (OpenRouter IDs),
    # so ``model_slot`` is "" and ``source="library"`` for all of them.
    # We render the per-model card with just the model_id as the label.
    completed_count = 0
    for pm in per_model:
        pm_status = pm.get("status", "unknown")
        slot_name = pm.get("model_slot", "")
        model_id = pm.get("model_id", "")
        answer = pm.get("answer", "")
        error = pm.get("error", "")
        latency = pm.get("latency_ms")

        # Build a clean display label: prefer just the model_id; fall
        # back to the slot name only when present (legacy callers that
        # still send slot names — external API clients, not the GUI).
        if model_id and slot_name:
            display_label = f"{slot_name} ({model_id})"
        elif model_id:
            display_label = model_id
        else:
            display_label = slot_name or "unknown"

        if pm_status == "completed" and answer:
            completed_count += 1
            turn_data = {
                "session_id": session_id,
                "turn_id": "",  # No originating chat turn
                "content": answer,
                "model": display_label,
                "mode": "multicast",
                "sources": [],
                "trace_available": False,
                "lexical_only": False,
                "vector_contributed": False,
                "direct_model": False,
            }
            add_answer_card(
                chat_container,
                content=answer,
                model=display_label,
                latency_ms=latency,
                sources=[],
                trace_available=False,
                lexical_only=False,
                vector_contributed=False,
                direct_model=False,
                mode="multicast",
                on_show_sources=None,
                on_show_trace=None,
                on_save_artifact=lambda td: _handle_save_artifact(state, td),
                on_beast_counsel=None,  # No originating turn to counsel on
                on_link_wiki=None,
                on_run_model_council=None,  # Already a council result
                turn_data=turn_data,
            )
        elif pm_status == "failed" and error:
            add_system_message(
                chat_container,
                f"Multi-Cast model '{display_label}' FAILED: {error[:200]}",
            )

    # Render the Beast Fusion synthesis as a final answer card if available.
    # Phase 1: ``fusion_answer`` is the headline (the Synth-Beast output).
    # Legacy structured fields (convergence, disagreements, etc.) are
    # rendered as supporting detail below the fusion answer.
    synthesis_status = result.get("synthesis_status", "unavailable")
    fusion_answer = result.get("fusion_answer", "")
    beast_conclusion = result.get("beast_conclusion", "")
    convergence = result.get("convergence", "")
    disagreements = result.get("disagreements", "")
    unique_contributions = result.get("unique_contributions", "")
    risks = result.get("risks", "")
    recommended_decision = result.get("recommended_decision", "")
    # Phase 1 Fix B: surface the full structured Judge JSON for audit.
    # The flattened legacy strings above lose per-model attribution; the
    # raw dict retains it. We render it as a markdown stance table +
    # bulleted lists + a fenced JSON block at the end.
    judge_analysis = result.get("judge_analysis", {})

    if synthesis_status == "completed" and (fusion_answer or beast_conclusion or convergence):
        synth_lines = ["## Beast Fusion Synthesis (ADVISORY ONLY)"]
        # Phase 1 headline: the Synth-Beast fused answer
        if fusion_answer:
            synth_lines.append(f"**Fusion Synthesis:** {fusion_answer}")
        # Legacy structured-analysis fields (best-effort from Judge JSON)
        if convergence:
            synth_lines.append(f"**Convergence:** {convergence}")
        if disagreements:
            synth_lines.append(f"**Disagreements:** {disagreements}")
        if unique_contributions:
            synth_lines.append(f"**Unique Contributions:** {unique_contributions}")
        if risks:
            synth_lines.append(f"**Risks:** {risks}")
        # beast_conclusion is mirrored from fusion_answer in Phase 1,
        # so only show it as a separate line if it differs (legacy fallback)
        if beast_conclusion and beast_conclusion != fusion_answer:
            synth_lines.append(f"**Beast Conclusion:** {beast_conclusion}")
        if recommended_decision:
            synth_lines.append(f"**Recommended Decision:** {recommended_decision}")
        # Phase 1 Fix B: append the structured Judge analysis as markdown
        # sections + a collapsible raw-JSON block.
        judge_md = _format_judge_analysis_markdown(judge_analysis)
        if judge_md:
            synth_lines.append(judge_md)
        synth_content = "\n\n".join(synth_lines)

        synth_turn_data = {
            "session_id": session_id,
            "turn_id": "",
            "content": synth_content,
            "model": "Beast Fusion",
            "mode": "multicast",
            "sources": [],
            "trace_available": False,
            "lexical_only": False,
            "vector_contributed": False,
            "direct_model": False,
        }
        add_answer_card(
            chat_container,
            content=synth_content,
            model="Beast Fusion",
            latency_ms=None,
            sources=[],
            trace_available=False,
            lexical_only=False,
            vector_contributed=False,
            direct_model=False,
            mode="multicast",
            on_show_sources=None,
            on_show_trace=None,
            on_save_artifact=lambda td: _handle_save_artifact(state, td),
            on_beast_counsel=None,
            on_link_wiki=None,
            on_run_model_council=None,
            turn_data=synth_turn_data,
        )
    elif synthesis_status == "unavailable":
        add_system_message(
            chat_container,
            "Beast Fusion synthesis unavailable — per-model results above for individual review",
        )
    elif synthesis_status == "failed":
        add_system_message(
            chat_container,
            "Beast Fusion synthesis call failed — per-model results above for individual review",
        )

    add_system_message(
        chat_container,
        f"Multi-Cast complete — {completed_count}/{len(per_model)} models returned, synthesis: {synthesis_status}",
    )


def _handle_beast_counsel(state: GuiState, turn_data: dict, panel: BeastPanel) -> None:
    """Open Beast Counsel panel for the selected turn."""
    turn_id = turn_data.get("turn_id", "")
    if not turn_id:
        ui.notify("No turn ID available for Beast Counsel", color="warning")
        return
    asyncio.ensure_future(
        panel.show_counsel(
            turn_id=turn_id,
            session_id=state.session_id or "",
            api_client=state.api_client,
            mode="continuity",
            question_text=turn_data.get("question", ""),
            answer_text=turn_data.get("content", ""),
            sources=turn_data.get("sources", []),
            trace_available=turn_data.get("trace_available", False),
            lexical_only=turn_data.get("lexical_only", False),
            vector_contributed=turn_data.get("vector_contributed", False),
        )
    )


async def _handle_link_wiki(state: GuiState, turn_data: dict) -> None:
    """Handle linking an answer to a wiki article via the knowledge link API."""
    session_id = turn_data.get("session_id", "")
    turn_id = turn_data.get("turn_id", "")
    if not session_id or not turn_id:
        ui.notify("Cannot link wiki — missing session/turn info", color="warning")
        return
    try:
        result = await state.api_client.create_knowledge_link(
            source_type="turn",
            source_id=turn_id,
            target_type="wiki",
            target_id="auto",
            relation_type="references",
        )
        if result.get("error"):
            ui.notify(f"Link failed: {result['error']}", color="negative")
        else:
            ui.notify("Wiki link created", color="positive")
    except Exception as exc:
        log.warning("link_wiki_failed: %s", exc)
        ui.notify("Wiki link failed — backend may be unavailable", color="warning")


def _handle_model_council(state: GuiState, turn_data: dict, panel: ModelCouncilPanel) -> None:
    """Open Model Council panel for the selected turn."""
    asyncio.ensure_future(
        panel.show_council(
            api_client=state.api_client,
            prompt=turn_data.get("question", ""),
            turn_id=turn_data.get("turn_id", ""),
            session_id=state.session_id or "",
            existing_answer=turn_data.get("content", ""),
            sources=turn_data.get("sources", []),
        )
    )


def _handle_save_artifact(state: GuiState, turn_data: dict[str, Any]) -> None:
    """Handle the 'Save as Artifact' action for a turn."""
    session_id = turn_data.get("session_id") or state.session_id
    content = turn_data.get("content", "")
    if not session_id or not content:
        ui.notify("Cannot save artifact: missing session or content data", color="warning")
        return
    asyncio.create_task(_save_artifact_async(state, session_id, content))


async def _save_artifact_async(state: GuiState, session_id: str, content: str) -> None:
    """Async implementation of save-as-artifact."""
    try:
        result = await state.api_client.save_turn_as_artifact(
            session_id=session_id,
            content=content,
            title=f"Ask turn from session {session_id[:12]}",
        )
        if result.get("artifact_id"):
            ui.notify(
                "Artifact saved — requires DEFINER review. See Artifacts page.",
                color="positive",
                timeout=6000,
            )
        else:
            error = result.get("error", "unknown error")
            ui.notify(f"Save failed: {error}", color="negative")
    except Exception as exc:
        ui.notify(f"Save artifact failed: {exc}", color="negative")


async def _send_prompt(
    state: GuiState,
    chat_container,
    input_field: ui.input,
    source_panel: SourcePanel,
    trace_panel: TracePanel,
    beast_panel: BeastPanel,
    model_council_panel: ModelCouncilPanel,
) -> None:
    """Handle the send button click — sends message via WebSocket or direct OpenRouter.

    CRITICAL: Called via asyncio.create_task(), so any unhandled exception is
    silently swallowed. We wrap in top-level try/except.
    CRITICAL 2: Must enter client context for UI operations.
    """
    try:
        if state.client is not None:
            with state.client:
                await _send_prompt_inner(
                    state,
                    chat_container,
                    input_field,
                    source_panel,
                    trace_panel,
                    beast_panel,
                    model_council_panel,
                )
        else:
            await _send_prompt_inner(
                state,
                chat_container,
                input_field,
                source_panel,
                trace_panel,
                beast_panel,
                model_council_panel,
            )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        try:
            ui.notify(f"Send failed: {exc}", color="negative", timeout=8000)
        except Exception:
            pass


async def _send_prompt_inner(
    state: GuiState,
    chat_container,
    input_field: ui.input,
    source_panel: SourcePanel,
    trace_panel: TracePanel,
    beast_panel: BeastPanel,
    model_council_panel: ModelCouncilPanel,
) -> None:
    """Inner implementation of send_prompt."""
    prompt = input_field.value.strip()
    if not prompt:
        return

    # Resolve chat model
    chat_model = get_role_model("synthesis")
    if not chat_model or chat_model.startswith("("):
        selected = get_selected_models()
        if selected:
            chat_model = selected[0]
        else:
            try:
                all_options = build_model_options(state.available_slots)
                if all_options and not all_options[0].startswith("("):
                    chat_model = all_options[0]
            except Exception:
                pass

    if not chat_model or chat_model.startswith("("):
        ui.notify("No model selected. Go to Settings to configure one.", color="warning")
        return

    log.info(
        "send_prompt: model=%s backend_reachable=%s prompt_len=%d", chat_model, state.backend_reachable, len(prompt)
    )

    add_message(chat_container, "user", prompt)
    input_field.value = ""

    # Create "Thinking..." label
    with chat_container:
        thinking_label = ui.label("Thinking...").style(f"color:{C_MUTED}; font-size:11px;")

    # Lazy backend retry
    if not state.backend_reachable:
        try:
            await asyncio.wait_for(state.api_client.check_health(), timeout=3.0)
            state.backend_reachable = True
            log.info("send_prompt: backend recovered (lazy retry)")
        except Exception:
            pass

    # ── Route 1: Backend reachable -> WebSocket chat ──────────────
    if state.backend_reachable:
        try:
            session_id = await state.ensure_session()
            log.info("send_prompt: session_id=%s", session_id)
        except Exception as exc:
            log.warning("send_prompt: ensure_session failed: %s", exc)
            state.backend_reachable = False

    if state.backend_reachable:

        def on_response(resp: dict[str, Any]) -> None:
            log.info("on_response: model=%s content_len=%d", resp.get("model", "?"), len(resp.get("content", "")))
            thinking_label.delete()
            content = resp.get("content", "")
            model = resp.get("model", resp.get("model_slot", ""))
            latency = resp.get("latency_ms")
            tokens = resp.get("tokens_used", 0)
            auto_saved = resp.get("auto_save", False)
            sources = resp.get("sources", [])
            mode = resp.get("mode", "normal")
            trace_available = resp.get("trace_available", False)
            lexical_only = resp.get("lexical_only", False)
            vector_contributed = resp.get("vector_contributed", False)
            direct_model = resp.get("direct_model", False)
            turn_id = resp.get("turn_id", "")

            # Build turn_data for action callbacks
            turn_data = {
                "session_id": state.session_id,
                "turn_id": turn_id,
                "content": content,
                "model": model,
                "mode": mode,
                "sources": sources,
                "trace_available": trace_available,
                "lexical_only": lexical_only,
                "vector_contributed": vector_contributed,
                "direct_model": direct_model,
            }

            # Use the enhanced answer card instead of plain add_message
            add_answer_card(
                chat_container,
                content=content,
                model=model,
                latency_ms=latency,
                sources=sources,
                trace_available=trace_available,
                lexical_only=lexical_only,
                vector_contributed=vector_contributed,
                direct_model=direct_model,
                mode=mode,
                on_show_sources=lambda td: source_panel.show_sources(td.get("sources", [])),
                on_show_trace=lambda td: asyncio.create_task(
                    trace_panel.show_trace(td.get("session_id", ""), state.api_client)
                ),
                on_save_artifact=lambda td: _handle_save_artifact(state, td),
                on_beast_counsel=lambda td: _handle_beast_counsel(state, td, beast_panel),
                on_link_wiki=lambda td: asyncio.create_task(_handle_link_wiki(state, td)),
                on_run_model_council=lambda td: _handle_model_council(state, td, model_council_panel),
                turn_data=turn_data,
            )

            if tokens > 0:
                add_system_message(chat_container, f"Tokens: {tokens}")
            if auto_saved:
                add_system_message(chat_container, "Auto-save: indexing...")

        def on_error(err: dict[str, Any]) -> None:
            log.error("on_error: %s", err.get("content", "Unknown"))
            thinking_label.delete()
            content = err.get("content", "Unknown error")
            add_system_message(chat_container, f"Error: {content}")
            ui.notify(content, color="negative")

        def on_gate(gate: dict[str, Any]) -> None:
            log.info("on_gate: gate_type=%s", gate.get("gate_type", "?"))
            thinking_label.delete()
            state.pending_gate = gate
            gate_type = gate.get("gate_type", "unknown")
            preview = gate.get("preview", "")
            add_system_message(chat_container, f"DEFINER Gate ({gate_type}): {preview}")
            with chat_container:
                with ui.row().classes("w-full justify-center gap-2").style("margin:8px 0;"):
                    ui.button(
                        "Approve",
                        on_click=lambda: asyncio.create_task(_handle_gate_response(True, state, chat_container)),
                    ).style(btn_primary()).props("dense")
                    ui.button(
                        "Reject",
                        on_click=lambda: asyncio.create_task(_handle_gate_response(False, state, chat_container)),
                    ).style(btn_secondary()).props("dense")

        try:
            log.info("send_prompt: calling chat_via_websocket session=%s slot=%s", session_id, state.current_model_slot)
            await state.api_client.chat_via_websocket(
                session_id=session_id,
                message=prompt,
                on_response=on_response,
                on_error=on_error,
                on_gate=on_gate,
                model_slot=state.current_model_slot,
            )
            return
        except Exception as exc:
            log.error("send_prompt: websocket failed: %s", exc)
            thinking_label.delete()
            state.backend_reachable = False
            state.reset_session()
            add_system_message(chat_container, f"Backend chat failed, trying direct OpenRouter: {exc}")

    # ── Route 2: Backend unreachable -> direct OpenRouter API call ─
    log.info("send_prompt: using direct OpenRouter with model=%s", chat_model)
    try:
        result = await state.api_client.chat_direct_openrouter(
            model=chat_model,
            messages=[{"role": "user", "content": prompt}],
            api_key=state.api_client.get_openrouter_api_key(),
        )
        thinking_label.delete()

        if result.get("error"):
            add_system_message(chat_container, f"Error: {result.get('content', 'Unknown error')}")
            ui.notify(result.get("content", "Chat failed"), color="negative")
        else:
            # Direct model fallback — use answer card with direct_model=True.
            # turn_id is intentionally empty here: the backend is unreachable,
            # so no turn is being persisted to corpus_turns. Per-turn actions
            # (Beast Counsel, Link Wiki) will honestly report "missing turn
            # info" rather than fabricate an ID that points at nothing.
            turn_data = {
                "session_id": state.session_id,
                "turn_id": "",
                "content": result.get("content", ""),
                "model": result.get("model", chat_model),
                "mode": "normal",
                "sources": [],
                "trace_available": False,
                "lexical_only": False,
                "vector_contributed": False,
                "direct_model": True,
            }

            add_answer_card(
                chat_container,
                content=result.get("content", ""),
                model=result.get("model", chat_model),
                latency_ms=result.get("latency_ms"),
                sources=[],
                trace_available=False,
                lexical_only=False,
                vector_contributed=False,
                direct_model=True,
                mode="normal",
                on_show_sources=None,  # No sources in direct mode
                on_show_trace=None,  # No trace in direct mode
                on_save_artifact=lambda td: _handle_save_artifact(state, td),
                on_beast_counsel=None,  # No Beast Counsel in direct model mode
                on_link_wiki=None,
                on_run_model_council=None,  # No Model Council in direct model mode
                turn_data=turn_data,
            )

            tokens = result.get("tokens_used", 0)
            if tokens > 0:
                add_system_message(chat_container, f"Tokens: {tokens}")
            add_system_message(chat_container, "DIRECT MODEL ONLY — NOT DOGFOOD — backend not connected")
    except Exception as exc:
        log.error("send_prompt: direct OpenRouter failed: %s", exc)
        thinking_label.delete()
        add_system_message(chat_container, f"Direct OpenRouter call failed: {exc}")
        ui.notify(f"Chat failed: {exc}", color="negative")


async def _handle_gate_response(approved: bool, state: GuiState, chat_container) -> None:
    """Handle a DEFINER gate approval/rejection."""
    if state.session_id is None:
        return

    ctx = state.client

    def _do_ui():
        decision_text = "approved" if approved else "rejected"
        add_system_message(chat_container, f"Gate {decision_text}")

    try:
        result = await state.api_client.send_gate_response(
            session_id=state.session_id,
            approved=approved,
        )
        if ctx is not None:
            with ctx:
                _do_ui()
                if result.get("type") == "error":
                    add_system_message(chat_container, f"Gate response error: {result.get('content', 'Unknown error')}")
                    ui.notify(f"Gate response failed: {result.get('content', 'Unknown error')}", color="negative")
                elif result.get("type") == "response":
                    content = result.get("content", "")
                    add_message(chat_container, "assistant", content)
        else:
            _do_ui()
            if result.get("type") == "error":
                add_system_message(chat_container, f"Gate response error: {result.get('content', 'Unknown error')}")
            elif result.get("type") == "response":
                add_message(chat_container, "assistant", result.get("content", ""))
    except Exception as exc:
        if ctx is not None:
            with ctx:
                add_system_message(chat_container, f"Gate response failed: {exc}")
                ui.notify(f"Gate response failed: {exc}", color="negative")
        else:
            add_system_message(chat_container, f"Gate response failed: {exc}")
            ui.notify(f"Gate response failed: {exc}", color="negative")
        return

    state.pending_gate = None


# ============================================================
# ARISTOTLE Tutoring Mode — student-facing interface
# ============================================================


async def _ask_page_aristotle(concept_from_url: str = "", is_debug: bool = False):
    """ARISTOTLE onboarding + tutoring — chat-primary surface.

    Per ADR-002 §9 + PLANNED_FEATURES.md (AIP_Aristotle): the INTAKE
    interview runs in the main Brain chat — NOT a separate /intake page.
    Chat IS the intake surface. The learner sees one prompt at a time
    and types their reply in the chat bar; the underlying state machine
    drives them through:

      INTAKE  (5 stages: GREETING → SUBJECT → PRIOR_KNOWLEDGE →
               GOALS → SCHEDULE → GENERATING_PLAN → COMPLETE)
        ↓ produces a learning plan with concept_ids
      PLACER  (samples concepts from the plan, probes each to calibrate
               where to start tutoring)
        ↓ sets starting_concept_idx on the plan
      TUTORING (PREDICT → TEACH → PROBE → QUIZ → EVALUATE →
                HINT_1/HINT_2 → REMEDIATE → NEXT_CONCEPT)

    The chat bar is visible from the first render. The first chat
    bubble is Aristotle's greeting from /aristotle/intake/start.

    ?concept=<id> deep-link (per design decision 2a): if a learning
    plan already exists containing that concept, skip intake+placer
    and jump straight to /aristotle/session/start. Otherwise fall
    through to intake — the learner will reach that concept naturally
    through the plan.
    """
    import httpx
    import os

    state = get_session_state()
    _BACKEND_URL = os.getenv("AIP_BACKEND_URL", "http://127.0.0.1:8000")

    # Activate extension context (top bar badge + right rail)
    set_active_extension("aristotle", "Tutoring")

    # Build shell
    build_top_bar(state)
    build_left_nav(state, active_page="/ask")

    # Shared mutable state across the three phases.
    #   _phase:             "INTAKE" | "PLACER" | "TUTORING" | "COMPLETE"
    #   _intake_session:    IntakeSession dict (carried between /intake/step calls)
    #   _placer_session:    PlacerSession dict (carried between /placer/step calls)
    #   _tutor_session:     SessionContext dict (carried between /session/step calls)
    #   _plan_id:           set when intake returns COMPLETE
    #   _concept_id:        set when tutoring starts (first concept from plan
    #                       or from ?concept= deep-link)
    _phase: str = "INTAKE"
    _intake_session: dict[str, Any] = {}
    _placer_session: dict[str, Any] = {}
    _tutor_session: dict[str, Any] = {}
    _plan_id: str = ""
    _concept_id: str = concept_from_url
    _student_name: str = "Student"
    # material_ids from uploads that haven't been sent to /intake/step yet.
    # Cleared after each successful intake step (the backend attaches them
    # to the session on receipt).
    _pending_material_ids: list[str] = []
    # The page-load _start_intake() task. Stored so _on_aristotle_send
    # can await it if the user sends before it completes (instead of
    # polling with a short timeout that gives up before the model call
    # finishes — the old polling approach produced "I couldn't start
    # the intake conversation" when the model was slow but working).
    _intake_start_task: Any = None
    # Task 19: when the plan picker is showing, the chat bar is gated.
    # The learner must pick a plan (Resume or Start new) before the chat
    # bar accepts input. Without this gate, typing "resume my lessons"
    # into the chat bar while the picker is showing would fall through
    # _on_aristotle_send's "no _intake_session → retry _start_intake"
    # branch and start a fresh intake, bypassing the picker entirely.
    _picker_showing: bool = False

    with (
        ui.column()
        .classes("flex-1")
        .style(
            f"background:{C_GROUND}; overflow-y:auto; min-height:calc(100vh - 44px); "
            f"display:flex; flex-direction:column;"
        )
    ):
        # Header bar — minimal. Aristotle is the only voice the learner
        # meets; the header carries just the extension badge + the
        # current phase so the operator (not the learner) can see where
        # they are in the state machine.
        with (
            ui.row()
            .classes("w-full items-center")
            .style(
                f"padding:8px 16px; background:{C_SURFACE}; "
                f"border-bottom:0.5px solid {C_INK40};"
            )
        ):
            ui.label("ARISTOTLE").style(
                f"font-size:10px; font-weight:700; color:{C_GROUND}; "
                f"background:{C_AMBER}; padding:2px 8px; border-radius:{R_SM}; "
                f"letter-spacing:0.5px;"
            )
            phase_label = ui.label("Onboarding").style(
                f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO}; "
                f"margin-left:12px; letter-spacing:0.3px;"
            )
            ui.space()

        # Chat container — the single conversation surface. Holds every
        # message from intake, placer, and tutoring. flex:1 so it fills
        # the viewport between header and chat bar.
        chat_container = ui.column().classes("w-full flex-1").style(
            f"padding:16px; gap:12px; overflow-y:auto; flex:1; min-height:300px;"
        )

        # Chat bar — VISIBLE from the first render. The learner types
        # every reply here: subject, prior knowledge, goals, schedule,
        # placement answers, and tutoring answers. No separate forms,
        # no START button, no concept cards. Chat IS the surface.
        with (
            ui.row()
            .classes("w-full items-center gap-2")
            .style(
                f"padding:8px 16px; background:{C_SURFACE}; "
                f"border-top:0.5px solid {C_INK40};"
            )
        ):
            input_field = ui.input(
                placeholder="Type your reply to Aristotle...",
            ).props("dense dark outlined").classes("flex-1").style("font-size:15px;")

            async def _render_aristotle_message(text: str) -> None:
                """Append an Aristotle chat bubble to chat_container."""
                if not text:
                    return
                with chat_container:
                    ui.label(text).style(
                        f"font-size:15px; color:{C_CREAM}; font-family:{F_MONO}; "
                        f"background:{C_SURFACE}; padding:12px 16px; "
                        f"border-radius:8px; max-width:80%; "
                        f"border-left:3px solid {C_AMBER}; "
                        f"white-space:pre-wrap; line-height:1.5;"
                    )
                # Auto-scroll to bottom so the latest message is visible.
                # ui.run_javascript returns an AwaitableResponse; awaiting it
                # waits for the JS to execute on the client (fast for a scroll
                # command). No `respond` kwarg exists in this NiceGUI version.
                try:
                    await ui.run_javascript(
                        f"getHtmlElement('{chat_container.id}').scrollTop = "
                        f"getHtmlElement('{chat_container.id}').scrollHeight;"
                    )
                except Exception:
                    pass  # auto-scroll is best-effort — don't fail the message

            async def _render_student_message(text: str) -> None:
                """Append a right-aligned student chat bubble."""
                with chat_container:
                    ui.label(text).style(
                        f"font-size:15px; color:{C_CREAM}; font-family:{F_MONO}; "
                        f"background:{C_INK40}; padding:10px 14px; border-radius:8px; "
                        f"max-width:80%; align-self:flex-end; text-align:right;"
                    )

            async def _render_draft_plan(draft_plan: list) -> None:
                """Render the model's proposed concept sequence as a structured block.

                Shown when the IntakeActor returns next_focus=PLAN_DRAFT.
                The learner reviews the list and types "looks good" or
                "add more about X" in the chat bar — their reply goes back
                through /intake/step and the model amends the draft.
                """
                if not draft_plan:
                    return
                with chat_container:
                    with ui.column().style(
                        f"background:{C_SURFACE}; padding:16px 20px; "
                        f"border-radius:8px; max-width:80%; gap:8px; "
                        f"border-left:3px solid {C_AMBER};"
                    ):
                        ui.label("Proposed Learning Plan").style(
                            f"font-size:14px; font-weight:700; color:{C_AMBER}; "
                            f"font-family:{F_MONO}; letter-spacing:0.3px;"
                        )
                        ui.label(
                            "Here's the concept sequence I'm proposing. "
                            "Type 'looks good' to confirm, or tell me what to change."
                        ).style(
                            f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO};"
                        )
                        for i, concept in enumerate(draft_plan, 1):
                            topic = concept.get("topic", f"Concept {i}")
                            subtopic = concept.get("subtopic", "")
                            bloom = concept.get("bloom_target", 3)
                            content = concept.get("content_primary", "")
                            with ui.row().style("align-items:flex-start; gap:8px;"):
                                ui.label(f"{i}.").style(
                                    f"font-size:13px; font-weight:700; color:{C_AMBER}; "
                                    f"font-family:{F_MONO}; min-width:20px;"
                                )
                                with ui.column().style("gap:2px; flex:1;"):
                                    ui.label(topic).style(
                                        f"font-size:13px; font-weight:600; color:{C_CREAM}; "
                                        f"font-family:{F_MONO};"
                                    )
                                    if subtopic:
                                        ui.label(subtopic).style(
                                            f"font-size:11px; color:{C_MUTED}; "
                                            f"font-family:{F_MONO};"
                                        )
                                    if content:
                                        ui.label(content).style(
                                            f"font-size:12px; color:{C_CREAM}; "
                                            f"font-family:{F_MONO}; opacity:0.85; "
                                            f"line-height:1.4;"
                                        )
                                    ui.label(f"Bloom level: {bloom}").style(
                                        f"font-size:10px; color:{C_MUTED}; "
                                        f"font-family:{F_MONO};"
                                    )

            async def _render_error(msg: str) -> None:
                """Append a red error bubble — backend unreachable, etc."""
                with chat_container:
                    ui.label(msg).style(
                        f"font-size:13px; color:{C_ERR_FG}; font-family:{F_MONO}; "
                        f"padding:8px;"
                    )

            async def _render_http_error(exc: Exception, route: str) -> None:
                """Render an actionable error for HTTP failures.

                Distinguishes 404 (route missing — AIP_Aristotle out of
                date) from other status codes and from connection errors.
                """
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status == 404:
                        await _render_error(
                            f"The Aristotle backend returned 404 for {route}. "
                            f"This usually means AIP_Aristotle is out of date — "
                            f"run 'git pull origin main' in ~/AIP_Aristotle "
                            f"and restart ./start.sh."
                        )
                    else:
                        body = ""
                        try:
                            body = exc.response.text[:200]
                        except Exception:
                            pass
                        await _render_error(
                            f"Backend returned HTTP {status} for {route}."
                            + (f" Response: {body}" if body else "")
                        )
                elif isinstance(exc, httpx.ConnectError):
                    await _render_error(
                        "I can't reach my brain right now. "
                        "Please make sure the AIP backend is running "
                        "(./start.sh from ~/AIP_Brain)."
                    )
                else:
                    # BUG-004 fix: surface the exception detail + HTTP status
                    # code + response body so the operator can diagnose
                    # instead of seeing a generic "Something went wrong".
                    detail = str(exc)
                    if hasattr(exc, "response"):
                        try:
                            detail += f" | HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                        except Exception:
                            pass
                    await _render_error(f"Something went wrong: {detail}")

            async def _set_phase(new_phase: str) -> None:
                """Update the phase label in the header (operator visibility)."""
                nonlocal _phase
                _phase = new_phase
                labels = {
                    "INTAKE": "Onboarding",
                    "PLACER": "Placement",
                    "TUTORING": "Tutoring",
                    "COMPLETE": "Complete",
                }
                phase_label.set_text(labels.get(new_phase, new_phase))

            async def _start_intake() -> None:
                """Kick off the intake conversation by calling /intake/start.

                No plan_id yet (first visit). Returns a greeting prompt
                that we render as the first chat bubble.
                """
                nonlocal _intake_session
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        resp = await client.post(
                            "/aristotle/intake/start",
                            json={"plan_id": None},
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        _intake_session = data.get("session") or {}
                        prompt = data.get("prompt") or ""
                        if prompt:
                            await _render_aristotle_message(prompt)
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/intake/start")

            async def _step_intake(student_input: str) -> None:
                """Advance intake one turn with the learner's reply.

                Includes any pending material_ids (from uploads since the
                last step) in the request body so the backend can attach
                them to the session and the IntakeActor can read their
                extracted text into the model context.
                """
                nonlocal _intake_session, _plan_id, _pending_material_ids
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        resp = await client.post(
                            "/aristotle/intake/step",
                            json={
                                "session": _intake_session,
                                "student_input": student_input,
                                "material_ids": list(_pending_material_ids),
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        _intake_session = data.get("session", _intake_session)
                        # Clear pending material_ids — they're now attached
                        # to the session on the backend.
                        _pending_material_ids = []
                        prompt = data.get("prompt")
                        if prompt:
                            await _render_aristotle_message(prompt)

                        # If the backend returned a draft_plan in the session,
                        # render it as a structured block so the learner can
                        # review before confirming.
                        draft_plan = _intake_session.get("draft_plan") or []
                        if draft_plan and _intake_session.get("current_focus") == "PLAN_DRAFT":
                            await _render_draft_plan(draft_plan)

                        # Intake complete → auto-transition to PLACER.
                        # ADR-003 Phase 3: if the multi-step plan pipeline
                        # started (plan_job_id returned), poll for completion
                        # before transitioning. The pipeline runs 6 steps
                        # (structure retrieval → gap analysis → plan design →
                        # concept detail) and takes 1-2 minutes.
                        if data.get("state") == "COMPLETE":
                            _plan_id = data.get("plan_id", "")
                            await _set_phase("PLACER")
                            await _start_placer()
                        elif data.get("plan_job_id"):
                            # Pipeline started — poll for completion
                            plan_job_id = data["plan_job_id"]
                            await _render_aristotle_message(
                                "I'm now designing your learning plan. This involves "
                                "analyzing the paper's structure, identifying your "
                                "knowledge gaps, and building a phased curriculum. "
                                "This takes 1-2 minutes — I'll let you know when it's ready."
                            )
                            async def _poll_plan_progress(job_id: str) -> None:
                                import asyncio as _asyncio
                                for _ in range(120):  # poll for up to 10 min
                                    await _asyncio.sleep(5)
                                    try:
                                        async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=10.0) as client:
                                            r = await client.get(f"/aristotle/plan/{job_id}/status")
                                            r.raise_for_status()
                                            status = r.json()
                                    except Exception:
                                        continue
                                    status_val = status.get("status", "")
                                    steps_done = status.get("steps_done", 0)
                                    steps_total = status.get("steps_total", 6)
                                    if status_val == "COMPLETE":
                                        nonlocal _plan_id
                                        _plan_id = status.get("plan_id", "")
                                        await _render_aristotle_message(
                                            f"✅ Learning plan ready! I've designed a "
                                            f"phased curriculum based on your paper + "
                                            f"background. Let's begin with a quick "
                                            f"placement check."
                                        )
                                        await _set_phase("PLACER")
                                        await _start_placer()
                                        return
                                    elif status_val == "FAILED":
                                        error = status.get("error", "unknown error")
                                        await _render_error(
                                            f"Plan generation failed: {error}. "
                                            f"Please try confirming your plan again."
                                        )
                                        return
                                await _render_error(
                                    "Plan generation is taking longer than expected. "
                                    "Please check the backend logs or try again."
                                )
                            asyncio.create_task(_poll_plan_progress(plan_job_id))
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/intake/step")

            async def _start_placer() -> None:
                """Begin placement calibration for the completed plan."""
                nonlocal _placer_session
                if not _plan_id:
                    await _render_error(
                        "Cannot start placement — no learning plan was produced."
                    )
                    return
                # Brief orientation message before the first probe.
                await _render_aristotle_message(
                    "Before we start, I'd like to get a sense of where you are. "
                    "I'll ask a few quick questions — answer in your own words. "
                    "This just helps me meet you where you are."
                )
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        resp = await client.post(
                            "/aristotle/placer/start",
                            json={"plan_id": _plan_id},
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        _placer_session = data.get("session", {})
                        question = data.get("question", "")
                        if question:
                            await _render_aristotle_message(question)
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/placer/start")

            async def _step_placer(student_input: str) -> None:
                """Advance placement one turn with the learner's answer.

                Task 25 Fix 2: when the student's input was classified as
                QUESTION/TANGENT/CHAT (Task 24), the backend returns a
                "response" field with the curiosity/chat answer text +
                an "intent_class" field. The curiosity response already
                includes a natural weave-back ("Want to keep exploring
                this, or shall we continue where we left off?") so we
                render it ALONE — not alongside the still-pending probe
                question, which would be redundant and clunky. The probe
                question stays pending on the backend (session state
                unchanged); the student can answer it on the next turn.
                """
                nonlocal _placer_session, _concept_id
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        resp = await client.post(
                            "/aristotle/placer/step",
                            json={
                                "session": _placer_session,
                                "student_input": student_input,
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        _placer_session = data.get("session", _placer_session)
                        question = data.get("question")
                        # Task 25 Fix 2: read the curiosity/chat response.
                        # None for normal ANSWER turns (the backend doesn't
                        # set it in that branch).
                        curiosity_response = data.get("response")

                        if data.get("state") == "COMPLETE":
                            # Placement done → transition to tutoring.
                            await _set_phase("TUTORING")
                            # Task 17: use the plan's own authoritative next
                            # concept (now returned directly by
                            # /placer/step) instead of falling through to
                            # _start_tutoring's GET /aristotle/concepts
                            # fallback, which returns every concept ever
                            # ingested by anyone with no plan/material
                            # scoping — it was silently handing every
                            # student "newton_first_law" (the oldest row
                            # in the table, a leftover dogfood-bootstrap
                            # concept), regardless of which plan or
                            # material was actually active.
                            next_concept_id = data.get("next_concept_id")
                            if next_concept_id:
                                _concept_id = next_concept_id
                            await _start_tutoring()
                        elif curiosity_response:
                            # Task 25 Fix 2: a curiosity/chat answer takes
                            # priority over re-rendering the pending probe
                            # question. The curiosity response already
                            # weaves back to the pending probe naturally
                            # ("Want to keep exploring this, or shall we
                            # continue where we left off?"), so rendering
                            # the raw question text underneath would be
                            # redundant.
                            await _render_aristotle_message(curiosity_response)
                        elif question:
                            await _render_aristotle_message(question)
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/placer/step")

            async def _start_tutoring() -> None:
                """Begin the tutoring loop with the first concept from the plan."""
                nonlocal _tutor_session, _concept_id
                # If we don't have a concept_id yet (?concept= absent and
                # placer didn't set one), read the plan and take the first
                # concept from its sequence.
                if not _concept_id:
                    try:
                        async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=30.0) as client:
                            # /concepts returns all ingested concepts; the
                            # plan's concept_sequence_json would be the
                            # authoritative source, but /concepts is what
                            # the existing API exposes. Take the first.
                            r = await client.get("/aristotle/concepts")
                            r.raise_for_status()
                            concepts = r.json()
                            if concepts:
                                _concept_id = concepts[0].get(
                                    "id",
                                    concepts[0].get("concept_id", ""),
                                )
                    except Exception:
                        pass

                if not _concept_id:
                    await _render_error(
                        "No concept available to start tutoring. "
                        "Ingest course material first."
                    )
                    return

                await _render_aristotle_message(
                    f"Great — let's begin. I'll ask what you think first, "
                    f"then teach, then check your understanding."
                )
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        resp = await client.post(
                            "/aristotle/session/start",
                            json={"concept_id": _concept_id},
                        )
                        resp.raise_for_status()
                        _tutor_session = resp.json()

                        # session/start creates state=PREDICT but no prompt.
                        # session/step with empty student_input generates the
                        # initial PREDICT question.
                        step_resp = await client.post(
                            "/aristotle/session/step",
                            json={"session": _tutor_session, "student_input": ""},
                        )
                        step_resp.raise_for_status()
                        step_data = step_resp.json()
                        _tutor_session = step_data.get("session", _tutor_session)
                        predict_prompt = step_data.get("output", "")
                        if predict_prompt:
                            await _render_aristotle_message(predict_prompt)
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/session/start")

            async def _step_tutoring(student_input: str) -> None:
                """Advance the tutoring loop one turn with the learner's answer."""
                nonlocal _tutor_session
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        resp = await client.post(
                            "/aristotle/session/step",
                            json={
                                "session": _tutor_session,
                                "student_input": student_input,
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        _tutor_session = data.get("session", _tutor_session)
                        output = data.get("output", "")
                        if output:
                            await _render_aristotle_message(output)

                        # Session complete → end of tutoring.
                        if _tutor_session.get("state") == "SESSION_COMPLETE":
                            await _set_phase("COMPLETE")
                            await _render_aristotle_message(
                                f"Great work, {_student_name}. "
                                f"That's the end of this session."
                            )
                            clear_active_extension()
                            input_field.set_enabled(False)
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/session/step")

            # -------------------------------------------------------
            # Task 19: plan picker — "Resume <subject>" UI.
            #
            # The pre-Task-19 page load unconditionally called
            # _start_intake() with plan_id=None, which always returned
            # a fresh GREETING. Existing plans (pharmacy, physics, etc.)
            # were undiscoverable from the UI — the learner had to know
            # the plan_id out-of-band. Saying "resume my lessons" just
            # went into the intake as a regular reply.
            #
            # The picker calls GET /aristotle/plans (Task 18 API) and
            # renders one button per existing plan + a "Start new
            # subject" button. Clicking "Resume" calls /intake/start
            # with the chosen plan_id; check_intake_triggers() returns
            # trigger=None for healthy plans (skip intake, jump straight
            # to PLACER), or the right re-engagement trigger for stale
            # / completed plans.
            # -------------------------------------------------------

            async def _show_plan_picker() -> None:
                """Fetch existing plans + render a Resume / Start-New picker.

                If the plans list is empty (genuinely first-time user, or
                the backend is unreachable), falls through to _start_intake()
                so the existing onboarding flow runs unchanged.
                """
                nonlocal _picker_showing
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=5.0) as client:
                        r = await client.get("/aristotle/plans")
                        r.raise_for_status()
                        plans = r.json() or []
                except Exception:
                    # Backend unreachable — don't block onboarding. Fall
                    # through to fresh intake; the learner can still
                    # onboard even if /plans is broken.
                    await _start_intake()
                    return

                if not plans:
                    # No existing plans — go straight to fresh intake.
                    await _start_intake()
                    return

                # Render the picker. Gate the chat bar so the learner
                # can't type "resume my lessons" into the chat bar while
                # the picker is showing (that would bypass the picker via
                # _on_aristotle_send's retry path).
                _picker_showing = True
                input_field.set_enabled(False)

                # Render the picker inside the chat container. Each plan
                # is a button; clicking it clears the picker and resumes
                # that plan. A "Start new subject" button at the bottom
                # runs the existing fresh-intake flow.
                with chat_container:
                    with ui.column().style(
                        f"background:{C_SURFACE}; padding:16px 20px; "
                        f"border-radius:8px; max-width:80%; gap:10px; "
                        f"border-left:3px solid {C_AMBER};"
                    ):
                        ui.label("Welcome back").style(
                            f"font-size:16px; font-weight:700; color:{C_AMBER}; "
                            f"font-family:{F_MONO}; letter-spacing:0.3px;"
                        )
                        ui.label(
                            "I found existing learning plans on this machine. "
                            "Pick one to resume, or start a new subject."
                        ).style(
                            f"font-size:13px; color:{C_CREAM}; font-family:{F_MONO}; "
                            f"line-height:1.5;"
                        )

                        for plan in plans:
                            plan_id = plan.get("id", "")
                            subject = plan.get("subject", "(untitled subject)")
                            status = plan.get("status", "active")
                            total = plan.get("total_concepts", 0)
                            idx = plan.get("current_concept_idx", 0)
                            last = plan.get("last_session_at")

                            # Build a one-line description per plan.
                            if status == "complete":
                                status_str = "complete"
                            elif last:
                                # Trim ISO timestamp to YYYY-MM-DD for display.
                                status_str = f"last session {last[:10]}"
                            else:
                                status_str = "not started yet"

                            label = (
                                f"Resume: {subject}  "
                                f"({idx}/{total} concepts, {status_str})"
                            )

                            # Task 20: each plan row is now a row
                            # containing the Resume button (main click
                            # target, left-aligned, fills most of the
                            # width) + a small delete icon button
                            # (separate click target, right-aligned).
                            # Clicking Resume must NEVER trigger delete
                            # — they are sibling elements, not nested.
                            #
                            # Delete is a two-step confirm: first click
                            # on the trash icon shows a "Confirm delete
                            # <subject>?" inline prompt with Confirm /
                            # Cancel buttons. Only the second click
                            # (Confirm) calls the DELETE route. This is
                            # a real destructive action with no undo —
                            # a one-click delete on live student data
                            # is not acceptable per the task brief.
                            with ui.row().classes("w-full items-center gap-2").style(
                                "margin:2px 0;"
                            ):
                                async def _on_resume(_evt, pid=plan_id) -> None:
                                    await _resume_plan(pid)

                                ui.button(label, on_click=_on_resume).props(
                                    "flat dense align=left no-caps"
                                ).style(
                                    f"color:{C_CREAM}; font-family:{F_MONO}; "
                                    f"font-size:13px; text-transform:none; "
                                    f"justify-content:flex-start; "
                                    f"border:0.5px solid {C_INK40}; "
                                    f"flex:1;"
                                )

                                # Trash icon button — first click opens
                                # the inline confirmation prompt.
                                async def _on_delete_click(_evt, pid=plan_id, psub=subject) -> None:
                                    await _confirm_delete_plan(pid, psub)

                                ui.button("🗑", on_click=_on_delete_click).props(
                                    "flat dense round no-caps"
                                ).style(
                                    f"color:{C_ERR_FG}; font-size:14px; "
                                    f"min-width:32px; padding:4px 8px; "
                                    f"border:0.5px solid {C_INK40};"
                                ).tooltip(
                                    f"Delete plan: {subject} (irreversible)"
                                )

                        ui.separator().style(f"background:{C_INK40}; margin:8px 0;")

                        async def _on_new(_evt) -> None:
                            await _start_new_plan()

                        ui.button("Start a new subject", on_click=_on_new).props(
                            "flat dense align=left no-caps"
                        ).style(
                            f"color:{C_AMBER}; font-family:{F_MONO}; "
                            f"font-size:13px; text-transform:none; "
                            f"justify-content:flex-start;"
                        )

            async def _resume_plan(plan_id: str) -> None:
                """Resume an existing plan via /intake/start with plan_id.

                check_intake_triggers() on the backend decides:
                  - trigger=None → plan is healthy; skip intake, jump
                    straight to PLACER.
                  - trigger='full'/'checkin'/'partial' → render the
                    re-engagement prompt, enter INTAKE phase with the
                    returned session so the learner can reply.
                """
                nonlocal _intake_session, _plan_id, _phase, _picker_showing
                # Clear the picker — we're moving past it. Re-enable the
                # chat bar so the learner can reply to the placement
                # questions or the re-engagement prompt.
                _picker_showing = False
                input_field.set_enabled(True)
                chat_container.clear()
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        resp = await client.post(
                            "/aristotle/intake/start",
                            json={"plan_id": plan_id},
                        )
                        resp.raise_for_status()
                        data = resp.json()
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/intake/start (resume)")
                    return

                trigger = data.get("trigger")
                # trigger=None means "no intake needed, plan is healthy."
                # The route returns {trigger: None, prompt: None, session: None}
                # — proceed directly to PLACER for the resumed plan.
                if trigger is None:
                    _plan_id = plan_id
                    _phase = "PLACER"
                    phase_label.set_text("Placement")
                    await _start_placer()
                    return

                # Otherwise, render the re-engagement prompt and enter
                # INTAKE so the learner can reply through the normal
                # chat bar. /intake/start already built the session at
                # the trigger's entry_state.
                _intake_session = data.get("session") or {}
                _plan_id = plan_id
                prompt = data.get("prompt") or ""
                if prompt:
                    await _render_aristotle_message(prompt)

            async def _start_new_plan() -> None:
                """Clear the picker + kick off fresh intake (existing flow)."""
                nonlocal _picker_showing
                _picker_showing = False
                input_field.set_enabled(True)
                chat_container.clear()
                await _start_intake()

            async def _confirm_delete_plan(plan_id: str, subject: str) -> None:
                """Two-step delete confirmation (Task 20).

                First click on the trash icon calls this. Renders an
                inline "Confirm delete <subject>? This is irreversible."
                prompt with Confirm + Cancel buttons. Only the second
                click (Confirm) calls _delete_plan() which hits the
                DELETE route. The chat bar stays gated (_picker_showing
                remains True) throughout so the learner can't type into
                the chat bar while the confirmation is up.

                Uses ui.dialog for modal semantics — the learner must
                pick one of the two buttons to dismiss it, no way to
                accidentally trigger the delete by clicking elsewhere.
                """
                with ui.dialog() as dialog, ui.card().style(
                    f"background:{C_SURFACE}; padding:24px; "
                    f"max-width:480px;"
                ):
                    ui.label("Delete this learning plan?").style(
                        f"font-size:16px; font-weight:700; "
                        f"color:{C_ERR_FG}; font-family:{F_MONO}; "
                        f"margin-bottom:12px;"
                    )
                    ui.label(
                        f"Plan: {subject}\n\n"
                        f"This will permanently delete the plan and all its "
                        f"concepts, mastery records, placement events, and "
                        f"session history. The source material (uploaded "
                        f"PDF/text) is NOT deleted — only the plan built "
                        f"from it.\n\n"
                        f"This action is irreversible. There is no undo."
                    ).style(
                        f"font-size:12px; color:{C_CREAM}; "
                        f"font-family:{F_MONO}; line-height:1.6; "
                        f"white-space:pre-wrap; margin-bottom:20px;"
                    )
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancel", on_click=dialog.close).props(
                            "flat no-caps"
                        ).style(
                            f"color:{C_MUTED}; font-family:{F_MONO}; "
                            f"font-size:12px;"
                        )

                        async def _on_confirm() -> None:
                            dialog.close()
                            await _delete_plan(plan_id, subject)

                        ui.button("Delete permanently", on_click=_on_confirm).props(
                            "no-caps"
                        ).style(
                            f"background:{C_ERR_FG}; color:{C_GROUND}; "
                            f"font-family:{F_MONO}; font-size:12px; "
                            f"font-weight:700;"
                        )
                dialog.open()

            async def _delete_plan(plan_id: str, subject: str) -> None:
                """Call DELETE /aristotle/plans/{plan_id} + refresh the picker.

                On success: show a brief "deleted" message in chat, then
                re-render the picker (the deleted plan is gone, the rest
                remain). On failure: show the error inline and re-render
                the picker so the learner can retry or pick a different
                plan.

                The DELETE route is destructive with no undo — the
                confirmation step in _confirm_delete_plan is the only
                thing standing between the user and data loss. That's
                why this function does NOT ask again; the user has
                already explicitly confirmed.
                """
                # Clear the picker (we're going to re-render it after).
                chat_container.clear()
                await _render_aristotle_message(
                    f"Deleting plan '{subject}'..."
                )
                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=30.0) as client:
                        resp = await client.delete(f"/aristotle/plans/{plan_id}")
                        resp.raise_for_status()
                        result = resp.json()
                except Exception as exc:
                    await _render_http_error(exc, f"DELETE /aristotle/plans/{plan_id}")
                    # Re-render the picker so the learner can retry.
                    await _show_plan_picker()
                    return

                concepts_n = result.get("concepts_deleted", 0)
                cascade_n = result.get("cascade_rows_deleted", 0)
                await _render_aristotle_message(
                    f"Deleted '{subject}' — {concepts_n} concepts and "
                    f"{cascade_n} related records (placement, mastery, "
                    f"session history, etc.) removed."
                )
                # Re-render the picker so the learner can pick another
                # plan to resume or start a new one. The deleted plan
                # will not appear in the new list.
                await _show_plan_picker()

            async def _on_aristotle_send() -> None:
                """Main chat dispatch — routes the learner's input to the
                correct backend endpoint based on the current phase.

                Resilience: if a phase's session state is empty (meaning
                the phase's /start call failed or hasn't succeeded yet),
                retry the /start call with the learner's text as a
                follow-up rather than calling /step with an empty
                session (which would 404 or error). This prevents
                cascading failures when the backend is temporarily
                unreachable or a route is missing.
                """
                nonlocal _intake_start_task
                text = input_field.value.strip()
                if not text:
                    return
                # Task 19: if the plan picker is showing, ignore chat-bar
                # input entirely. The learner must click a picker button
                # (Resume / Start new) to move past this gate. The chat
                # bar is also disabled at the input level, but this is a
                # belt-and-braces guard against keyboard shortcuts /
                # programmatic submits.
                if _picker_showing:
                    return
                input_field.value = ""
                await _render_student_message(text)

                if _phase == "INTAKE":
                    # If the page-load _start_intake() task is still in
                    # flight, await it (instead of polling with a short
                    # timeout). The model call can take 30-60s on
                    # OpenRouter — the old 10s poll gave up prematurely.
                    if _intake_start_task is not None and not _intake_start_task.done():
                        await _render_aristotle_message(
                            "Let me think for a moment..."
                        )
                        await _intake_start_task
                    _intake_start_task = None

                    if not _intake_session:
                        # Start failed or never ran — retry.
                        await _start_intake()
                    if _intake_session:
                        await _step_intake(text)
                elif _phase == "PLACER":
                    if not _placer_session:
                        await _start_placer()
                    else:
                        await _step_placer(text)
                elif _phase == "TUTORING":
                    if not _tutor_session:
                        await _start_tutoring()
                    else:
                        await _step_tutoring(text)
                # COMPLETE → no-op (chat bar disabled)

            # Upload button — paperclip icon. Opens a file picker; the
            # selected file is POSTed to /aristotle/upload which extracts
            # text (PDF via pypdf, images via pytesseract OCR, txt/html/
            # json as text). The extracted text is shown in chat as a
            # student-uploaded-material bubble so Aristotle can reference
            # it in the intake/tutoring conversation.
            async def _handle_aristotle_upload(e) -> None:
                """Handle a file upload from the ARISTOTLE chat bar.

                NiceGUI 3.x upload event API:
                  - e.file is a FileUpload instance (NOT e.content)
                  - e.file.name is the filename
                  - await e.file.read() returns bytes (read() is async)

                A previous version used e.content.read() (missing await +
                wrong attribute name) which raised AttributeError BEFORE
                the try/except block, so the task died silently with no
                chat bubble feedback. The learner saw the upload widget
                hit 100% but nothing happened — no "Uploading..." bubble,
                no error, no acknowledgment from Aristotle.

                DIAGNOSTIC: a ui.notify toast fires at the VERY TOP of
                this handler (before any try/except) so we can definitively
                tell whether on_upload is being called at all. If the toast
                appears but no chat bubble follows, the error is inside the
                handler. If the toast does NOT appear, the on_upload
                callback itself is not firing (NiceGUI widget issue).
                """
                # DIAGNOSTIC: fire a toast immediately so the user can see
                # the handler was called. This is BEFORE any try/except so
                # it fires even if everything else below is broken.
                try:
                    ui.notify(
                        f"Upload handler fired — processing file...",
                        color="info",
                        timeout=2000,
                    )
                except Exception:
                    pass  # don't let the toast itself crash the handler

                import os as _os
                import traceback as _tb

                try:
                    filename = getattr(e.file, "name", "file")
                    content = await e.file.read()
                except Exception as exc:
                    await _render_error(
                        f"Could not read uploaded file: {exc}\n\n"
                        f"Traceback:\n{_tb.format_exc()[:500]}"
                    )
                    return

                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                _ext_types = {
                    "pdf": "application/pdf",
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "gif": "image/gif",
                    "webp": "image/webp",
                    "bmp": "image/bmp",
                    "tiff": "image/tiff",
                    "tif": "image/tiff",
                    "txt": "text/plain",
                    "md": "text/plain",
                    "csv": "text/plain",
                    "html": "text/html",
                    "htm": "text/html",
                    "json": "application/json",
                    "yaml": "text/plain",
                    "yml": "text/plain",
                }
                content_type = _ext_types.get(ext, "application/octet-stream")

                # Show a "uploading" bubble so the learner sees feedback.
                await _render_aristotle_message(f"Uploading {filename}...")

                try:
                    async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=300.0) as client:
                        upload_resp = await client.post(
                            "/aristotle/upload",
                            content=content,
                            headers={
                                "Content-Type": content_type,
                                "Content-Disposition": f'attachment; filename="{filename}"',
                            },
                        )
                        upload_resp.raise_for_status()
                        data = upload_resp.json()
                        extracted = data.get("extracted_text", "")
                        source_type = data.get("source_type", "file")
                        char_count = data.get("char_count", len(extracted))
                        material_id = data.get("material_id", "")

                        # Track the material_id so it gets attached to the
                        # next /intake/step call. The backend persists the
                        # extracted text and the IntakeActor reads it into
                        # the model context.
                        if material_id:
                            _pending_material_ids.append(material_id)
                        else:
                            # material_id empty → upload route failed to persist
                            # (DB error caught silently server-side). Tell the
                            # learner so they know Aristotle won't see the paper.
                            await _render_error(
                                f"Upload of {filename} succeeded but the server "
                                f"couldn't persist it for Aristotle to read. "
                                f"Check the backend logs — this is usually a DB "
                                f"schema or INSERT bug. The LLM will NOT see "
                                f"this paper's content until the upload route "
                                f"is fixed."
                            )
                            return

                        if extracted.strip():
                            # BUG-002 Part B: warn the learner when extraction
                            # is suspiciously thin (< 200 chars). This is
                            # common with math-heavy PDFs where pypdf can't
                            # parse LaTeX-rendered glyphs. Without this
                            # warning, the learner thinks Aristotle read the
                            # paper, but the LLM context is effectively empty.
                            if char_count < 200:
                                await _render_error(
                                    f"Uploaded {filename} but extracted only "
                                    f"{char_count} chars — likely a math-heavy "
                                    f"or scanned PDF that pypdf can't parse. "
                                    f"Aristotle will NOT be able to read this "
                                    f"paper's content. Try pasting the abstract "
                                    f"+ section headings as text instead, or "
                                    f"convert the PDF to a text-based format."
                                )
                                # Still attach the material_id so the
                                # backend logs the thin-text warning, but
                                # don't auto-trigger an intake step —
                                # the LLM would just hallucinate.
                            else:
                                # Show a truncated preview in chat (first 500
                                # chars) so the learner sees what was extracted.
                                preview = extracted[:500]
                                if len(extracted) > 500:
                                    preview += f"\n\n... ({char_count} chars total, {source_type})"
                                ingest_job_id = data.get("ingest_job_id", "")
                                ingest_msg = (
                                    f"Got it — I've read {filename} "
                                    f"({source_type}, {char_count} chars). "
                                    f"I'll use this as context for our tutoring.\n\n"
                                    f"Preview:\n{preview}"
                                )
                                if ingest_job_id:
                                    ingest_msg += (
                                        f"\n\n📋 Ingesting paper into knowledge base "
                                        f"(job: {ingest_job_id[:8]}...) — "
                                        f"chunking + embedding + structural analysis. "
                                        f"This takes 30-60 seconds; you can continue "
                                        f"chatting while it runs."
                                    )
                                await _render_aristotle_message(ingest_msg)

                                # ADR-003: poll the ingest job in the background
                                # and update the learner when it's done. The
                                # ingestion chunks + embeds + analyzes the paper
                                # for RAG retrieval. Until it's done, the
                                # IntakeActor falls back to legacy truncation.
                                #
                                # IMPORTANT: do NOT auto-trigger _step_intake
                                # until ingestion is COMPLETE. The RAG retrieval
                                # needs the structural map (from the ANALYZE
                                # phase) to build a good prompt. Auto-triggering
                                # before ingestion finishes causes:
                                #   - empty structural_maps → legacy truncation fallback
                                #   - large RAG prompt → long LLM call → OpenRouter timeout
                                #   - "Something went wrong" error
                                if ingest_job_id:
                                    async def _poll_ingest_progress(job_id: str) -> None:
                                        import asyncio as _asyncio
                                        for _ in range(60):  # poll for up to 5 min
                                            await _asyncio.sleep(5)
                                            try:
                                                async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=10.0) as client:
                                                    r = await client.get(f"/aristotle/ingest/{job_id}/status")
                                                    r.raise_for_status()
                                                    status = r.json()
                                            except Exception:
                                                continue
                                            phase = status.get("phase", "")
                                            status_val = status.get("status", "")
                                            if status_val == "COMPLETE":
                                                chunks = status.get("chunks_total", 0)
                                                analysis = "✓" if status.get("analysis_complete") else "✗"
                                                await _render_aristotle_message(
                                                    f"✅ Paper ingestion complete — {chunks} chunks indexed, "
                                                    f"structural analysis: {analysis}. "
                                                    f"I can now retrieve specific sections as we discuss."
                                                )
                                                # NOW auto-trigger an intake step — the
                                                # structural map is ready, RAG retrieval
                                                # will work properly.
                                                if _phase == "INTAKE" and _intake_session:
                                                    await _step_intake("")
                                                return
                                            elif status_val == "FAILED":
                                                error = status.get("error", "unknown error")
                                                await _render_error(
                                                    f"Paper ingestion failed: {error}. "
                                                    f"You can continue chatting, but I'll use "
                                                    f"the legacy truncation path instead of RAG."
                                                )
                                                # On failure, still auto-trigger so the
                                                # learner gets a response (legacy path).
                                                if _phase == "INTAKE" and _intake_session:
                                                    await _step_intake("")
                                                return
                                        # Timeout — don't error, just stop polling
                                        await _render_aristotle_message(
                                            f"(Paper ingestion is still running in the background. "
                                            f"I'll use the legacy path for now — once ingestion "
                                            f"completes, I'll automatically switch to RAG retrieval.)"
                                        )
                                        # Auto-trigger even on timeout (legacy path)
                                        if _phase == "INTAKE" and _intake_session:
                                            await _step_intake("")
                                    asyncio.create_task(_poll_ingest_progress(ingest_job_id))
                                else:
                                    # No ingest job (paper too short or upload issue) —
                                    # auto-trigger immediately via legacy path.
                                    if _phase == "INTAKE" and _intake_session:
                                        await _step_intake("")
                        else:
                            await _render_aristotle_message(
                                f"Uploaded {filename} but couldn't extract any text. "
                                f"If it's a scanned PDF, try a higher-resolution scan."
                            )
                except Exception as exc:
                    await _render_http_error(exc, "/aristotle/upload")

            # NOTE: on_upload takes the async function DIRECTLY (not wrapped
            # in `lambda e: asyncio.create_task(...)`). The lambda+create_task
            # pattern creates an UNTRACKED task whose exceptions are silently
            # swallowed by the event loop (Python never retrieves them).
            # Passing the async function directly lets NiceGUI's handle_event
            # create the task with proper exception logging, so any error in
            # the handler surfaces in the console + the ui.notify diagnostic
            # toast at the top of the handler.
            ui.upload(
                on_upload=_handle_aristotle_upload,
                auto_upload=True,
                max_file_size=10_000_000,
            ).props("flat dense dark").style(
                f"max-width:40px; min-width:40px;"
            ).tooltip("Upload textbook, paper, or notes")

            ui.button(
                "Send",
                on_click=lambda: asyncio.create_task(_on_aristotle_send()),
            ).props("dense").style(
                f"background:{C_AMBER}; color:#0d1117; font-family:{F_MONO};"
            )

            input_field.on(
                "keydown.enter",
                lambda: asyncio.create_task(_on_aristotle_send()),
            )

        # Kick off the plan picker on page load. If existing plans are
        # found, the learner picks one to resume or starts a new subject;
        # otherwise the picker falls through to _start_intake() and the
        # greeting appears as the first chat bubble (pre-Task-19 behavior).
        # Store the task so _on_aristotle_send can await it if the user
        # sends before it completes (model calls can take 30-60s on
        # OpenRouter).
        #
        # Task 19: previously this was asyncio.create_task(_start_intake())
        # unconditionally — every page load started fresh intake, existing
        # plans were undiscoverable, and "resume my lessons" went into
        # the intake as a regular reply.
        _intake_start_task = asyncio.create_task(_show_plan_picker())

    # Right rail — the SINGLE global right sidebar. Shared layout component
    # renders the extension context panel via _right_extension_panel(), which
    # branches on _active_extension["name"] to expose context-relevant nav
    # (ARISTOTLE: Teacher Dashboard / Session Stats / Curriculum Map /
    # Settings). Other pages with no active extension render the drawer
    # hidden (value=False), so it is simply absent rather than blank.
    build_right_rail(state)
