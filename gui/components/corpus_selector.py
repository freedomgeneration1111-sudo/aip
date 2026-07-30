"""Corpus Selector — ADR-008 Multi-Corpus session corpus binding.

A NiceGUI component that lets the DEFINER select which corpora are active
for the current session. Multi-select for non-sensitive corpora; sensitive
corpora (sensitive=True) are shown with an amber "⚠ sensitive" tag and
require session opt-in via ``allowed_restricted_corpora``.

Selection is written to the session metadata via
``api_client.update_session_corpora()``, which PATCHes
``active_corpus_ids`` into the session's metadata. The chat WebSocket
reads this via ``get_session_meta()`` and ``_augmented_context.py`` uses
it to scope multi-corpus retrieval (ADR-008 §4).

QW8 (2026-07-23): rewired to use the real API client methods
(``get_registered_corpora`` + ``update_session_corpora``) instead of
the phantom ``GET /corpus-registry/corpora`` + ``POST /sessions/{id}/corpora``
endpoints that never existed. The component was previously dead code.

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

    QW8 (2026-07-23): uses ``api_client.get_registered_corpora()`` which
    calls ``GET /api/v1/corpus-registry/corpora`` (QW9 endpoint).

    Returns a list of dicts with keys: corpus_id, corpus_type, sensitive,
    deletion_state, access_note. Returns [] on error.
    """
    try:
        return await api_client.get_registered_corpora()
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
                on_change=on_change if on_change else None,
            )
            checkboxes[cid] = cb

            if sensitive:
                ui.label("⚠ sensitive").classes(f"text-xs {C_AMBER}")

            if cid == "definer":
                ui.label("(always active)").classes(f"text-xs {C_INK40}")

    if on_change:
        # Keep the button as a manual trigger for backward compat, but
        # the checkbox on_change (above) now fires immediately when
        # toggled — no need to click "Update Selection" separately.
        ui.button("Update Selection", on_click=on_change).classes(f"mt-2 {C_SURFACE} {C_CREAM}")

    return checkboxes


async def update_session_corpora(
    api_client: Any,
    session_id: str,
    active_corpus_ids: list[str],
    branham_allowlist: bool = False,
) -> bool:
    """Update the session's active corpora via the API.

    QW8 (2026-07-23): uses ``api_client.update_session_corpora()`` which
    PATCHes ``active_corpus_ids`` into the session metadata via the existing
    ``PATCH /api/v1/sessions/{id}`` endpoint. The ``branham_allowlist``
    parameter is kept for backward-compat but is handled by the session
    binding helpers (``session_corpus_binding.py``) based on whether
    ``allowed_restricted_corpora`` includes the sensitive corpus id.

    Returns True on success, False on error.
    """
    try:
        await api_client.update_session_corpora(session_id, active_corpus_ids)
        return True
    except Exception as exc:
        logger.warning("update_session_corpora_failed error=%s", exc)
        return False
