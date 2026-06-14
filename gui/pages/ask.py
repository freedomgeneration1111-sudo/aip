"""AIP Ask Page — Route: /ask

THE MOST IMPORTANT PAGE — the Ask Workbench.

UI Cycle 4 upgrades the migrated Ask page into the Full Dogfood Ask Workbench.
Every assistant answer is now inspectable, source-grounded, and linkable, with
visible retrieval health and degraded/direct-model warnings.

Flow:
  1. API key check on load
  2. Backend health check with 4s timeout
  3. Model slot loading from /api/v1/models/slots
  4. Session creation via POST /api/v1/sessions
  5. WebSocket chat via ws://backend/api/v1/chat/session_id
  6. Message types: message, response, gate, error, pong
  7. Gate handling: approve/reject buttons for DEFINER gates
  8. Auto-save toggle with session update
  9. Direct OpenRouter fallback when backend unreachable — MUST show
     "DIRECT MODEL ONLY — NOT DOGFOOD" banner
  10. Per-answer status strip: retrieval healthy / degraded / lexical only /
      no sources / direct model only / trace unavailable
  11. Per-answer actions: Show Sources, Show Trace, Save as Artifact,
      Link Wiki (not wired), Run Model Council (not wired)
  12. Source panel: drawer with source title/path, snippet, score, channel
  13. Trace panel: drawer with channels attempted/used, degradation, warnings

CRITICAL RULES:
  - Direct model fallback must be labeled
    "DIRECT MODEL ONLY — NOT DOGFOOD — No retrieval. No corpus.
     No actors. No artifact lifecycle."
  - If retrieval trace/source data is unavailable, show unavailable honestly.
  - Do not create fake traces.
  - Do not silently save artifacts, mutate wiki, approve gates, or export.
  - Beast Counsel and Model Council are shown as disabled/unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nicegui import context, ui

from gui.components.answer_card import add_answer_card
from gui.components.beast_panel import BeastPanel
from gui.components.chat import add_message, add_system_message, build_chat_input
from gui.components.layout import build_left_nav, build_right_rail, build_top_bar
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
async def ask_page():
    """Ask Workbench — chat interface with backend or direct model fallback."""
    state = get_session_state()
    state.client = context.client

    # ── Initialize panels ────────────────────────────────────────
    source_panel = SourcePanel()
    trace_panel = TracePanel()
    beast_panel = BeastPanel()
    model_council_panel = ModelCouncilPanel()

    # ── API Key Check ──────────────────────────────────────────
    if not state.api_client.has_openrouter_api_key():
        key = await show_api_key_prompt()
        if key:
            state.api_client.set_openrouter_api_key(key)
            ui.notify("API key saved!", color="positive", position="top")
        else:
            ui.notify("No API key set. Chat will not work.", color="warning", position="top")

    # ── Backend Health Check ───────────────────────────────────
    await _check_backend_health(state)

    # ── Load Model Slots ──────────────────────────────────────
    slots = await _load_model_slots(state)

    # Populate role model assignments from backend slot config
    for s in slots:
        sn = s.get("slot_name")
        m = s.get("model")
        if sn and m and not str(m).startswith("<"):
            set_role_model(sn, m)

    # ── Build Model Options ───────────────────────────────────
    # Refresh enabled models from backend library so the dropdown
    # includes models toggled on via the Models page.
    await refresh_enabled_models()
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

    # Refresh dogfood mode
    state.refresh_dogfood_mode()

    # ── BUILD LAYOUT ──────────────────────────────────────────
    build_top_bar(state)
    build_left_nav(state, active_page="/ask")

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
        with (
            ui.row()
            .classes("w-full items-center")
            .style(f"padding:8px 16px; background:{C_SURFACE}; border-bottom:0.5px solid {C_INK40};")
        ):
            # Model slot selector
            ui.label("Chat Model").style(
                f"font-size:10px; font-weight:600; color:{C_AMBER}; letter-spacing:0.5px; margin-right:8px;"
            )
            (
                ui.select(
                    all_model_options,
                    value=current_chat_model,
                    on_change=lambda e: _on_chat_model_changed(e.value, state),
                )
                .props("dense dark")
                .classes("min-w-[180px]")
                .style("font-size:11px;")
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
        input_field = build_chat_input(
            state,
            chat_container,
            send_fn=lambda: _send_prompt(
                state,
                chat_container,
                input_field,
                source_panel,
                trace_panel,
                beast_panel,
                model_council_panel,
            ),
        )

    build_right_rail(state)


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
    """Handle chat model selection change — awaits backend confirmation."""
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

            # Build turn_data for action callbacks
            turn_data = {
                "session_id": state.session_id,
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
            # Direct model fallback — use answer card with direct_model=True
            turn_data = {
                "session_id": state.session_id,
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
