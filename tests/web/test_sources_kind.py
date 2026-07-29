"""WS-4 sources route kind discriminator tests (ADR-017 WS-4).

Verifies that GET /api/v1/sources:
    - Returns corpus sources with kind="corpus"
    - Returns web sources with kind="web" when the WebSourceStore is wired
    - Filters by kind=corpus / kind=web
    - Returns both when kind is omitted
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import sources as sources_routes
from aip.adapter.web.provenance import build_web_source_record
from aip.adapter.web.snapshot import (
    InMemoryWebSnapshotStore,
    InMemoryWebSourceStore,
)
from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    SearchResult,
)

# ---------------------------------------------------------------------------
# App + container factory
# ---------------------------------------------------------------------------


def _make_app(
    *,
    web_source_store=None,
    entity_store=None,
    knowledge_store=None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(sources_routes.router, prefix="/api/v1")

    container = AipContainer({})
    container.web_source_store = web_source_store or InMemoryWebSourceStore()
    container.web_snapshot_store = InMemoryWebSnapshotStore()
    container.entity_store = entity_store  # None by default
    container.knowledge_store = knowledge_store  # None by default
    container.vector_store = None
    container.lexical_store = None

    app.state.container = container
    app.state.raw_config = {}
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _make_web_source_record(url: str, title: str, query: str = ""):
    """Build a WebSourceRecord for testing."""
    sr = SearchResult(
        provider="fake", query=query, rank=1, url=url, title=title, snippet="snippet",
    )
    fr = FetchedResource(
        requested_url=url, final_url=url, status_code=200, content_type="text/html",
        content_bytes_ref=f"fake:{url}", retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_hash="hash_" + url,
    )
    ed = ExtractedDocument(
        source_url=url, canonical_url=url, title=title, text="body text",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_hash="hash_" + url, extraction_method="html_readability",
    )
    return build_web_source_record(search_result=sr, fetched=fr, extracted=ed)


# ---------------------------------------------------------------------------
# Corpus sources carry kind="corpus"
# ---------------------------------------------------------------------------


def test_sources_returns_empty_when_no_stores():
    """With no stores wired, /sources returns an empty list."""
    app = _make_app(web_source_store=None)
    with _client(app) as client:
        resp = client.get("/api/v1/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sources"] == []
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# Web sources carry kind="web"
# ---------------------------------------------------------------------------


def test_sources_includes_web_sources_with_kind_tag():
    """When the WebSourceStore has records, they appear with kind="web"."""
    store = InMemoryWebSourceStore()
    import asyncio
    record = _make_web_source_record("https://example.com/article", "Test Article", query="python")
    asyncio.run(store.put(record))

    app = _make_app(web_source_store=store)
    with _client(app) as client:
        resp = client.get("/api/v1/sources")
    assert resp.status_code == 200
    data = resp.json()
    web_sources = [s for s in data["sources"] if s.get("kind") == "web"]
    assert len(web_sources) >= 1
    assert web_sources[0]["url"] == "https://example.com/article"
    assert web_sources[0]["title"] == "Test Article"
    assert web_sources[0]["source_type"] == "web"
    assert web_sources[0]["kind"] == "web"


# ---------------------------------------------------------------------------
# kind filter
# ---------------------------------------------------------------------------


def test_sources_filter_kind_web_returns_only_web():
    """kind=web returns only web sources."""
    store = InMemoryWebSourceStore()
    import asyncio
    record = _make_web_source_record("https://example.com/a1", "A1", query="q")
    asyncio.run(store.put(record))

    app = _make_app(web_source_store=store)
    with _client(app) as client:
        resp = client.get("/api/v1/sources?kind=web")
    assert resp.status_code == 200
    data = resp.json()
    assert all(s["kind"] == "web" for s in data["sources"])
    assert len(data["sources"]) >= 1


def test_sources_filter_kind_corpus_returns_only_corpus():
    """kind=corpus returns only corpus sources (no web sources)."""
    store = InMemoryWebSourceStore()
    import asyncio
    record = _make_web_source_record("https://example.com/a1", "A1", query="q")
    asyncio.run(store.put(record))

    app = _make_app(web_source_store=store)
    with _client(app) as client:
        resp = client.get("/api/v1/sources?kind=corpus")
    assert resp.status_code == 200
    data = resp.json()
    assert all(s["kind"] == "corpus" for s in data["sources"])
    # No web sources should appear
    assert not any(s.get("kind") == "web" for s in data["sources"])


def test_sources_no_kind_filter_returns_both():
    """Omitting kind returns both corpus and web sources (when both exist)."""
    store = InMemoryWebSourceStore()
    import asyncio
    record = _make_web_source_record("https://example.com/a1", "A1", query="q")
    asyncio.run(store.put(record))

    app = _make_app(web_source_store=store)
    with _client(app) as client:
        resp = client.get("/api/v1/sources")
    assert resp.status_code == 200
    data = resp.json()
    # At least the web source should be present
    kinds = {s.get("kind") for s in data["sources"]}
    assert "web" in kinds


# ---------------------------------------------------------------------------
# Web source record fields
# ---------------------------------------------------------------------------


def test_web_source_record_carries_provenance_fields():
    """Web source records in /sources carry url, retrieved_at, content_hash, extraction_method."""
    store = InMemoryWebSourceStore()
    import asyncio
    record = _make_web_source_record("https://example.com/provenance", "Provenance Test", query="q")
    asyncio.run(store.put(record))

    app = _make_app(web_source_store=store)
    with _client(app) as client:
        resp = client.get("/api/v1/sources?kind=web")
    data = resp.json()
    web_sources = [s for s in data["sources"] if s.get("kind") == "web"]
    assert len(web_sources) >= 1
    s = web_sources[0]
    assert s["url"] == "https://example.com/provenance"
    assert s["retrieved_at"]  # non-empty
    assert s["content_hash"]  # non-empty
    assert s["extraction_method"] == "html_readability"
    assert "provider" in s["metadata"]
    assert "fetch_warnings" in s["metadata"]
    assert "extraction_warnings" in s["metadata"]


# ---------------------------------------------------------------------------
# No web source store wired
# ---------------------------------------------------------------------------


def test_sources_no_web_store_returns_no_web_sources():
    """When web_source_store is None, no web sources appear (even with kind omitted)."""
    app = _make_app(web_source_store=None)
    with _client(app) as client:
        resp = client.get("/api/v1/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert not any(s.get("kind") == "web" for s in data["sources"])


def test_sources_kind_web_with_no_store_returns_empty():
    """kind=web with no web_source_store returns an empty list (not 500)."""
    app = _make_app(web_source_store=None)
    with _client(app) as client:
        resp = client.get("/api/v1/sources?kind=web")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sources"] == []
    assert data["total"] == 0
