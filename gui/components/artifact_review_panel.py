"""Artifact Review Panel — action buttons for artifact lifecycle.

Provides:
  - Approve button (only for GENERATED/REVIEWED artifacts)
  - Reject button (only for GENERATED/REVIEWED artifacts)
  - Needs Revision button (only for GENERATED/REVIEWED artifacts)
  - Export button (only for APPROVED artifacts)
  - Force Export button (visibly dangerous, requires confirmation)
  - Result feedback (success/error notifications)

All actions are explicit DEFINER actions. No auto-approve, no auto-export.
Force-export is visibly exceptional with red danger styling.

Import boundary: imports only gui.* (no aip.* imports).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_BG,
    C_ERR_FG,
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

log = logging.getLogger("gui.components.artifact_review_panel")


def render_artifact_review_panel(
    artifact_id: str,
    ecs_state: str,
    export_eligible: bool,
    export_requires_force: bool,
    has_needs_revision: bool,
    api_client: Any,
    *,
    on_action_complete: Callable[[], None] | None = None,
) -> ui.column:
    """Render the review action panel for an artifact.

    Parameters:
        artifact_id: The artifact ID
        ecs_state: Current ECS state
        export_eligible: Whether normal export is allowed
        export_requires_force: Whether force-export is needed
        has_needs_revision: Whether artifact has NEEDS_REVISION verdict
        api_client: AipApiClient instance
        on_action_complete: Callback when action completes successfully

    Returns:
        The container column
    """
    container = ui.column().classes("w-full").style(f"padding:16px 20px; border-bottom:0.5px solid {C_INK40};")

    with container:
        # Section header
        with ui.row().classes("w-full items-center"):
            ui.label("REVIEW ACTIONS").style(
                f"font-size:10px; font-weight:700; font-family:{F_MONO}; "
                f"color:{C_INK60}; letter-spacing:0.5px; text-transform:uppercase;"
            )

        # State indicator
        with ui.row().classes("w-full items-center").style("margin-top:8px;"):
            ui.label("State:").style(f"font-size:11px; color:{C_INK60}; font-family:{F_MONO}; font-weight:600;")
            state_color = {
                "GENERATED": C_AMBER,
                "REVIEWED": C_AMBER,
                "APPROVED": C_OK_FG,
                "REJECTED": C_ERR_FG,
                "SUPERSEDED": C_MUTED,
                "FAILED": C_ERR_FG,
            }.get(ecs_state, C_MUTED)
            ui.label(ecs_state).style(
                f"font-size:11px; color:{state_color}; font-family:{F_MONO}; font-weight:700; margin-left:6px;"
            )

            if has_needs_revision:
                ui.label("(has revision request)").style(
                    f"font-size:10px; color:{C_WARN_FG}; font-family:{F_MONO}; font-style:italic; margin-left:8px;"
                )

        # Action buttons
        with ui.row().classes("w-full items-center").style("margin-top:12px; flex-wrap:wrap; gap:8px;"):
            # Approve — only for GENERATED/REVIEWED
            if ecs_state in ("GENERATED", "REVIEWED"):
                ui.button(
                    "Approve",
                    on_click=lambda: asyncio.ensure_future(_do_approve(artifact_id, api_client, on_action_complete)),
                ).props("dense unelevated size=sm").style(
                    f"background:{C_OK_FG}; color:#0E0800; border:0.5px solid {C_OK_FG}; "
                    f"border-radius:{R_SM}; font-size:12px; font-weight:600; "
                    f"font-family:{F_MONO}; padding:6px 14px;"
                )

                # Reject
                ui.button(
                    "Reject",
                    on_click=lambda: asyncio.ensure_future(_do_reject(artifact_id, api_client, on_action_complete)),
                ).props("dense unelevated size=sm").style(
                    f"background:{C_ERR_FG}; color:#0E0800; border:0.5px solid {C_ERR_FG}; "
                    f"border-radius:{R_SM}; font-size:12px; font-weight:600; "
                    f"font-family:{F_MONO}; padding:6px 14px;"
                )

                # Needs Revision
                ui.button(
                    "Needs Revision",
                    on_click=lambda: asyncio.ensure_future(
                        _do_needs_revision(artifact_id, api_client, on_action_complete)
                    ),
                ).props("dense unelevated size=sm").style(
                    f"background:transparent; color:{C_WARN_FG}; border:0.5px solid {C_WARN_FG}; "
                    f"border-radius:{R_SM}; font-size:12px; font-weight:500; "
                    f"font-family:{F_MONO}; padding:6px 14px;"
                )

            # Export — only for APPROVED
            if export_eligible and ecs_state == "APPROVED":
                ui.button(
                    "Export",
                    on_click=lambda: asyncio.ensure_future(_do_export(artifact_id, api_client, on_action_complete)),
                ).props("dense unelevated size=sm").style(
                    f"background:{C_AMBER}; color:#0E0800; border:0.5px solid {C_AMBER}; "
                    f"border-radius:{R_SM}; font-size:12px; font-weight:600; "
                    f"font-family:{F_MONO}; padding:6px 14px;"
                )

            # Force Export — visibly dangerous, available for non-APPROVED states
            if export_requires_force and ecs_state not in ("APPROVED", "SUPERSEDED"):
                ui.button(
                    "Force Export",
                    on_click=lambda: _open_force_export_dialog(artifact_id, ecs_state, api_client, on_action_complete),
                ).props("dense unelevated size=sm").style(
                    f"background:{C_ERR_BG}; color:{C_ERR_FG}; "
                    f"border:1px solid {C_ERR_FG}; "
                    f"border-radius:{R_SM}; font-size:12px; font-weight:700; "
                    f"font-family:{F_MONO}; padding:6px 14px; "
                    f"text-transform:uppercase; letter-spacing:0.5px;"
                )

            # Already approved — informational
            if ecs_state == "APPROVED" and not export_eligible:
                ui.label("Approved — export unavailable (backend may be down)").style(
                    f"font-size:9px; color:{C_WARN_FG}; font-family:{F_MONO};"
                )

            # Terminal states
            if ecs_state in ("SUPERSEDED", "FAILED"):
                ui.label(f"Artifact is in terminal state ({ecs_state})").style(
                    f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO};"
                )

    return container


def _open_force_export_dialog(
    artifact_id: str,
    ecs_state: str,
    api_client: Any,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Open the force-export confirmation dialog."""
    with (
        ui.dialog().classes("") as dialog,
        ui.card().style(
            f"background:{C_SURFACE}; border:1px solid {C_ERR_FG}; border-radius:{R_MD}; min-width:400px; padding:24px;"
        ),
    ):
        # Danger header
        ui.label("SOVEREIGN OVERRIDE").style(
            f"font-size:14px; font-weight:700; color:{C_ERR_FG}; font-family:{F_SANS}; letter-spacing:1px;"
        )
        ui.label(
            f"Force-exporting artifact from {ecs_state} state (not APPROVED). "
            f"This bypass is a sovereign override that will be permanently recorded in the audit trail."
        ).style(f"font-size:11px; color:{C_CREAM}; font-family:{F_SANS}; margin-top:8px; line-height:1.5;")

        # Reason input
        reason_input = (
            ui.input(
                label="Reason for override (required)",
                placeholder="Explain why this artifact must be exported without approval...",
            )
            .props("dense outlined")
            .classes("w-full")
            .style(f"margin-top:16px; font-size:11px; font-family:{F_MONO}; color:{C_CREAM};")
        )

        # Confirmation
        confirm_input = (
            ui.input(label='Type "FORCE" to confirm', placeholder="FORCE")
            .props("dense outlined")
            .classes("w-full")
            .style(f"margin-top:8px; font-size:11px; font-family:{F_MONO}; color:{C_CREAM};")
        )

        # Action buttons
        with ui.row().style("margin-top:16px; gap:8px;"):
            ui.button("Cancel", on_click=dialog.close).props("flat dense").style(
                f"color:{C_MUTED}; font-size:11px; font-family:{F_MONO};"
            )
            ui.button(
                "FORCE EXPORT",
                on_click=lambda: asyncio.ensure_future(
                    _do_force_export(artifact_id, reason_input, confirm_input, dialog, api_client, on_action_complete)
                ),
            ).props("dense unelevated").style(
                f"background:{C_ERR_FG}; color:#0E0800; border:0.5px solid {C_ERR_FG}; "
                f"border-radius:{R_SM}; font-size:11px; font-weight:700; "
                f"font-family:{F_MONO}; padding:6px 14px; "
                f"text-transform:uppercase; letter-spacing:0.5px;"
            )

    dialog.open()


