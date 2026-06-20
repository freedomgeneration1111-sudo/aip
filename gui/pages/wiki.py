"""AIP Wiki / CODEX Home Page — Route: /wiki

The wiki is the living knowledge map of AIP_Brain. This page provides:
  - Article tree/list with search and state filter
  - Selected article view with content, tags, metadata
  - Side panels for backlinks, related objects, contradictions
  - Create article flow (DEFINER action, always GENERATED)
  - Edit article flow (DEFINER action, version bump, no state change)
  - Honest empty/unavailable/degraded states

Sovereignty guarantees:
  - No auto-approve: CREATE always sets state to GENERATED
  - No silent mutation: every write is explicit and logged
  - No fake data: unavailable fields return empty/null honestly
  - No secret exposure
"""

from __future__ import annotations

import logging

from nicegui import context, ui

from gui.api_client import AipApiClient
from gui.components.layout import build_left_nav, build_top_bar, build_right_rail
from gui.components.wiki_article_list import render_wiki_article_list
from gui.components.wiki_article_view import render_wiki_article_view
from gui.components.wiki_editor import WikiEditorDialog
from gui.state import get_session_state
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_OK_FG,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.pages.wiki")


@ui.page("/wiki")
async def wiki_page():
    """Wiki / CODEX Home — navigable knowledge map."""
    state = get_session_state()
    state.client = context.client

    # Fetch live data
    await state.refresh_status_summary()

    # Build layout
    build_top_bar(state)
    build_left_nav(state, active_page="/wiki")
    build_right_rail(state)

    # Wiki page state
    wiki_state = _WikiPageState(api_client=state.api_client)

    # Build editor dialog (shared between create and edit)
    editor = WikiEditorDialog(on_submit=wiki_state.handle_editor_submit)

    # Main content
    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:16px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        # Page heading
        with ui.row().classes("w-full items-center").style("margin-bottom:12px;"):
            ui.label("Wiki / CODEX").style(f"font-family:{F_SANS}; font-size:24px; font-weight:700; color:{C_CREAM};")
            ui.space()
            ui.button("Create Article", on_click=editor.open_create).props("flat dense unelevated").style(
                f"color:{C_GROUND}; background:{C_AMBER}; border-radius:{R_SM}; "
                f"font-size:11px; font-family:{F_MONO}; font-weight:600; padding:6px 16px;"
            )

        # Status bar
        with ui.row().classes("w-full items-center").style("margin-bottom:12px; gap:12px; flex-wrap:wrap;"):
            # Backend status
            if not state.backend_reachable:
                ui.label("UNAVAILABLE — backend unreachable").style(
                    f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO}; "
                    f"border:0.5px solid {C_ERR_FG}; border-radius:{R_SM}; padding:2px 8px;"
                )
            else:
                ui.label("BACKEND OK").style(
                    f"font-size:10px; color:{C_OK_FG}; font-family:{F_MONO}; "
                    f"border:0.5px solid {C_OK_FG}; border-radius:{R_SM}; padding:2px 8px;"
                )

            # Wiki stats from status summary
            ws = state.status_summary.get("wiki_summary", {})
            if ws:
                total = ws.get("total", 0)
                approved = ws.get("approved", 0)
                generated = ws.get("generated", 0)
                ui.label(f"Articles: {total}").style(f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};")
                ui.label(f"Approved: {approved}").style(f"font-size:10px; color:{C_OK_FG}; font-family:{F_MONO};")
                if generated > 0:
                    ui.label(f"Pending: {generated}").style(f"font-size:10px; color:{C_WARN_FG}; font-family:{F_MONO};")

        # Main layout: two columns
        # Left: article list | Center: article view (full width, no nested sidebar)
        with ui.row().classes("w-full").style("gap:12px;"):
            # Left panel: article list
            with ui.column().style(
                f"width:240px; min-width:200px; background:{C_SURFACE}; "
                f"border:0.5px solid {C_INK40}; border-radius:{R_MD}; padding:0; "
                f"max-height:calc(100vh - 140px); overflow-y:auto;"
            ):
                # Search bar
                with (
                    ui.row()
                    .classes("w-full items-center")
                    .style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};")
                ):
                    search_input = (
                        ui.input(placeholder="Search articles...")
                        .classes("flex-1")
                        .style(
                            f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM}; padding:4px 8px; color:{C_CREAM}; "
                            f"font-size:11px; font-family:{F_MONO};"
                        )
                    )

                # State filter
                with (
                    ui.row().classes("w-full").style(f"padding:4px 12px; border-bottom:0.5px solid {C_INK40}; gap:4px;")
                ):
                    state_select = (
                        ui.select(
                            options=["all", "APPROVED", "GENERATED", "REVIEWED", "REJECTED"],
                            value="all",
                        )
                        .props("dense flat")
                        .style(f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};")
                    )

                # Article list container
                list_container = ui.column().classes("w-full").style("padding:8px;")

            # Center + right: article view
            view_container = (
                ui.column()
                .classes("flex-1")
                .style(
                    f"min-width:0; background:{C_SURFACE}; "
                    f"border:0.5px solid {C_INK40}; border-radius:{R_MD}; "
                    f"max-height:calc(100vh - 140px); overflow-y:auto;"
                )
            )

    # ── Data loading and rendering ──────────────────────────────────────

    async def _load_and_render():
        """Load wiki articles from API and render the UI."""
        articles = []
        articles_error = None
        backend_ok = state.backend_reachable

        if backend_ok:
            try:
                result = await state.api_client.list_wiki_articles()
                articles = result.get("items", [])
            except Exception as exc:
                log.error("Failed to load wiki articles: %s", exc)
                articles = []
                articles_error = str(exc)
                log.warning("wiki_articles_load_failed: %s", exc)

        # Store in wiki state
        wiki_state.articles = articles

        # Render article list
        list_container.clear()
        with list_container:
            if articles_error:
                ui.label(f"Failed to load articles: {articles_error}").style(f"font-size:11px; color:{C_ERR_FG};")
            render_wiki_article_list(
                articles,
                on_select=_on_article_select,
                selected_id=wiki_state.selected_article_id,
                search_text=wiki_state.search_text,
                state_filter=wiki_state.state_filter,
            )

        # Render article view
        view_container.clear()
        with view_container:
            if wiki_state.selected_article_id and wiki_state.selected_article:
                backlinks_data = wiki_state.backlinks_data
                render_wiki_article_view(
                    wiki_state.selected_article,
                    backlinks_data=backlinks_data,
                    on_edit=_on_article_edit,
                    on_create=editor.open_create,
                    api_client=state.api_client,
                )
            else:
                render_wiki_article_view(
                    None,
                    on_create=editor.open_create,
                )

    async def _on_article_select(article_id: str):
        """Handle article selection from the list."""
        wiki_state.selected_article_id = article_id

        # Find article in list
        selected = None
        for a in wiki_state.articles:
            if a.get("id") == article_id:
                selected = a
                break

        if selected is None and state.backend_reachable:
            # Fetch full article from API
            try:
                selected = await state.api_client.get_wiki_article(article_id)
            except Exception as exc:
                log.error("Failed to load article '%s': %s", article_id, exc)
                selected = None

        wiki_state.selected_article = selected

        # Fetch backlinks
        if article_id and state.backend_reachable:
            try:
                wiki_state.backlinks_data = await state.api_client.get_wiki_backlinks(article_id)
            except Exception as exc:
                log.warning("Failed to load backlinks for '%s': %s", article_id, exc)
                wiki_state.backlinks_data = None
        else:
            wiki_state.backlinks_data = None

        await _load_and_render()

    def _on_article_edit(article_id: str):
        """Handle edit button click."""
        if wiki_state.selected_article:
            editor.open_edit(wiki_state.selected_article)

    # ── Search/filter handlers ──────────────────────────────────────────

    def _on_search_change(e):
        wiki_state.search_text = e.value or ""
        # Re-render list only
        list_container.clear()
        with list_container:
            render_wiki_article_list(
                wiki_state.articles,
                on_select=_on_article_select,
                selected_id=wiki_state.selected_article_id,
                search_text=wiki_state.search_text,
                state_filter=wiki_state.state_filter,
            )

    def _on_state_filter_change(e):
        wiki_state.state_filter = e.value or "all"
        # Re-render list only
        list_container.clear()
        with list_container:
            render_wiki_article_list(
                wiki_state.articles,
                on_select=_on_article_select,
                selected_id=wiki_state.selected_article_id,
                search_text=wiki_state.search_text,
                state_filter=wiki_state.state_filter,
            )

    search_input.on("update:model-value", _on_search_change)
    state_select.on("update:model-value", _on_state_filter_change)

    # ── Initial data load ───────────────────────────────────────────────
    await _load_and_render()


