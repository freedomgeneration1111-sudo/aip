"""Corpus API route — corpus_turns statistics, embedding progress, and audit.

Provides aggregate statistics about the project-agnostic corpus of
ingested conversation turns stored in CorpusTurnStore (corpus_turns
table in state.db).  Distinct from /sources which covers entity store
and knowledge store content.

Sprint 6.1: Added /corpus/embedding-progress endpoint for real-time
embedding pipeline visibility.

Sprint 9: Added /corpus/audit, /corpus/status, /corpus/backfill-queue,
and /corpus/ingest endpoints for corpus reliability and document ingestion.

UI Cycle 10: Added /corpus/documents, /corpus/documents/{source_path},
/corpus/problems, /corpus/unembedded, /corpus/backfill, /corpus/retry-failed
endpoints for the Corpus Workbench. All endpoints return honest
unavailable/not_wired states — never fake success.

QW9 (2026-07-23): Added /corpus-registry/corpora endpoint — returns the
list of registered corpora (corpus_id, corpus_type, sensitive,
deletion_state, access_note). Consumed by gui/components/corpus_selector.py
and any tooling that needs to enumerate corpora for multi-corpus retrieval.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# QW9 (2026-07-23): Corpus Registry — enumerate registered corpora
# ADR-008 Multi-Corpus: /corpus-registry/corpora
# ---------------------------------------------------------------------------


@router.get("/corpus-registry/corpora")
async def list_registered_corpora(container: AipContainer = Depends(get_container)):
    """List all registered corpora in the CorpusRegistry.

    QW9 (2026-07-23) — ADR-008 Multi-Corpus. Returns the list of corpora
    currently registered in the ``container.corpus_registry``. Each entry
    has: ``corpus_id``, ``corpus_type``, ``sensitive``, ``deletion_state``,
    ``access_note``. Consumed by ``gui/components/corpus_selector.py`` and
    any tooling that needs to enumerate corpora for multi-corpus retrieval.

    Returns an empty list (not an error) when the registry is not wired
    or no corpora are registered — honest unavailable state, never fake.
    """
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return []

    try:
        corpora_ids = await registry.list_corpora()
    except Exception as exc:
        logger.warning("list_registered_corpora_failed error=%s", exc)
        return []

    result: list[dict[str, Any]] = []
    for cid in corpora_ids:
        # Access the registry's internal CorpusStores bundle directly.
        # We don't call registry.get_stores() because that enforces the
        # 4-layer restricted-corpus access check (Layer 3) — and this
        # endpoint is informational, not a retrieval path. The
        # ``sensitive`` flag is returned so the GUI can show the amber
        # "⚠ sensitive" tag, but access is still gated at retrieval time.
        stores = registry._corpora.get(cid)
        if stores is None:
            continue
        result.append(
            {
                "corpus_id": stores.corpus_id,
                "corpus_type": stores.corpus_type.value
                if hasattr(stores.corpus_type, "value")
                else str(stores.corpus_type),
                "sensitive": bool(getattr(stores, "_sensitive", False)),
                "deletion_state": stores.deletion_state.value
                if hasattr(stores.deletion_state, "value")
                else str(stores.deletion_state),
                "access_note": getattr(stores, "_access_note", ""),
            }
        )
    return result


@router.get("/corpus/stats")
async def get_corpus_stats(container: AipContainer = Depends(get_container)):
    """Get aggregate statistics about the corpus of ingested turns.

    Returns:
      - total_turns: total number of turns in corpus_turns table
      - tagged: turns with primary_domain IS NOT NULL AND != "" (domain-assigned)
      - untagged: turns without a primary_domain
      - embedded: turns with embedded == 1
      - domains: list of {name, count} for each primary_domain
      - top_turns: top 10 turns by importance score
    """
    result: dict[str, Any] = {
        "total_turns": 0,
        "tagged": 0,
        "untagged": 0,
        "embedded": 0,
        "domains": [],
        "top_turns": [],
    }

    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return result

    try:
        result["total_turns"] = await cts.total_turns()
    except Exception as exc:
        logger.warning("CorpusTurnStore total_turns failed: %s", exc)

    try:
        result["tagged"] = await cts.count_tagged()
    except Exception as exc:
        logger.warning("CorpusTurnStore count_tagged failed: %s", exc)

    result["untagged"] = result["total_turns"] - result["tagged"]

    try:
        result["embedded"] = result["total_turns"] - await cts.count_unembedded()
    except Exception as exc:
        logger.warning("CorpusTurnStore embedded count failed: %s", exc)

    try:
        domain_counts = await cts.count_by_domain()
        result["domains"] = [{"name": name, "count": count} for name, count in domain_counts.items()]
    except Exception as exc:
        logger.warning("CorpusTurnStore domain counts failed: %s", exc)

    try:
        result["top_turns"] = await cts.top_turns_by_importance(limit=10)
    except Exception as exc:
        logger.warning("CorpusTurnStore top_turns_by_importance failed: %s", exc)

    return result


@router.get("/corpus/embedding-progress")
async def get_embedding_progress(container: AipContainer = Depends(get_container)):
    """Get real-time embedding pipeline progress.

    Returns embedding coverage statistics and current Sexton embedding
    pass state. This endpoint provides visibility into the embedding
    pipeline's progress toward full corpus coverage.

    Returns:
      - total: total number of turns in the corpus
      - embedded: turns with embedded == 1
      - unembedded: turns not yet embedded
      - needs_reembed: turns flagged for re-embedding (model changed)
      - percentage: embedded/total as a percentage
      - last_embed_at: ISO timestamp of most recent embed operation
      - embedding_models: dict of model_name -> count of turns embedded with that model
      - sexton_pass: current Sexton embedding pass status (running, last batch stats)
    """
    result: dict[str, Any] = {
        "total": 0,
        "embedded": 0,
        "unembedded": 0,
        "needs_reembed": 0,
        "percentage": 0.0,
        "last_embed_at": None,
        "embedding_models": {},
        "sexton_pass": None,
    }

    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return result

    # Get progress from CorpusTurnStore
    try:
        progress = await cts.get_embedding_progress()
        result.update(progress)
    except Exception as exc:
        logger.warning("CorpusTurnStore get_embedding_progress failed: %s", exc)
        # Fallback: compute from basic methods
        try:
            total = await cts.total_turns()
            unembedded = await cts.count_unembedded()
            result["total"] = total
            result["embedded"] = total - unembedded
            result["unembedded"] = unembedded
            result["percentage"] = round((total - unembedded) / total * 100, 2) if total > 0 else 0.0
        except Exception as exc2:
            logger.warning("CorpusTurnStore fallback progress failed: %s", exc2)

    # Get Sexton in-progress state
    sexton = getattr(container, "sexton_actor", None)
    if sexton is not None:
        try:
            pass_state = getattr(sexton, "_embedding_pass_state", None)
            if pass_state:
                result["sexton_pass"] = dict(pass_state)
        except Exception as exc:
            logger.warning("Sexton embedding pass state read failed: %s", exc)

    return result


@router.get("/corpus/status")
async def get_corpus_status(container: AipContainer = Depends(get_container)):
    """Quick corpus status summary (Sprint 9).

    Returns key metrics: total turns, embedding coverage, tagging coverage,
    documents, conversations, embed failures, and needs_reembed count.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"total_turns": 0, "embedded": 0, "tagged": 0}

    try:
        return await cts.get_corpus_status()
    except Exception as exc:
        logger.warning("CorpusTurnStore get_corpus_status failed: %s", exc)
        return {"error": str(exc)}