async def _do_approve(
    artifact_id: str,
    api_client: Any,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Execute approve action."""
    try:
        result = await api_client.approve_artifact(artifact_id)
        new_state = result.get("new_state", "?")
        ui.notify(f"Artifact approved → {new_state}", color="positive")
        if on_action_complete:
            on_action_complete()
    except Exception as exc:
        _handle_action_error("approve", artifact_id, exc)


async def _do_reject(
    artifact_id: str,
    api_client: Any,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Execute reject action."""
    try:
        result = await api_client.reject_artifact(artifact_id)
        new_state = result.get("new_state", "?")
        ui.notify(f"Artifact rejected → {new_state}", color="warning")
        if on_action_complete:
            on_action_complete()
    except Exception as exc:
        _handle_action_error("reject", artifact_id, exc)


async def _do_needs_revision(
    artifact_id: str,
    api_client: Any,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Execute needs-revision action."""
    try:
        await api_client.needs_revision_artifact(artifact_id)
        ui.notify("Revision requested — artifact preserved", color="warning")
        if on_action_complete:
            on_action_complete()
    except Exception as exc:
        _handle_action_error("needs-revision", artifact_id, exc)


async def _do_export(
    artifact_id: str,
    api_client: Any,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Execute export action."""
    try:
        result = await api_client.export_artifact(artifact_id)
        exported_at = result.get("exported_at", "")
        ui.notify(f"Artifact exported at {exported_at[:16]}", color="positive")
        if on_action_complete:
            on_action_complete()
    except Exception as exc:
        _handle_action_error("export", artifact_id, exc)


async def _do_force_export(
    artifact_id: str,
    reason_input: ui.input,
    confirm_input: ui.input,
    dialog: Any,
    api_client: Any,
    on_action_complete: Callable[[], None] | None,
) -> None:
    """Execute force-export action after validation."""
    reason = reason_input.value or ""
    confirm = confirm_input.value or ""

    if not reason.strip():
        ui.notify("Reason is required for force-export", color="negative")
        return

    if confirm.strip().upper() != "FORCE":
        ui.notify('Type "FORCE" to confirm the override', color="negative")
        return

    try:
        result = await api_client.force_export_artifact(artifact_id, reason=reason)
        audit = result.get("audit_recorded", False)
        ui.notify(
            f"Force-export completed — audit recorded: {audit}",
            color="warning",
        )
        dialog.close()
        if on_action_complete:
            on_action_complete()
    except Exception as exc:
        _handle_action_error("force-export", artifact_id, exc)


def _handle_action_error(action: str, artifact_id: str, exc: Exception) -> None:
    """Handle an action error with user notification."""
    error_msg = str(exc)
    # Try to extract meaningful error from httpx response
    if hasattr(exc, "response") and hasattr(exc.response, "text"):
        try:
            detail = exc.response.json().get("detail", error_msg)
            error_msg = detail
        except Exception:
            pass

    log.error("artifact_action_%s_failed: %s — %s", action, artifact_id, exc)
    ui.notify(f"{action} failed: {error_msg[:100]}", color="negative")
