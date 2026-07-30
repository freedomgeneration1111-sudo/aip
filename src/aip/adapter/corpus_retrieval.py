"""Multi-corpus retrieval helpers — ADR-008 Rev 3.1 Chunk 4.

Provides the building blocks for cross-corpus retrieval:
  - Hit ID namespacing: `{corpus_id}:{hit_id}` so RRF never collapses
    cross-corpus hits.
  - Fusion-layer ECS filter (§A2): excludes ARCHIVED/SUPERSEDED turns from
    retrieval results AFTER channel retrieval but BEFORE RRF fusion. This
    is the channel-agnostic guarantee — it catches leaks from lexical and
    vector channels that don't join corpus_turns and would otherwise return
    archived content.
  - Cache key: SHA256 of (query, sorted(active_corpus_ids), model_id) so
    different corpus selections get different cache entries.
  - Multi-corpus fan-out: asyncio.gather with return_exceptions=True so a
    RestrictedCorpusAccessViolation on one corpus doesn't abort the others.

Layer: adapter. Imports from foundation and adapter. Consumed by
routes/_augmented_context.py.

ADR-008 Rev 3.1 §A2, §4, Amendment §A12.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from aip.foundation.corpus_exceptions import RestrictedCorpusAccessViolation
from aip.foundation.corpus_types import RETRIEVAL_EXCLUDED_STATES

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hit ID namespacing — ADR-008 Rev 3.1 §4
# ---------------------------------------------------------------------------


def namespace_hit_id(corpus_id: str, hit_id: str) -> str:
    """Namespacer: `{corpus_id}:{hit_id}`.

    Ensures RRF deduplication correctly identifies same-corpus hits and
    cross-corpus hits are never collapsed. The corpus_id is the prefix
    before the first colon; the hit_id is everything after.
    """
    return f"{corpus_id}:{hit_id}"


def parse_hit_id(namespaced_id: str) -> tuple[str, str]:
    """Parse a namespaced hit ID back into (corpus_id, hit_id).

    Uses partition on the first colon so hit_ids containing colons are
    preserved. Returns ("", namespaced_id) if no colon is present.
    """
    corpus_id, sep, hit_id = namespaced_id.partition(":")
    if not sep:
        return ("", namespaced_id)
    return (corpus_id, hit_id)


# ---------------------------------------------------------------------------
# Cache key — ADR-008 Rev 3.1 §4
# ---------------------------------------------------------------------------


def corpus_aware_cache_key(
    query: str,
    corpus_ids: list[str],
    model_id: str = "",
) -> str:
    """SHA256 cache key including sorted active_corpus_ids + model_id.

    Different corpus selections get different cache entries. Old cache keys
    (without corpus_ids) must be invalidated on deployment.
    """
    sorted_corpora = sorted(corpus_ids) if corpus_ids else ["definer"]
    raw = f"{query}::{':'.join(sorted_corpora)}::{model_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fusion-layer ECS filter — ADR-008 Rev 3.1 §A2
# ---------------------------------------------------------------------------


async def filter_excluded_states(
    hits: list[dict],
    turn_store: Any,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Filter out ARCHIVED/SUPERSEDED turns from retrieval hits.

    ADR-008 Rev 3.1 §A2: the fusion-layer filter is the GUARANTEE that
    archived/superseded content doesn't leak through lexical or vector
    channels (which don't join corpus_turns and would otherwise return
    archived content). The search()-level filter on CorpusTurnStore is a
    fast-path for the corpus channel; this filter is the channel-agnostic
    backstop.

    Args:
        hits: list of hit dicts. Each hit must have a "turn_id" key
            (hits without turn_id are passed through unchanged — they're
            not corpus turns).
        turn_store: CorpusTurnStore with states_for() method.
        include_archived: if True, pass all hits through (for history queries).

    Returns:
        Filtered list of hits with ARCHIVED/SUPERSEDED turns removed.
    """
    if include_archived or not hits:
        return hits

    # Collect turn_ids from hits that have one
    turn_ids: list[str] = []
    for hit in hits:
        tid = hit.get("turn_id")
        if tid:
            turn_ids.append(tid)

    if not turn_ids:
        return hits

    # Batch lookup latest_ecs_state
    try:
        states = await turn_store.states_for(turn_ids)
    except Exception as exc:
        logger.warning("fusion_filter_states_for_failed error=%s", exc)
        return hits  # fail open — don't block retrieval on filter failure

    # Filter out excluded states
    filtered: list[dict] = []
    for hit in hits:
        tid = hit.get("turn_id")
        if not tid:
            filtered.append(hit)  # non-turn hit, pass through
            continue
        state = states.get(tid, "GENERATED")  # default to GENERATED if not found
        if state not in RETRIEVAL_EXCLUDED_STATES:
            filtered.append(hit)
        else:
            logger.debug("fusion_filter_excluded turn_id=%s state=%s", tid, state)

    return filtered


