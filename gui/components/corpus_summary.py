"""Corpus Summary Cards — summary metrics for the Corpus Workbench.

Shows key metrics: Documents, Chunks, Embeddings, Problems, Backfill State.
Honest unavailable/degraded states — never fake healthy.

Import boundary: imports ONLY from gui.* (theme, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_BG,
    C_ERR_FG,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_BG,
    C_OK_FG,
    C_SURFACE,
    C_WARN_BG,
    F_MONO,
    F_SANS,
    R_MD,
)

log = logging.getLogger("gui.components.corpus_summary")


def _stat_card(
    label: str,
    value: str,
    subtitle: str = "",
    color: str = C_CREAM,
    bg_color: str = C_SURFACE,
) -> ui.card:
    """Create a single stat card."""
    with (
        ui.card()
        .classes("q-pa-sm")
        .style(
            f"background:{bg_color}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; min-width:120px; flex:1; "
            f"font-family:{F_SANS};"
        ) as card
    ):
        ui.label(label).style(
            f"font-size:10px; color:{C_MUTED}; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;"
        )
        ui.label(value).style(f"font-size:22px; font-weight:700; color:{color}; font-family:{F_MONO}; line-height:1;")
        if subtitle:
            ui.label(subtitle).style(f"font-size:10px; color:{C_INK60}; margin-top:2px;")
    return card


class CorpusSummaryCards:
    """Corpus summary cards component.

    Displays Documents, Chunks, Embeddings, Problems, and Backfill State.
    All data comes from backend API. Honest unavailable states.
    """

    def __init__(self) -> None:
        self._container: ui.row | None = None

    def render(self, status: dict[str, Any], problems: dict[str, Any]) -> None:
        """Render the summary cards with given status and problems data."""
        if self._container is not None:
            self._container.clear()

        with ui.row().classes("w-full q-gutter-sm no-wrap").style("overflow-x:auto; padding-bottom:8px;") as row:
            self._container = row

            # Documents card
            doc_count = status.get("documents", 0)
            conv_count = status.get("conversations", 0)
            _stat_card(
                "Documents",
                str(doc_count),
                f"{conv_count} conversations",
            )

            # Chunks card
            total_turns = status.get("total_turns", 0)
            _stat_card(
                "Chunks",
                str(total_turns),
                f"{status.get('tagged', 0)} tagged",
            )

            # Embeddings card
            embedded = status.get("embedded", 0)
            embed_pct = status.get("embed_coverage", 0.0)
            embed_color = C_OK_FG if embed_pct > 80 else (C_AMBER if embed_pct > 20 else C_ERR_FG)
            _stat_card(
                "Embeddings",
                f"{embed_pct:.1f}%",
                f"{embedded}/{total_turns} embedded",
                color=embed_color,
            )

            # Problems card
            fail_count = len(problems.get("failed_ingest_jobs", []))
            unembedded = problems.get("unembedded_count", status.get("total_turns", 0) - embedded)
            needs_reembed = problems.get("needs_reembed_count", 0)
            problem_count = fail_count + (1 if unembedded > 0 else 0) + (1 if needs_reembed > 0 else 0)
            problem_color = C_ERR_FG if problem_count > 0 else C_OK_FG
            problem_bg = C_ERR_BG if problem_count > 2 else (C_WARN_BG if problem_count > 0 else C_OK_BG)
            _stat_card(
                "Problems",
                str(problem_count),
                f"{fail_count} failed, {unembedded} unembedded",
                color=problem_color,
                bg_color=problem_bg if problem_count > 0 else C_SURFACE,
            )

            # Backfill state card — honest about API key status
            backfill_status = status.get("backfill_state", "")
            # Check for API key / provider status
            has_api_key = not bool(status.get("error", ""))
            embed_error = status.get("error", "")
            if embed_error and (
                "api_key" in embed_error.lower()
                or "provider" in embed_error.lower()
                or "openrouter" in embed_error.lower()
            ):
                bf_label = "BLOCKED"
                bf_color = C_ERR_FG
                bf_detail = "API key / provider missing"
            elif backfill_status in ("backfill_running", "configured_idle"):
                bf_label = "ACTIVE"
                bf_color = C_OK_FG if backfill_status == "configured_idle" else C_AMBER
                bf_detail = backfill_status
            elif backfill_status in ("backfill_pending",):
                bf_label = "SCHEDULED"
                bf_color = C_AMBER
                bf_detail = backfill_status
            elif backfill_status in ("not_configured", ""):
                bf_label = "IDLE"
                bf_color = C_MUTED
                bf_detail = "not configured"
            elif backfill_status == "embedded":
                bf_label = "COMPLETE"
                bf_color = C_OK_FG
                bf_detail = "all embedded"
            else:
                bf_label = backfill_status.upper() if backfill_status else "UNKNOWN"
                bf_color = C_MUTED
                bf_detail = backfill_status or "unknown"
            _stat_card(
                "Backfill",
                bf_label,
                bf_detail,
                color=bf_color,
            )