# ── Wiki page state ─────────────────────────────────────────────────────


class _WikiPageState:
    """Per-page state for the Wiki/CODEX Home page.

    Not persisted across page navigations — data is re-fetched on each load.
    """

    def __init__(self, api_client: AipApiClient) -> None:
        self.api_client = api_client
        self.articles: list[dict] = []
        self.selected_article_id: str | None = None
        self.selected_article: dict | None = None
        self.backlinks_data: dict | None = None
        self.search_text: str = ""
        self.state_filter: str = "all"

    async def handle_editor_submit(self, payload: dict) -> None:
        """Handle the submit callback from WikiEditorDialog.

        Performs the actual API call for create or update.
        """
        mode = payload.get("mode", "create")

        try:
            if mode == "edit":
                article_id = payload.get("article_id", "")
                if not article_id:
                    log.error("Edit submitted without article_id")
                    return

                result = await self.api_client.update_wiki_article(
                    article_id,
                    title=payload.get("title"),
                    summary=payload.get("summary"),
                    body=payload.get("body"),
                    tags=payload.get("tags"),
                )
                log.info("Article updated: %s (v%d)", result.get("id"), result.get("version"))

                # Refresh the article if it's currently selected
                if article_id == self.selected_article_id:
                    try:
                        self.selected_article = await self.api_client.get_wiki_article(article_id)
                    except Exception as exc:
                        log.warning("Failed to refresh article '%s' after edit: %s", article_id, exc)

            else:
                result = await self.api_client.create_wiki_article(
                    title=payload.get("title", ""),
                    domain=payload.get("domain", ""),
                    summary=payload.get("summary", ""),
                    body=payload.get("body", ""),
                    tags=payload.get("tags"),
                )
                log.info("Article created: %s (state=%s)", result.get("id"), result.get("state"))

                # Select the newly created article
                self.selected_article_id = result.get("id")

        except Exception as exc:
            log.error("Wiki editor submit failed: %s", exc)
