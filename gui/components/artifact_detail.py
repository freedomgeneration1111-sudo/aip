"""Artifact Detail — renders the detail view for a selected artifact.

Shows:
  - Content preview (scrollable, truncated)
  - Metadata (type, domain, project, model, session)
  - State badge
  - Source provenance list
  - Review history (ledger entries)
  - ECS transition history
  - Crosslink panel (reuses link_panel component)
  - Export eligibility indicator

Empty/honest states:
  - No review history
  - No sources
  - No linked objects
  - Backend unavailable

Import boundary: imports only gui.* (no aip.* imports).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from nicegui import ui

from gui.components.artifact_review_panel import render_artifact_review_panel
from gui.components.artifact_state_badge import render_artifact_state_badge
from gui.components.link_panel import render_link_panel
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
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.artifact_detail")


def render_artifact_detail(
    artifact_id: str,
    api_client: Any,
    *,
    on_action_complete: Callable[[], None] | None = None,
) -> ui.column:
    """Render the artifact detail panel.

    Parameters:
        artifact_id: The artifact ID to display
        api_client: AipApiClient instance
        on_action_complete: Callback when review/export action completes (for list refresh)

    Returns:
        The container column
    """
    container = (
        ui.column()
        .classes("w-full")
        .style(f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; border-radius:{R_MD}; overflow:hidden;")
    )

    state = {
        "artifact_id": artifact_id,
        "loading": True,
        "error": None,
        "data": None,
    }

    with container:
        # Header
        with ui.row().classes("w-full items-center").style(f"padding:12px 20px; border-bottom:0.5px solid {C_INK40};"):
            ui.label("ARTIFACT DETAIL").style(
                f"font-size:11px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase; font-family:{F_MONO};"
            )
            ui.space()
            ui.button(
                "↻", on_click=lambda: asyncio.ensure_future(_load(state, api_client, container, on_action_complete))
            ).props("flat dense unelevated size=xs").style(f"color:{C_INK60}; font-size:14px; font-family:{F_MONO};")

        # Content area (populated after load) — expanded height for readability
        content_area = ui.column().classes("w-full").style("max-height:calc(100vh - 160px); overflow-y:auto;")
        state["_content_area"] = content_area

    # Initial load
    asyncio.ensure_future(_load(state, api_client, container, on_action_complete))

    return container


async def _load(
    state: dict,
    api_client: Any,
    container: ui.column,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Load artifact detail from the API."""
    content_area = state.get("_content_area")
    if content_area is None:
        return

    state["loading"] = True
    state["error"] = None

    try:
        data = await api_client.get_artifact_detail(state["artifact_id"])
        state["data"] = data
    except Exception as exc:
        log.error("artifact_detail_load_failed: %s", exc)
        state["error"] = str(exc)
        state["data"] = None

    state["loading"] = False
    _render_content(content_area, state, api_client, container, on_action_complete)


