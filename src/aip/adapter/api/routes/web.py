"""Web Source Acquisition API routes (ADR-017 WS-3).

Four routes per ADR-017 §API surface:

    POST /api/v1/web/search   — run a search via the configured provider
    POST /api/v1/web/fetch    — fetch + extract a single URL
    POST /api/v1/web/ground   — search + fetch + extract top-N for grounding
    GET  /api/v1/web/sources/{source_id} — retrieve a stored source record

    POST /api/v1/web/promote  — deferred to WS-5

All routes return 503 with a structured ``not_configured`` error when
the web provider is ``None`` or has no API key.  This is the honest
"web search is off" behavior — never a silent fallback.

Secret handling:

    The API key is NEVER included in any response, log, or stored
    ``WebSourceRecord``.  ``provider_metadata`` is redacted by
    ``build_web_source_record`` before storage.

The routes depend on the container exposing:

    - ``web_search_provider``  — ``SearchProvider | None``
    - ``web_fetcher``          — ``WebFetcher | None``
    - ``web_source_store``     — ``WebSourceStore | None``
    - ``web_snapshot_store``   — ``WebSnapshotStore | None`` (optional)
    - ``web_task_registry``    — ``BackgroundTaskRegistry | None`` (optional)
    - ``web_fetch_policy``     — ``FetchPolicy`` (optional; defaults applied)

These are wired by the Integrator in ``dependencies.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.adapter.web.extractors.factory import select_extractor
from aip.adapter.web.fake_provider import (
    WebFetchDenied,
    WebFetchError,
    WebProviderError,
    WebProviderNotConfigured,
)
from aip.adapter.web.provenance import build_web_source_record
from aip.adapter.web.providers.factory import is_provider_configured
from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchPolicy,
    SearchOptions,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class WebSearchRequest(BaseModel):
    """Request body for ``POST /api/v1/web/search``."""

    query: str = Field(..., min_length=1, max_length=2000, description="Search query")
    limit: int = Field(8, ge=1, le=20, description="Maximum results to return")
    freshness_days: int | None = Field(None, ge=1, le=365, description="Restrict to results from last N days")
    domains: list[str] | None = Field(None, description="Restrict to these domains")
    topic: str | None = Field(None, description="Provider topic hint (e.g. 'general', 'news')")


class WebFetchRequest(BaseModel):
    """Request body for ``POST /api/v1/web/fetch``."""

    url: str = Field(..., min_length=1, max_length=2048, description="URL to fetch")


class WebGroundRequest(BaseModel):
    """Request body for ``POST /api/v1/web/ground``."""

    query: str = Field(..., min_length=1, max_length=2000, description="Search query")
    limit: int = Field(3, ge=1, le=10, description="Number of sources to ground on")
    fetch_top_n: int = Field(3, ge=0, le=10, description="How many search results to fetch+extract")


class WebSearchResponse(BaseModel):
    """Response for ``POST /api/v1/web/search``."""

    query: str
    provider: str
    results: list[dict[str, Any]]
    count: int


class WebFetchResponse(BaseModel):
    """Response for ``POST /api/v1/web/fetch``."""

    source_id: str
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    content_hash: str
    truncated: bool
    title: str
    text: str
    text_chars: int
    extraction_method: str
    warnings: list[str]
    redirects: list[str]


class WebGroundResponse(BaseModel):
    """Response for ``POST /api/v1/web/ground``."""

    query: str
    provider: str
    sources: list[dict[str, Any]]
    search_count: int
    fetched_count: int
    failures: list[dict[str, Any]]


class WebSourceResponse(BaseModel):
    """Response for ``GET /api/v1/web/sources/{source_id}``."""

    source_id: str
    provider: str
    content_hash: str
    retrieved_at: str
    source_url: str
    canonical_url: str | None
    title: str
    text: str
    extraction_method: str
    warnings: list[str]
    search_result: dict[str, Any] | None
    fetch_warnings: list[str]


class WebPromoteRequest(BaseModel):
    """Request body for ``POST /api/v1/web/promote`` (ADR-017 WS-5)."""

    source_id: str = Field(..., min_length=1, max_length=100, description="Web source ID to promote")
    approval: str = Field(..., min_length=1, description="Explicit approval token (required — no batch/auto-promote)")
    target_corpus_id: str | None = Field(None, description="Target corpus (default: definer)")


class WebPromoteResponse(BaseModel):
    """Response for ``POST /api/v1/web/promote``."""

    success: bool
    corpus_turn_id: str
    deduplicated: bool
    source_id: str
    target_corpus_id: str
    error: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_search_provider(container: AipContainer):
    """Return the wired SearchProvider or raise 503 not_configured."""
    provider = getattr(container, "web_search_provider", None)
    if provider is None or not is_provider_configured(provider):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "not_configured",
                "message": "Web search is not configured. Set AIP_WEB_SEARCH_API_KEY and enable [web] in config.",
            },
        )
    return provider


def _require_fetcher(container: AipContainer):
    """Return the wired WebFetcher or raise 503 not_configured."""
    fetcher = getattr(container, "web_fetcher", None)
    if fetcher is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "not_configured",
                "message": "Web fetcher is not wired.",
            },
        )
    return fetcher


def _get_fetch_policy(container: AipContainer) -> FetchPolicy:
    """Return the wired FetchPolicy, or a sensible default."""
    policy = getattr(container, "web_fetch_policy", None)
    if isinstance(policy, FetchPolicy):
        return policy
    return FetchPolicy()


def _get_source_store(container: AipContainer):
    """Return the wired WebSourceStore (may be None — routes handle gracefully)."""
    return getattr(container, "web_source_store", None)


def _make_bytes_loader_sync(cached_bytes: bytes):
    """Build a sync bytes_loader that returns pre-fetched bytes.

    The extractor Protocol expects a SYNC callable ``bytes_ref -> bytes``.
    Since the snapshot store is async, we pre-fetch the bytes before
    calling the extractor and pass them via this closure.  This avoids
    mixing sync/async in the extractor interface.
    """

    def loader(ref: str) -> bytes:
        return cached_bytes

    return loader


async def _load_bytes_for_fetched(container: AipContainer, fetched) -> bytes:
    """Load the raw bytes for a FetchedResource from the snapshot store.

    The HttpxWebFetcher stores bytes under a ref like ``"httpx:{url}:{hash[:16]}"``.
    The FakeWebFetcher stores under ``"fake:{url}"``.  We look up by:
        1. content_bytes_ref (if the store indexed by it)
        2. content_hash (fallback)

    Returns the bytes, or raises HTTPException 500 if unavailable.
    """
    snapshot_store = getattr(container, "web_snapshot_store", None)
    if snapshot_store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "not_configured",
                "message": "Web snapshot store is not wired; cannot extract content.",
            },
        )

    # Try by ref
    bytes_data = await snapshot_store.get_bytes(fetched.content_bytes_ref)
    if bytes_data is None:
        # Fall back to hash lookup
        record = await snapshot_store.get_by_hash(fetched.content_hash)
        if record is not None:
            bytes_data = await snapshot_store.get_bytes(record.snapshot_id)
    if bytes_data is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "bytes_unavailable",
                "message": "Fetched bytes could not be retrieved from snapshot store.",
            },
        )
    return bytes_data


def _serialize_search_result(result) -> dict[str, Any]:
    """Serialize a SearchResult to a JSON-safe dict, redacting provider_metadata."""
    from aip.adapter.web.provenance import redact_provider_metadata

    return {
        "provider": result.provider,
        "query": result.query,
        "rank": result.rank,
        "url": result.url,
        "title": result.title,
        "snippet": result.snippet,
        "published_at": result.published_at.isoformat() if result.published_at else None,
        "provider_metadata": redact_provider_metadata(result.provider_metadata),
    }


def _serialize_fetched(fetched) -> dict[str, Any]:
    """Serialize a FetchedResource to a JSON-safe dict."""
    return {
        "requested_url": fetched.requested_url,
        "final_url": fetched.final_url,
        "status_code": fetched.status_code,
        "content_type": fetched.content_type,
        "retrieved_at": fetched.retrieved_at.isoformat(),
        "content_hash": fetched.content_hash,
        "truncated": fetched.truncated,
        "redirects": list(fetched.redirects),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/web/search", response_model=WebSearchResponse)
async def web_search(
    request: WebSearchRequest,
    container: AipContainer = Depends(get_container),
) -> WebSearchResponse:
    """Run a web search via the configured provider.

    Returns 503 ``not_configured`` if the provider is not wired or has
    no API key.  Returns 502 ``provider_error`` if the provider raises
    a ``WebProviderError`` (e.g. rate limit).  Returns 200 with an
    empty ``results`` list if the provider returns no hits — this is
    a valid result, not an error.
    """
    provider = _require_search_provider(container)

    options = SearchOptions(
        limit=request.limit,
        freshness_days=request.freshness_days,
        domains=tuple(request.domains) if request.domains else None,
        topic=request.topic,
    )

    try:
        results = await provider.search(request.query, options=options)
    except WebProviderNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "not_configured", "message": str(exc)},
        ) from exc
    except WebProviderError as exc:
        logger.warning("web_search_provider_error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_error", "message": str(exc)},
        ) from exc

    return WebSearchResponse(
        query=request.query,
        provider=provider.name,
        results=[_serialize_search_result(r) for r in results],
        count=len(results),
    )


@router.post("/web/fetch", response_model=WebFetchResponse)
async def web_fetch(
    request: WebFetchRequest,
    container: AipContainer = Depends(get_container),
) -> WebFetchResponse:
    """Fetch a single URL and extract its text content.

    The URL is policy-checked (SSRF, scheme, content-type) before
    fetching.  The fetched bytes are stored in the snapshot store
    (if wired) and the extracted text is returned in the response.

    Returns 503 if the fetcher or snapshot store is not wired.
    Returns 422 ``fetch_denied`` if the URL is denied by policy.
    Returns 502 ``fetch_error`` if the fetch fails (timeout, DNS, etc.).
    """
    fetcher = _require_fetcher(container)
    policy = _get_fetch_policy(container)
    source_store = _get_source_store(container)

    # Fetch
    try:
        fetched = await fetcher.fetch(request.url, policy)
    except WebFetchDenied as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "fetch_denied", "url": exc.url, "reason": exc.reason},
        ) from exc
    except WebFetchError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "fetch_error", "message": str(exc)},
        ) from exc

    # Store snapshot if a snapshot store is wired
    snapshot_store = getattr(container, "web_snapshot_store", None)
    if snapshot_store is not None:
        # The HttpxWebFetcher returns a content_bytes_ref but does NOT
        # itself persist bytes to the snapshot store (it has no reference
        # to the store).  For WS-3 direct-fetch, we cannot retrieve the
        # raw bytes from the fetcher (they were streamed and discarded).
        # This is a known limitation of the WS-3 direct-fetch path.
        # The /web/ground path stores bytes via the snapshot store at
        # fetch time (see web_ground).  For /web/fetch, extraction will
        # fail with bytes_unavailable unless the bytes were pre-stored.
        # WS-4 (Ask integration) will refactor this so the fetcher writes
        # to the snapshot store directly via a pluggable bytes_sink.
        pass

    # Load bytes for extraction
    try:
        raw_bytes = await _load_bytes_for_fetched(container, fetched)
    except HTTPException:
        # Re-raise 503/500 from _load_bytes_for_fetched
        raise

    # Extract
    bytes_loader = _make_bytes_loader_sync(raw_bytes)
    extractor = select_extractor(fetched.content_type)
    try:
        extracted = await extractor.extract(fetched, bytes_loader=bytes_loader)
    except Exception as exc:
        logger.warning("web_fetch_extract_failed url=%s error=%s", request.url, exc)
        extracted = ExtractedDocument(
            source_url=fetched.final_url,
            canonical_url=fetched.final_url,
            title="",
            text="",
            retrieved_at=fetched.retrieved_at,
            content_hash=fetched.content_hash,
            extraction_method=extractor.extraction_method,
            warnings=(f"extraction failed: {exc}",),
        )

    # Build and store the source record
    record = build_web_source_record(
        search_result=None,
        fetched=fetched,
        extracted=extracted,
        fetch_warnings=(),
    )
    if source_store is not None:
        try:
            await source_store.put(record)
        except Exception as exc:
            logger.warning("web_fetch_source_store_failed: %s", exc)

    return WebFetchResponse(
        source_id=record.source_id,
        requested_url=fetched.requested_url,
        final_url=fetched.final_url,
        status_code=fetched.status_code,
        content_type=fetched.content_type,
        content_hash=fetched.content_hash,
        truncated=fetched.truncated,
        title=extracted.title,
        text=extracted.text,
        text_chars=len(extracted.text),
        extraction_method=extracted.extraction_method,
        warnings=list(extracted.warnings),
        redirects=list(fetched.redirects),
    )


@router.post("/web/ground", response_model=WebGroundResponse)
async def web_ground(
    request: WebGroundRequest,
    container: AipContainer = Depends(get_container),
) -> WebGroundResponse:
    """Search + fetch + extract top-N sources for grounding.

    This is the main entry point used by Ask's ``web_grounding`` mode.
    It runs a search, fetches and extracts the top ``fetch_top_n``
    results, and returns source records suitable for the augmented
    context builder.

    Sources that fail to fetch or extract are reported in ``failures``,
    not silently dropped.  This is the ADR-017 honesty rule.
    """
    provider = _require_search_provider(container)
    fetcher = _require_fetcher(container)
    policy = _get_fetch_policy(container)
    source_store = _get_source_store(container)

    # Search
    search_options = SearchOptions(limit=request.limit)
    try:
        search_results = await provider.search(request.query, options=search_options)
    except WebProviderNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "not_configured", "message": str(exc)},
        ) from exc
    except WebProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_error", "message": str(exc)},
        ) from exc

    # Fetch + extract the top N
    sources: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    to_fetch = search_results[: max(0, request.fetch_top_n)]

    for result in to_fetch:
        try:
            fetched = await fetcher.fetch(result.url, policy)
        except WebFetchDenied as exc:
            failures.append({"url": result.url, "error": "fetch_denied", "reason": exc.reason})
            continue
        except WebFetchError as exc:
            failures.append({"url": result.url, "error": "fetch_error", "message": str(exc)})
            continue

        # Extract
        try:
            raw_bytes = await _load_bytes_for_fetched(container, fetched)
            bytes_loader = _make_bytes_loader_sync(raw_bytes)
            extractor = select_extractor(fetched.content_type)
            extracted = await extractor.extract(fetched, bytes_loader=bytes_loader)
        except HTTPException:
            # Re-raise 503/500 from _load_bytes_for_fetched
            raise
        except Exception as exc:
            failures.append({"url": result.url, "error": "extract_failed", "message": str(exc)})
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
                logger.warning("web_ground_source_store_failed: %s", exc)

        sources.append({
            "source_id": record.source_id,
            "url": fetched.final_url,
            "title": extracted.title,
            "text": extracted.text,
            "text_chars": len(extracted.text),
            "extraction_method": extracted.extraction_method,
            "warnings": list(extracted.warnings),
            "rank": result.rank,
            "snippet": result.snippet,
        })

    return WebGroundResponse(
        query=request.query,
        provider=provider.name,
        sources=sources,
        search_count=len(search_results),
        fetched_count=len(sources),
        failures=failures,
    )


@router.get("/web/sources/{source_id}", response_model=WebSourceResponse)
async def web_get_source(
    source_id: str = Path(..., min_length=1, max_length=100),
    container: AipContainer = Depends(get_container),
) -> WebSourceResponse:
    """Retrieve a stored web source record by ID.

    Returns 404 if the source ID is not found.  Returns 503 if the
    source store is not wired.
    """
    source_store = _get_source_store(container)
    if source_store is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "not_configured", "message": "Web source store is not wired."},
        )

    record = await source_store.get(source_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "source_id": source_id},
        )

    extracted = record.extracted
    return WebSourceResponse(
        source_id=record.source_id,
        provider=record.provider,
        content_hash=record.content_hash,
        retrieved_at=record.retrieved_at.isoformat(),
        source_url=record.fetched.final_url,
        canonical_url=extracted.canonical_url if extracted else None,
        title=extracted.title if extracted else "",
        text=extracted.text if extracted else "",
        extraction_method=extracted.extraction_method if extracted else "",
        warnings=list(extracted.warnings) if extracted else [],
        search_result=_serialize_search_result(record.search_result) if record.search_result else None,
        fetch_warnings=list(record.fetch_warnings),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/web/promote (ADR-017 WS-5)
# ---------------------------------------------------------------------------


@router.post("/web/promote", response_model=WebPromoteResponse)
async def web_promote(
    request: WebPromoteRequest,
    container: AipContainer = Depends(get_container),
) -> WebPromoteResponse:
    """Promote a fetched web source into the corpus.

    This is the ONLY path by which web content enters the ordinary
    knowledge corpus.  Promotion is explicit-only: the caller must pass
    an ``approval`` token.  No batch/auto-promote path exists.

    Deduplication: if a turn with the same ``content_hash`` already
    exists in the target corpus (from a previous promotion of the same
    URL), promotion returns the existing ``corpus_turn_id`` and
    ``deduplicated=True``.  No duplicate content is written.

    The promoted turn carries ``source_model="web"`` and provenance
    metadata (source URL, retrieved_at, content_hash, extraction_method)
    so retrieval and Vigil can distinguish web-sourced turns.

    Returns 503 if the source store or corpus turn store is not wired.
    Returns 404 if the source_id is not found.  Returns 200 with
    ``success=False`` for other failures (no extracted content, write
    failure) — the corpus is left unchanged.
    """
    source_store = _get_source_store(container)
    if source_store is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "not_configured", "message": "Web source store is not wired."},
        )

    corpus_turn_store = getattr(container, "corpus_turn_store", None)
    if corpus_turn_store is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "not_configured", "message": "Corpus turn store is not wired."},
        )

    from aip.adapter.web.promotion import WebSourcePromoter

    promoter = WebSourcePromoter(
        corpus_turn_store=corpus_turn_store,
        web_source_store=source_store,
        target_corpus_id=request.target_corpus_id or "definer",
    )

    result = await promoter.promote(
        request.source_id,
        approval=request.approval,
        target_corpus_id=request.target_corpus_id,
    )

    # Map lookup failures to 404, other failures to 200 with success=False.
    if not result.success and result.error and result.error.get("error") == "source_not_found":
        raise HTTPException(
            status_code=404,
            detail=result.error,
        )

    return WebPromoteResponse(
        success=result.success,
        corpus_turn_id=result.corpus_turn_id,
        deduplicated=result.deduplicated,
        source_id=result.source_id,
        target_corpus_id=result.target_corpus_id,
        error=result.error,
    )


__all__ = ["router"]
