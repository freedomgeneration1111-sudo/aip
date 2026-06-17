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
from aip.adapter.api.routes._augmented_context import (
    AugmentedContext,
    assemble_augmented_context,
)
from aip.adapter.api.routes.sessions import get_session_meta, increment_turn_count
from aip.foundation.schemas.corpus_turn import make_turn_id
from aip.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ── Backward-compat re-exports ──────────────────────────────────────────
#
# The four retrieval helpers below were previously defined inline in this
# module. They have been moved to ``_augmented_context.py`` so both this
# route and ``model_council.py`` can share them. They are re-exported here
# to keep the public surface stable (no external consumer imports them
# today, but the re-export prevents breakage if any test or future caller
# does ``from aip.adapter.api.routes.chat import _search_corpus_turns``).
#
# New callers should import directly from ``_augmented_context`` or use
# the high-level ``assemble_augmented_context()`` function.

from aip.adapter.api.routes._augmented_context import (  # noqa: E402, F401
    _assemble_corpus_context,
    _get_graph_neighbors,
    _get_wiki_overview,
    _search_corpus_turns,
)


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
                            _container.corpus_turn_store is not None
                            or _container.lexical_store is not None
                        ):
                            # Phase 1 retrieval bridge: call the shared
                            # ``assemble_augmented_context()`` helper that
                            # lives in ``routes/_augmented_context.py``.
                            # The helper encapsulates definer profile
                            # injection, domain resolution, corpus turn
                            # search, orchestrator fallback (RRF), wiki
                            # overview injection, graph neighbors injection,
                            # sources assembly, and the synthesis instruction.
                            # It NEVER raises — on any failure it returns
                            # ``AugmentedContext(assembled=False)`` with
                            # empty messages, and the caller proceeds with
                            # the bare prompt (graceful degradation).
                            aug = await assemble_augmented_context(
                                content=content,
                                session_id=session_id,
                                container=_container,
                                session_meta=session_meta,
                            )
                            messages.extend(aug.messages)
                            response_sources = aug.sources
                            ret_trace = aug.trace
                            _augmented_source_turn_ids = aug.source_turn_ids
                            if not aug.assembled:
                                # Retrieval was skipped or failed — the
                                # helper already logged the failure. Fall
                                # back to the role hint if present.
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
                            _augmented_source_turn_ids = []
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
                                # Collect source_turn_ids from augmented retrieval for Vigil.
                                # The helper exposes these directly (aug.source_turn_ids)
                                # so we don't need to re-extract them from the raw
                                # source_dicts (which now live inside the helper).
                                _source_turn_ids: list[str] = []
                                if session_mode == "augmented" and response_sources:
                                    _source_turn_ids = list(_augmented_source_turn_ids)
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