@router.get("/corpus/audit")
async def get_corpus_audit(container: AipContainer = Depends(get_container)):
    """Comprehensive corpus audit (Sprint 9).

    Returns detailed integrity check results: duplicate hashes, missing
    content hashes, embed failures, source model distribution, domain
    distribution, source path distribution, and computed health status.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"total_turns": 0, "healthy": False, "issues": ["CorpusTurnStore not available"]}

    try:
        return await cts.get_corpus_audit()
    except Exception as exc:
        logger.warning("CorpusTurnStore get_corpus_audit failed: %s", exc)
        return {"error": str(exc), "healthy": False}


@router.get("/corpus/backfill-queue")
async def get_backfill_queue(
    limit: int = 100,
    container: AipContainer = Depends(get_container),
):
    """Get the embedding backfill queue (Sprint 9).

    Returns turns that need embedding: failures first, then needs_reembed,
    then first-time unembedded. Ordered by priority.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"turns": [], "count": 0}

    try:
        turns = await cts.get_backfill_queue(limit=limit)
        return {
            "count": len(turns),
            "turns": [
                {
                    "turn_id": t.turn_id,
                    "source_path": t.source_path,
                    "source_model": t.source_model,
                    "embed_fail_count": t.embed_fail_count,
                    "last_embed_error": t.last_embed_error[:200] if t.last_embed_error else "",
                    "needs_reembed": t.needs_reembed,
                    "embedded": t.embedded,
                    "importance": t.importance,
                }
                for t in turns
            ],
        }
    except Exception as exc:
        logger.warning("CorpusTurnStore get_backfill_queue failed: %s", exc)
        return {"error": str(exc), "turns": [], "count": 0}


