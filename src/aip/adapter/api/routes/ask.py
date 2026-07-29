"""Ask API route — source-grounded knowledge queries.

Exposes the ask_pipeline via REST endpoint so the GUI can submit
knowledge-augmented queries without requiring CLI access.

Layer discipline: This module imports ONLY from adapter and foundation.
Orchestration functions (ask, AskStores, _search_sources_with_trace,
_sanitize_fts_query) are accessed through the container, not imported
directly from orchestration.

ADR-017 WS-4: When ``web_grounding=True`` is passed in the payload, the
route runs the web ground pipeline (search + fetch + extract top-N
sources) and injects the results into the synthesis system prompt as a
``WebSourceContextBlock``.  The block is enclosed in
``BEGIN_WEB_SOURCE`` / ``END_WEB_SOURCE`` markers so the synthesis model
treats the content as untrusted data, not instructions.  Web sources
are reported in the response as ``web_sources`` (distinct from corpus
``sources``) and per-source failures are reported as ``web_failures``
(ADR-017 honesty rule: never silently drop a failed source).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.adapter.api.routes._augmented_context import (
    build_web_source_context_block,
    load_web_grounding_prompt_fragment,
)
from aip.adapter.web.extractors.factory import select_extractor
from aip.adapter.web.fake_provider import (
    WebFetchDenied,
    WebFetchError,
    WebProviderError,
    WebProviderNotConfigured,
)
from aip.adapter.web.provenance import build_web_source_record
from aip.adapter.web.providers.factory import is_provider_configured
from aip.foundation.schemas.ask import AskSource
from aip.foundation.schemas.web import SearchOptions
from aip.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# ADR-017 WS-4: Web grounding helper
# ---------------------------------------------------------------------------


async def _run_web_grounding(
    container: AipContainer,
    question: str,
    *,
    max_sources: int = 3,
) -> tuple[list[dict], list[dict], str | None]:
    """Run the web ground pipeline for an Ask query.

    Returns ``(web_sources, web_failures, error)``:
        - ``web_sources``: list of source dicts suitable for the response
          payload AND for ``build_web_source_context_block``.  Each dict
          has url, title, text, rank, retrieved_at, extraction_method,
          warnings, source_id.
        - ``web_failures``: list of per-source failure dicts with url
          and error/reason.
        - ``error``: a top-level error string if the whole pipeline
          failed (e.g. not configured, provider error).  ``None`` on
          success or partial success.

    The function NEVER raises — all exceptions are caught and reported
    via ``error`` or ``web_failures``.  This matches the existing ask
    route pattern (failures are surfaced, not propagated).
    """
    provider = getattr(container, "web_search_provider", None)
    fetcher = getattr(container, "web_fetcher", None)
    policy = getattr(container, "web_fetch_policy", None)
    source_store = getattr(container, "web_source_store", None)

    # Not-configured checks
    if provider is None or not is_provider_configured(provider):
        return [], [], "not_configured"
    if fetcher is None or policy is None:
        return [], [], "not_configured"

    # Search
    try:
        search_results = await provider.search(
            question, options=SearchOptions(limit=max_sources)
        )
    except WebProviderNotConfigured as exc:
        return [], [], f"not_configured: {exc}"
    except WebProviderError as exc:
        return [], [], f"provider_error: {exc}"
    except Exception as exc:
        logger.warning("ask_web_search_failed: %s", exc)
        return [], [], f"search_failed: {exc}"

    # Fetch + extract each result
    web_sources: list[dict] = []
    web_failures: list[dict] = []
    for result in search_results[:max_sources]:
        try:
            fetched = await fetcher.fetch(result.url, policy)
        except WebFetchDenied as exc:
            web_failures.append({"url": result.url, "error": "fetch_denied", "reason": exc.reason})
            continue
        except WebFetchError as exc:
            web_failures.append({"url": result.url, "error": "fetch_error", "message": str(exc)})
            continue
        except Exception as exc:
            web_failures.append({"url": result.url, "error": "fetch_error", "message": str(exc)})
            continue

        # Extract — load bytes from snapshot store (pre-fetched by the
        # fetcher's bytes_sink, or unavailable for direct-fetch path).
        try:
            raw_bytes = await _load_bytes_for_extraction(container, fetched)
            bytes_loader = _make_sync_bytes_loader(raw_bytes)
            extractor = select_extractor(fetched.content_type)
            extracted = await extractor.extract(fetched, bytes_loader=bytes_loader)
        except Exception as exc:
            web_failures.append({"url": result.url, "error": "extract_failed", "message": str(exc)})
            continue

        # Build and store the source record
        record = build_web_source_record(
            search_result=result,
            fetched=fetched,
            extracted=extracted,
            fetch_warnings=(),
        )
        if source_store is not None:
            try:
                await source_store.put(record)
            except Exception as exc:
                logger.warning("ask_web_source_store_failed: %s", exc)

        web_sources.append({
            "source_id": record.source_id,
            "url": fetched.final_url,
            "title": extracted.title or result.title,
            "text": extracted.text,
            "text_chars": len(extracted.text),
            "rank": result.rank,
            "retrieved_at": fetched.retrieved_at.isoformat(),
            "content_hash": extracted.content_hash,
            "extraction_method": extracted.extraction_method,
            "warnings": list(extracted.warnings),
            "snippet": result.snippet,
        })

    return web_sources, web_failures, None


async def _load_bytes_for_extraction(container: AipContainer, fetched) -> bytes:
    """Load raw bytes for a FetchedResource from the snapshot store.

    Looks up by content_bytes_ref first, then falls back to content_hash.
    Raises HTTPException 500 if bytes are unavailable.
    """
    from fastapi import HTTPException

    snapshot_store = getattr(container, "web_snapshot_store", None)
    if snapshot_store is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "not_configured", "message": "Web snapshot store is not wired."},
        )

    bytes_data = await snapshot_store.get_bytes(fetched.content_bytes_ref)
    if bytes_data is None:
        record = await snapshot_store.get_by_hash(fetched.content_hash)
        if record is not None:
            bytes_data = await snapshot_store.get_bytes(record.snapshot_id)
    if bytes_data is None:
        raise HTTPException(
            status_code=500,
            detail={"error": "bytes_unavailable", "message": "Fetched bytes could not be retrieved."},
        )
    return bytes_data


def _make_sync_bytes_loader(cached_bytes: bytes):
    """Build a sync bytes_loader returning pre-fetched bytes."""

    def loader(ref: str) -> bytes:
        return cached_bytes

    return loader


# ---------------------------------------------------------------------------
# Ask routes
# ---------------------------------------------------------------------------


@router.post("/ask")
async def ask_query(payload: dict, container: AipContainer = Depends(get_container)):
    """Execute a source-grounded ask query against the AIP knowledge substrate.

    Accepts:
      - question (str, required): The query text
      - project_name (str, required): Project to search within
      - source (str, optional): "ingested" | "artifacts" | "all" (default: "all")
      - max_sources (int, optional): Max sources to retrieve (default: 10)
      - save_artifact (bool, optional): Save answer as draft artifact (default: false)
      - model_slot (str, optional): Model slot to use (default: "synthesis")
      - system_prompt_modifier (str, optional): Chat mode modifier text
        prepended to the synthesis system prompt (per AIP_UNIFIED_CHAT_SPEC)
      - web_grounding (bool, optional): Also fetch+use ephemeral web sources
        (ADR-017 WS-4, default: false).  When true, the route runs the web
        ground pipeline and injects results into the synthesis prompt as a
        WebSourceContextBlock.  Web sources are reported in the response
        as ``web_sources`` and failures as ``web_failures``.

    Returns AskResult dict with status, answer, sources, and metadata.
    """
    question = payload.get("question", "").strip()
    project_name = payload.get("project_name", "").strip()

    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if not project_name:
        raise HTTPException(status_code=400, detail="project_name is required")

    source: AskSource = payload.get("source", "all")  # type: ignore[assignment]
    if source not in ("ingested", "artifacts", "all"):
        source = "all"

    max_sources = payload.get("max_sources", 10)
    save_artifact = payload.get("save_artifact", False)
    model_slot = payload.get("model_slot", "synthesis")
    system_prompt_modifier = payload.get("system_prompt_modifier", "")
    web_grounding = bool(payload.get("web_grounding", False))

    # ADR-017 WS-4: run web grounding before the ask pipeline so the
    # web source context block can be appended to system_prompt_modifier.
    # Failures are reported in the response, not raised — the ask
    # pipeline proceeds with corpus-only grounding if web fails.
    web_sources: list[dict] = []
    web_failures: list[dict] = []
    web_grounding_error: str | None = None
    if web_grounding:
        web_sources, web_failures, web_grounding_error = await _run_web_grounding(
            container, question, max_sources=3,
        )
        if web_sources:
            # Build the prompt-injection-isolated context block and
            # append it to system_prompt_modifier.  The synthesis model
            # receives corpus sources (via the ask pipeline) AND web
            # sources (via this block) in the same augmented context.
            web_block = build_web_source_context_block(web_sources)
            prompt_fragment = load_web_grounding_prompt_fragment()
            web_modifier = f"\n\n{prompt_fragment}\n\n{web_block}"
            system_prompt_modifier = (system_prompt_modifier or "") + web_modifier

    # Validate required stores
    if container.lexical_store is None:
        raise HTTPException(
            status_code=503,
            detail="Lexical store not available — cannot perform knowledge queries. "
            "Ensure the AIP backend is configured with FTS5 support.",
        )

    if container.artifact_store is None:
        raise HTTPException(
            status_code=503,
            detail="Artifact store not available — cannot perform knowledge queries.",
        )

    # Project store is optional — corpus is project-agnostic and search
    # proceeds even when no project exists in the database.

    # Build AskStores from container's already-wired components.
    # Access AskStores class through the container (layer discipline:
    # routes do not import from orchestration directly).
    AskStores = container._ask_stores_class
    if AskStores is None:
        raise HTTPException(status_code=503, detail="Ask pipeline not available")

    stores = AskStores(
        artifact_store=container.artifact_store,
        lexical_store=container.lexical_store,
        vector_store=container.vector_store,
        event_store=container.event_store,
        project_store=container.project_store,
        ecs_store=container.ecs_store,
        model_provider=container.model_provider,
        embedding_provider=container.embedding_provider,
        corpus_turn_store=container.corpus_turn_store,
        graph_store=getattr(container, "graph_store", None),
    )

    # Call the ask pipeline through the container (layer discipline).
    ask_fn = container._ask_fn
    if ask_fn is None:
        raise HTTPException(status_code=503, detail="Ask pipeline not available")

    try:
        result = await ask_fn(
            question=question,
            project_name=project_name,
            stores=stores,
            source=source,
            max_sources=max_sources,
            save_artifact=save_artifact,
            model_slot=model_slot,
            system_prompt_modifier=system_prompt_modifier,
        )
    except Exception as exc:
        logger.error("Ask pipeline failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ask pipeline error: {exc}") from exc

    # Convert dataclass to dict for JSON response
    return {
        "status": result.status,
        "answer": result.answer,
        "sources": [
            {
                "source_id": s.source_id,
                "source_type": s.source_type,
                "title": s.title,
                "score": s.score,
                "content_snippet": s.content_snippet,
                "domain": s.domain,
                "metadata": s.metadata,
            }
            for s in result.sources
        ],
        "model_slot": result.model_slot,
        "model_provider": result.model_provider,
        "artifact_id": result.artifact_id,
        "session_id": result.session_id,
        "project_id": result.project_id,
        "project_name": result.project_name,
        "prompt": result.prompt,
        "errors": result.errors,
        "trace_available": bool(result.sources),
        "lexical_only": result.retrieval_degradation.get("lexical_only", False)
        if result.retrieval_degradation
        else False,
        "vector_contributed": result.retrieval_degradation.get("vector_contributed", False)
        if result.retrieval_degradation
        else False,
        # ADR-017 WS-4: Web grounding provenance (ephemeral, not in corpus)
        "web_grounding": web_grounding,
        "web_sources": web_sources,
        "web_failures": web_failures,
        "web_grounding_error": web_grounding_error,
    }


@router.post("/ask/retrieve")
async def ask_retrieve_only(payload: dict, container: AipContainer = Depends(get_container)):
    """Retrieve sources for a query without generating an answer.

    Lightweight endpoint for the Vector search panel: returns matching
    sources from LexicalStore + VectorStore without dispatching to a model.

    Accepts:
      - question (str, required): The query text
      - project_name (str, optional): Project domain to filter by
      - domain (str, optional): Domain to filter by (alternative to project_name)
      - source (str, optional): "ingested" | "artifacts" | "all" (default: "all")
      - max_sources (int, optional): Max sources to retrieve (default: 20)
    """
    question = payload.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    payload.get("domain") or payload.get("project_name")
    source: AskSource = payload.get("source", "all")  # type: ignore[assignment]
    if source not in ("ingested", "artifacts", "all"):
        source = "all"
    max_sources = payload.get("max_sources", 20)

    if container.lexical_store is None:
        raise HTTPException(status_code=503, detail="Lexical store not available")

    # Access orchestration functions through the container (layer discipline).
    search_sources_fn = container._search_sources_fn
    AskStores = container._ask_stores_class
    if search_sources_fn is None or AskStores is None:
        raise HTTPException(status_code=503, detail="Retrieval pipeline not available")

    # Corpus is project-agnostic: do not filter by domain/project.
    # project_domain is kept for future use but does not limit retrieval.
    project_domain = None

    # Use the orchestrator pipeline for retrieval
    trace = None
    try:
        sources, trace, _packed = await search_sources_fn(
            query=question,
            stores=AskStores(
                artifact_store=container.artifact_store,
                lexical_store=container.lexical_store,
                vector_store=container.vector_store,
                event_store=container.event_store,
                project_store=container.project_store,
                ecs_store=container.ecs_store,
                embedding_provider=container.embedding_provider,
                corpus_turn_store=container.corpus_turn_store,
                graph_store=getattr(container, "graph_store", None),
            ),
            source_filter=source,
            max_sources=max_sources,
        )
    except Exception as exc:
        logger.error("Source retrieval failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Retrieval error: {exc}") from exc

    return {
        "question": question,
        "domain": project_domain,
        "sources": [
            {
                "source_id": s.source_id,
                "source_type": s.source_type,
                "title": s.title,
                "score": s.score,
                "content_snippet": s.content_snippet,
                "domain": s.domain,
                "metadata": s.metadata,
            }
            for s in sources
        ],
        "total": len(sources),
        "trace_available": trace is not None and bool(trace),
        "lexical_only": getattr(trace, "lexical_only", False) if trace is not None else False,
        "vector_contributed": getattr(trace, "vector_contributed", False) if trace is not None else False,
    }
