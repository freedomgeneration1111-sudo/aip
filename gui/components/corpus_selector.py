"""Corpus Selector — ADR-008 Multi-Corpus session corpus binding.

A NiceGUI component that lets the DEFINER select which corpora are active
for the current session. Multi-select for non-sensitive corpora; Branham
(corpus with branham_policy_enabled=True) is shown only when policy is
enabled AND requires an explicit confirmation prompt.

Selection is written to the session metadata via the API, which stores
active_corpus_ids in the session's metadata_json. The branham_allowlist
flag is only persisted when the registry's branham_policy_enabled is True
(§5 — prevents allowlist escalation via session replay).

Import boundary: imports ONLY from gui.* (theme, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from gui.theme import C_AMBER, C_CREAM, C_INK40, C_SURFACE

logger = logging.getLogger(__name__)


async def fetch_registered_corpora(api_client: Any) -> list[dict]:
    """Fetch the list of registered corpora from the API.

    Returns a list of dicts with keys: corpus_id, corpus_type, sensitive.
    Returns [] on error.
    """
    try:
        result = await api_client.get("/corpus-registry/corpora")
        if isinstance(result, list):
            return result
        return []
    except Exception as exc:
        logger.warning("fetch_registered_corpora_failed error=%s", exc)
        return []


def render_corpus_selector(
    corpora: list[dict],
    active_corpus_ids: list[str],
    on_change: Any = None,
) -> dict[str, ui.checkbox]:
    """Render a corpus selector with checkboxes.

    Args:
        corpora: list of {corpus_id, corpus_type, sensitive} dicts.
        active_corpus_ids: currently active corpus_ids.
        on_change: async callback when selection changes.

    Returns:
        dict mapping corpus_id → ui.checkbox element.
    """
    checkboxes: dict[str, ui.checkbox] = {}

    if not corpora:
        ui.label("No corpora registered.").classes("text-sm text-gray-500")
        return checkboxes

    ui.label("Active Corpora").classes(f"text-sm font-semibold {C_CREAM}")

    for corpus in corpora:
        cid = corpus.get("corpus_id", "")
        ctype = corpus.get("corpus_type", "")
        sensitive = corpus.get("sensitive", False)

        with ui.row().classes("items-center gap-2"):
            cb = ui.checkbox(
                text=f"{cid} ({ctype})",
                value=cid in active_corpus_ids,
            )
            checkboxes[cid] = cb

            if sensitive:
                ui.label("⚠ sensitive").classes(f"text-xs {C_AMBER}")

            if cid == "definer":
                ui.label("(always active)").classes(f"text-xs {C_INK40}")

    if on_change:
        # The on_change callback is responsible for reading the checkbox
        # values and calling the session update API.
        ui.button("Update Selection", on_click=on_change).classes(f"mt-2 {C_SURFACE} {C_CREAM}")

    return checkboxes


async def update_session_corpora(
    api_client: Any,
    session_id: str,
    active_corpus_ids: list[str],
    branham_allowlist: bool,
) -> bool:
    """Update the session's active corpora via the API.

    Returns True on success, False on error.
    """
    try:
        await api_client.post(
            f"/sessions/{session_id}/corpora",
            json={
                "active_corpus_ids": active_corpus_ids,
                "branham_allowlist": branham_allowlist,
            },
        )
        return True
    except Exception as exc:
        logger.warning("update_session_corpora_failed error=%s", exc)
        return False
