"""Web health integration tests for ``aip.adapter.api.routes.health`` (ADR-017 WS-3).

Verifies that the /health endpoint reports the web provider state honestly:
    - not_configured when no provider is wired
    - not_configured when provider is wired but has no API key
    - available when provider is wired and has a key
    - fetcher/source_store/snapshot_store wired flags
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import health as health_routes
from aip.adapter.api.routes import web as web_routes
from aip.adapter.web.fake_provider import FakeSearchProvider
from aip.adapter.web.providers.tavily import TavilySearchProvider


def _make_app(container: AipContainer) -> FastAPI:
    app = FastAPI()
    app.include_router(health_routes.router, prefix="/api/v1")
    app.include_router(web_routes.router, prefix="/api/v1")
    app.state.container = container
    app.state.raw_config = {}
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _make_container(**web_attrs) -> AipContainer:
    container = AipContainer({})
    for k, v in web_attrs.items():
        setattr(container, k, v)
    return container


# ---------------------------------------------------------------------------
# not_configured: no provider wired
# ---------------------------------------------------------------------------


def test_health_web_not_configured_when_no_provider():
    container = _make_container()
    app = _make_app(container)
    with _client(app) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    web_block = resp.json()["web"]
    assert web_block["enabled"] is False
    assert web_block["provider"] is None
    assert web_block["provider_state"] == "not_configured"
    assert web_block["fetcher_wired"] is False


# ---------------------------------------------------------------------------
# not_configured: provider wired but no key
# ---------------------------------------------------------------------------


def test_health_web_not_configured_when_provider_has_no_key():
    """A Tavily provider with no API key reports not_configured."""
    provider = TavilySearchProvider(key_loader=lambda: "")
    container = _make_container(web_search_provider=provider)
    app = _make_app(container)
    with _client(app) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    web_block = resp.json()["web"]
    assert web_block["enabled"] is True
    assert web_block["provider"] == "tavily"
    assert web_block["provider_state"] == "not_configured"


# ---------------------------------------------------------------------------
# available: provider wired with key
# ---------------------------------------------------------------------------


def test_health_web_available_when_provider_has_key():
    """A Tavily provider with an API key reports available."""
    provider = TavilySearchProvider(key_loader=lambda: "tvly-fake-key")
    container = _make_container(web_search_provider=provider)
    app = _make_app(container)
    with _client(app) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    web_block = resp.json()["web"]
    assert web_block["enabled"] is True
    assert web_block["provider"] == "tavily"
    assert web_block["provider_state"] == "available"


def test_health_web_fake_provider_is_available():
    """The FakeSearchProvider (used in CI) reports available (no key concept)."""
    provider = FakeSearchProvider({})
    container = _make_container(web_search_provider=provider)
    app = _make_app(container)
    with _client(app) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    web_block = resp.json()["web"]
    assert web_block["provider"] == "fake"
    assert web_block["provider_state"] == "available"


# ---------------------------------------------------------------------------
# Wired flags for fetcher / source_store / snapshot_store
# ---------------------------------------------------------------------------


def test_health_web_reports_wired_flags():
    """The health block reports fetcher/source_store/snapshot_store wired state."""
    from aip.adapter.web.fake_provider import FakeWebFetcher
    from aip.adapter.web.snapshot import (
        InMemoryWebSnapshotStore,
        InMemoryWebSourceStore,
    )

    provider = FakeSearchProvider({})
    fetcher = FakeWebFetcher({})
    container = _make_container(
        web_search_provider=provider,
        web_fetcher=fetcher,
        web_source_store=InMemoryWebSourceStore(),
        web_snapshot_store=InMemoryWebSnapshotStore(),
    )
    app = _make_app(container)
    with _client(app) as client:
        resp = client.get("/api/v1/health")
    web_block = resp.json()["web"]
    assert web_block["fetcher_wired"] is True
    assert web_block["source_store_wired"] is True
    assert web_block["snapshot_store_wired"] is True


def test_health_web_reports_partially_wired():
    """Only some components wired."""
    from aip.adapter.web.snapshot import InMemoryWebSourceStore

    provider = FakeSearchProvider({})
    container = _make_container(
        web_search_provider=provider,
        web_source_store=InMemoryWebSourceStore(),
        # fetcher and snapshot_store NOT wired
    )
    app = _make_app(container)
    with _client(app) as client:
        resp = client.get("/api/v1/health")
    web_block = resp.json()["web"]
    assert web_block["fetcher_wired"] is False
    assert web_block["source_store_wired"] is True
    assert web_block["snapshot_store_wired"] is False