# ---------------------------------------------------------------------------
# Multi-corpus fan-out — ADR-008 Rev 3.1 §4, Amendment §A12
# ---------------------------------------------------------------------------


async def gather_corpus_results(
    query: str,
    active_corpus_ids: list[str],
    container: Any,
    *,
    allowed_restricted_corpora: list[str] | None = None,
    session_branham_allowlist: bool | None = None,  # deprecated
    audit_fn: Any = None,
) -> tuple[list[dict], list[Exception]]:
    """Fan out retrieval across active corpora, graceful on restricted-corpus denial.

    ADR-008 Rev 3.1 Amendment §A12: uses asyncio.gather with
    return_exceptions=True so a RestrictedCorpusAccessViolation on one corpus
    doesn't abort the others. Non-restricted exceptions are re-raised.

    Args:
        query: the search query.
        active_corpus_ids: list of corpus_ids to search.
        container: AipContainer with corpus_registry.
        allowed_restricted_corpora: session-level opt-in list for sensitive corpora.
        session_branham_allowlist: DEPRECATED — if True, adds "branham" to
            allowed_restricted_corpora for backward compat.
        audit_fn: optional async callback for RESTRICTED_CORPUS_ACCESS_DENIED audit.

    Returns:
        (hits, exceptions) where hits is a list of namespaced hit dicts
        and exceptions is a list of non-fatal exceptions (RestrictedCorpusAccessViolation
        only — other exceptions are re-raised).
    """
    if not active_corpus_ids:
        return ([], [])

    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return ([], [])

    # Build effective allowed list (handle deprecated alias)
    effective_allowed: list[str] = list(allowed_restricted_corpora or [])
    if session_branham_allowlist:
        if "branham" not in effective_allowed:
            effective_allowed.append("branham")

    # Build per-corpus search coroutines
    async def _search_one_corpus(cid: str) -> list[dict]:
        stores = await registry.get_stores(cid, allowed_restricted_corpora=effective_allowed)
        if stores.turn_store is None:
            return []

        # Search the corpus — include_archived=False by default.
        # Per-corpus errors (e.g. FTS5 syntax errors from unsanitized
        # queries) are caught and logged so one bad corpus doesn't
        # kill the entire multi-corpus retrieval.
        from aip.adapter.api.routes._augmented_context import _search_corpus_turns

        try:
            source_dicts = await _search_corpus_turns(
                query=query,
                corpus_turn_store=stores.turn_store,
                domain=None,
                limit=8,
                min_importance=0.3,
                container=container,
            )
        except Exception as exc:
            logger.warning(
                "corpus_search_failed corpus_id=%s error=%s",
                cid, exc,
            )
            return []

        # Apply fusion-layer ECS filter (§A2)
        source_dicts = await filter_excluded_states(source_dicts, stores.turn_store, include_archived=False)

        # Namespace hit IDs
        for d in source_dicts:
            d["corpus_id"] = cid
            if "turn_id" in d and d["turn_id"]:
                d["namespaced_id"] = namespace_hit_id(cid, d["turn_id"])
            if "source_id" in d:
                d["source_id"] = namespace_hit_id(cid, d["source_id"].split(":")[-1])

        return source_dicts

    # Fan out with return_exceptions=True (§A12)
    results = await asyncio.gather(
        *[_search_one_corpus(cid) for cid in active_corpus_ids],
        return_exceptions=True,
    )

    all_hits: list[dict] = []
    suppressed: list[Exception] = []

    for cid, result in zip(active_corpus_ids, results):
        if isinstance(result, RestrictedCorpusAccessViolation):
            # Suppressed — audit and continue
            suppressed.append(result)
            if audit_fn is not None:
                try:
                    await audit_fn(
                        action="RESTRICTED_CORPUS_ACCESS_DENIED",
                        corpus_id=cid,
                        outcome="DENIED",
                    )
                except Exception:
                    pass
            logger.info("branham_isolation_suppressed corpus=%s", cid)
        elif isinstance(result, Exception):
            # Non-Branham exception — re-raise (per §A12)
            raise result
        else:
            all_hits.extend(result)

    return (all_hits, suppressed)


# Late import to avoid circular dependency at module load
import asyncio  # noqa: E402
