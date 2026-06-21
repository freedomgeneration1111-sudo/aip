"""AIP Maintenance Center — Route: /maintenance

UI Cycle 12: Primary workbench for maintenance operations.
Displays actor status, maintenance job controls, recent logs,
and problem panels. All maintenance actions are explicit DEFINER actions.
Honest about unavailable, not_wired, and scheduled_only states.
Never fakes actor health or maintenance results.

Import boundary: imports ONLY from gui.* — never imports from aip.orchestration.
"""

from __future__ import annotations

import asyncio

from nicegui import context, ui

from gui.api_client import get_api_client
from gui.components.actor_status_table import ActorStatusTable
from gui.components.layout import build_left_nav, build_right_rail, build_top_bar
from gui.components.maintenance_jobs import MaintenanceJobs
from gui.components.maintenance_log import MaintenanceLog
from gui.components.maintenance_problem_panel import MaintenanceProblemPanel
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
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
)


@ui.page("/maintenance")
async def maintenance_page():
    """Maintenance Center — actor status, maintenance jobs, logs, and problems."""
    state = get_session_state()
    state.client = context.client
    api = get_api_client()

    # Refresh backend status before rendering layout
    await state.refresh_status_summary()

    build_top_bar(state)
    build_left_nav(state, active_page="/maintenance")
    build_right_rail(state)

    # Page-local mutable state
    maintenance_status: dict = {}
    maintenance_logs: dict = {}
    backend_available: bool = True

    # Instantiate class-based components with callbacks
    actor_table = ActorStatusTable(
        on_run_actor=lambda actor_name: _handle_run_actor(actor_name),
        on_view_logs=lambda actor_name: _handle_view_actor_logs(actor_name),
    )
    maintenance_jobs = MaintenanceJobs(
        on_backfill=lambda: _handle_backfill(),
        on_rebuild_graph=lambda: _handle_rebuild_graph(),
        on_rebuild_codex=lambda: _handle_rebuild_codex(),
        on_retrieval_eval=lambda: _handle_retrieval_eval(),
        on_check_stale=lambda: _handle_check_stale(),
        on_check_contradictions=lambda: _handle_check_contradictions(),
    )
    log_panel = MaintenanceLog()
    problem_panel = MaintenanceProblemPanel()

    # ── Main layout ────────────────────────────────────────────────────
    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:32px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Title row
        with ui.row().classes("w-full items-center").style("gap:12px;"):
            ui.label("Maintenance Center").style(
                f"font-family:{F_SANS}; font-size:28px; font-weight:700; color:{C_CREAM};"
            )
            ui.label("v1").style(
                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; "
                f"border:0.5px solid {C_INK40}; border-radius:4px; padding:1px 6px;"
            )

        # Backend status (mutable)
        backend_label = ui.label("").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_SANS}; margin-top:4px;")

        # ── Problems panel ─────────────────────────────────────────────
        ui.label("PROBLEMS").style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; "
            f"color:{C_ERR_FG}; text-transform:uppercase; font-family:{F_SANS}; "
            f"margin-top:16px;"
        )
        problems_container = ui.column().classes("w-full").style("gap:0;")

        # ── Actor status table ─────────────────────────────────────────
        ui.label("ACTOR STATUS").style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; "
            f"color:{C_AMBER}; text-transform:uppercase; font-family:{F_SANS}; "
            f"margin-top:20px;"
        )
        actors_container = ui.column().classes("w-full").style("gap:0;")

        # ── Retrieval health (moved from right rail) ───────────────────
        ui.label("RETRIEVAL HEALTH").style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; "
            f"color:{C_AMBER}; text-transform:uppercase; font-family:{F_SANS}; "
            f"margin-top:20px;"
        )
        retrieval_container = ui.column().classes("w-full").style("gap:0;")

        # ── Pending gates (moved from right rail) ──────────────────────
        ui.label("PENDING GATES").style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; "
            f"color:{C_AMBER}; text-transform:uppercase; font-family:{F_SANS}; "
            f"margin-top:20px;"
        )
        gates_container = ui.column().classes("w-full").style("gap:0;")

        # ── Warnings (moved from right rail) ───────────────────────────
        ui.label("WARNINGS").style(
            f"font-size:11px; font-weight:600; letter-spacing:1px; "
            f"color:{C_ERR_FG}; text-transform:uppercase; font-family:{F_SANS}; "
            f"margin-top:20px;"
        )
        warnings_container = ui.column().classes("w-full").style("gap:0;")

        # ── Maintenance jobs ───────────────────────────────────────────
        jobs_container = ui.column().classes("w-full").style("margin-top:20px;")

        # ── Backfill progress ──────────────────────────────────────────
        backfill_container = ui.column().classes("w-full").style("margin-top:16px;")

        # ── Recent maintenance log ─────────────────────────────────────
        with (
            ui.expansion("Recent Maintenance Log", value=False)
            .classes("w-full")
            .style(f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; border-radius:{R_MD}; margin-top:20px;")
        ):
            log_container = ui.column().classes("w-full").style("padding:8px; gap:0;")

        # ── Action result notification area ────────────────────────────
        ui.label("").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_SANS}; margin-top:12px; min-height:20px;")

    # ── Data loading functions ─────────────────────────────────────────

    async def _load_all():
        nonlocal maintenance_status, maintenance_logs, backend_available

        try:
            status_task = asyncio.create_task(api.get_maintenance_status())
            logs_task = asyncio.create_task(api.get_maintenance_logs(limit=30))

            maintenance_status = await status_task
            maintenance_logs = await logs_task
            backend_available = True

            # Check if backend returned error indicators
            if maintenance_status.get("warnings") and len(maintenance_status.get("warnings", [])) > 0:
                first_warning = maintenance_status["warnings"][0]
                if "Backend unavailable" in first_warning:
                    backend_available = False

        except Exception as exc:
            backend_available = False
            maintenance_status = {
                "actors": {},
                "backfill": {"state": "unavailable", "running": False, "progress": {}},
                "capabilities": {},
                "warnings": [f"Backend unreachable: {exc}"],
            }
            maintenance_logs = {"logs": [], "available": False, "count": 0}

        _refresh_ui()

    def _refresh_ui():
        # Backend status
        if backend_available:
            backfill = maintenance_status.get("backfill", {})
            backfill_state = backfill.get("state", "unknown")
            running = backfill.get("running", False)
            if running:
                backend_label.text = f"Backend: connected | Backfill: RUNNING ({backfill_state})"
                backend_label.style(f"font-size:11px; color:{C_WARN_FG}; font-family:{F_SANS};")
            else:
                backend_label.text = f"Backend: connected | Backfill: {backfill_state}"
                backend_label.style(f"font-size:11px; color:{C_MUTED}; font-family:{F_SANS};")
        else:
            backend_label.text = "Backend: unreachable"
            backend_label.style(f"font-size:11px; color:{C_ERR_FG}; font-family:{F_SANS};")

        # Problems
        problems_container.clear()
        with problems_container:
            problem_panel.render(maintenance_status)

        # Actor table
        actors_container.clear()
        with actors_container:
            actor_table.render(maintenance_status)

        # Retrieval health (moved from right rail)
        retrieval_container.clear()
        with retrieval_container:
            _render_retrieval_health(state)

        # Pending gates (moved from right rail)
        gates_container.clear()
        with gates_container:
            _render_pending_gates(state)

        # Warnings (moved from right rail)
        warnings_container.clear()
        with warnings_container:
            _render_warnings(state)

        # Jobs
        backfill_running = maintenance_status.get("backfill", {}).get("running", False)
        capabilities = maintenance_status.get("capabilities", {})
        jobs_container.clear()
        with jobs_container:
            maintenance_jobs.render(capabilities, backfill_running=backfill_running)

        # Backfill progress
        backfill_container.clear()
        with backfill_container:
            _render_backfill_progress(maintenance_status.get("backfill", {}))

        # Logs
        log_container.clear()
        with log_container:
            log_panel.render(maintenance_logs)

    def _render_backfill_progress(backfill: dict):
        """Render backfill progress section."""
        if not backfill:
            return

        state = backfill.get("state", "unknown")
        running = backfill.get("running", False)
        progress = backfill.get("progress", {})
        last_result = backfill.get("last_result")

        with ui.row().classes("w-full items-center").style("gap:8px;"):
            ui.label("BACKFILL").style(
                f"font-size:10px; font-weight:600; color:{C_AMBER}; font-family:{F_MONO}; letter-spacing:0.5px;"
            )
            if running:
                ui.label("RUNNING").style(
                    f"font-size:9px; font-weight:600; color:{C_WARN_FG}; font-family:{F_MONO}; "
                    f"border:1px solid {C_WARN_FG}; background:{C_SURFACE}; "
                    f"border-radius:4px; padding:0px 4px;"
                )
                scanned = progress.get("scanned", 0)
                embedded = progress.get("embedded", 0)
                failed = progress.get("failed", 0)
                ui.label(f"Scanned: {scanned} | Embedded: {embedded} | Failed: {failed}").style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                )
            else:
                ui.label(state.upper()).style(
                    f"font-size:9px; font-weight:600; color:{C_MUTED}; font-family:{F_MONO}; "
                    f"border:1px solid {C_INK40}; background:{C_SURFACE}; "
                    f"border-radius:4px; padding:0px 4px;"
                )

            if last_result:
                lr_ok = last_result.get("ok", False)
                lr_embedded = last_result.get("embedded", 0)
                lr_failed = last_result.get("failed", 0)
                result_fg = C_OK_FG if lr_ok else C_ERR_FG
                ui.label(f"Last: {lr_embedded} embedded, {lr_failed} failed").style(
                    f"font-size:9px; color:{result_fg}; font-family:{F_MONO};"
                )

    # ── Action handlers ────────────────────────────────────────────────

    async def _handle_run_actor(actor_name: str):
        """Handle Run button for an actor. Explicit DEFINER action."""
        result = await api.trigger_actor_run(actor_name)
        triggered = result.get("triggered", False)
        error = result.get("error", "")

        if triggered:
            ui.notify(f"{actor_name.upper()} cycle triggered", type="positive")
        elif error:
            if "not initialized" in str(error).lower():
                ui.notify(f"{actor_name.upper()} not initialized: {error}", type="warning")
            else:
                ui.notify(f"{actor_name.upper()} trigger failed: {error}", type="negative")
        else:
            ui.notify(f"{actor_name.upper()} trigger returned unknown state", type="warning")

        await _load_all()

    async def _handle_view_actor_logs(actor_name: str):
        """Handle View Logs button for an actor."""
        result = await api.get_actor_runs(actor_name, limit=10)
        available = result.get("available", False)
        runs = result.get("runs", [])

        if not available:
            ui.notify(f"No event store available for {actor_name.upper()} logs", type="warning")
        elif not runs:
            ui.notify(f"No recent events for {actor_name.upper()}", type="info")
        else:
            # Show logs in a dialog
            with (
                ui.dialog() as dialog,
                ui.card().style(
                    (
                        f"background:{C_SURFACE}; border-radius:{R_MD}; "
                        f"padding:20px; min-width:500px; max-height:400px; overflow-y:auto;"
                    )
                ),
            ):
                ui.label(f"{actor_name.upper()} — Recent Events").style(
                    f"font-size:14px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS}; margin-bottom:8px;"
                )
                for run in runs[:15]:
                    evt_type = run.get("event_type", "")
                    timestamp = run.get("timestamp", "")
                    ts_short = timestamp.split("T")[1][:8] if "T" in timestamp else timestamp[:8]
                    with ui.row().style("gap:8px; margin-bottom:2px;"):
                        ui.label(ts_short).style(
                            f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; min-width:64px;"
                        )
                        ui.label(evt_type).style(f"font-size:9px; color:{C_CREAM}; font-family:{F_MONO};")
                ui.button("Close", on_click=dialog.close).props("flat dense").style(
                    f"color:{C_CREAM}; font-family:{F_SANS}; margin-top:12px;"
                )
            dialog.open()

    async def _handle_backfill():
        """Handle Backfill Embeddings button. Explicit DEFINER action."""
        with (
            ui.dialog() as dialog,
            ui.card().style(f"background:{C_SURFACE}; border-radius:{R_MD}; padding:20px; min-width:350px;"),
        ):
            ui.label("Run Embedding Backfill").style(
                f"font-size:14px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS};"
            )
            ui.label("Explicit DEFINER action. Generates vector embeddings for unembedded corpus turns.").style(
                f"font-size:11px; color:{C_MUTED}; margin-bottom:8px; font-family:{F_SANS};"
            )
            limit_input = ui.number(label="Limit", value=500).props("dense outlined")

            with ui.row().style("gap:8px; margin-top:12px;"):
                ui.button("Start Backfill", on_click=lambda: _do_backfill()).props("dense").style(
                    f"color:{C_OK_FG}; font-family:{F_SANS};"
                )
                ui.button("Cancel", on_click=dialog.close).props("flat dense").style(
                    f"color:{C_CREAM}; font-family:{F_SANS};"
                )

            async def _do_backfill():
                dialog.close()
                ui.notify("Starting backfill...", type="info")
                result = await api.trigger_maintenance_backfill(limit=int(limit_input.value))
                status = result.get("status", "error")
                message = result.get("message", "")

                if status == "accepted":
                    ui.notify("Backfill started. Monitor progress above.", type="positive")
                elif status == "already_running":
                    ui.notify("Backfill already running.", type="info")
                elif status == "not_wired":
                    ui.notify(f"Backfill unavailable: {message}", type="warning")
                else:
                    ui.notify(f"Backfill failed: {message}", type="negative")

                await _load_all()

    async def _handle_rebuild_graph():
        """Handle Rebuild Graph button. Explicit DEFINER action."""
        result = await api.trigger_maintenance_rebuild_graph()
        status = result.get("status", "error")
        message = result.get("message", "")
        alternative = result.get("alternative", "")

        if status == "scheduled_only":
            ui.notify(f"Graph rebuild: {message}", type="warning")
            if alternative:
                ui.notify(f"Alternative: {alternative}", type="info")
        elif status == "not_wired":
            ui.notify(f"Graph rebuild not wired: {message}", type="warning")
        else:
            ui.notify(f"Graph rebuild: {message}", type="positive" if status == "accepted" else "negative")

        await _load_all()

    async def _handle_rebuild_codex():
        """Handle Rebuild CODEX/Wiki button. Explicit DEFINER action."""
        result = await api.trigger_maintenance_rebuild_codex()
        status = result.get("status", "error")
        message = result.get("message", "")
        alternative = result.get("alternative", "")

        if status == "scheduled_only":
            ui.notify(f"CODEX rebuild: {message}", type="warning")
            if alternative:
                ui.notify(f"Alternative: {alternative}", type="info")
        elif status == "not_wired":
            ui.notify(f"CODEX rebuild not wired: {message}", type="warning")
        else:
            ui.notify(f"CODEX rebuild: {message}", type="positive" if status == "accepted" else "negative")

        await _load_all()

    async def _handle_retrieval_eval():
        """Handle Run Retrieval Eval button. Explicit DEFINER action."""
        result = await api.trigger_maintenance_retrieval_eval()
        status = result.get("status", "error")
        message = result.get("message", "")
        alternative = result.get("alternative", "")

        if status == "not_wired":
            ui.notify(f"Retrieval eval: {message}", type="warning")
            if alternative:
                ui.notify(f"Alternative: {alternative}", type="info")
        else:
            ui.notify(f"Retrieval eval: {message}", type="positive" if status == "accepted" else "negative")

        await _load_all()

    async def _handle_check_stale():
        """Handle Check Stale Docs button. Explicit DEFINER action."""
        result = await api.trigger_maintenance_check_stale_docs()
        status = result.get("status", "error")
        message = result.get("message", "")
        stale_count = result.get("stale_count", 0)

        if status == "completed":
            ui.notify(f"Stale docs check: {stale_count} stale document(s) found", type="info")
        elif status == "not_wired":
            ui.notify(f"Stale docs check unavailable: {message}", type="warning")
        else:
            ui.notify(f"Stale docs check: {message}", type="negative")

        await _load_all()

    async def _handle_check_contradictions():
        """Handle Check Contradictions button. Explicit DEFINER action."""
        result = await api.trigger_maintenance_check_contradictions()
        status = result.get("status", "error")
        message = result.get("message", "")
        alternative = result.get("alternative", "")

        if status == "not_wired":
            ui.notify(f"Contradiction check: {message}", type="warning")
            if alternative:
                ui.notify(f"Alternative: {alternative}", type="info")
        else:
            ui.notify(f"Contradiction check: {message}", type="positive" if status == "accepted" else "negative")

        await _load_all()

    # ── Initial data load ──────────────────────────────────────────────
    await _load_all()


