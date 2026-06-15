"""AIP Corpus Page — Route: /corpus

Corpus Workbench v1 — UI Cycle 10.

Allows the DEFINER to ingest, inspect, repair, and backfill the corpus
from the Operator Console. Makes corpus health, document status, chunk
counts, embedding coverage, failed ingest jobs, duplicate/stale documents,
and backfill state visible without using the CLI.

Architecture requirements enforced:
- GUI remains API-first
- No import of orchestration internals
- Ingest/backfill actions are explicit DEFINER actions
- No fake corpus counts, no fake embedding status
- No silent document deletion or overwrite
- Failed jobs/problems visible
- Degraded/unavailable states visible
- No secrets exposed
- If backend capability not wired, returns/shows unavailable or not_wired

Import boundary: this module imports ONLY from gui.* (theme, api_client,
components, state). Never imports from aip.orchestration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nicegui import context, ui

from gui.api_client import get_api_client
from gui.components.corpus_actions import CorpusActions
from gui.components.corpus_problems import CorpusProblems
from gui.components.corpus_summary import CorpusSummaryCards
from gui.components.document_detail import DocumentDetail
from gui.components.document_table import DocumentTable
from gui.components.layout import build_left_nav, build_right_rail, build_top_bar
from gui.state import get_session_state
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_GROUND,
    C_INK40,
    C_MUTED,
    C_SURFACE,
    F_MONO,
    F_SANS,
    R_MD,
)

log = logging.getLogger("gui.pages.corpus")


@ui.page("/corpus")
async def corpus_page():
    """Corpus Workbench v1 — ingest, inspect, repair, and backfill the corpus."""
    state = get_session_state()
    state.client = context.client
    api = get_api_client()

    # Refresh backend status BEFORE rendering layout so top bar/right rail
    # show accurate state instead of stale defaults (backend_reachable=False).
    await state.refresh_status_summary()

    build_top_bar(state)
    build_left_nav(state, active_page="/corpus")

    # ── State ──────────────────────────────────────────────────────

    # Mutable state containers for reactive updates
    corpus_status: dict[str, Any] = {}
    corpus_problems: dict[str, Any] = {}
    documents_data: dict[str, Any] = {"items": [], "total": 0}
    document_detail: dict[str, Any] = {}
    selected_source_path: str = ""
    search_query: str = ""
    embedding_progress: dict[str, Any] = {}

    # ── Components ─────────────────────────────────────────────────

    summary_cards = CorpusSummaryCards()
    corpus_actions = CorpusActions(
        on_ingest=lambda: _handle_ingest(),
        on_backfill=lambda: _handle_backfill(),
        on_retry_failed=lambda: _handle_retry_failed(),
    )
    document_table = DocumentTable(on_select=lambda sp: _handle_document_select(sp))
    document_detail_panel = DocumentDetail(on_close=lambda: _handle_detail_close())
    problems_panel = CorpusProblems()

    # ── Layout ─────────────────────────────────────────────────────

    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:24px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Title
        ui.label("Corpus Workbench").style(f"font-family:{F_SANS}; font-size:24px; font-weight:700; color:{C_CREAM};")
        ui.label("Inspect, ingest, repair, and backfill the knowledge corpus.").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:12px;"
        )

        # Summary cards
        with ui.row().classes("w-full").style("margin-bottom:12px;"):
            summary_container = ui.column().classes("w-full")
            with summary_container:
                summary_cards.render({}, {})

        # Actions bar
        ui.label("Actions").style(
            f"font-size:11px; color:{C_MUTED}; text-transform:uppercase; "
            f"letter-spacing:0.5px; margin-bottom:4px; font-family:{F_SANS};"
        )
        actions_container = ui.row().classes("w-full")
        with actions_container:
            corpus_actions.render()

        # Search bar
        with ui.row().classes("w-full").style("margin-top:12px; margin-bottom:8px; gap:8px;"):
            search_input = (
                ui.input(
                    placeholder="Search documents by path...",
                )
                .props("dense outlined size=sm")
                .style(f"flex:1; font-family:{F_MONO};")
            )
            ui.button("Search", on_click=lambda: _handle_search()).props("dense size=sm").style(
                f"font-family:{F_SANS};"
            )

        # Main content: document table + detail panel
        with ui.row().classes("w-full").style("gap:16px; min-height:300px;"):
            # Document table (left)
            table_container = ui.column().classes("flex-3").style("min-width:0;")
            with table_container:
                ui.label("Documents").style(
                    f"font-size:14px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS}; margin-bottom:4px;"
                )
                table_inner = ui.column().classes("w-full")
                with table_inner:
                    document_table.render({"items": [], "total": 0})

            # Document detail (right, shown when selected)
            detail_container = ui.column().classes("flex-2").style("min-width:280px; max-width:400px;")
            with detail_container:
                detail_inner = ui.column().classes("w-full")
                with detail_inner:
                    document_detail_panel.render({"not_found": True, "source_path": ""})

        # Retrieval Lab link
        with ui.row().classes("w-full items-center").style("padding:8px 16px;"):
            ui.label("Test retrieval quality:").style(f"font-size:10px; color:{C_MUTED};")
            ui.link("Retrieval Lab", "/retrieval").style(f"font-size:10px; color:{C_AMBER}; text-decoration:underline;")

        # Problems panel
        ui.html("<hr>").style(f"border-color:{C_INK40}; margin:8px 0;")
        problems_container = ui.column().classes("w-full")
        with problems_container:
            problems_panel.render({"available": False})

    build_right_rail(state)

    # ── Data loading ───────────────────────────────────────────────

    async def _load_all():
        """Load all corpus data from backend API."""
        nonlocal corpus_status, corpus_problems, documents_data, embedding_progress

        try:
            # Load in parallel
            status_task = asyncio.create_task(api.get_corpus_status())
            problems_task = asyncio.create_task(api.get_corpus_problems())
            docs_task = asyncio.create_task(api.list_corpus_documents(limit=50))
            progress_task = asyncio.create_task(api.get_corpus_embedding_progress())

            corpus_status = await status_task
            corpus_problems = await problems_task
            documents_data = await docs_task
            embedding_progress = await progress_task

            # Enrich corpus_status with backfill state from embedding progress
            corpus_status["backfill_state"] = (
                embedding_progress.get("sexton_pass", {}).get("state", "")
                if embedding_progress.get("sexton_pass")
                else ""
            )

            _refresh_ui()
        except Exception as exc:
            log.warning("corpus_load_all_failed: %s", exc)
            state.backend_reachable = False
            # Set fallback empty data so UI renders degraded state
            corpus_status = corpus_status or {
                "total_turns": 0,
                "embedded": 0,
                "tagged": 0,
                "unembedded": 0,
                "error": str(exc),
            }
            corpus_problems = corpus_problems or {"available": False, "problems": [], "error": str(exc)}
            documents_data = documents_data or {"documents": [], "error": str(exc)}
            embedding_progress = embedding_progress or {
                "total": 0,
                "embedded": 0,
                "unembedded": 0,
                "percentage": 0.0,
                "error": str(exc),
            }
            _refresh_ui()
            ui.notify("Failed to load corpus data — backend may be unavailable", color="warning")

    def _refresh_ui():
        """Refresh all UI components with current data."""
        # Summary cards
        summary_container.clear()
        with summary_container:
            summary_cards.render(corpus_status, corpus_problems)

        # Actions
        actions_container.clear()
        with actions_container:
            backfill_running = embedding_progress.get("sexton_pass", {}) is not None and embedding_progress.get(
                "sexton_pass", {}
            ).get("running", False)
            has_provider = corpus_status.get("error", "") == "" or bool(corpus_status.get("total_turns") is not None)
            corpus_actions.render(
                backfill_running=backfill_running,
                has_embedding_provider=has_provider,
            )

        # Document table
        table_inner.clear()
        with table_inner:
            document_table.render(documents_data, search_query)

        # Problems
        problems_container.clear()
        with problems_container:
            problems_panel.render(corpus_problems)

    # ── Event handlers ─────────────────────────────────────────────

    async def _handle_search():
        """Handle search button click."""
        nonlocal search_query
        search_query = search_input.value or ""
        docs = await api.list_corpus_documents(limit=50, search=search_query)
        nonlocal documents_data
        documents_data = docs
        table_inner.clear()
        with table_inner:
            document_table.render(documents_data, search_query)

    async def _handle_document_select(source_path: str):
        """Handle document row selection — load detail."""
        nonlocal selected_source_path, document_detail
        selected_source_path = source_path
        detail = await api.get_corpus_document_detail(source_path)
        document_detail = detail
        detail_inner.clear()
        with detail_inner:
            document_detail_panel.render(detail)

    def _handle_detail_close():
        """Close the document detail panel."""
        nonlocal selected_source_path
        selected_source_path = ""
        detail_inner.clear()
        with detail_inner:
            document_detail_panel.render({"not_found": True, "source_path": ""})

    async def _handle_ingest():
        """Handle ingest action — show path input dialog."""
        with (
            ui.dialog() as dialog,
            ui.card().style(f"background:{C_SURFACE}; border-radius:{R_MD}; padding:20px; min-width:400px;"),
        ):
            ui.label("Ingest File or Directory").style(
                f"font-size:14px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS};"
            )
            ui.label("Explicit DEFINER action. Will not silently overwrite existing documents.").style(
                f"font-size:11px; color:{C_MUTED}; margin-bottom:8px;"
            )
            path_input = (
                ui.input(
                    label="File or directory path",
                    placeholder="/path/to/file.json",
                )
                .props("dense outlined")
                .style("width:100%;")
            )

            model_input = (
                ui.input(
                    label="Source model (optional)",
                    placeholder="auto-detected",
                )
                .props("dense outlined")
                .style("width:100%;")
            )

            with ui.row().style("gap:8px; margin-top:12px;"):
                ui.button("Ingest", on_click=lambda: _do_ingest()).props("dense").style(f"font-family:{F_SANS};")
                ui.button("Cancel", on_click=dialog.close).props("flat dense").style(
                    f"color:{C_MUTED}; font-family:{F_SANS};"
                )

            async def _do_ingest():
                path = path_input.value or ""
                if not path:
                    ui.notify("Path is required", type="warning")
                    return
                dialog.close()
                ui.notify("Starting ingestion...", type="info")
                result = await api.ingest_to_corpus(
                    path=path,
                    source_model=model_input.value or "",
                )
                if result.get("type") == "error":
                    ui.notify(f"Ingestion failed: {result.get('error', 'unknown')}", type="negative")
                else:
                    ingested = result.get("turns_ingested", 0)
                    failed = result.get("turns_failed", 0)
                    skipped = result.get("turns_skipped", 0)
                    msg = f"Ingested: {ingested}, Skipped: {skipped}, Failed: {failed}"
                    ui.notify(msg, type="positive" if failed == 0 else "warning")
                await _load_all()

    async def _handle_backfill():
        """Handle backfill action — confirm and trigger."""
        with (
            ui.dialog() as dialog,
            ui.card().style(f"background:{C_SURFACE}; border-radius:{R_MD}; padding:20px; min-width:350px;"),
        ):
            ui.label("Run Embedding Backfill").style(
                f"font-size:14px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS};"
            )
            ui.label("Explicit DEFINER action. Generates vector embeddings for unembedded chunks.").style(
                f"font-size:11px; color:{C_MUTED}; margin-bottom:8px;"
            )

            limit_input = (
                ui.number(
                    label="Limit (chunks to embed)",
                    value=500,
                )
                .props("dense outlined")
                .style("width:100%;")
            )

            with ui.row().style("gap:8px; margin-top:12px;"):
                ui.button("Start Backfill", on_click=lambda: _do_backfill()).props("dense").style(
                    f"font-family:{F_SANS};"
                )
                ui.button("Cancel", on_click=dialog.close).props("flat dense").style(
                    f"color:{C_MUTED}; font-family:{F_SANS};"
                )

            async def _do_backfill():
                dialog.close()
                ui.notify("Starting backfill...", type="info")
                result = await api.trigger_corpus_backfill(
                    limit=int(limit_input.value or 500),
                )
                status = result.get("status", "error")
                msg = result.get("message", "")
                if status == "accepted":
                    ui.notify("Backfill started. Monitor progress in the summary cards.", type="positive")
                elif status == "not_wired":
                    ui.notify(f"Backfill unavailable: {msg}", type="warning")
                elif status == "already_running":
                    ui.notify("Backfill already running.", type="info")
                else:
                    ui.notify(f"Backfill failed: {msg}", type="negative")
                await _load_all()

    async def _handle_retry_failed():
        """Handle retry failed embeds action."""
        with (
            ui.dialog() as dialog,
            ui.card().style(f"background:{C_SURFACE}; border-radius:{R_MD}; padding:20px; min-width:300px;"),
        ):
            ui.label("Retry Failed Embeds").style(
                f"font-size:14px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS};"
            )
            ui.label(
                (
                    "Explicit DEFINER action. Clears failure counters for failed embeds "
                    "so they will be retried in the next cycle."
                )
            ).style(f"font-size:11px; color:{C_MUTED}; margin-bottom:8px;")

            with ui.row().style("gap:8px; margin-top:12px;"):
                ui.button("Retry Failed", on_click=lambda: _do_retry()).props("dense").style(f"font-family:{F_SANS};")
                ui.button("Cancel", on_click=dialog.close).props("flat dense").style(
                    f"color:{C_MUTED}; font-family:{F_SANS};"
                )

            async def _do_retry():
                dialog.close()
                ui.notify("Retrying failed embeds...", type="info")
                result = await api.retry_failed_embeds()
                status = result.get("status", "error")
                msg = result.get("message", "")
                retried = result.get("retried_count", 0)
                if status == "accepted":
                    ui.notify(f"Cleared {retried} failures for retry.", type="positive")
                elif status == "no_failed":
                    ui.notify("No failed embed jobs found.", type="info")
                elif status == "not_wired":
                    ui.notify(f"Retry unavailable: {msg}", type="warning")
                else:
                    ui.notify(f"Retry failed: {msg}", type="negative")
                await _load_all()

    # ── Initial load ───────────────────────────────────────────────

    await _load_all()
