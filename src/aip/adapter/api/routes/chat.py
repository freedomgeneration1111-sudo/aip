"""Chat WebSocket surface.

WebSocket chat endpoint at /api/v1/chat/{session_id}

Augmented mode routes through retrieval + context injection before model
dispatch, producing source-grounded answers. Sources are included in the
response payload for citation display. Normal mode dispatches directly.

Auto-save hooks trigger ingestion after each completed chat turn.
Ingestion is non-blocking: the response is sent immediately, ingestion
runs as a background task. Session auto_save flag controls whether
ingestion fires (default: True). Trajectory regulation checks run after
each turn when SessionManager is available.

Message flow (normal): message → model dispatch → response → [auto-save]
Message flow (augmented): message → retrieve sources → assemble context → model dispatch
→ response + sources → [auto-save]
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aip.adapter.api.dependencies import get_container
from aip.adapter.api.routes.sessions import get_session_meta, increment_turn_count
from aip.foundation.schemas.corpus_turn import make_turn_id
from aip.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


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


@router.websocket("/chat/{session_id}")
async def chat_websocket(websocket: WebSocket, session_id: str):
    """WebSocket chat endpoint with DEFINER gate handling and ModelSlotResolver routing.

    The GUI connects to this endpoint after creating a session via POST /api/v1/sessions.
    Each message is routed through the configured model slot (from session metadata),
    allowing the backend's ModelSlotResolver to dispatch to the appropriate provider.
    """
    await websocket.accept()
    logger.info("chat_ws_connected", session=session_id)

    _container = get_container(websocket)  # type: ignore  # in real lifespan context

    # Look up session metadata to determine which model slot to use
    session_meta = get_session_meta(session_id)
    model_slot = "synthesis"  # default
    session_mode = "normal"  # default
    auto_save_enabled = True  # default — sessions created with auto_save=True
    if session_meta:
        model_slot = session_meta.get("model_slot", "synthesis")
        session_mode = session_meta.get("mode", "normal")
        auto_save_enabled = session_meta.get("auto_save", True)
    logger.info(
        "chat_ws_session",
        session=session_id,
        slot=model_slot,
        mode=session_mode,
        model_provider="yes" if _container.model_provider is not None else "NONE",
    )

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                await websocket.send_json({"type": "error", "content": "invalid json"})
                continue

            if msg.get("type") == "message":
                content = msg.get("content", "")
                # Allow per-message slot override
                override_slot = msg.get("model_slot")
                effective_slot = override_slot or model_slot
                logger.info(
                    "chat_message_received",
                    slot=effective_slot,
                    content_len=len(content),
                    session=session_id,
                    mode=session_mode,
                )

                # Route through ModelSlotResolver if available
                model_provider = _container.model_provider
                if model_provider is not None:
                    try:
                        # Build messages list for the model call
                        # Include system context from session if available
                        messages = []
                        response_sources = []  # Sources for augmented mode
                        ret_trace = None  # Retrieval trace metadata (populated in augmented mode)

                        if session_mode == "augmented" and (
                            _container.corpus_turn_store is not None or _container.lexical_store is not None
                        ):
                            # Definer profile injection
                            try:
                                # Use the full app config — same pattern as ask pipeline.
                                # _container.config is set in AipContainer.__init__ from the
                                # full TOML dict, but fall back to app.state.raw_config if empty
                                # (e.g. test mode without lifespan).
                                definer_cfg = getattr(_container, "config", {}) or {}
                                if not definer_cfg:
                                    definer_cfg = getattr(websocket.app.state, "raw_config", {}) or {}
                                logger.debug(
                                    "chat_route_config",
                                    keys=list(definer_cfg.keys())[:10]
                                    if isinstance(definer_cfg, dict)
                                    else "not-a-dict",
                                )
                                dcfg = definer_cfg.get("definer", {}) if isinstance(definer_cfg, dict) else {}
                                if dcfg.get("inject_in_augmented_chat", True):
                                    dp = getattr(_container, "definer_profile", None)
                                    if dp is not None:
                                        block = dp.get_injection_block(
                                            max_tokens_estimate=dcfg.get("max_profile_tokens", 800)
                                        )
                                        if block:
                                            messages.append({"role": "system", "content": block})
                            except Exception as exc:
                                logger.warning("definer_profile_injection_failed", error=str(exc))

                            # Augmented mode: retrieve sources and inject context
                            try:
                                # Access orchestration through container (layer discipline)
                                AskStores = _container._ask_stores_class
                                _search_sources_fn = _container._search_sources_fn
                                if AskStores is None or _search_sources_fn is None:
                                    raise RuntimeError("Retrieval pipeline not available via container")

                                # Resolve domain from session or project
                                domain = (session_meta or {}).get("domain")
                                project_id = (session_meta or {}).get("project_id")
                                if project_id and _container.project_store is not None:
                                    try:
                                        projects = await _container.project_store.list_projects()
                                        for p in projects:
                                            if p.get("project_id") == project_id or p.get("name") == project_id:
                                                domain = p.get("domain") or domain
                                                break
                                    except Exception:
                                        logger.warning("project_lookup_failed", exc_info=True)

                                # Corpus turn retrieval
                                corpus_turns_used = False
                                source_dicts: list[dict] = []
                                if _container.corpus_turn_store is not None:
                                    source_dicts = await _search_corpus_turns(
                                        query=content,
                                        corpus_turn_store=_container.corpus_turn_store,
                                        domain=domain,
                                        limit=8,
                                        min_importance=0.3,
                                        container=_container,
                                    )
                                    if source_dicts:
                                        corpus_turns_used = True
                                    else:
                                        logger.info(
                                            "corpus_turn_search_empty_fallback",
                                            query_len=len(content),
                                            domain=domain,
                                        )

                                # Fallback: orchestrator pipeline
                                source_refs: list = []
                                packed_ctx = None
                                if not corpus_turns_used and _container.lexical_store is not None:
                                    _ask_stores = AskStores(
                                        artifact_store=_container.artifact_store,
                                        lexical_store=_container.lexical_store,
                                        vector_store=_container.vector_store,
                                        event_store=_container.event_store,
                                        project_store=_container.project_store,
                                        ecs_store=_container.ecs_store,
                                        embedding_provider=_container.embedding_provider,
                                        corpus_turn_store=_container.corpus_turn_store,
                                        graph_store=getattr(_container, "graph_store", None),
                                    )
                                    source_refs, ret_trace, packed_ctx = await _search_sources_fn(
                                        query=content,
                                        stores=_ask_stores,
                                        source_filter="all",
                                        max_sources=10,
                                    )

                                # Determine active domain for wiki/graph
                                if corpus_turns_used and source_dicts:
                                    query_domain: str | None = source_dicts[0].get("domain") or domain
                                elif source_refs:
                                    query_domain = source_refs[0].domain
                                else:
                                    query_domain = domain

                                has_sources = bool(source_dicts or source_refs)

                                if has_sources:
                                    # Wiki overview injection
                                    try:
                                        if (
                                            query_domain
                                            and _container.artifact_store is not None
                                            and _container.ecs_store is not None
                                        ):
                                            wiki_overview = await _get_wiki_overview(
                                                query_domain,
                                                _container.artifact_store,
                                                _container.ecs_store,
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

                                    # Graph connections injection
                                    try:
                                        if query_domain:
                                            graph_neighbors = await _get_graph_neighbors(
                                                query_domain, container=_container
                                            )
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

                                    # Sources injection
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
                                    else:
                                        # Use SmartContextPacker output
                                        context = (
                                            packed_ctx.context_text if packed_ctx else "No relevant sources found."
                                        )
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

                                    # Synthesis instruction
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
                            except Exception as exc:
                                logger.warning("augmented_retrieval_failed", error=str(exc))
                                if session_meta and session_meta.get("role"):
                                    role_hint = session_meta.get("role", "")
                                    if role_hint:
                                        messages.append(
                                            {
                                                "role": "system",
                                                "content": (
                                                    f"You are acting in the {role_hint} role. Respond accordingly."
                                                ),
                                            }
                                        )
                        else:
                            # Normal mode: direct model dispatch
                            if session_meta and session_meta.get("role"):
                                role_hint = session_meta.get("role", "")
                                if role_hint:  # only inject for explicit actor roles
                                    # (plain chat uses role=None; prevents Beast leak)
                                    messages.append(
                                        {
                                            "role": "system",
                                            "content": f"You are acting in the {role_hint} role. Respond accordingly.",
                                        }
                                    )

                        messages.append({"role": "user", "content": content})

                        # Budget check before model call
                        if _container.budget_manager is not None:
                            try:
                                budget_ok = await _container.budget_manager.check_before_call(
                                    scope="session",
                                    scope_id=session_id,
                                    estimated_tokens=2000,  # rough estimate per turn
                                )
                                if not budget_ok:
                                    await websocket.send_json(
                                        {
                                            "type": "error",
                                            "content": (
                                                "Budget limit reached. Session token budget "
                                                "has been exceeded. Consider starting a new session."
                                            ),
                                            "error_type": "budget_exhausted",
                                            "model_slot": effective_slot,
                                        }
                                    )
                                    continue
                            except Exception as exc:
                                # Budget check failure is non-critical — log and proceed
                                logger.warning("Budget check failed, proceeding", error=str(exc))

                        result = await model_provider.call(effective_slot, messages)

                        # Check for error from model provider
                        if result.get("error"):
                            error_msg = result.get("error_message", "Model call failed — provider returned an error.")
                            logger.error(
                                "chat_model_provider_error",
                                slot=effective_slot,
                                error=error_msg,
                                model=result.get("model", "?"),
                                session=session_id,
                            )
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "content": result.get(
                                        "error_message",
                                        "Model call failed — provider returned an error.",
                                    ),
                                    "model_slot": effective_slot,
                                }
                            )
                            continue

                        response_content = result.get("content", "")
                        model_used = result.get("model", effective_slot)
                        usage = result.get("usage", {})
                        latency = result.get("latency_ms", 0)
                        logger.info(
                            "chat_response_sent",
                            slot=effective_slot,
                            model=model_used,
                            latency_ms=latency,
                            content_len=len(response_content),
                            session=session_id,
                        )

                        # Capture turn_index before increment
                        turn_index = 0
                        _pre_meta = get_session_meta(session_id)
                        if _pre_meta:
                            turn_index = _pre_meta.get("turn_count", 0)

                        # Compute deterministic turn_id upfront so it can be
                        # echoed back to the GUI in the response payload AND
                        # used by the downstream auto-save path. The
                        # auto_save_chat_turn() helper computes the same ID
                        # via make_turn_id(session_id, turn_index), so the
                        # value we surface here will match the persisted turn.
                        # The GUI uses turn_id to power per-turn actions like
                        # Beast Counsel, Link Wiki, and Model Council turn
                        # linkage — without this, every per-turn action fails
                        # with "No turn ID available".
                        chat_turn_id = make_turn_id(session_id, turn_index)

                        # Increment turn counter
                        increment_turn_count(session_id, _container)

                        # Record budget consumption
                        if _container.budget_manager is not None:
                            try:
                                tokens_used = usage.get(
                                    "total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                                )
                                await _container.budget_manager.record_consumption(
                                    scope="session",
                                    scope_id=session_id,
                                    tokens_used=tokens_used or 0,
                                    cost_usd=result.get("cost_usd", 0.0),
                                    model_slot=effective_slot,
                                )
                            except Exception as exc:
                                logger.debug("Budget record failed", error=str(exc))

                        # Build response payload
                        response_payload = {
                            "type": "response",
                            "content": response_content,
                            "turn_id": chat_turn_id,
                            "model_slot": effective_slot,
                            "model": model_used,
                            "artifacts": [],
                            "tokens_used": usage.get(
                                "total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                            ),
                            "latency_ms": result.get("latency_ms", 0),
                            "cost_usd": result.get("cost_usd", 0.0),
                            "auto_save": auto_save_enabled
                            and (
                                (_container.artifact_store is not None and _container.lexical_store is not None)
                                or _container.corpus_turn_store is not None
                            ),
                            "sources": response_sources,  # Empty in normal mode, populated in augmented mode
                            "mode": session_mode,  # Echo the mode so GUI knows how the response was generated
                            "trace_available": ret_trace is not None and bool(ret_trace),
                            "lexical_only": getattr(ret_trace, "lexical_only", False)
                            if ret_trace is not None
                            else False,
                            "vector_contributed": getattr(ret_trace, "vector_contributed", False)
                            if ret_trace is not None
                            else False,
                            "direct_model": False,  # WS path always goes through the backend
                        }

                        # Check if review is available for augmented mode sessions
                        # This is a transitional approach — full workflow integration comes later.
                        # For now, augmented mode + ReviewQueueStore means the response
                        # includes a review_available flag so the GUI can show the review panel.
                        if session_mode == "augmented" and _container.review_queue_store is not None:
                            response_payload["review_available"] = True

                        await websocket.send_json(response_payload)

                        # Trajectory regulation check after each turn
                        # When SessionManager is available, check if trajectory
                        # is degrading and send warnings to the client.
                        if _container.session_manager is not None and _container.event_store is not None:
                            try:
                                from aip.foundation.schemas import SessionContext

                                # Build a SessionContext from current session metadata
                                updated_meta = get_session_meta(session_id)
                                if updated_meta is not None:
                                    ctx = SessionContext(
                                        session_id=session_id,
                                        project_id=updated_meta.get("project_id", ""),
                                        turn_count=updated_meta.get("turn_count", 0),
                                        context_tokens_estimate=updated_meta.get("context_tokens_estimate", 0),
                                        artifacts_produced=updated_meta.get("artifacts_produced", []),
                                    )
                                    signals, should_intervene = await _container.session_manager.check_trajectory(
                                        ctx,
                                        _container.event_store,
                                    )
                                    if should_intervene:
                                        # Send trajectory warning to client
                                        signal_summaries = [
                                            {
                                                "type": s.signal_type,
                                                "failure_type": s.failure_type,
                                                "detail": s.detail,
                                            }
                                            for s in signals
                                        ]
                                        await websocket.send_json(
                                            {
                                                "type": "trajectory_warning",
                                                "signals": signal_summaries,
                                                "intervention_recommended": True,
                                                "message": "Trajectory degradation detected. Consider context reset.",
                                            }
                                        )
                            except Exception:
                                # Non-critical — trajectory check is advisory
                                pass

                        # Auto-save ingestion: after a successful chat turn,
                        # trigger background ingestion if auto_save is enabled
                        # and at least one storage path is available (legacy pipeline
                        # or corpus_turn_store for Sexton tagging).
                        _has_legacy_stores = (
                            _container.artifact_store is not None and _container.lexical_store is not None
                        )
                        _has_corpus_store = _container.corpus_turn_store is not None
                        if auto_save_enabled and (_has_legacy_stores or _has_corpus_store):
                            try:
                                from aip.adapter.api.routes.ingest import auto_save_chat_turn

                                domain = (session_meta or {}).get("domain", "chat")
                                # Collect source_turn_ids from augmented retrieval for Vigil
                                _source_turn_ids: list[str] = []
                                if session_mode == "augmented" and response_sources:
                                    _source_turn_ids = [
                                        s.get("turn_id", "") for s in (source_dicts or []) if s.get("turn_id")
                                    ]
                                asyncio.create_task(
                                    auto_save_chat_turn(
                                        session_id=session_id,
                                        user_message=content,
                                        assistant_response=response_content,
                                        container=_container,
                                        domain=domain,
                                        turn_index=turn_index,
                                        model_used=model_used,
                                        augmented=(session_mode == "augmented"),
                                        source_turn_ids=_source_turn_ids or None,
                                    ),
                                    name=f"auto-save-{session_id}",
                                )
                            except Exception:
                                # Non-critical — auto-save is advisory
                                pass

                    except ValueError as exc:
                        # Slot not found or invalid
                        logger.error("chat_model_slot_error", slot=effective_slot, error=str(exc), session=session_id)
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Model slot error: {exc}",
                                "model_slot": effective_slot,
                            }
                        )
                    except Exception as exc:
                        # Model call failed — send error rather than crashing
                        logger.error(
                            "chat_model_call_failed",
                            slot=effective_slot,
                            error=str(exc),
                            session=session_id,
                            exc_info=True,
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Model call failed: {exc}",
                                "model_slot": effective_slot,
                            }
                        )
                else:
                    # No model provider configured — return degradation notice
                    # Still compute turn_id so the GUI's per-turn action
                    # buttons don't bail with "No turn ID available" even in
                    # this degraded path. The turn may not be persisted (no
                    # real response content), but the ID is deterministic and
                    # safe to surface.
                    _pre_meta_degraded = get_session_meta(session_id)
                    _degraded_turn_index = (
                        _pre_meta_degraded.get("turn_count", 0) if _pre_meta_degraded else 0
                    )
                    _degraded_turn_id = make_turn_id(session_id, _degraded_turn_index)
                    increment_turn_count(session_id, _container)
                    await websocket.send_json(
                        {
                            "type": "response",
                            "content": f"[No model provider configured] Echo: {content}",
                            "turn_id": _degraded_turn_id,
                            "model_slot": effective_slot,
                            "model": "none",
                            "artifacts": [],
                            "tokens_used": 0,
                            "direct_model": True,  # Degraded path: no backend model dispatch
                        }
                    )

            elif msg.get("type") == "gate_response":
                approved = msg.get("approved", False)
                queue_item_id = msg.get("queue_item_id")

                # Integrate with ReviewQueueStore when available
                if _container.review_queue_store is not None and queue_item_id is not None:
                    try:
                        decision = "approved" if approved else "rejected"
                        result = await _container.review_queue_store.decide(
                            item_id=int(queue_item_id),
                            decision=decision,
                            decided_by="definer",
                        )
                        if not result.get("ok"):
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "content": (
                                        f"Review decision failed: {result.get('error', {}).get('message', 'unknown')}"
                                    ),
                                }
                            )
                            continue
                        await websocket.send_json(
                            {
                                "type": "response",
                                "content": f"Gate {'approved' if approved else 'rejected'} (workflow resumed)",
                                "artifacts": [result.get("artifact_id", "")] if approved else [],
                                "tokens_used": 10,
                                "queue_item_id": queue_item_id,
                                "decision": decision,
                            },
                        )
                    except Exception as exc:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Review decision failed: {exc}",
                            }
                        )
                else:
                    # No ReviewQueueStore or no queue_item_id — legacy response
                    await websocket.send_json(
                        {
                            "type": "response",
                            "content": f"Gate {'approved' if approved else 'rejected'} (workflow resumed)",
                            "artifacts": [],
                            "tokens_used": 10,
                        },
                    )

            elif msg.get("type") == "ping":
                # Keepalive / latency check
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({"type": "error", "content": f"unknown message type: {msg.get('type')}"})

    except WebSocketDisconnect:
        # Client disconnected — normal flow
        pass
    except Exception as exc:
        # Unexpected error during WebSocket communication
        # In production, this would log to the event store
        try:
            await websocket.send_json({"type": "error", "content": f"WebSocket error: {exc}"})
        except Exception:
            pass  # Connection already broken