# ── Module-level helpers (moved from right rail) ─────────────────────────


def _render_retrieval_health(state: Any) -> None:
    """Render retrieval health section from status_summary data.

    Moved here from gui.panels.right_rail so that all live system status
    lives in Settings/Maintenance instead of a persistent right sidebar.
    """
    if not state.backend_reachable:
        for ch in ("Lexical", "Vector", "Graph", "CODEX", "Procedural"):
            ui.label(f"  {ch}: UNAVAILABLE").style(
                f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO}; margin-top:2px;"
            )
        return

    summary = state.status_summary
    retrieval_summary = summary.get("retrieval_health_summary", state.retrieval_health)

    if retrieval_summary:
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
                    f"font-size:11px; font-family:{F_MONO}; color:{color}; margin-top:2px;"
                )
            else:
                ui.label(f"  {display_name}: NO DATA").style(
                    f"font-size:11px; font-family:{F_MONO}; color:{C_MUTED}; margin-top:2px;"
                )
    else:
        for ch in ("Lexical", "Vector", "Graph", "CODEX", "Procedural"):
            ui.label(f"  {ch}: NOT WIRED").style(
                f"font-size:11px; font-family:{F_MONO}; color:{C_MUTED}; margin-top:2px;"
            )


def _render_pending_gates(state: Any) -> None:
    """Render pending gates count.

    Moved here from gui.panels.right_rail.
    """
    count = state.pending_gates_count
    color = C_AMBER if count > 0 else C_MUTED
    ui.label(f"{count} pending review{'s' if count != 1 else ''}").style(
        f"font-size:12px; font-family:{F_MONO}; color:{color}; margin-top:2px;"
    )
    if count > 0:
        ui.label("Review pending artifacts on the Artifacts page.").style(
            f"font-size:10px; font-family:{F_MONO}; color:{C_MUTED}; margin-top:2px;"
        )


def _render_warnings(state: Any) -> None:
    """Render active warnings.

    Moved here from gui.panels.right_rail.
    """
    if state.warnings:
        for w in state.warnings[:12]:
            ui.label(f"! {w}").style(f"font-size:11px; font-family:{F_MONO}; color:{C_ERR_FG}; margin-top:2px;")
    else:
        ui.label("None").style(f"font-size:11px; font-family:{F_MONO}; color:{C_MUTED}; margin-top:2px;")