def _render_content(
    content_area: ui.column,
    state: dict,
    api_client: Any,
    container: ui.column,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Render the artifact detail content."""
    content_area.clear()

    with content_area:
        # Loading state
        if state.get("loading"):
            with ui.row().classes("w-full items-center justify-center").style("padding:32px;"):
                ui.label("Loading artifact...").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};")
            return

        # Error state
        if state.get("error"):
            with ui.column().classes("w-full").style("padding:16px;"):
                ui.label("Artifact detail unavailable").style(
                    f"font-size:12px; color:{C_CREAM}; font-family:{F_SANS}; font-weight:600;"
                )
                ui.label(state["error"]).style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; margin-top:4px;"
                )
            return

        data = state.get("data")
        if data is None:
            ui.label("No data available").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO}; padding:16px;")
            return

        artifact_id = data.get("artifact_id", "?")
        title = data.get("title", artifact_id)
        ecs_state = data.get("ecs_state", "UNKNOWN")
        has_needs_revision = data.get("has_needs_revision", False)
        has_export = data.get("has_export", False)
        artifact_type = data.get("artifact_type", "")
        content = data.get("content", "")
        domain = data.get("domain", "")
        project = data.get("project", "")
        prompt = data.get("prompt", "")
        model_slot = data.get("model_slot", "")
        model_name = data.get("model_name", "")
        source_count = data.get("source_count", 0)
        export_eligible = data.get("export_eligible", False)
        export_requires_force = data.get("export_requires_force", False)

        # ── Title + State Badge ────────────────────────────────
        with (
            ui.row()
            .classes("w-full items-center")
            .style(f"padding:16px 20px; border-bottom:0.5px solid {C_INK40};")
        ):
            render_artifact_state_badge(
                ecs_state,
                has_needs_revision=has_needs_revision,
                has_export=has_export,
            )
            with ui.column().style("margin-left:12px; flex:1; min-width:0;"):
                ui.label(title).style(
                    f"font-size:18px; color:{C_CREAM}; font-family:{F_SANS}; "
                    f"font-weight:600; line-height:1.3; word-break:break-word;"
                )
                ui.label(artifact_id).style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; "
                    f"word-break:break-all;"
                )

        # ── Review Action Panel ────────────────────────────────
        render_artifact_review_panel(
            artifact_id=artifact_id,
            ecs_state=ecs_state,
            export_eligible=export_eligible,
            export_requires_force=export_requires_force,
            has_needs_revision=has_needs_revision,
            api_client=api_client,
            on_action_complete=lambda: asyncio.ensure_future(
                _on_action_complete(state, api_client, container, on_action_complete)
            ),
        )

        # ── Content Preview ────────────────────────────────────
        with ui.column().classes("w-full").style(f"padding:16px 20px; border-bottom:0.5px solid {C_INK40};"):
            _render_section_label("CONTENT PREVIEW")
            if content:
                # Show full content — no truncation, scrollable
                ui.label(content).style(
                    f"font-size:13px; color:{C_CREAM}; font-family:{F_MONO}; "
                    f"white-space:pre-wrap; word-break:break-word; "
                    f"max-height:60vh; overflow-y:auto; "
                    f"background:{C_GROUND}; border-radius:{R_MD}; padding:16px; "
                    f"line-height:1.6; flex:1;"
                )
            else:
                ui.label("(empty content)").style(f"font-size:11px; color:{C_INK60}; font-family:{F_MONO};")

        # ── Metadata ──────────────────────────────────────────
        with ui.column().classes("w-full").style(f"padding:16px 20px; border-bottom:0.5px solid {C_INK40};"):
            _render_section_label("METADATA")
            meta_rows = [
                ("Type", artifact_type),
                ("Domain", domain),
                ("Project", project),
                ("Model Slot", model_slot),
                ("Model", model_name),
                ("Sources", str(source_count)),
                ("Export Eligible", "Yes" if export_eligible else "No"),
            ]
            if prompt:
                meta_rows.append(("Prompt", prompt[:120]))

            for label, value in meta_rows:
                if value:
                    with ui.row().classes("items-center").style("margin-top:4px;"):
                        ui.label(f"{label}:").style(
                            f"font-size:11px; color:{C_INK60}; font-family:{F_MONO}; min-width:100px; font-weight:600;"
                        )
                        ui.label(value).style(
                            f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO}; "
                            f"word-break:break-all;"
                        )

        # ── Sources ───────────────────────────────────────────
        with ui.column().classes("w-full").style(f"padding:16px 20px; border-bottom:0.5px solid {C_INK40};"):
            _render_section_label("SOURCES")
            source_ids = data.get("source_ids", [])
            if source_ids:
                for sid in source_ids[:10]:
                    display_sid = sid[:40] + "..." if len(sid) > 40 else sid
                    ui.label(f"  {display_sid}").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; margin-top:4px; word-break:break-all;"
                    )
                if len(source_ids) > 10:
                    ui.label(f"  + {len(source_ids) - 10} more sources").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                    )
            else:
                ui.label("No source links recorded").style(f"font-size:11px; color:{C_INK60}; font-family:{F_MONO};")

        # ── Review History ────────────────────────────────────
        review_notes = data.get("review_notes", [])
        transition_history = data.get("transition_history", [])
        _render_review_history(review_notes, transition_history)

        # ── Force-Export Events ───────────────────────────────
        force_export_events = data.get("force_export_events", [])
        if force_export_events:
            with ui.column().classes("w-full").style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"):
                _render_section_label("FORCE-EXPORT AUDIT")
                for fe in force_export_events[:5]:
                    reason = fe.get("metadata", {}).get("reason", "(no reason)")
                    ts = fe.get("timestamp", "")[:16]
                    with ui.row().classes("items-center").style("margin-top:2px;"):
                        ui.label("SOVEREIGN OVERRIDE").style(
                            f"font-size:8px; font-weight:700; color:{C_ERR_FG}; font-family:{F_MONO};"
                        )
                        ui.label(f"{ts} — {reason[:80]}").style(
                            f"font-size:8px; color:{C_MUTED}; font-family:{F_MONO}; margin-left:4px;"
                        )

        # ── Crosslinks ───────────────────────────────────────
        with ui.column().classes("w-full").style("padding:0 0 4px 0;"):
            render_link_panel(
                object_type="artifact",
                object_id=artifact_id,
                api_client=api_client,
                show_create=True,
                on_link_changed=lambda: None,
            )


async def _on_action_complete(
    state: dict,
    api_client: Any,
    container: ui.column,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Handle action completion — reload detail and notify parent."""
    await _load(state, api_client, container, on_action_complete)
    if on_action_complete:
        on_action_complete()


def _render_section_label(text: str) -> None:
    """Render a section label."""
    ui.label(text).style(
        f"font-size:10px; font-weight:700; font-family:{F_MONO}; "
        f"color:{C_INK60}; letter-spacing:0.5px; margin-bottom:8px; "
        f"text-transform:uppercase;"
    )


def _render_review_history(
    review_notes: list[dict],
    transition_history: list[dict],
) -> None:
    """Render review history section."""
    with ui.column().classes("w-full").style(f"padding:16px 20px; border-bottom:0.5px solid {C_INK40};"):
        _render_section_label("REVIEW HISTORY")

        if not review_notes and not transition_history:
            ui.label("No review history recorded").style(f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};")
            return

        # ECS transitions
        if transition_history:
            for t in transition_history[:8]:
                from_s = t.get("from_state", "?")
                to_s = t.get("to_state", "?")
                actor = t.get("actor", "")
                ts = t.get("timestamp", "")[:16]
                reason = t.get("reason", "")[:60]
                with ui.row().classes("items-center").style("margin-top:2px;"):
                    ui.label(f"{from_s} → {to_s}").style(
                        f"font-size:9px; color:{C_CREAM}; font-family:{F_MONO}; font-weight:600;"
                    )
                    ui.label(f"by {actor}").style(
                        f"font-size:8px; color:{C_INK60}; font-family:{F_MONO}; margin-left:4px;"
                    )
                    if ts:
                        ui.label(ts).style(f"font-size:8px; color:{C_INK60}; font-family:{F_MONO}; margin-left:4px;")
                    if reason:
                        ui.label(f"— {reason}").style(
                            f"font-size:8px; color:{C_MUTED}; font-family:{F_MONO}; margin-left:4px; "
                            f"max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                        )

        # Review notes
        if review_notes:
            for note in review_notes[:8]:
                verdict = note.get("verdict", "")
                detail = note.get("detail", "")[:80]
                actor = note.get("actor", "")
                ts = note.get("timestamp", "")[:16]
                instruction = note.get("instruction", "")

                verdict_color = {
                    "APPROVED": C_OK_FG,
                    "REJECTED": C_ERR_FG,
                    "NEEDS_REVISION": C_WARN_FG,
                }.get(verdict, C_INK60)

                with ui.row().classes("items-center").style("margin-top:2px;"):
                    ui.label(verdict).style(
                        f"font-size:8px; font-weight:700; color:{verdict_color}; font-family:{F_MONO};"
                    )
                    if detail:
                        ui.label(detail).style(
                            f"font-size:8px; color:{C_MUTED}; font-family:{F_MONO}; margin-left:4px; "
                            f"max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                        )
                    if ts:
                        ui.label(ts).style(f"font-size:8px; color:{C_INK60}; font-family:{F_MONO}; margin-left:4px;")
                if instruction:
                    ui.label(f"  Instruction: {instruction[:100]}").style(
                        f"font-size:8px; color:{C_WARN_FG}; font-family:{F_MONO}; "
                        f"font-style:italic; margin-top:1px; "
                        f"max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                    )
