"""No-key-in-logs test for the WS-3 web surface (ADR-017 WS-3).

Defensive test: the API key must NEVER appear in:
    - response bodies (any /api/v1/web/* route)
    - response headers
    - the /health response
    - provider_metadata fields in stored source records

This test uses a Tavily provider with a known fake key and asserts the
key string does not appear anywhere in the HTTP response.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import web as web_routes
from aip.adapter.web.providers.tavily import (
    DEFAULT_TAVILY_ENDPOINT,
    TavilySearchProvider,
)

KNOWN_KEY = "tvly-LEAK-TEST-KEY-12345"


@pytest.fixture
def app_with_tavily():
    """Build an app with a Tavily provider wired to a known fake key."""
    provider = TavilySearchProvider(key_loader=lambda: KNOWN_KEY)
    container = AipContainer({})
    container.web_search_provider = provider
    container.web_fetcher = None
    container.web_source_store = None
    container.web_snapshot_store = None

    app = FastAPI()
    app.include_router(web_routes.router, prefix="/api/v1")
    app.state.container = container
    app.state.raw_config = {}
    return app


def _client(app) -> TestClient:
    return TestClient(app)


def _assert_no_key(text: str):
    """Assert the known key does not appear anywhere in ``text``."""
    assert KNOWN_KEY not in text, (
        f"API key leaked into response body.  "
        f"Key {KNOWN_KEY!r} found in response (length {len(text)})."
    )


# ---------------------------------------------------------------------------
# /api/v1/web/search
# ---------------------------------------------------------------------------


@respx.mock
def test_search_response_does_not_leak_key(app_with_tavily):
    """The /search response body must not contain the API key."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {
                    "url": "https://example.com/x",
                    "title": "Title",
                    "content": "Snippet",
                    "score": 0.9,
                }
            ]
        })
    )
    with _client(app_with_tavily) as client:
        resp = client.post("/api/v1/web/search", json={"query": "test"})
    assert resp.status_code == 200
    _assert_no_key(resp.text)
    # Also check headers
    for header_name, header_value in resp.headers.items():
        assert KNOWN_KEY not in header_value, f"Key leaked into header {header_name!r}"


@respx.mock
def test_search_error_response_does_not_leak_key(app_with_tavily):
    """When Tavily echoes the key in an error, the 502 response must not leak it."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(
            500,
            text=f'{{"error": "bad key {KNOWN_KEY} provided"}}',
        )
    )
    with _client(app_with_tavily) as client:
        resp = client.post("/api/v1/web/search", json={"query": "test"})
    assert resp.status_code == 502
    _assert_no_key(resp.text)


# ---------------------------------------------------------------------------
# /api/v1/web/fetch (not-configured path)
# ---------------------------------------------------------------------------


def test_fetch_not_configured_response_does_not_leak_key(app_with_tavily):
    """The 503 not_configured response must not leak the key."""
    # fetcher is None → 503 not_configured.  Even though the provider is wired
    # (with the known key), the response must not include it.
    with _client(app_with_tavily) as client:
        resp = client.post("/api/v1/web/fetch", json={"url": "https://example.com/"})
    assert resp.status_code == 503
    _assert_no_key(resp.text)


# ---------------------------------------------------------------------------
# /api/v1/web/sources/{id} (not-configured path)
# ---------------------------------------------------------------------------


def test_sources_not_configured_response_does_not_leak_key(app_with_tavily):
    """The 503 not_configured response must not leak the key."""
    with _client(app_with_tavily) as client:
        resp = client.get("/api/v1/web/sources/src_anything")
    assert resp.status_code == 503
    _assert_no_key(resp.text)


# ---------------------------------------------------------------------------
# /api/v1/health
# ---------------------------------------------------------------------------


def test_health_response_does_not_leak_key(app_with_tavily):
    """The /health response must not contain the API key, even though the
    provider is wired with it."""
    from aip.adapter.api.routes import health as health_routes

    app = app_with_tavily
    app.include_router(health_routes.router, prefix="/api/v1")
    with _client(app) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    _assert_no_key(resp.text)
