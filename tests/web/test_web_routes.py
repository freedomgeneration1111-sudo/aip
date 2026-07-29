"""Web API route tests for ``aip.adapter.api.routes.web`` (ADR-017 WS-3).

Uses FastAPI's TestClient with a minimal container wired to fakes.
No live network — all HTTP is mocked via respx (for Tavily) or the
FakeWebFetcher (for fetch/ground).

Coverage:
    - POST /api/v1/web/search: happy path, empty results, not-configured 503,
      provider error 502
    - POST /api/v1/web/fetch: happy path, SSRF denial 422, fetch error 502,
      not-configured 503
    - POST /api/v1/web/ground: happy path with N sources, partial failures,
      not-configured 503
    - GET /api/v1/web/sources/{id}: 200, 404, not-configured 503
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import web as web_routes
from aip.adapter.web.fake_provider import (
    FakeSearchProvider,
    FakeWebFetcher,
    WebProviderError,
)
from aip.adapter.web.snapshot import (
    InMemoryWebSnapshotStore,
    InMemoryWebSourceStore,
)
from aip.foundation.schemas.web import (
    FetchedResource,
    SearchResult,
    sha256_hex,
)

# ---------------------------------------------------------------------------
# App + container factory
# ----------------------------------------------------------------------


def _make_app(
    *,
    search_provider=None,
    fetcher=None,
    source_store=None,
    snapshot_store=None,
) -> tuple[FastAPI, AipContainer]:
    """Build a minimal FastAPI app with the web router and a wired container."""
    app = FastAPI()
    app.include_router(web_routes.router, prefix="/api/v1")

    container = AipContainer({})
    container.web_search_provider = search_provider
    container.web_fetcher = fetcher
    container.web_source_store = source_store or InMemoryWebSourceStore()
    container.web_snapshot_store = snapshot_store or InMemoryWebSnapshotStore()
    app.state.container = container
    app.state.raw_config = {}
    return app, container


def _client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def fake_search_results() -> list[SearchResult]:
    return [
        SearchResult(
            provider="fake",
            query="python type hints",
            rank=1,
            url="https://example.com/article1",
            title="Article 1",
            snippet="First article snippet.",
        ),
        SearchResult(
            provider="fake",
            query="python type hints",
            rank=2,
            url="https://example.com/article2",
            title="Article 2",
            snippet="Second article snippet.",
        ),
    ]


@pytest.fixture
def fake_search_provider(fake_search_results) -> FakeSearchProvider:
    return FakeSearchProvider(results={"python type hints": fake_search_results})


@pytest.fixture
def fake_pages() -> dict[str, bytes]:
    return {
        "https://example.com/article1": (
            b"<html><head><title>Article 1</title></head>"
            b"<body><article><p>Article 1 body text.</p></article></body></html>"
        ),
        "https://example.com/article2": (
            b"<html><head><title>Article 2</title></head>"
            b"<body><article><p>Article 2 body text.</p></article></body></html>"
        ),
    }


@pytest.fixture
def fake_web_fetcher(fake_pages) -> FakeWebFetcher:
    return FakeWebFetcher(
        pages=fake_pages,
        retrieved_at=datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/web/search
# ----------------------------------------------------------------------


def test_search_happy_path(fake_search_provider, fake_web_fetcher):
    app, _ = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.post("/api/v1/web/search", json={"query": "python type hints"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "python type hints"
    assert data["provider"] == "fake"
    assert data["count"] == 2
    assert data["results"][0]["url"] == "https://example.com/article1"
    assert data["results"][0]["rank"] == 1


def test_search_empty_results(fake_search_provider, fake_web_fetcher):
    app, _ = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.post("/api/v1/web/search", json={"query": "nonexistent topic"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["results"] == []


def test_search_not_configured_returns_503():
    """When no provider is wired, /search returns 503 not_configured."""
    app, _ = _make_app(search_provider=None, fetcher=None)
    with _client(app) as client:
        resp = client.post("/api/v1/web/search", json={"query": "test"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "not_configured"


def test_search_provider_error_returns_502(fake_web_fetcher):
    """When the provider raises WebProviderError, /search returns 502."""

    class FailingProvider:
        @property
        def name(self) -> str:
            return "failing"

        async def search(self, query, *, options=None):
            raise WebProviderError("provider exploded")

        def _get_api_key(self):
            return "fake-key"  # is_provider_configured returns True

    app, _ = _make_app(search_provider=FailingProvider(), fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.post("/api/v1/web/search", json={"query": "test"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "provider_error"


def test_search_honors_limit(fake_search_provider, fake_web_fetcher):
    app, _ = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.post(
            "/api/v1/web/search",
            json={"query": "python type hints", "limit": 1},
        )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_search_validates_query_length(fake_search_provider, fake_web_fetcher):
    """An empty query is rejected by pydantic validation (422)."""
    app, _ = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.post("/api/v1/web/search", json={"query": ""})
    assert resp.status_code == 422  # pydantic validation


# ---------------------------------------------------------------------------
# POST /api/v1/web/fetch
# ----------------------------------------------------------------------


def _wire_snapshot_bytes(fetcher: FakeWebFetcher, container):
    """Pre-populate the snapshot store with the fetcher's fixture bytes.

    The HttpxWebFetcher stores bytes via the snapshot store; the FakeWebFetcher
    keeps them in its own dict.  For route tests we bridge the two by pre-
    populating the snapshot store under the content_bytes_ref the fake fetcher
    will produce.
    """
    # The FakeWebFetcher stores bytes under "fake:{url}" refs.  We pre-populate
    # the snapshot store so the route's bytes_loader can find them.
    async def _populate():
        for url, body in fetcher._pages.items():
            content_hash = sha256_hex(body)
            # Store under both the "fake:{url}" ref and the content_hash.
            sid, _ = await container.web_snapshot_store.put(
                requested_url=url,
                final_url=url,
                retrieved_at=datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc),
                content_type="text/html",
                content_hash=content_hash,
                bytes_data=body,
            )
            # Also store under the "fake:{url}" ref name so the bytes_loader finds it.
            # The InMemoryWebSnapshotStore.get_bytes takes snapshot_id, so we need
            # to alias.  For test simplicity, we monkey-patch the bytes_loader path
            # by storing the bytes under the ref name as the snapshot_id.
            # This is a test-only bridge.
    import asyncio
    asyncio.get_event_loop().run_until_complete(_populate())


def test_fetch_happy_path(fake_search_provider, fake_web_fetcher, fake_pages):
    """POST /web/fetch fetches a URL and extracts text."""
    app, container = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    # Pre-populate snapshot store so bytes_loader can find the bytes
    url = "https://example.com/article1"
    body = fake_pages[url]
    import asyncio
    asyncio.run(container.web_snapshot_store.put(
        requested_url=url, final_url=url,
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html", content_hash=sha256_hex(body), bytes_data=body,
    ))

    with _client(app) as client:
        resp = client.post("/api/v1/web/fetch", json={"url": url})
    assert resp.status_code == 200
    data = resp.json()
    assert data["requested_url"] == url
    assert data["final_url"] == url
    assert data["status_code"] == 200
    assert "Article 1" in data["title"]
    assert "Article 1 body text" in data["text"]
    assert data["extraction_method"] == "html_readability"


def test_fetch_ssrf_denied_returns_422(fake_search_provider, fake_web_fetcher):
    """An SSRF URL is denied with 422 fetch_denied."""
    app, _ = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.post("/api/v1/web/fetch", json={"url": "http://127.0.0.1/"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "fetch_denied"


def test_fetch_not_configured_returns_503():
    """When no fetcher is wired, /fetch returns 503."""
    # search_provider is configured (so _require_search_provider passes) but
    # fetcher is None.
    app, _ = _make_app(search_provider=FakeSearchProvider({}), fetcher=None)
    with _client(app) as client:
        resp = client.post("/api/v1/web/fetch", json={"url": "https://example.com/"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "not_configured"


def test_fetch_unknown_url_returns_502(fake_search_provider, fake_web_fetcher):
    """A fetch error (e.g. unknown URL) returns 502."""
    app, _ = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.post(
            "/api/v1/web/fetch",
            json={"url": "https://unknown.example.com/"},
        )
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "fetch_error"


# ---------------------------------------------------------------------------
# POST /api/v1/web/ground
# ----------------------------------------------------------------------


def test_ground_happy_path(fake_search_provider, fake_web_fetcher, fake_pages):
    """POST /web/ground returns sources with extracted text."""
    app, container = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    # Pre-populate snapshot store for both URLs
    import asyncio
    for url, body in fake_pages.items():
        asyncio.run(container.web_snapshot_store.put(
            requested_url=url, final_url=url,
            retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
            content_type="text/html", content_hash=sha256_hex(body), bytes_data=body,
        ))

    with _client(app) as client:
        resp = client.post(
            "/api/v1/web/ground",
            json={"query": "python type hints", "limit": 2, "fetch_top_n": 2},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "python type hints"
    assert data["provider"] == "fake"
    assert data["search_count"] == 2
    assert data["fetched_count"] == 2
    assert len(data["sources"]) == 2
    assert data["failures"] == []
    assert "Article 1 body text" in data["sources"][0]["text"]


def test_ground_partial_failure_reported(fake_search_provider, fake_web_fetcher, fake_pages):
    """Fetch failures are reported in 'failures', not silently dropped."""
    # Only one URL has fixture bytes; the other will fail to fetch.
    app, container = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    url = "https://example.com/article1"
    body = fake_pages[url]
    import asyncio
    asyncio.run(container.web_snapshot_store.put(
        requested_url=url, final_url=url,
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html", content_hash=sha256_hex(body), bytes_data=body,
    ))
    # Remove article2 from fetcher pages so it fails
    fake_web_fetcher._pages.pop("https://example.com/article2", None)

    with _client(app) as client:
        resp = client.post(
            "/api/v1/web/ground",
            json={"query": "python type hints", "limit": 2, "fetch_top_n": 2},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["fetched_count"] == 1
    assert len(data["failures"]) == 1
    assert data["failures"][0]["url"] == "https://example.com/article2"


def test_ground_not_configured_returns_503():
    app, _ = _make_app(search_provider=None, fetcher=None)
    with _client(app) as client:
        resp = client.post("/api/v1/web/ground", json={"query": "test"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/v1/web/sources/{source_id}
# ----------------------------------------------------------------------


def test_get_source_happy_path(fake_search_provider, fake_web_fetcher):
    """GET /web/sources/{id} returns a stored source record."""
    app, container = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    # Manually insert a source record
    from aip.adapter.web.provenance import build_web_source_record
    sr = SearchResult(
        provider="fake", query="q", rank=1,
        url="https://example.com/x", title="T", snippet="S",
    )
    fr = FetchedResource(
        requested_url="https://example.com/x", final_url="https://example.com/x",
        status_code=200, content_type="text/html", content_bytes_ref="ref",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_hash="hash_x",
    )
    from aip.foundation.schemas.web import ExtractedDocument
    ed = ExtractedDocument(
        source_url="https://example.com/x", canonical_url="https://example.com/x",
        title="T", text="body text", content_hash="hash_x",
        extraction_method="html_readability",
    )
    record = build_web_source_record(search_result=sr, fetched=fr, extracted=ed)
    import asyncio
    asyncio.run(container.web_source_store.put(record))

    with _client(app) as client:
        resp = client.get(f"/api/v1/web/sources/{record.source_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_id"] == record.source_id
    assert data["provider"] == "fake"
    assert data["title"] == "T"
    assert data["text"] == "body text"


def test_get_source_not_found_returns_404(fake_search_provider, fake_web_fetcher):
    app, _ = _make_app(search_provider=fake_search_provider, fetcher=fake_web_fetcher)
    with _client(app) as client:
        resp = client.get("/api/v1/web/sources/src_nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"


def test_get_source_not_configured_returns_503():
    """When no source store is wired, /sources returns 503."""
    app, container = _make_app(search_provider=None, fetcher=None)
    container.web_source_store = None  # explicitly unwire
    with _client(app) as client:
        resp = client.get("/api/v1/web/sources/src_anything")
    assert resp.status_code == 503
