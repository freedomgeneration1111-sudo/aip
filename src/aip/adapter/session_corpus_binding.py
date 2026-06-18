"""Session-corpus binding helpers — ADR-008 Rev 3.1 Chunk 5.

Provides helpers to read/write active_corpus_ids and branham_allowlist in
the session metadata_json. The session store already handles arbitrary keys
in metadata_json (sqlite_session_store.py update_session() puts unknown keys
there), so these helpers are thin wrappers that enforce the ADR-008 policy:

  - active_corpus_ids: defaults to ["definer"] when not set
  - branham_allowlist: NEVER persisted when branham_policy_enabled=False on
    the registry (prevents allowlist escalation via session replay — §5)

Layer: adapter. Imports from foundation and adapter. Consumed by routes/
sessions.py and the GUI corpus selector.

ADR-008 Rev 3.1 §5, §3.4 (Branham 4-layer defense Layer 2).
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


def get_branham_allowlist(session_meta: dict | None) -> bool:
    """Read branham_allowlist from session metadata.

    Returns False when not set. This is the session-level opt-in (Layer 2
    of the 4-layer Branham defense). The registry's Layer 3 check in
    get_stores() enforces it.
    """
    if not session_meta:
        return False
    return bool(session_meta.get("branham_allowlist", False))


def build_session_meta_update(
    active_corpus_ids: list[str] | None,
    branham_allowlist: bool,
    *,
    branham_policy_enabled: bool,
) -> dict:
    """Build a session metadata update dict that enforces Branham policy.

    ADR-008 Rev 3.1 §5: branham_allowlist is NEVER written when
    branham_policy_enabled=False. This prevents allowlist escalation via
    session replay — an attacker who captures a session with allowlist=True
    can't replay it after policy is disabled.

    Args:
        active_corpus_ids: the corpora to activate. If None, the field is
            not updated (preserves existing value). If empty list, defaults
            to ["definer"].
        branham_allowlist: whether to allow Branham access for this session.
        branham_policy_enabled: if False, branham_allowlist is stripped from
            the update (never persisted).

    Returns:
        A dict suitable for passing to session_store.update_session().
        Keys are active_corpus_ids and (optionally) branham_allowlist.
    """
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

    # branham_allowlist is only persisted when policy is enabled
    if branham_policy_enabled:
        update["branham_allowlist"] = branham_allowlist
    else:
        # Explicitly strip it — if it was previously True and policy is
        # now disabled, clear it so the session can't replay the allowlist.
        update["branham_allowlist"] = False

    return update


def is_sensitive_corpus(corpus_id: str, registry: Any) -> bool:
    """Check if a corpus is sensitive (has branham_policy_enabled=True).

    Used by the GUI corpus selector to show sensitive corpora with a
    confirmation prompt and to filter them out when policy is disabled.
    """
    if registry is None:
        return False
    try:
        # Check the registry's _corpora dict for the _branham_policy_enabled flag
        stores = registry._corpora.get(corpus_id)
        if stores is None:
            return False
        return getattr(stores, "_branham_policy_enabled", False)
    except Exception:
        return False
