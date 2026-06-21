"""Beast Counsel Panel — advisory second perspective on each assistant turn.

Beast provides an advisory second perspective on each turn:
  - Continuity: How does this connect to prior context?
  - Critique: What are the strengths and weaknesses?
  - Strategy: What should be explored next?
  - Librarian: Are there knowledge gaps or missing sources?
  - Risk: What could go wrong?

Beast is ADVISORY ONLY. Beast may suggest actions but must never silently
execute them. Suggested actions always require DEFINER approval.

UI Cycle 5.1: Mode-aware commentary — each turn can have multiple Beast
commentaries keyed by mode. Switching modes fetches the commentary for
the selected mode; it never shows stale data from another mode.

Import boundary: this module imports ONLY from gui.* (theme, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_DOGFOOD_BARE,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_OK_FG,
    C_RAISED,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_SM,
)

log = logging.getLogger("gui.components.beast_panel")

# Valid Beast commentary modes
BEAST_MODES = {
    "continuity": "Continuity — how this connects to prior context",
    "critique": "Critique — strengths and weaknesses",
    "strategy": "Strategy — what to explore next",
    "librarian": "Librarian — knowledge gaps and missing sources",
    "risk": "Risk — what could go wrong",
}

# Dialog container width — single source of truth for all Beast Counsel dialogs.
# Was previously a right-side drawer (width:420px). Moved to centered modal in
# this cycle so it doesn't compete with main content for horizontal space.
_DIALOG_STYLE = (
    f"width:90vw; max-width:900px; background:{C_GROUND}; "
    f"border:0.5px solid {C_INK40}; border-radius:{R_SM}; padding:0;"
)


class BeastPanel:
    """Beast Counsel panel — advisory side panel for turn-level commentary.

    Mode-aware: each turn can have multiple commentaries, one per mode.
    Switching the mode selector fetches commentary for that mode from the
    backend. Running counsel generates commentary for the selected mode.

    Usage:
        panel = BeastPanel()
        # On answer card action:
        panel.show_counsel(turn_id, session_id, api_client, ...)
    """

    def __init__(self) -> None:
        # _drawer holds the ui.dialog; _content_container holds the inner
        # scrollable column. Content rendering methods must add to
        # _content_container (not _drawer) so new children appear inside the
        # scrollable region rather than as siblings of it.
        self._drawer: Any = None
        self._content_container: Any = None
        self._current_turn_id: str = ""
        self._current_mode: str = "continuity"
        self._loading: bool = False
        # Context stored for re-fetching on mode switch
        self._session_id: str = ""
        self._api_client: Any = None
        self._question_text: str = ""
        self._answer_text: str = ""
        self._sources: list[dict] | None = None
        self._trace_available: bool = False
        self._lexical_only: bool = False
        self._vector_contributed: bool = False

    def _open_dialog(self) -> Any:
        """Open a fresh centered dialog and return the inner content column.

        Replaces the previous right-drawer surface. The dialog itself is
        assigned to ``self._drawer`` and its inner scrollable column to
        ``self._content_container`` so subsequent render methods can append
        children inside the scrollable region.
        """
        self.close()
        dialog = ui.dialog().props("persistent=false; maximized=false").style(_DIALOG_STYLE)
        self._drawer = dialog
        content_column = ui.column().classes("w-full").style("max-height:85vh; overflow-y:auto;")
        self._content_container = content_column
        try:
            dialog.open()
        except Exception as exc:
            log.debug("beast_dialog_open_failed: %s", exc)
        return content_column

    async def show_counsel(
        self,
        turn_id: str,
        session_id: str,
        api_client: Any,
        *,
        mode: str = "continuity",
        question_text: str = "",
        answer_text: str = "",
        sources: list[dict] | None = None,
        trace_available: bool = False,
        lexical_only: bool = False,
        vector_contributed: bool = False,
    ) -> None:
        """Open Beast Counsel panel and fetch/generate commentary for a turn + mode.

        First tries to GET existing commentary for the selected mode.
        If none exists, offers a Run button to generate it.
        Switching modes re-fetches commentary for the new mode.
        """
        self._current_turn_id = turn_id
        self._current_mode = mode.strip().lower() if mode else "continuity"
        self._session_id = session_id
        self._api_client = api_client
        self._question_text = question_text
        self._answer_text = answer_text
        self._sources = sources
        self._trace_available = trace_available
        self._lexical_only = lexical_only
        self._vector_contributed = vector_contributed
        self._loading = True

        # Open fresh dialog and capture the inner content column
        content_column = self._open_dialog()
        with content_column:
            # Header with mode selector
            self._render_header(turn_id)

            # Loading state
            loading_label = ui.label("Loading commentary...").style(
                f"font-size: 11px; color: {C_INK60}; font-family: {F_MONO}; padding: 16px;"
            )

        # Fetch existing commentary for the selected mode
        await self._fetch_and_render(self._current_mode, loading_label)

    def _render_header(self, turn_id: str) -> None:
        """Render the panel header with mode selector."""
        with ui.row().classes("w-full items-center").style(f"padding: 12px 16px; border-bottom: 1px solid {C_INK40};"):
            ui.label("BEAST COUNSEL").style(
                f"font-size: 13px; font-weight: 700; font-family: {F_MONO}; color: {C_AMBER}; letter-spacing: 1px;"
            )
            ui.label(f"Turn: {turn_id[:12]}...").style(
                f"font-size: 9px; font-family: {F_MONO}; color: {C_INK60}; margin-left: 8px;"
            )
            ui.space()
            ui.button(icon="close", on_click=self.close).props("dense flat size=xs").style(f"color: {C_INK60};")

        # Mode selector row
        with ui.row().classes("w-full items-center").style(f"padding: 8px 16px; border-bottom: 1px solid {C_INK40};"):
            ui.label("Mode:").style(f"font-size: 10px; color: {C_CREAM}; font-family: {F_MONO};")
            mode_select = (
                ui.select(
                    options=list(BEAST_MODES.keys()),
                    value=self._current_mode,
                    on_change=self._on_mode_change,
                )
                .props("dense outlined size=sm")
                .style(f"margin-left: 8px; font-size: 10px; font-family: {F_MONO};")
            )
            self._mode_select = mode_select

    async def _on_mode_change(self, event: Any) -> None:
        """Handle mode selector change — fetch commentary for the new mode."""
        new_mode = event.value if hasattr(event, "value") else self._mode_select.value
        new_mode = new_mode.strip().lower() if new_mode else "continuity"
        self._current_mode = new_mode
        # Re-render the entire panel for the new mode
        await self._refresh_for_mode(new_mode)

    async def _refresh_for_mode(self, mode: str) -> None:
        """Re-render the panel for a specific mode by fetching fresh data."""
        content_column = self._open_dialog()
        with content_column:
            self._render_header(self._current_turn_id)
            loading_label = ui.label("Loading commentary...").style(
                f"font-size: 11px; color: {C_INK60}; font-family: {F_MONO}; padding: 16px;"
            )

        await self._fetch_and_render(mode, loading_label)

    async def _fetch_and_render(self, mode: str, loading_label: Any) -> None:
        """Fetch commentary for a mode and render the appropriate state."""
        # Fetch existing commentary for this turn + mode
        try:
            result = await self._api_client.get_beast_commentary(self._current_turn_id, mode=mode)
        except Exception as exc:
            log.error("beast_counsel_fetch_failed: %s", exc)
            result = {"status": "error", "error": str(exc), "mode": mode}

        self._loading = False

        # Update content (added inside the scrollable column, not the dialog)
        with self._content_container:
            loading_label.set_text("")  # Clear loading
            loading_label.visible = False

            if result.get("status") == "available":
                self._render_commentary(result)
            elif result.get("status") == "not_available":
                self._render_no_commentary(
                    self._current_turn_id,
                    self._session_id,
                    self._api_client,
                    mode=mode,
                    question_text=self._question_text,
                    answer_text=self._answer_text,
                    sources=self._sources,
                    trace_available=self._trace_available,
                    lexical_only=self._lexical_only,
                    vector_contributed=self._vector_contributed,
                )
            elif result.get("status") == "not_wired":
                self._render_not_wired()
            elif result.get("status") == "unavailable":
                self._render_unavailable(result)
            else:
                self._render_error(result)

    def _render_commentary(self, data: dict[str, Any]) -> None:
        """Render a full Beast commentary result."""
        mode = data.get("mode", "unknown")
        summary = data.get("summary", "")
        critique = data.get("critique", "")
        continuity = data.get("continuity_notes", "")
        risk = data.get("risk_notes", "")
        retrieval = data.get("retrieval_notes", "")
        source_notes = data.get("source_notes", "")
        actions = data.get("suggested_actions", [])
        wiki_links = data.get("suggested_wiki_links", [])
        artifacts = data.get("suggested_artifacts", [])
        created_at = data.get("created_at", "")

        # Mode badge
        with ui.row().classes("w-full items-center").style("padding: 8px 16px;"):
            ui.label(f"Mode: {mode.upper()}").style(
                f"font-size: 10px; font-weight: 700; font-family: {F_MONO}; "
                f"color: {C_OK_FG}; letter-spacing: 0.5px; "
                f"background: {C_RAISED}; padding: 2px 8px; border-radius: {R_SM};"
            )
            if created_at:
                ui.label(created_at[:19]).style(
                    f"font-size: 9px; color: {C_INK60}; font-family: {F_MONO}; margin-left: 8px;"
                )

        # Summary
        if summary:
            self._render_section("Assessment", summary, C_CREAM)

        # Critique
        if critique:
            self._render_section("Critique", critique, C_AMBER)

        # Continuity
        if continuity:
            self._render_section("Continuity", continuity, C_CREAM)

        # Risk
        if risk:
            self._render_section("Risk", risk, C_ERR_FG)

        # Retrieval/Source notes
        if retrieval:
            self._render_section("Retrieval Notes", retrieval, C_INK60)
        if source_notes:
            self._render_section("Source Notes", source_notes, C_INK60)

        # Suggested actions
        if actions:
            self._render_section_label("Suggested Actions")
            for act in actions:
                with ui.row().classes("w-full").style("padding: 4px 16px;"):
                    action_text = act.get("action", "")
                    target = act.get("target", "")
                    if target:
                        action_text += f" → {target}"
                    ui.label(action_text).style(
                        f"font-size: 11px; color: {C_CREAM}; font-family: {F_SANS}; "
                        f"max-width: 300px; word-wrap: break-word;"
                    )
                with ui.row().classes("w-full").style("padding: 0 16px 4px 16px;"):
                    ui.label("advisory only — requires DEFINER approval").style(
                        f"font-size: 8px; color: {C_WARN_FG}; font-family: {F_MONO}; font-style: italic;"
                    )

        # Suggested wiki links
        if wiki_links:
            self._render_section_label("Suggested Wiki Links")
            for link in wiki_links:
                with ui.row().classes("w-full").style("padding: 2px 16px;"):
                    ui.label(f"  {link}").style(f"font-size: 10px; color: {C_AMBER}; font-family: {F_MONO};")

        # Suggested artifacts
        if artifacts:
            self._render_section_label("Suggested Artifacts")
            for art in artifacts:
                with ui.row().classes("w-full").style("padding: 2px 16px;"):
                    ui.label(f"  {art}").style(f"font-size: 10px; color: {C_AMBER}; font-family: {F_MONO};")

    def _render_no_commentary(
        self,
        turn_id: str,
        session_id: str,
        api_client: Any,
        **kwargs: Any,
    ) -> None:
        """Render the 'no commentary yet for this mode' state with a Run button."""
        mode = kwargs.get("mode", self._current_mode)
        with ui.column().classes("w-full").style("padding: 16px;"):
            ui.label(f"No Beast commentary yet for this turn (mode: {mode}).").style(
                f"font-size: 12px; color: {C_INK60}; font-family: {F_SANS};"
            )
            ui.label("Run Beast Counsel to generate advisory commentary.").style(
                f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;"
            )

            # Run button — uses the currently selected mode
            async def _run_counsel() -> None:
                selected_mode = mode
                result = await api_client.run_beast_commentary(
                    turn_id, session_id=session_id, mode=selected_mode, **kwargs
                )
                # Re-render with result
                content_column = self._open_dialog()
                with content_column:
                    self._render_header(turn_id)

                    if result.get("status") == "available":
                        self._render_commentary(result)
                    elif result.get("status") == "not_wired":
                        self._render_not_wired()
                    elif result.get("status") == "error":
                        self._render_error(result)
                    else:
                        self._render_error(result)

            ui.button(f"Run Beast Counsel ({mode})", on_click=_run_counsel).props("dense unelevated size=sm").style(
                f"margin-top: 12px; color: {C_CREAM}; background: {C_AMBER}; "
                f"font-size: 10px; font-family: {F_MONO}; font-weight: 600;"
            )

    def _render_not_wired(self) -> None:
        """Render the 'not wired' state — no model provider configured."""
        with ui.column().classes("w-full").style("padding: 16px;"):
            ui.label("BEAST NOT WIRED").style(
                f"font-size: 12px; font-weight: 700; font-family: {F_MONO}; "
                f"color: {C_DOGFOOD_BARE}; letter-spacing: 0.5px;"
            )
            ui.label(
                "Beast commentary requires a configured model provider. "
                "No model is currently available for the Beast slot."
            ).style(f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;")

    def _render_unavailable(self, data: dict[str, Any]) -> None:
        """Render the 'unavailable' state — persistence or backend not available."""
        reason = data.get("error", "")
        persistence = data.get("persistence", "")
        msg = "Beast commentary is currently unavailable."
        if persistence == "not_available":
            msg = "Artifact store not available — cannot persist commentary."
        if reason:
            msg += f" ({reason})"

        with ui.column().classes("w-full").style("padding: 16px;"):
            ui.label("BEAST UNAVAILABLE").style(
                f"font-size: 12px; font-weight: 700; font-family: {F_MONO}; color: {C_WARN_FG}; letter-spacing: 0.5px;"
            )
            ui.label(msg).style(f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;")

    def _render_error(self, data: dict[str, Any]) -> None:
        """Render the 'error' state — generation or retrieval failed."""
        error_msg = data.get("error", "Unknown error")
        with ui.column().classes("w-full").style("padding: 16px;"):
            ui.label("BEAST ERROR").style(
                f"font-size: 12px; font-weight: 700; font-family: {F_MONO}; color: {C_ERR_FG}; letter-spacing: 0.5px;"
            )
            ui.label(f"Commentary generation failed: {error_msg}").style(
                f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;"
            )

    def _render_section(self, title: str, content: str, color: str) -> None:
        """Render a titled content section."""
        self._render_section_label(title)
        with ui.row().classes("w-full").style("padding: 4px 16px 8px 16px;"):
            ui.label(content).style(
                f"font-size: 11px; color: {color}; font-family: {F_SANS}; "
                f"line-height: 1.5; max-width: 380px; word-wrap: break-word;"
            )

    def _render_section_label(self, text: str) -> None:
        """Render a section label."""
        with ui.row().classes("w-full").style("padding: 8px 16px 2px 16px; margin-top: 4px;"):
            ui.label(text.upper()).style(
                f"font-size: 9px; font-weight: 700; font-family: {F_MONO}; color: {C_INK60}; letter-spacing: 0.5px;"
            )

    def close(self) -> None:
        """Close the Beast Counsel dialog."""
        if self._drawer is not None:
            try:
                self._drawer.close()
            except Exception:
                pass
            self._drawer = None
