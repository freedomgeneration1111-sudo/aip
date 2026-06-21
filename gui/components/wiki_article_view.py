"""Wiki Article View component — displays a selected wiki article.

Renders the center panel of the Wiki/CODEX Home page showing article
title, summary, body, status, tags, timestamps, and side panels for
backlinks, related objects, contradictions, and crosslinks.

UI Cycle 8: Added Crosslink System link panel integration.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

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
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.wiki_article_view")


def render_wiki_article_view(
    article: dict[str, Any] | None,
    *,
    backlinks_data: dict[str, Any] | None = None,
    on_edit: Callable[[str], None] | None = None,
    on_create: Callable[[], None] | None = None,
    api_client: Any = None,
) -> None:
    """Render the wiki article view panel.

    Parameters:
        article: WikiArticle dict from the API, or None for empty state
        backlinks_data: Backlinks response dict, or None
        on_edit: Callback when edit is clicked (receives article ID)
        on_create: Callback when "Create Article" is clicked in empty state
        api_client: AipApiClient instance (for Crosslink System link panel)
    """
    if article is None:
        _render_empty_state(on_create=on_create)
        return

    # Article header
    with (
        ui.row()
        .classes("w-full items-center")
        .style(f"padding:20px 28px; border-bottom:0.5px solid {C_INK40}; background:{C_SURFACE};")
    ):
        # Title and state
        with ui.column().style("flex:1;"):
            ui.label(article.get("title", "Untitled")).style(
                f"font-size:24px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS}; line-height:1.3;"
            )
            state = article.get("status", article.get("state", "UNKNOWN"))
            _render_state_badge(state)

        # Action buttons
        with ui.row().style("gap:8px;"):
            if on_edit:
                ui.button("Edit", on_click=lambda: on_edit(article.get("id", ""))).props("flat dense unelevated").style(
                    f"color:{C_AMBER}; border:0.5px solid {C_AMBER}; border-radius:{R_SM}; "
                    f"font-size:12px; font-family:{F_MONO}; padding:6px 16px;"
                )

    # Article content area — full width, no sidebar
    with ui.column().classes("w-full").style("padding:20px 28px;"):
        _render_article_content(article)

    # Related info as horizontal row of cards below content (was sidebar)
    with ui.column().classes("w-full").style("padding:0 28px 20px 28px; gap:12px;"):
        _render_related_info_row(article, backlinks_data, api_client=api_client)


def _render_empty_state(*, on_create: Callable[[], None] | None = None) -> None:
    """Render the empty/none-selected state."""
    with ui.column().classes("w-full items-center justify-center").style("padding:48px; min-height:300px;"):
        ui.icon("menu_book", size="48px").style(f"color:{C_INK60}; margin-bottom:16px;")
        ui.label("No article selected").style(f"font-size:16px; color:{C_MUTED}; font-family:{F_SANS};")
        ui.label("Select an article from the list, or create a new one.").style(
            f"font-size:12px; color:{C_INK60}; font-family:{F_MONO}; margin-top:4px;"
        )
        if on_create:
            ui.button("Create First Article", on_click=on_create).props("flat dense unelevated").style(
                f"color:{C_AMBER}; border:0.5px solid {C_AMBER}; border-radius:{R_SM}; "
                f"font-size:11px; font-family:{F_MONO}; padding:6px 16px; margin-top:16px;"
            )


def _render_state_badge(state: str) -> None:
    """Render a state badge for the article."""
    state_colors = {
        "APPROVED": (C_OK_FG, "#0E1F17"),
        "GENERATED": (C_WARN_FG, "#1A1A0E"),
        "REVIEWED": (C_AMBER, "#1A170E"),
        "REJECTED": (C_ERR_FG, "#1A0E0E"),
        "SUPERSEDED": (C_INK60, C_SURFACE),
        "UNKNOWN": (C_MUTED, C_SURFACE),
    }
    fg, bg = state_colors.get(state, (C_MUTED, C_SURFACE))
    ui.label(state).style(
        f"font-size:9px; font-weight:600; color:{fg}; background:{bg}; "
        f"border:0.5px solid {fg}; border-radius:{R_SM}; "
        f"padding:2px 8px; letter-spacing:0.5px; font-family:{F_MONO}; margin-top:4px;"
    )


def _render_article_content(article: dict[str, Any]) -> None:
    """Render the main content area of the article."""
    # Summary
    summary = article.get("summary", "")
    if summary:
        with (
            ui.card()
            .classes("w-full")
            .style(
                f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD}; padding:16px 20px; margin-bottom:16px;"
            )
        ):
            ui.label("SUMMARY").style(
                f"font-size:10px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase; margin-bottom:8px;"
            )
            ui.label(summary).style(f"font-size:14px; color:{C_CREAM}; font-family:{F_SANS}; line-height:1.6;")

    # Body
    body = article.get("body", "")
    if body:
        with (
            ui.card()
            .classes("w-full")
            .style(
                f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD}; padding:20px; margin-bottom:16px;"
            )
        ):
            ui.label("CONTENT").style(
                f"font-size:10px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase; margin-bottom:12px;"
            )
            # Render body as preformatted text (wiki content is often markdown)
            ui.label(body).style(
                f"font-size:13px; color:{C_CREAM}; font-family:{F_MONO}; "
                f"white-space:pre-wrap; line-height:1.7; word-break:break-word;"
            )
    elif not summary:
        ui.label("No content yet.").style(f"font-size:13px; color:{C_MUTED}; font-family:{F_MONO}; margin-bottom:16px;")

    # Tags
    tags = article.get("tags", [])
    if tags:
        with ui.row().style("gap:6px; margin-bottom:16px; flex-wrap:wrap;"):
            for tag in tags[:10]:
                ui.label(f"#{tag}").style(
                    f"font-size:11px; color:{C_AMBER}; font-family:{F_MONO}; "
                    f"border:0.5px solid {C_INK40}; border-radius:{R_SM}; padding:3px 8px;"
                )

    # Metadata row
    with ui.row().style("gap:20px; flex-wrap:wrap;"):
        domain = article.get("domain", "")
        if domain:
            ui.label(f"Domain: {domain}").style(f"font-size:11px; color:{C_INK60}; font-family:{F_MONO};")
        word_count = article.get("word_count", 0)
        ui.label(f"Words: {word_count}").style(f"font-size:11px; color:{C_INK60}; font-family:{F_MONO};")
        version = article.get("version", 1)
        ui.label(f"Version: {version}").style(f"font-size:11px; color:{C_INK60}; font-family:{F_MONO};")
        updated = article.get("updated_at", "")
        if updated:
            # Show just the date portion
            date_str = updated[:10] if len(updated) >= 10 else updated
            ui.label(f"Updated: {date_str}").style(f"font-size:11px; color:{C_INK60}; font-family:{F_MONO};")
        # Cycle 7.1: Storage backend indicator
        storage_backend = article.get("storage_backend", "")
        if storage_backend:
            backend_color = (
                C_OK_FG
                if storage_backend == "artifact_store"
                else C_WARN_FG
                if storage_backend == "sqlite_compat"
                else C_MUTED
            )
            ui.label(f"Storage: {storage_backend}").style(
                f"font-size:10px; color:{backend_color}; font-family:{F_MONO}; "
                f"border:0.5px solid {backend_color}; border-radius:{R_SM}; padding:2px 8px;"
            )


def _render_related_info_row(
    article: dict[str, Any],
    backlinks_data: dict[str, Any] | None,
    *,
    api_client: Any = None,
) -> None:
    """Render backlinks/related/contradictions as horizontal cards below the article.

    This replaces the former right sidebar layout with a horizontal row that
    gives the article content the full width above.
    """
    # Horizontal row of info cards
    with ui.row().classes("w-full").style("gap:12px; flex-wrap:wrap;"):
        # Backlinks
        with ui.card().style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; padding:0; flex:1; min-width:200px; max-width:300px;"
        ):
            with (
                ui.row().classes("w-full items-center").style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};")
            ):
                ui.label("BACKLINKS").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; color:{C_AMBER}; text-transform:uppercase;"
                )
            with ui.column().style("padding:8px 12px; min-height:40px;"):
                if backlinks_data and not backlinks_data.get("available", False):
                    ui.label("Graph store not available").style(
                        f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                    )
                elif backlinks_data:
                    backlinks = backlinks_data.get("backlinks", [])
                    if not backlinks:
                        ui.label("No backlinks found").style(f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};")
                    else:
                        for bl in backlinks[:5]:
                            source_id = bl.get("source_id", "?")
                            rel = bl.get("relation_type", "?")
                            ui.label(f"{rel}: {source_id[:32]}").style(
                                f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                            )
                        if len(backlinks) > 5:
                            ui.label(f"+ {len(backlinks) - 5} more").style(
                                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                            )
                else:
                    ui.label("Not loaded").style(f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};")

        # Related sources
        source_docs = article.get("source_documents", [])
        related_artifacts = article.get("related_artifacts", [])
        related_turns = article.get("related_turns", [])
        with ui.card().style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; padding:0; flex:1; min-width:200px; max-width:300px;"
        ):
            with (
                ui.row().classes("w-full items-center").style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};")
            ):
                ui.label("RELATED").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; color:{C_AMBER}; text-transform:uppercase;"
                )
            with ui.column().style("padding:8px 12px; min-height:40px;"):
                if source_docs:
                    ui.label(f"Sources: {len(source_docs)}").style(
                        f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                    )
                if related_artifacts:
                    ui.label(f"Artifacts: {len(related_artifacts)}").style(
                        f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                    )
                if related_turns:
                    ui.label(f"Turns: {len(related_turns)}").style(
                        f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                    )
                if not source_docs and not related_artifacts and not related_turns:
                    ui.label("No related objects linked yet").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                    )

        # Contradictions
        contradictions = article.get("contradictions", [])
        with ui.card().style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; padding:0; flex:1; min-width:200px; max-width:300px;"
        ):
            with (
                ui.row().classes("w-full items-center").style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};")
            ):
                ui.label("CONTRADICTIONS").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; "
                    f"color:{C_ERR_FG if contradictions else C_AMBER}; text-transform:uppercase;"
                )
            with ui.column().style("padding:8px 12px; min-height:40px;"):
                if contradictions:
                    for c in contradictions[:5]:
                        severity = c.get("severity", "unknown")
                        claim = c.get("claim_a", "")[:40]
                        ui.label(f"[{severity}] {claim}").style(
                            f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};"
                        )
                else:
                    ui.label("No contradictions detected").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                    )

        # Open questions
        open_questions = article.get("open_questions", [])
        if open_questions:
            with ui.card().style(
                f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD}; padding:0; flex:1; min-width:200px; max-width:300px;"
            ):
                with (
                    ui.row()
                    .classes("w-full items-center")
                    .style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};")
                ):
                    ui.label("OPEN QUESTIONS").style(
                        f"font-size:9px; font-weight:600; letter-spacing:1px; color:{C_WARN_FG}; text-transform:uppercase;"
                    )
                with ui.column().style("padding:8px 12px; min-height:40px;"):
                    for q in open_questions[:5]:
                        ui.label(f"? {q}").style(f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};")

    # Crosslink System — Link Panel (UI Cycle 8) — full width below the row
    article_id = article.get("id", "")
    if article_id and api_client is not None:
        from gui.components.link_panel import render_link_panel

        render_link_panel(
            object_type="wiki_article",
            object_id=article_id,
            api_client=api_client,
            show_create=True,
        )


def _render_sidebar(
    article: dict[str, Any],
    backlinks_data: dict[str, Any] | None,
    *,
    api_client: Any = None,
) -> None:
    """Render the sidebar with backlinks, related objects, contradictions, and crosslinks.

    DEPRECATED: This function is kept for backward compatibility but is no longer
    used. The same info is now rendered by _render_related_info_row() as a
    horizontal card row below the article content, freeing the article content
    to use the full width.
    """
    _render_related_info_row(article, backlinks_data, api_client=api_client)
