"""Model Council Panel — multi-model comparison advisory report.

The Model Council compares multiple model outputs for a prompt/turn/context,
producing a structured advisory synthesis of convergence, disagreements,
risks, and recommended decision.

Reports are ADVISORY ONLY. They require DEFINER review before canonical use.
No auto-approve, no auto-export, no wiki mutation, no config changes.

Import boundary: this module imports ONLY from gui.* (theme, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_SM,
)

log = logging.getLogger("gui.components.model_council_panel")

# Default text-generation slots to pre-select if none provided
_DEFAULT_SELECTED_SLOTS = ["synthesis", "evaluation", "beast"]

# Dialog container width — single source of truth for all Model Council dialogs.
# Was previously a right-side drawer (width:480px). Moved to centered modal in
# this cycle so it doesn't compete with main content for horizontal space.
_DIALOG_STYLE = (
    f"width:90vw; max-width:1000px; background:{C_GROUND}; "
    f"border:0.5px solid {C_INK40}; border-radius:{R_SM}; padding:0;"
)


class ModelCouncilPanel:
    """Model Council panel — advisory multi-model comparison report.

    Usage:
        panel = ModelCouncilPanel()
        # On answer card action:
        panel.show_council(api_client, turn_data)
    """

    def __init__(self) -> None:
        # _drawer holds the ui.dialog; _content_container holds the inner
        # scrollable column. Content rendering methods must add to
        # _content_container (not _drawer) so new children appear inside the
        # scrollable region rather than as siblings of it.
        self._drawer: Any = None
        self._content_container: Any = None
        self._loading: bool = False
        self._last_report: dict[str, Any] | None = None
        self._available_slots: list[dict[str, Any]] = []
        self._selected_slots: list[str] = []
        self._slots_loaded: bool = False
        self._slots_sufficient: bool = False

    def _open_dialog(self) -> Any:
        """Open a fresh centered dialog and return the inner content column.

        Replaces the previous right-drawer surface. The dialog itself is
        assigned to ``self._drawer`` and its inner scrollable column to
        ``self._content_container`` so subsequent render methods can append
        children inside the scrollable region.
        """
        self.close()
        dialog = (
            ui.dialog()
            .props("persistent=false; maximized=false")
            .style(_DIALOG_STYLE)
        )
        self._drawer = dialog
        content_column = (
            ui.column()
            .classes("w-full")
            .style("max-height:85vh; overflow-y:auto;")
        )
        self._content_container = content_column
        try:
            dialog.open()
        except Exception as exc:
            log.debug("model_council_dialog_open_failed: %s", exc)
        return content_column

    async def show_council(
        self,
        api_client: Any,
        *,
        prompt: str = "",
        turn_id: str = "",
        session_id: str = "",
        existing_answer: str = "",
        sources: list[dict] | None = None,
        selected_model_slots: list[str] | None = None,
    ) -> None:
        """Open Model Council panel and optionally run a comparison.

        Shows the Model Council report for the given prompt/turn. If no
        comparison has been run yet, offers a Run button with slot selection.
        """
        self._loading = False
        self._last_report = None
        self._selected_slots = list(selected_model_slots or [])
        self._slots_loaded = False
        self._slots_sufficient = False
        self._available_slots = []

        # Open fresh dialog and capture the inner content column
        content_column = self._open_dialog()
        with content_column:
            # Header
            self._render_header()

        # Load slots and show initial state
        await self._load_slots_and_render(
            api_client=api_client,
            prompt=prompt,
            turn_id=turn_id,
            session_id=session_id,
            existing_answer=existing_answer,
            sources=sources or [],
        )

    async def _load_slots_and_render(
        self,
        api_client: Any,
        prompt: str,
        turn_id: str,
        session_id: str,
        existing_answer: str,
        sources: list[dict],
    ) -> None:
        """Load available text-generation slots and render the initial state."""
        try:
            slots_data = await api_client.list_text_generation_slots()
            self._available_slots = slots_data.get("slots", [])
            self._slots_sufficient = slots_data.get("sufficient_for_council", False)
            self._slots_loaded = True
        except Exception as exc:
            log.error("model_council_slot_load_failed: %s", exc)
            self._available_slots = []
            self._slots_sufficient = False
            self._slots_loaded = False

        # If no slots were pre-selected, use defaults that are actually available
        if not self._selected_slots and self._available_slots:
            available_names = [s.get("slot_name", "") for s in self._available_slots]
            self._selected_slots = [s for s in _DEFAULT_SELECTED_SLOTS if s in available_names]
            # If still empty, select all available
            if not self._selected_slots:
                self._selected_slots = available_names

        # Show initial state — offer slot selector and run button
        self._render_initial_state(
            api_client=api_client,
            prompt=prompt,
            turn_id=turn_id,
            session_id=session_id,
            existing_answer=existing_answer,
            sources=sources,
        )

    def _render_header(self) -> None:
        """Render the panel header."""
        with ui.row().classes("w-full items-center").style(f"padding: 12px 16px; border-bottom: 1px solid {C_INK40};"):
            ui.label("MODEL COUNCIL").style(
                f"font-size: 13px; font-weight: 700; font-family: {F_MONO}; color: {C_AMBER}; letter-spacing: 1px;"
            )
            ui.space()
            ui.button(icon="close", on_click=self.close).props("dense flat size=xs").style(f"color: {C_INK60};")

        # Advisory label
        with ui.row().classes("w-full items-center").style(f"padding: 4px 16px; border-bottom: 1px solid {C_INK40};"):
            ui.label("ADVISORY ONLY — requires DEFINER review before canonical use").style(
                f"font-size: 9px; font-weight: 600; font-family: {F_MONO}; color: {C_WARN_FG}; letter-spacing: 0.3px;"
            )

    def _render_initial_state(
        self,
        api_client: Any,
        prompt: str,
        turn_id: str,
        session_id: str,
        existing_answer: str,
        sources: list[dict],
    ) -> None:
        """Render initial state with slot selector and run button."""
        with self._content_container:
            if not prompt and not existing_answer:
                ui.label("No prompt or answer available for Model Council.").style(
                    f"font-size: 12px; color: {C_INK60}; font-family: {F_SANS};"
                )
                return

            ui.label("Compare multiple model perspectives on this prompt.").style(
                f"font-size: 12px; color: {C_CREAM}; font-family: {F_SANS};"
            )
            ui.label(
                "The Model Council runs the same prompt through multiple configured "
                "model slots and synthesizes the results into a structured advisory report."
            ).style(f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;")

            # Slot selector section
            self._render_slot_selector()

            # Run button (disabled if insufficient)
            if not self._slots_loaded:
                ui.label("Could not load model slots — will use backend defaults.").style(
                    f"font-size: 10px; color: {C_WARN_FG}; font-family: {F_MONO}; margin-top: 8px;"
                )

                # Still allow running with defaults
                async def _run_council_default() -> None:
                    await self._run_comparison(
                        api_client=api_client,
                        prompt=prompt,
                        turn_id=turn_id,
                        session_id=session_id,
                        existing_answer=existing_answer,
                        sources=sources,
                        selected_model_slots=[],
                    )

                ui.button("Run Model Council (defaults)", on_click=_run_council_default).props(
                    "dense unelevated size=sm"
                ).style(
                    f"margin-top: 8px; color: {C_CREAM}; background: {C_AMBER}; "
                    f"font-size: 10px; font-family: {F_MONO}; font-weight: 600;"
                )
            elif not self._slots_sufficient:
                self._render_insufficient_models_inline()
            else:

                async def _run_council() -> None:
                    await self._run_comparison(
                        api_client=api_client,
                        prompt=prompt,
                        turn_id=turn_id,
                        session_id=session_id,
                        existing_answer=existing_answer,
                        sources=sources,
                        selected_model_slots=self._selected_slots,
                    )

                selected_count = len(self._selected_slots)
                btn_label = f"Run Model Council ({selected_count} slot{'s' if selected_count != 1 else ''})"

                ui.button(btn_label, on_click=_run_council).props("dense unelevated size=sm").style(
                    f"margin-top: 12px; color: {C_CREAM}; background: {C_AMBER}; "
                    f"font-size: 10px; font-family: {F_MONO}; font-weight: 600;"
                ).bind_enabled_from(self, "_selected_slots", backward=lambda s: len(s) >= 2)

    def _render_slot_selector(self) -> None:
        """Render the model slot selector with checkboxes."""
        self._render_section_label("Select Model Slots")

        if not self._available_slots:
            ui.label("No text-generation slots available.").style(
                f"font-size: 11px; color: {C_WARN_FG}; font-family: {F_SANS}; padding: 4px 0;"
            )
            return

        ui.label("Select at least 2 text-generation slots for comparison:").style(
            f"font-size: 10px; color: {C_INK60}; font-family: {F_SANS}; padding: 2px 0; margin-bottom: 4px;"
        )

        for slot_info in self._available_slots:
            slot_name = slot_info.get("slot_name", "")
            model_display = slot_info.get("model", "")
            provider = slot_info.get("provider", "")
            has_real_model = slot_info.get("has_real_model", False)

            # Determine if this slot should be checked by default
            is_checked = slot_name in self._selected_slots

            with ui.row().classes("w-full items-center").style("padding: 2px 0;"):
                (
                    ui.checkbox(
                        value=is_checked,
                        on_change=lambda checked, sn=slot_name: self._toggle_slot(sn, checked.value),
                    )
                    .props("dense size=xs")
                    .style(f"color: {C_AMBER};")
                )

                ui.label(f"{slot_name}").style(
                    f"font-size: 10px; font-weight: 700; font-family: {F_MONO}; color: {C_CREAM};"
                )

                # Model display
                if has_real_model:
                    ui.label(f"({model_display})").style(
                        f"font-size: 9px; color: {C_INK60}; font-family: {F_MONO}; margin-left: 4px;"
                    )
                else:
                    ui.label("(unconfigured)").style(
                        f"font-size: 9px; color: {C_WARN_FG}; font-family: {F_MONO}; "
                        f"margin-left: 4px; font-style: italic;"
                    )

                # Provider badge
                ui.label(f"[{provider}]").style(
                    f"font-size: 8px; color: {C_INK60}; font-family: {F_MONO}; margin-left: 4px;"
                )

        # Selection count indicator
        count_label = ui.label().style(f"font-size: 9px; color: {C_INK60}; font-family: {F_MONO}; margin-top: 4px;")
        count_label.text = f"{len(self._selected_slots)} selected — minimum 2 required"

    def _toggle_slot(self, slot_name: str, checked: bool) -> None:
        """Toggle a slot in the selected list."""
        if checked and slot_name not in self._selected_slots:
            self._selected_slots.append(slot_name)
        elif not checked and slot_name in self._selected_slots:
            self._selected_slots.remove(slot_name)
        log.debug("slot_toggled slot=%s checked=%s selected=%s", slot_name, checked, self._selected_slots)

    def _render_insufficient_models_inline(self) -> None:
        """Render insufficient models notice inline in the initial state."""
        with (
            ui.column()
            .classes("w-full")
            .style(
                f"padding: 8px; margin-top: 8px; "
                f"background: {C_RAISED}; border: 0.5px solid {C_INK40}; "
                f"border-radius: {R_SM};"
            )
        ):
            ui.label("INSUFFICIENT MODELS").style(
                f"font-size: 10px; font-weight: 700; font-family: {F_MONO}; color: {C_WARN_FG}; letter-spacing: 0.5px;"
            )
            ui.label(
                "Model Council requires at least two configured text-generation "
                "model slots to produce a comparison report. The embedding slot "
                "is excluded from text generation."
            ).style(f"font-size: 10px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;")
            if self._available_slots:
                slot_names = [s.get("slot_name", "?") for s in self._available_slots]
                ui.label(f"Available: {', '.join(slot_names)}").style(
                    f"font-size: 9px; color: {C_INK60}; font-family: {F_MONO}; margin-top: 2px;"
                )

    async def _run_comparison(
        self,
        api_client: Any,
        prompt: str,
        turn_id: str,
        session_id: str,
        existing_answer: str,
        sources: list[dict],
        selected_model_slots: list[str],
    ) -> None:
        """Run Model Council comparison and render results."""
        self._loading = True

        # Close and reopen with loading state
        content_column = self._open_dialog()
        with content_column:
            self._render_header()
            ui.label("Running Model Council comparison...").style(
                f"font-size: 11px; color: {C_INK60}; font-family: {F_MONO}; padding: 16px;"
            )

        try:
            result = await api_client.run_model_council(
                prompt=prompt or existing_answer[:500],
                turn_id=turn_id,
                session_id=session_id,
                existing_answer=existing_answer,
                sources=sources,
                selected_model_slots=selected_model_slots,
            )
        except Exception as exc:
            log.error("model_council_run_failed: %s", exc)
            result = {"status": "error", "error": str(exc)}

        self._loading = False
        self._last_report = result

        # Re-render with results
        content_column = self._open_dialog()
        with content_column:
            self._render_header()
            self._render_report(result, api_client)

    def _render_report(self, data: dict[str, Any], api_client: Any) -> None:
        """Render a full Model Council report."""
        status = data.get("status", "unknown")

        # Status banner
        status_colors = {
            "completed": C_OK_FG,
            "partial": C_AMBER,
            "insufficient_models": C_WARN_FG,
            "unavailable": C_WARN_FG,
            "error": C_ERR_FG,
        }
        status_color = status_colors.get(status, C_MUTED)

        with ui.row().classes("w-full items-center").style("padding: 8px 16px;"):
            ui.label(f"Status: {status.upper()}").style(
                f"font-size: 10px; font-weight: 700; font-family: {F_MONO}; "
                f"color: {status_color}; letter-spacing: 0.5px; "
                f"background: {C_RAISED}; padding: 2px 8px; border-radius: {R_SM};"
            )
            created_at = data.get("created_at", "")
            if created_at:
                ui.label(created_at[:19]).style(
                    f"font-size: 9px; color: {C_INK60}; font-family: {F_MONO}; margin-left: 8px;"
                )

        # Show selected slots in report header
        selected_models = data.get("selected_models", [])
        if selected_models:
            slot_names = [m.get("model_slot", "") for m in selected_models]
            with ui.row().classes("w-full items-center").style("padding: 2px 16px;"):
                ui.label(f"Slots: {', '.join(slot_names)}").style(
                    f"font-size: 9px; color: {C_INK60}; font-family: {F_MONO}; letter-spacing: 0.3px;"
                )

        # Insufficient models state
        if status == "insufficient_models":
            self._render_insufficient_models(data)
            return

        # Error state
        if status == "error":
            self._render_error(data)
            return

        # Per-model results table
        per_model = data.get("selected_models", [])
        if per_model:
            self._render_per_model_results(per_model)

        # Degraded/failed models
        degraded = data.get("degraded_models", [])
        failed = data.get("failed_models", [])
        if degraded or failed:
            self._render_degraded_failed(degraded, failed)

        # Synthesis sections
        synthesis_status = data.get("synthesis_status", "unknown")
        if synthesis_status == "completed":
            # Phase 1 Fusion: the new headline is ``fusion_answer`` (the
            # Synth-Beast output). Legacy structured fields (convergence,
            # disagreements, etc.) are still rendered below as supporting
            # detail — they're populated from the Judge JSON.
            fusion_answer = data.get("fusion_answer", "")
            if fusion_answer:
                self._render_section("Fusion Synthesis", fusion_answer, C_OK_FG)
            # Legacy structured-analysis fields (best-effort from Judge JSON)
            self._render_section("Convergence", data.get("convergence", ""), C_OK_FG)
            self._render_section("Disagreements", data.get("disagreements", ""), C_AMBER)
            self._render_section("Unique Contributions", data.get("unique_contributions", ""), C_CREAM)
            self._render_section("Risks", data.get("risks", ""), C_ERR_FG)
            # Beast Conclusion is mirrored from fusion_answer in Phase 1,
            # so only render it separately if it differs (legacy fallback)
            beast_conclusion = data.get("beast_conclusion", "")
            if beast_conclusion and beast_conclusion != fusion_answer:
                self._render_section("Beast Conclusion", beast_conclusion, C_CREAM)
            self._render_section("Recommended Decision", data.get("recommended_decision", ""), C_AMBER)
            # Phase 1 Fix B: render the full structured Judge JSON
            # (consensus / contradictions stance table / partial_coverage /
            # unique_insights / blind_spots) plus a collapsible raw-JSON
            # disclosure for audit. Empty dict → nothing rendered.
            self._render_judge_analysis(data.get("judge_analysis", {}))
        elif synthesis_status == "unavailable":
            with ui.column().classes("w-full").style("padding: 8px 16px;"):
                ui.label("SYNTHESIS UNAVAILABLE").style(
                    f"font-size: 10px; font-weight: 700; font-family: {F_MONO}; "
                    f"color: {C_WARN_FG}; letter-spacing: 0.5px;"
                )
                ui.label(
                    "Beast synthesis model is unavailable. Per-model results are available for individual review."
                ).style(f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;")
                # Show beast_conclusion if available (partial synthesis)
                conclusion = data.get("beast_conclusion", "")
                if conclusion:
                    self._render_section("Note", conclusion, C_MUTED)
        elif synthesis_status == "failed":
            with ui.column().classes("w-full").style("padding: 8px 16px;"):
                ui.label("SYNTHESIS FAILED").style(
                    f"font-size: 10px; font-weight: 700; font-family: {F_MONO}; "
                    f"color: {C_ERR_FG}; letter-spacing: 0.5px;"
                )
                ui.label("Beast synthesis call failed. Per-model results are still available.").style(
                    f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;"
                )

        # Advisory labels
        with (
            ui.row()
            .classes("w-full items-center")
            .style(f"padding: 8px 16px; margin-top: 8px; border-top: 1px solid {C_INK40};")
        ):
            ui.label("advisory_only: true  |  requires_DEFINER_approval: true").style(
                f"font-size: 8px; color: {C_WARN_FG}; font-family: {F_MONO}; font-style: italic; letter-spacing: 0.3px;"
            )

        # Save as artifact button
        if data.get("artifact_id"):
            with ui.row().classes("w-full").style("padding: 8px 16px;"):
                ui.label(f"Saved as artifact: {data['artifact_id'][:24]}...").style(
                    f"font-size: 9px; color: {C_OK_FG}; font-family: {F_MONO};"
                )
        elif status in ("completed", "partial"):

            async def _save_artifact() -> None:
                save_result = await api_client.run_model_council(
                    prompt=data.get("prompt", ""),
                    turn_id=data.get("turn_id", ""),
                    session_id=data.get("session_id", ""),
                    existing_answer=data.get("existing_answer", ""),
                    sources=data.get("sources", []),
                    selected_model_slots=[m.get("model_slot", "") for m in data.get("selected_models", [])],
                    save_as_artifact=True,
                )
                if save_result.get("artifact_id"):
                    ui.notify(
                        f"Report saved as artifact: {save_result['artifact_id'][:24]}... — requires DEFINER review",
                        color="positive",
                        timeout=6000,
                    )
                else:
                    ui.notify("Failed to save report as artifact", color="negative")

            ui.button("Save as Artifact", on_click=_save_artifact).props("dense flat size=xs").style(
                f"color: {C_OK_FG}; font-size: 9px; font-family: {F_MONO}; margin: 4px 16px;"
            )

    def _render_per_model_results(self, per_model: list[dict[str, Any]]) -> None:
        """Render per-model comparison results."""
        self._render_section_label("Per-Model Results")

        for pm in per_model:
            model_slot = pm.get("model_slot", "unknown")
            model_id = pm.get("model_id", "")
            pm_status = pm.get("status", "unknown")
            answer = pm.get("answer", "")
            error = pm.get("error", "")
            latency = pm.get("latency_ms")

            status_color = C_OK_FG if pm_status == "completed" else C_ERR_FG

            with (
                ui.column()
                .classes("w-full")
                .style(
                    f"padding: 4px 16px; margin: 2px 0; "
                    f"background: {C_SURFACE}; border: 0.5px solid {C_INK40}; "
                    f"border-radius: {R_SM};"
                )
            ):
                # Model header
                with ui.row().classes("w-full items-center"):
                    ui.label(f"{model_slot}").style(
                        f"font-size: 10px; font-weight: 700; font-family: {F_MONO}; color: {C_AMBER};"
                    )
                    ui.label(f"({model_id})").style(
                        f"font-size: 9px; color: {C_INK60}; font-family: {F_MONO}; margin-left: 4px;"
                    )
                    ui.label(f"[{pm_status.upper()}]").style(
                        f"font-size: 8px; font-weight: 700; color: {status_color}; "
                        f"font-family: {F_MONO}; margin-left: 8px;"
                    )
                    if latency is not None:
                        ui.label(f"{latency}ms").style(
                            f"font-size: 8px; color: {C_INK60}; font-family: {F_MONO}; margin-left: 4px;"
                        )

                # Answer text
                if answer:
                    ui.label(answer[:500]).style(
                        f"font-size: 11px; color: {C_CREAM}; font-family: {F_SANS}; "
                        f"line-height: 1.4; margin-top: 4px; max-width: 420px; "
                        f"word-wrap: break-word;"
                    )
                elif error:
                    ui.label(f"Error: {error[:200]}").style(
                        f"font-size: 10px; color: {C_ERR_FG}; font-family: {F_SANS}; margin-top: 4px;"
                    )

    def _render_degraded_failed(self, degraded: list[str], failed: list[str]) -> None:
        """Render degraded/failed model notifications."""
        with (
            ui.row()
            .classes("w-full items-center")
            .style(f"padding: 4px 16px; background: {C_RAISED}; border-radius: {R_SM}; border: 0.5px solid {C_INK40};")
        ):
            if degraded:
                ui.label(f"Degraded: {', '.join(degraded)}").style(
                    f"font-size: 9px; color: {C_AMBER}; font-family: {F_MONO};"
                )
            if failed:
                ui.label(f"Failed: {', '.join(failed)}").style(
                    f"font-size: 9px; color: {C_ERR_FG}; font-family: {F_MONO}; "
                    f"{'margin-left: 8px;' if degraded else ''}"
                )

    def _render_insufficient_models(self, data: dict[str, Any]) -> None:
        """Render the insufficient_models state."""
        with ui.column().classes("w-full").style("padding: 16px;"):
            ui.label("INSUFFICIENT MODELS").style(
                f"font-size: 12px; font-weight: 700; font-family: {F_MONO}; color: {C_WARN_FG}; letter-spacing: 0.5px;"
            )
            ui.label(
                "Model Council requires at least two configured text-generation "
                "model slots to produce a comparison report. The embedding slot "
                "is excluded from text generation."
            ).style(f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;")
            error_msg = data.get("error", "")
            if error_msg:
                ui.label(error_msg[:300]).style(
                    f"font-size: 10px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px; word-wrap: break-word;"
                )

    def _render_error(self, data: dict[str, Any]) -> None:
        """Render the error state."""
        error_msg = data.get("error", "Unknown error")
        with ui.column().classes("w-full").style("padding: 16px;"):
            ui.label("MODEL COUNCIL ERROR").style(
                f"font-size: 12px; font-weight: 700; font-family: {F_MONO}; color: {C_ERR_FG}; letter-spacing: 0.5px;"
            )
            ui.label(f"Comparison failed: {error_msg[:300]}").style(
                f"font-size: 11px; color: {C_INK60}; font-family: {F_SANS}; margin-top: 4px;"
            )

    def _render_section(self, title: str, content: str, color: str) -> None:
        """Render a titled content section."""
        if not content:
            return
        self._render_section_label(title)
        with ui.row().classes("w-full").style("padding: 4px 16px 8px 16px;"):
            ui.label(content).style(
                f"font-size: 11px; color: {color}; font-family: {F_SANS}; "
                f"line-height: 1.5; max-width: 420px; word-wrap: break-word;"
            )

    def _render_section_label(self, text: str) -> None:
        """Render a section label."""
        with ui.row().classes("w-full").style("padding: 8px 16px 2px 16px; margin-top: 4px;"):
            ui.label(text.upper()).style(
                f"font-size: 9px; font-weight: 700; font-family: {F_MONO}; color: {C_INK60}; letter-spacing: 0.5px;"
            )

    def _render_judge_analysis(self, judge_analysis: dict[str, Any]) -> None:
        """Render the full structured Judge JSON for audit visibility.

        Phase 1 Fix B: previously the rich ``judge_analysis`` dict was
        returned by the backend but never surfaced in the GUI — only the
        flattened legacy strings (``convergence``, ``disagreements``,
        etc.) were rendered, losing the per-model attribution that the
        new schema provides. This method renders:

          - ``analysis.consensus[]`` as a bulleted list
          - ``analysis.contradictions[]`` as a per-topic stance table
            (each row = one topic, with per-model stance cells)
          - ``analysis.partial_coverage[]`` as a per-model-attributed list
          - ``analysis.unique_insights[]`` as a per-model-attributed list
          - ``analysis.blind_spots[]`` as a bulleted list (the gaps NO
            model addressed — the most important field for the human)
          - a collapsible raw-JSON disclosure at the end (``ui.expansion``)
            for full audit

        Empty/missing dict → nothing rendered (no empty sections).
        """
        if not judge_analysis or not isinstance(judge_analysis, dict):
            return

        analysis = judge_analysis.get("analysis")
        if not isinstance(analysis, dict):
            # Judge produced something but no ``analysis`` key — fall back
            # to showing just the raw JSON disclosure so the human still
            # has visibility.
            analysis = {}

        # ── Consensus ──
        consensus = analysis.get("consensus", [])
        if isinstance(consensus, list) and consensus:
            self._render_section_label("Judge · Consensus (all models agree)")
            with ui.column().classes("w-full").style("padding: 2px 16px 8px 24px;"):
                for point in consensus:
                    ui.label(f"• {point}").style(
                        f"font-size: 11px; color: {C_OK_FG}; font-family: {F_SANS}; line-height: 1.5;"
                    )

        # ── Contradictions stance table ──
        contradictions = analysis.get("contradictions", [])
        if isinstance(contradictions, list) and contradictions:
            self._render_section_label("Judge · Contradictions (per-model stances)")
            with ui.column().classes("w-full").style("padding: 2px 16px 8px 16px;"):
                for c in contradictions:
                    if not isinstance(c, dict):
                        continue
                    topic = c.get("topic", "?")
                    stances = c.get("stances", [])
                    with (
                        ui.column()
                        .classes("w-full")
                        .style(
                            f"padding: 6px 10px; margin: 2px 0; "
                            f"border-left: 2px solid {C_AMBER}; background: {C_GROUND};"
                        )
                    ):
                        ui.label(topic).style(
                            f"font-size: 11px; font-weight: 700; color: {C_CREAM}; "
                            f"font-family: {F_SANS}; margin-bottom: 4px;"
                        )
                        if isinstance(stances, list):
                            for s in stances:
                                if not isinstance(s, dict):
                                    continue
                                model = s.get("model", "?")
                                stance = s.get("stance", "?")
                                with ui.row().classes("w-full").style("gap: 6px;"):
                                    ui.label(model).style(
                                        f"font-size: 10px; font-weight: 600; color: {C_AMBER}; "
                                        f"font-family: {F_MONO}; min-width: 120px; max-width: 200px; "
                                        f"word-break: break-all;"
                                    )
                                    ui.label(stance).style(
                                        f"font-size: 10px; color: {C_INK60}; "
                                        f"font-family: {F_SANS}; line-height: 1.4; flex: 1;"
                                    )

        # ── Partial coverage ──
        partial = analysis.get("partial_coverage", [])
        if isinstance(partial, list) and partial:
            self._render_section_label("Judge · Partial Coverage (some models only)")
            with ui.column().classes("w-full").style("padding: 2px 16px 8px 24px;"):
                for p in partial:
                    if not isinstance(p, dict):
                        continue
                    models = p.get("models", [])
                    point = p.get("point", "?")
                    models_str = ", ".join(str(m) for m in models) if isinstance(models, list) else str(models)
                    ui.label(f"• [{models_str}] {point}").style(
                        f"font-size: 11px; color: {C_CREAM}; font-family: {F_SANS}; line-height: 1.5;"
                    )

        # ── Unique insights ──
        unique = analysis.get("unique_insights", [])
        if isinstance(unique, list) and unique:
            self._render_section_label("Judge · Unique Insights (per-model)")
            with ui.column().classes("w-full").style("padding: 2px 16px 8px 24px;"):
                for u in unique:
                    if not isinstance(u, dict):
                        continue
                    model = u.get("model", "?")
                    insight = u.get("insight", "?")
                    ui.label(f"• [{model}] {insight}").style(
                        f"font-size: 11px; color: {C_CREAM}; font-family: {F_SANS}; line-height: 1.5;"
                    )

        # ── Blind spots (the most important field for the human) ──
        blind = analysis.get("blind_spots", [])
        if isinstance(blind, list) and blind:
            self._render_section_label("Judge · Blind Spots (no model addressed)")
            with ui.column().classes("w-full").style("padding: 2px 16px 8px 24px;"):
                for b in blind:
                    ui.label(f"• {b}").style(
                        f"font-size: 11px; color: {C_ERR_FG}; font-family: {F_SANS}; "
                        f"line-height: 1.5; font-style: italic;"
                    )

        # ── Collapsible raw JSON for full audit ──
        try:
            raw_json = json.dumps(judge_analysis, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            raw_json = str(judge_analysis)
        with ui.expansion(
            "Judge Analysis (raw JSON)",
            icon="format_quote",
        ).classes("w-full").style(f"padding: 4px 16px; font-family: {F_MONO};"):
            ui.code(raw_json, language="json").style(
                f"font-size: 10px; font-family: {F_MONO}; "
                f"background: {C_GROUND}; color: {C_INK60};"
            )

    def close(self) -> None:
        """Close the Model Council dialog."""
        if self._drawer is not None:
            try:
                self._drawer.close()
            except Exception as exc:
                log.debug("drawer_close_error: %s", exc)
            self._drawer = None
            self._content_container = None