@router.post("/corpus/ingest")
async def ingest_to_corpus(
    payload: dict,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Ingest a file or directory into the corpus (Sprint 9).

    Explicit DEFINER action. Must not silently overwrite existing documents.
    Accepts:
      - path: file or directory path to ingest
      - source_model: optional (auto-detected if not specified)
      - source_account: optional (defaults to "api_ingest")
      - recursive: optional (for directory ingestion)

    Returns CorpusIngestResult with counts of ingested, skipped, updated, failed turns.
    """
    # Chunk 6: Use container-mediated access instead of direct orchestration import
    CorpusIngestConfig = getattr(container, "_corpus_ingest_config_class", None)
    ingest_directory_to_corpus = getattr(container, "_ingest_directory_to_corpus_fn", None)
    ingest_file_to_corpus = getattr(container, "_ingest_file_to_corpus_fn", None)

    if CorpusIngestConfig is None or ingest_file_to_corpus is None:
        raise HTTPException(status_code=503, detail="Corpus ingestion pipeline not wired")

    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        raise HTTPException(status_code=503, detail="CorpusTurnStore not wired")

    import os

    path = payload.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="No path provided")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    config = CorpusIngestConfig(
        source_model=payload.get("source_model", ""),
        source_account=payload.get("source_account", "api_ingest"),
        export_date=payload.get("export_date", ""),
        db_path=getattr(container, "_db_path", ""),
        recursive=payload.get("recursive", False),
    )

    try:
        if os.path.isdir(path):
            results = await ingest_directory_to_corpus(path, cts, config)
            return {
                "type": "directory",
                "files_processed": len([r for r in results if r.source_type != "directory"]),
                "total_ingested": sum(r.turns_ingested for r in results),
                "total_skipped": sum(r.turns_skipped for r in results),
                "total_updated": sum(r.turns_updated for r in results),
                "total_failed": sum(r.turns_failed for r in results),
            }
        else:
            result = await ingest_file_to_corpus(path, cts, config)
            return {
                "type": "file",
                "source_path": result.source_path,
                "source_type": result.source_type,
                "turns_ingested": result.turns_ingested,
                "turns_skipped": result.turns_skipped,
                "turns_updated": result.turns_updated,
                "turns_failed": result.turns_failed,
                "warnings": result.warnings,
                "errors": result.errors,
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")


# ------------------------------------------------------------------
# UI Cycle 10: Corpus Workbench endpoints
# ------------------------------------------------------------------


@router.get("/corpus/documents")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    search: str = "",
    container: AipContainer = Depends(get_container),
):
    """List documents (distinct source_paths) in the corpus.

    UI Cycle 10: Returns document-level summary with chunk counts,
    embedding status, and per-document problems. Returns empty list
    honestly when no documents exist.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    try:
        total = await cts.count_documents()
        docs = await cts.list_documents(
            limit=limit,
            offset=offset,
            source_path_filter=search,
        )
        return {
            "items": docs,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as exc:
        logger.warning("CorpusTurnStore list_documents failed: %s", exc)
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "error": str(exc)}


@router.get("/corpus/documents/{source_path:path}")
async def get_document_detail(
    source_path: str,
    container: AipContainer = Depends(get_container),
):
    """Get detailed information about a single document (source_path).

    UI Cycle 10: Returns metadata, chunk summary, embedding status,
    errors/problems, and sample turns. Returns 404/not_found honestly
    for missing documents.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"not_found": True, "source_path": source_path, "error": "CorpusTurnStore not wired"}

    try:
        detail = await cts.get_document_detail(source_path)
        if detail.get("not_found"):
            raise HTTPException(status_code=404, detail=f"Document not found: {source_path}")
        return detail
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("CorpusTurnStore get_document_detail failed: %s", exc)
        return {"not_found": True, "source_path": source_path, "error": str(exc)}


@router.get("/corpus/problems")
async def get_corpus_problems(container: AipContainer = Depends(get_container)):
    """Get corpus problems summary for the Corpus Workbench.

    UI Cycle 10: Returns failed ingest jobs, unembedded chunk count,
    stale documents, and duplicate hashes. Returns honest empty lists
    when no problems exist. Never fakes healthy state.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {
            "failed_ingest_jobs": [],
            "unembedded_count": 0,
            "needs_reembed_count": 0,
            "duplicate_hashes": [],
            "stale_docs": [],
            "available": False,
        }

    try:
        result = await cts.get_corpus_problems()
        result["available"] = True
        return result
    except Exception as exc:
        logger.warning("CorpusTurnStore get_corpus_problems failed: %s", exc)
        return {
            "failed_ingest_jobs": [],
            "unembedded_count": 0,
            "needs_reembed_count": 0,
            "duplicate_hashes": [],
            "stale_docs": [],
            "available": False,
            "error": str(exc),
        }


@router.get("/corpus/unembedded")
async def get_unembedded_chunks(
    limit: int = 100,
    container: AipContainer = Depends(get_container),
):
    """Get list of unembedded chunks/turns.

    UI Cycle 10: Returns turns without embeddings. Honest empty list
    when all chunks are embedded or when store is unavailable.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"items": [], "count": 0, "available": False}

    try:
        turns = await cts.get_unembedded_turns(limit=limit)
        return {
            "count": len(turns),
            "available": True,
            "items": [
                {
                    "turn_id": t.turn_id,
                    "source_path": t.source_path,
                    "source_model": t.source_model,
                    "primary_domain": t.primary_domain,
                    "importance": t.importance,
                    "needs_reembed": t.needs_reembed,
                    "embed_fail_count": t.embed_fail_count,
                }
                for t in turns
            ],
        }
    except Exception as exc:
        logger.warning("CorpusTurnStore get_unembedded_turns failed: %s", exc)
        return {"items": [], "count": 0, "available": False, "error": str(exc)}


@router.post("/corpus/backfill")
async def trigger_embedding_backfill(
    payload: dict | None = None,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Trigger embedding backfill for the corpus.

    UI Cycle 10: Explicit DEFINER action. Uses existing Sexton/backfill
    path. If only scheduled backfill exists, returns 'scheduled_only'
    honestly. Reports accepted/running/completed/failed honestly.
    Never fakes backfill success.
    """
    # Check if embedding provider is available
    embedding_provider = getattr(container, "embedding_provider", None)
    if embedding_provider is None:
        return {
            "status": "not_wired",
            "message": "Embedding provider not configured. Configure an embedding model slot first.",
        }

    # Check if backfill is already running
    backfill_status = getattr(container, "backfill_status", {})
    if backfill_status.get("running"):
        return {
            "status": "already_running",
            "message": (
                "Backfill is already in progress. "
                "Poll GET /api/v1/corpus/embedding-progress or "
                "GET /api/v1/admin/embeddings/backfill/status for progress."
            ),
        }

    # Delegate to the existing admin backfill endpoint logic
    body = payload or {}
    limit = body.get("limit", 500)
    batch_size = body.get("batch_size", 20)
    dry_run = body.get("dry_run", False)
    domain = body.get("domain")

    # Use the existing backfill mechanism from admin routes
    # Import the backfill function from the admin module via container
    try:
        # Trigger via the same mechanism as admin/embeddings/backfill
        from aip.adapter.api.routes.admin import BackfillRequest, _run_backfill_in_background

        request = BackfillRequest(
            domain=domain,
            limit=limit,
            batch_size=batch_size,
            dry_run=dry_run,
        )

        # Initialize backfill status if needed
        if not backfill_status:
            container.backfill_status = {
                "running": False,
                "scanned": 0,
                "embedded": 0,
                "failed": 0,
                "skipped": 0,
                "total_estimated": 0,
                "last_result": None,
            }

        container.backfill_status["running"] = True
        container.backfill_status["scanned"] = 0
        container.backfill_status["embedded"] = 0
        container.backfill_status["failed"] = 0
        container.backfill_status["skipped"] = 0

        # Run backfill in background
        import asyncio

        asyncio.create_task(_run_backfill_in_background(request, container))

        return {
            "status": "accepted",
            "message": (
                "Embedding backfill started. "
                "Poll GET /api/v1/corpus/embedding-progress or "
                "GET /api/v1/admin/embeddings/backfill/status for progress."
            ),
            "limit": limit,
            "batch_size": batch_size,
            "dry_run": dry_run,
        }
    except ImportError:
        return {
            "status": "not_wired",
            "message": "Backfill pipeline not wired. Use POST /api/v1/admin/embeddings/backfill instead.",
        }
    except Exception as exc:
        logger.warning("Corpus backfill trigger failed: %s", exc)
        return {"status": "error", "message": f"Backfill failed to start: {exc}"}


@router.post("/corpus/retry-failed")
async def retry_failed_embeds(
    payload: dict | None = None,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Retry embedding for turns that previously failed.

    UI Cycle 10: Explicit DEFINER action. Clears embed failure counters
    and adds turns back to the backfill queue. Returns honest status
    if the capability is not available.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"status": "not_wired", "message": "CorpusTurnStore not wired"}

    embedding_provider = getattr(container, "embedding_provider", None)
    if embedding_provider is None:
        return {
            "status": "not_wired",
            "message": "Embedding provider not configured. Configure an embedding model slot first.",
        }

    try:
        # Get failed turns and clear their failure counters
        body = payload or {}
        limit = body.get("limit", 50)

        # Use get_backfill_queue to get failed turns (they are first in priority)
        failed_turns = await cts.get_backfill_queue(limit=limit)
        retried_count = 0
        for turn in failed_turns:
            if turn.embed_fail_count > 0:
                await cts.clear_embed_failure(turn.turn_id)
                retried_count += 1

        if retried_count == 0:
            return {
                "status": "no_failed",
                "message": "No failed embed jobs found to retry.",
                "retried_count": 0,
            }

        return {
            "status": "accepted",
            "message": (
                f"Cleared failure counters for {retried_count} turns. They will be retried in the next backfill cycle."
            ),
            "retried_count": retried_count,
        }
    except Exception as exc:
        logger.warning("Retry failed embeds failed: %s", exc)
        return {"status": "error", "message": f"Retry failed: {exc}"}


@router.get("/corpus/duplicates")
async def get_duplicate_documents(
    container: AipContainer = Depends(get_container),
):
    """Get duplicate documents detected by content hash.

    UI Cycle 10: Returns content hashes that appear more than once.
    Honest empty list if no duplicates found.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"items": [], "total": 0, "available": False}

    try:
        audit = await cts.get_corpus_audit()
        duplicates = audit.get("duplicate_hashes", [])
        return {
            "items": duplicates,
            "total": len(duplicates),
            "available": True,
        }
    except Exception as exc:
        logger.warning("CorpusTurnStore get_duplicates failed: %s", exc)
        return {"items": [], "total": 0, "available": False, "error": str(exc)}


@router.get("/corpus/stale")
async def get_stale_documents(
    container: AipContainer = Depends(get_container),
):
    """Get stale documents (not updated in 30+ days).

    UI Cycle 10: Returns documents that may need re-ingestion.
    Honest empty list if no stale documents found.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"items": [], "total": 0, "available": False}

    try:
        problems = await cts.get_corpus_problems()
        stale = problems.get("stale_docs", [])
        return {
            "items": stale,
            "total": len(stale),
            "available": True,
        }
    except Exception as exc:
        logger.warning("CorpusTurnStore get_stale failed: %s", exc)
        return {"items": [], "total": 0, "available": False, "error": str(exc)}
