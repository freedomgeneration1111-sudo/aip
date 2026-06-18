"""Shared augmented-context retrieval helper.

Extracted from ``routes/chat.py`` L225-441 (the inline ~220-line retrieval
block) so that BOTH the WebSocket chat route AND the Multi-Cast model council
route can call the same retrieval pipeline. This is the Phase 1 retrieval
bridge fix for the AIP-acronym bug documented in the Fusion for AIP
Multimodel Synthesis report (Part I + Part VI).

Layer discipline: this module lives in ``adapter/api/routes/`` alongside
``chat.py`` and ``model_council.py``. It imports only from ``adapter`` and
``foundation``, matching the existing route module pattern. Store access
is through the container's Protocol interfaces — no new orchestration
imports.

Contract (producer → consumers):
  - ``AugmentedContext.messages``    → ``list[dict]`` of system msgs to PREPEND
  - ``AugmentedContext.sources``     → ``list[dict]`` for the response payload
  - ``AugmentedContext.source_turn_ids`` → ``list[str]`` for the auto-save
    ingestion path (propagates provenance to Vigil)
  - ``AugmentedContext.trace``       → ``RetrievalTrace | None``
  - ``AugmentedContext.domain``      → ``str | None``
  - ``AugmentedContext.assembled``   → ``bool`` (False = caller proceeds bare)

When ``assembled=False`` (no stores, or retrieval raised), ``messages`` is
empty and the caller proceeds with the bare prompt — current Multi-Cast
behavior. The helper NEVER raises; all exceptions are logged and degraded
to ``assembled=False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aip.adapter.api.routes.sessions import get_session_meta
from aip.logging import get_logger

logger = get_logger(__name__)


# ── Result dataclass ────────────────────────────────────────────────────


@dataclass
class AugmentedContext:
    """Result of assembling augmented context for a chat turn.

    Attributes:
        messages: list of system-message dicts to PREPEND to the
            user message before model dispatch. Empty list when no
            augmented context was assembled (e.g., normal mode, no
            corpus hits, retrieval failure).
        sources: list of source dicts for the response payload.
            Each dict has source_id, source_type, title, score,
            content_snippet, domain. Empty when no sources were found.
        source_turn_ids: list of turn_ids from corpus_turn sources.
            Used by the auto-save ingestion path to propagate
            provenance to Vigil. Empty for the orchestrator path
            (SourceReference objects don't carry turn_id) and for
            the no-sources path.
        trace: RetrievalTrace | None. Populated when the
            RetrievalOrchestrator fallback ran; None when
            corpus turn search succeeded directly.
        domain: the resolved domain string (or None).
        assembled: bool — True if retrieval ran at all, False if
            the caller was in normal mode or retrieval was
            skipped (e.g., container missing stores, or retrieval
            raised an exception that was gracefully degraded).
    """

    messages: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    source_turn_ids: list[str] = field(default_factory=list)
    trace: Any = None
    domain: str | None = None
    assembled: bool = False


# ── Retrieval helpers (moved from chat.py) ──────────────────────────────
#
# These four helpers were previously inline in ``routes/chat.py``. They
# are moved here so both ``chat.py`` and ``model_council.py`` can share
# them via the ``assemble_augmented_context()`` function below. ``chat.py``
# re-exports them for backward compatibility (no external consumer imports
# them today, but the re-export keeps the public surface stable).


async def _get_graph_neighbors(domain: str, container: Any = None) -> list[str]:
    """Return domain neighbors from the knowledge graph.

    Uses the container's graph_store when available. Falls back to
    creating one from container config db_path (matching the pattern
    in routes/graph.py). This ensures consistent path resolution
    across all graph-accessing routes.

    BUG-002: Previously used a separate db_path resolution that could
    diverge from the one used in routes/graph.py. Now reuses the same
    container.config.get("db_path") / config.get("database") pattern
    with get_default_db_path() fallback.
    """
    try:
        store = getattr(container, "graph_store", None) if container is not None else None
        if store is None:
            from aip.adapter.graph_store import GraphStore

            db_path = ""
            if container is not None:
                db_path = container.config.get("db_path", "") or container.config.get("database", {}).get("db_path", "")
            if not db_path:
                try:
                    from aip.cli._db_path import get_default_db_path

                    db_path = get_default_db_path()
                except Exception:
                    db_path = "db/state.db"
            store = GraphStore(db_path, config=getattr(container, "config", None))
            await store.initialize()
        neighbors = await store.get_neighbors(domain, min_confidence=0.4)
        return [n.canonical_name for n in neighbors if n.id != domain]
    except Exception:
        return []


async def _get_wiki_overview(domain: str, artifact_store: Any, ecs_store: Any) -> str | None:
    """Return wiki overview_text for domain from APPROVED (fallback GENERATED) artifact.

    Returns None if no wiki exists. Never raises.
    """
    try:
        arts = await artifact_store.list_artifacts_by_metadata(key="artifact_type", value="beast_wiki", limit=200)
        domain_arts = [a for a in arts if (a.get("metadata", {}) or {}).get("domain") == domain]
        if not domain_arts:
            return None
        domain_arts.sort(key=lambda a: a.get("created_at", ""), reverse=True)

        # Prefer APPROVED, fall back to GENERATED
        approved_overview = None
        generated_overview = None
        for art in domain_arts:
            aid = art.get("id", "")
            if not aid:
                continue
            try:
                state = await ecs_store.current_state(aid)
            except Exception:
                state = None
            overview = (art.get("metadata", {}) or {}).get("overview_text", "")
            if state == "APPROVED" and overview and approved_overview is None:
                approved_overview = overview
            elif state == "GENERATED" and overview and generated_overview is None:
                generated_overview = f"[Draft] {overview}"
        return approved_overview or generated_overview
    except Exception:
        return None


async def _search_corpus_turns(
    query: str,
    corpus_turn_store: Any,
    domain: str | None = None,
    limit: int = 8,
    min_importance: float = 0.3,
    container: Any = None,
) -> list[dict]:
    """Search corpus turns via FTS5 and return formatted source dicts."""
    try:
        _sanitize_fn = container._sanitize_fts_query_fn if container else None
        if _sanitize_fn:
            fts_query = _sanitize_fn(query)
        else:
            fts_query = query
    except Exception:
        fts_query = query
    turns = await corpus_turn_store.search(
        query=fts_query,
        primary_domain=domain,
        min_importance=min_importance,
        limit=limit,
    )
    return [
        {
            "source_id": f"corpus:{t.turn_id[:12]}",
            "turn_id": t.turn_id,
            "user_text": t.user_text,
            "assistant_text": t.assistant_text,
            "content_preview": t.searchable_text[:500],
            "score": t.importance,
            "domain": t.primary_domain,
            "importance": t.importance,
            "conversation_name": t.conversation_name or "",
        }
        for t in turns
    ]


def _assemble_corpus_context(source_dicts: list[dict]) -> str:
    """Format corpus turns as Q/A pairs for model context."""
    if not source_dicts:
        return "No relevant corpus turns found."
    parts: list[str] = []
    for i, s in enumerate(source_dicts, 1):
        domain = s.get("domain") or "unknown"
        importance = float(s.get("importance") or 0.0)
        conv_name = (s.get("conversation_name") or "")[:40]
        user_text = (s.get("user_text") or "")[:200]
        assistant_text = (s.get("assistant_text") or "")[:400]
        parts.append(
            f"[Source {i}: {domain} | importance:{importance:.2f} | {conv_name}]\nQ: {user_text}\nA: {assistant_text}"
        )
    return "\n\n".join(parts)


# ── Main entry point ────────────────────────────────────────────────────


async def assemble_augmented_context(
    content: str,
    session_id: str,
    container: Any,
    *,
    session_meta: dict | None = None,
) -> AugmentedContext:
    """Assemble augmented context (corpus + wiki + graph + definer).

    Shared helper used by both ``routes/chat.py`` and
    ``routes/model_council.py``. Mirrors the inline block that lived
    at ``chat.py`` L225-441 before extraction. Behavior is identical
    to that block; this is a pure refactor.

    Args:
        content: the user's prompt text.
        session_id: the active session ID (for project/domain lookup).
        container: AipContainer — must expose corpus_turn_store,
            lexical_store, artifact_store, ecs_store, project_store,
            graph_store, definer_profile, config, and the
            _ask_stores_class / _search_sources_fn attributes.
        session_meta: optional session metadata dict (for domain
            and project_id hints). When None, the helper looks up
            session_meta via get_session_meta(session_id).

    Returns:
        AugmentedContext. The .assembled flag is False when:
          - container.corpus_turn_store is None AND
            container.lexical_store is None (no retrieval possible)
          - retrieval raised an exception (logged, graceful degrade)
        In those cases .messages is empty and the caller proceeds
        with the bare prompt (current Multi-Cast behavior).

    Never raises — all exceptions are caught, logged at WARNING level,
    and degraded to AugmentedContext(assembled=False).
    """
    # Short-circuit: no retrieval possible without at least one store.
    # The caller proceeds with the bare prompt (current Multi-Cast behavior).
    # ADR-008 Chunk 4: if corpus_registry is wired, retrieval is possible
    # even if legacy singletons are None.
    _has_registry = getattr(container, "corpus_registry", None) is not None
    _has_legacy = (
        getattr(container, "corpus_turn_store", None) is not None
        or getattr(container, "lexical_store", None) is not None
    )
    if not _has_registry and not _has_legacy:
        return AugmentedContext(assembled=False)

    # Look up session_meta when not provided by the caller.
    if session_meta is None:
        try:
            session_meta = get_session_meta(session_id) or {}
        except Exception:
            session_meta = {}

    messages: list[dict] = []
    response_sources: list[dict] = []
    source_turn_ids: list[str] = []
    ret_trace: Any = None

    try:
        # ── Definer profile injection ──────────────────────────────
        try:
            definer_cfg = getattr(container, "config", {}) or {}
            dcfg = definer_cfg.get("definer", {}) if isinstance(definer_cfg, dict) else {}
            if dcfg.get("inject_in_augmented_chat", True):
                dp = getattr(container, "definer_profile", None)
                if dp is not None:
                    block = dp.get_injection_block(max_tokens_estimate=dcfg.get("max_profile_tokens", 800))
                    if block:
                        messages.append({"role": "system", "content": block})
        except Exception as exc:
            logger.warning("definer_profile_injection_failed", error=str(exc))

        # ── Domain resolution from session or project ─────────────
        domain = (session_meta or {}).get("domain")
        project_id = (session_meta or {}).get("project_id")
        if project_id and getattr(container, "project_store", None) is not None:
            try:
                projects = await container.project_store.list_projects()
                for p in projects:
                    if p.get("project_id") == project_id or p.get("name") == project_id:
                        domain = p.get("domain") or domain
                        break
            except Exception:
                logger.warning("project_lookup_failed", exc_info=True)

        # ── Corpus turn retrieval ─────────────────────────────────
        # ADR-008 Rev 3.1 Chunk 4: multi-corpus path when active_corpus_ids
        # is in session_meta AND corpus_registry is wired. Falls back to
        # legacy single-corpus path otherwise.
        corpus_turns_used = False
        source_dicts: list[dict] = []

        active_corpus_ids = (session_meta or {}).get("active_corpus_ids")
        registry = getattr(container, "corpus_registry", None)

        if active_corpus_ids and registry is not None:
            # Multi-corpus path (§4, §A12)
            from aip.adapter.corpus_retrieval import gather_corpus_results

            branham_allowlist = (session_meta or {}).get("branham_allowlist", False)
            audit_fn = getattr(registry, "_write_audit", None)
            multi_hits, _suppressed = await gather_corpus_results(
                query=content,
                active_corpus_ids=active_corpus_ids,
                container=container,
                session_branham_allowlist=branham_allowlist,
                audit_fn=audit_fn,
            )
            source_dicts = multi_hits
            if source_dicts:
                corpus_turns_used = True
        elif getattr(container, "corpus_turn_store", None) is not None:
            # Legacy single-corpus path
            source_dicts = await _search_corpus_turns(
                query=content,
                corpus_turn_store=container.corpus_turn_store,
                domain=domain,
                limit=8,
                min_importance=0.3,
                container=container,
            )
            if source_dicts:
                corpus_turns_used = True
            else:
                logger.info(
                    "corpus_turn_search_empty_fallback",
                    query_len=len(content),
                    domain=domain,
                )

        # ── Orchestrator fallback (RRF over lexical + vector) ─────
        source_refs: list = []
        packed_ctx = None
        if not corpus_turns_used and getattr(container, "lexical_store", None) is not None:
            AskStores = getattr(container, "_ask_stores_class", None)
            _search_sources_fn = getattr(container, "_search_sources_fn", None)
            if AskStores is not None and _search_sources_fn is not None:
                _ask_stores = AskStores(
                    artifact_store=container.artifact_store,
                    lexical_store=container.lexical_store,
                    vector_store=container.vector_store,
                    event_store=container.event_store,
                    project_store=container.project_store,
                    ecs_store=container.ecs_store,
                    embedding_provider=container.embedding_provider,
                    corpus_turn_store=container.corpus_turn_store,
                    graph_store=getattr(container, "graph_store", None),
                )
                source_refs, ret_trace, packed_ctx = await _search_sources_fn(
                    query=content,
                    stores=_ask_stores,
                    source_filter="all",
                    max_sources=10,
                )

        # ── Determine active domain for wiki/graph ────────────────
        if corpus_turns_used and source_dicts:
            query_domain: str | None = source_dicts[0].get("domain") or domain
        elif source_refs:
            query_domain = source_refs[0].domain
        else:
            query_domain = domain

        has_sources = bool(source_dicts or source_refs)

        if has_sources:
            # ── Wiki overview injection ────────────────────────────
            try:
                if (
                    query_domain
                    and getattr(container, "artifact_store", None) is not None
                    and getattr(container, "ecs_store", None) is not None
                ):
                    wiki_overview = await _get_wiki_overview(
                        query_domain,
                        container.artifact_store,
                        container.ecs_store,
                    )
                    if wiki_overview:
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    f"=== DOMAIN CONTEXT: {query_domain} ===\n"
                                    f"{wiki_overview}\n"
                                    f"=== END DOMAIN CONTEXT ==="
                                ),
                            }
                        )
            except Exception as _wiki_exc:
                logger.debug("wiki_overview_injection_failed", error=str(_wiki_exc))

            # ── Graph connections injection ────────────────────────
            try:
                if query_domain:
                    graph_neighbors = await _get_graph_neighbors(query_domain, container=container)
                    if graph_neighbors:
                        neighbors_str = ", ".join(graph_neighbors[:5])
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    f"=== GRAPH CONNECTIONS ===\n"
                                    f"Domain '{query_domain}' connects to: {neighbors_str}\n"
                                    f"These domains may provide relevant context.\n"
                                    f"=== END GRAPH CONNECTIONS ==="
                                ),
                            }
                        )
            except Exception as _graph_exc:
                logger.debug("graph_neighbors_injection_failed", error=str(_graph_exc))

            # ── Sources injection ──────────────────────────────────
            if corpus_turns_used:
                context = _assemble_corpus_context(source_dicts)
                response_sources = [
                    {
                        "source_id": s["source_id"],
                        "source_type": "corpus_turn",
                        "title": (s["conversation_name"][:60] or s["domain"]),
                        "score": s["score"],
                        "content_snippet": (s.get("user_text") or s["content_preview"])[:200],
                        "domain": s["domain"],
                    }
                    for s in source_dicts
                ]
                # Capture turn_ids for the auto-save path (provenance → Vigil)
                source_turn_ids = [s["turn_id"] for s in source_dicts if s.get("turn_id")]
            else:
                # Use SmartContextPacker output
                context = packed_ctx.context_text if packed_ctx else "No relevant sources found."
                response_sources = [
                    {
                        "source_id": s.source_id,
                        "source_type": s.source_type,
                        "title": s.title,
                        "score": s.score,
                        "content_snippet": s.content_snippet,
                        "domain": s.domain,
                    }
                    for s in source_refs
                ]

            messages.append(
                {
                    "role": "system",
                    "content": f"Corpus turns retrieved from knowledge base:\n\n{context}",
                }
            )

            # ── Synthesis instruction ──────────────────────────────
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "You are AIP, a source-grounded knowledge assistant "
                        "for B. Moses Jorgensen. "
                        "Answer based on the provided corpus turns. "
                        "Cite sources using [source: turn_id] notation. "
                        "Draw on the DEFINER profile and domain context above. "
                        "If sources don't contain enough information, say so explicitly."
                    ),
                }
            )
        else:
            # ── No sources found ───────────────────────────────────
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "You are AIP, a knowledge assistant for B. Moses Jorgensen. "
                        "No relevant sources were found in the knowledge base for this query. "
                        "Answer based on your general knowledge but note "
                        "that no source material was available."
                    ),
                }
            )

        return AugmentedContext(
            messages=messages,
            sources=response_sources,
            source_turn_ids=source_turn_ids,
            trace=ret_trace,
            domain=query_domain,
            assembled=True,
        )

    except Exception as exc:
        logger.warning("augmented_retrieval_failed", error=str(exc))
        return AugmentedContext(assembled=False)
