"""Session-corpus binding helpers — ADR-008 Rev 3.1 Chunk 5.

Provides helpers to read/write active_corpus_ids and allowed_restricted_corpora
in the session metadata_json. The session store already handles arbitrary keys
in metadata_json (sqlite_session_store.py update_session() puts unknown keys
there), so these helpers are thin wrappers that enforce the ADR-008 policy:

  - active_corpus_ids: defaults to ["definer"] when not set
  - allowed_restricted_corpora: list of corpus_ids the session has opted into.
    NEVER persisted when restricted_policy_enabled=False on the registry
    (prevents escalation via session replay — §5). This is the GENERIC
    replacement for the old branham_allowlist boolean — any sensitive corpus
    can be in the list, not just "branham".

Layer: adapter. Imports from foundation and adapter. Consumed by routes/
sessions.py and the GUI corpus selector.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default active corpora when none are specified — always the definer.
DEFAULT_ACTIVE_CORPUS_IDS: list[str] = ["definer"]


def get_active_corpus_ids(session_meta: dict | None) -> list[str]:
    """Read active_corpus_ids from session metadata.

    Returns ["definer"] when not set or empty. The definer corpus is always
    available; other corpora must be explicitly activated by the session.
    """
    if not session_meta:
        return list(DEFAULT_ACTIVE_CORPUS_IDS)
    ids = session_meta.get("active_corpus_ids")
    if not ids or not isinstance(ids, list):
        return list(DEFAULT_ACTIVE_CORPUS_IDS)
    # Ensure definer is always present (it's the anchor for bridge edges
    # and the review queue fan-in)
    if "definer" not in ids:
        ids = ["definer"] + ids
    return ids


def get_allowed_restricted_corpora(session_meta: dict | None) -> list[str]:
    """Read allowed_restricted_corpora from session metadata.

    Returns [] when not set. This is the session-level opt-in (Layer 2
    of the 4-layer restricted-corpus defense). Any sensitive corpus whose
    corpus_id is in this list is accessible; others raise
    RestrictedCorpusAccessViolation at get_stores().

    Backward compat: also reads the old "branham_allowlist" boolean — if
    True, adds "branham" to the list.
    """
    if not session_meta:
        return []
    allowed = session_meta.get("allowed_restricted_corpora", [])
    if not isinstance(allowed, list):
        allowed = []
    # Backward compat: old branham_allowlist boolean
    if session_meta.get("branham_allowlist", False):
        if "branham" not in allowed:
            allowed = list(allowed) + ["branham"]
    return allowed


def get_branham_allowlist(session_meta: dict | None) -> bool:
    """Read branham_allowlist from session metadata.

    DEPRECATED — use get_allowed_restricted_corpora() instead. Kept for
    backward compat with existing callers. Returns True if "branham" is
    in allowed_restricted_corpora OR the old branham_allowlist flag is True.
    """
    return "branham" in get_allowed_restricted_corpora(session_meta)


def build_session_meta_update(
    active_corpus_ids: list[str] | None,
    allowed_restricted_corpora: list[str],
    *,
    restricted_policy_enabled: bool,
) -> dict:
    """Build a session metadata update dict that enforces restricted-corpus policy.

    ADR-008 Rev 3.1 §5: allowed_restricted_corpora is NEVER written when
    restricted_policy_enabled=False. This prevents escalation via session
    replay — an attacker who captures a session with allowed_restricted_corpora
    set can't replay it after policy is disabled.

    Args:
        active_corpus_ids: the corpora to activate. If None, the field is
            not updated (preserves existing value). If empty list, defaults
            to ["definer"].
        allowed_restricted_corpora: list of sensitive corpus_ids the session
            is allowed to access. Cleared to [] when policy is disabled.
        restricted_policy_enabled: if False, allowed_restricted_corpora is
            stripped from the update (never persisted).

    Returns:
        A dict suitable for passing to session_store.update_session().
    """
    # Handle deprecated alias

    update: dict = {}

    if active_corpus_ids is not None:
        if not active_corpus_ids:
            update["active_corpus_ids"] = list(DEFAULT_ACTIVE_CORPUS_IDS)
        else:
            # Ensure definer is present
            ids = list(active_corpus_ids)
            if "definer" not in ids:
                ids = ["definer"] + ids
            update["active_corpus_ids"] = ids

    # allowed_restricted_corpora is only persisted when policy is enabled
    if restricted_policy_enabled:
        update["allowed_restricted_corpora"] = list(allowed_restricted_corpora)
        # Clear the old branham_allowlist flag to avoid stale state
        update["branham_allowlist"] = "branham" in allowed_restricted_corpora
    else:
        # Explicitly strip it — if it was previously set and policy is
        # now disabled, clear it so the session can't replay the allowlist.
        update["allowed_restricted_corpora"] = []
        update["branham_allowlist"] = False

    return update


def is_sensitive_corpus(corpus_id: str, registry: Any) -> bool:
    """Check if a corpus is sensitive (has sensitive=True).

    Used by the GUI corpus selector to show sensitive corpora with a
    confirmation prompt and to filter them out when policy is disabled.
    """
    if registry is None:
        return False
    try:
        stores = registry._corpora.get(corpus_id)
        if stores is None:
            return False
        return getattr(stores, "_sensitive", False)
    except Exception:
        return False


def get_access_note(corpus_id: str, registry: Any) -> str:
    """Get the access_note for a sensitive corpus.

    Used by the GUI to show the confirmation dialog text. Returns empty
    string if the corpus isn't sensitive or has no access_note.
    """
    if registry is None:
        return ""
    try:
        stores = registry._corpora.get(corpus_id)
        if stores is None:
            return ""
        return getattr(stores, "_access_note", "")
    except Exception:
        return ""
