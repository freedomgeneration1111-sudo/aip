"""WS-5 route-level E2E tests for POST /api/v1/web/promote (ADR-017 WS-5).

Verifies the HTTP route end-to-end: request validation, 503/404 error
paths, and the happy path through the WebSourcePromoter.

Uses a StubCorpusTurnStore + InMemoryWebSourceStore so no real database
is needed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import web as web_routes
from aip.adapter.web.snapshot import InMemoryWebSourceStore
from aip.foundation.schemas.corpus_turn import CorpusTurn
from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    SearchResult,
    WebSourceRecord,
    sha256_hex,
)

# ---------------------------------------------------------------------------
# Stub CorpusTurnStore
# ---------------------------------------------------------------------------


class StubCorpusTurnStore:
    def __init__(self) -> None:
        self._turns: dict[str, CorpusTurn] = {}

    async def get_turn(self, turn_id: str) -> CorpusTurn | None:
        return self._turns.get(turn_id)

    async def write_turn(self, turn: CorpusTurn) -> None:
        self._turns[turn.turn_id] = turn

    def all_turns(self) -> list[CorpusTurn]:
        return list(self._turns.values())


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(
    *,
    source_store=None,
    corpus_turn_store=None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(web_routes.router, prefix="/api/v1")

    container = AipContainer({})
    container.web_source_store = source_store or InMemoryWebSourceStore()
    container.corpus_turn_store = corpus_turn_store or StubCorpusTurnStore()
    app.state.container = container
    app.state.raw_config = {}
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _make_and_store_record(source_store, *, source_id="src_test", url="https://example.com/a", text="article body"):
    """Create a web source record and store it."""
    retrieved_at = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
    sr = SearchResult(
        provider="tavily", query="q", rank=1, url=url, title="Title", snippet="s",
    )
    fr = FetchedResource(
        requested_url=url, final_url=url, status_code=200, content_type="text/html",
        content_bytes_ref=f"fake:{url}", retrieved_at=retrieved_at, content_hash=sha256_hex(text),
    )
    ed = ExtractedDocument(
        source_url=url, canonical_url=url, title="Title", text=text,
        retrieved_at=retrieved_at, content_hash=sha256_hex(text),
        extraction_method="html_readability",
    )
    record = WebSourceRecord(
        source_id=source_id, search_result=sr, fetched=fr, extracted=ed,
        provider="tavily", retrieved_at=retrieved_at, content_hash=sha256_hex(text),
    )
    asyncio.run(source_store.put(record))
    return record


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_promote_happy_path():
    """POST /web/promote writes a new corpus turn."""
    source_store = InMemoryWebSourceStore()
    corpus_store = StubCorpusTurnStore()
    record = _make_and_store_record(source_store)

    app = _make_app(source_store=source_store, corpus_turn_store=corpus_store)
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": record.source_id,
            "approval": "definer-approved",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["deduplicated"] is False
    assert data["corpus_turn_id"]  # non-empty
    assert data["source_id"] == record.source_id
    assert data["target_corpus_id"] == "definer"
    assert data["error"] is None

    # The turn was written
    turns = corpus_store.all_turns()
    assert len(turns) == 1
    assert turns[0].source_model == "web"


def test_promote_dedup():
    """Promoting the same source twice returns deduplicated=True."""
    source_store = InMemoryWebSourceStore()
    corpus_store = StubCorpusTurnStore()
    record = _make_and_store_record(source_store)

    app = _make_app(source_store=source_store, corpus_turn_store=corpus_store)
    with _client(app) as client:
        resp1 = client.post("/api/v1/web/promote", json={
            "source_id": record.source_id, "approval": "yes",
        })
        resp2 = client.post("/api/v1/web/promote", json={
            "source_id": record.source_id, "approval": "yes",
        })
    assert resp1.json()["deduplicated"] is False
    assert resp2.json()["deduplicated"] is True
    assert resp1.json()["corpus_turn_id"] == resp2.json()["corpus_turn_id"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_promote_source_not_found_returns_404():
    """A non-existent source_id returns 404."""
    source_store = InMemoryWebSourceStore()
    corpus_store = StubCorpusTurnStore()
    app = _make_app(source_store=source_store, corpus_turn_store=corpus_store)
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": "src_nonexistent", "approval": "yes",
        })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "source_not_found"


def test_promote_missing_approval_returns_422():
    """Missing approval field fails pydantic validation (422)."""
    app = _make_app()
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": "src_test",
            # approval missing
        })
    assert resp.status_code == 422  # pydantic validation


def test_promote_empty_approval_returns_422():
    """Empty approval string fails pydantic min_length=1 validation."""
    app = _make_app()
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": "src_test", "approval": "",
        })
    assert resp.status_code == 422


def test_promote_source_store_not_wired_returns_503():
    """When web_source_store is None, /promote returns 503."""
    app = _make_app(source_store=None)
    # Explicitly unwire
    app.state.container.web_source_store = None
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": "src_test", "approval": "yes",
        })
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "not_configured"


def test_promote_corpus_store_not_wired_returns_503():
    """When corpus_turn_store is None, /promote returns 503."""
    source_store = InMemoryWebSourceStore()
    _make_and_store_record(source_store)
    app = _make_app(source_store=source_store)
    app.state.container.corpus_turn_store = None  # unwire
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": "src_test", "approval": "yes",
        })
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "not_configured"


# ---------------------------------------------------------------------------
# Provenance in promoted turn
# ---------------------------------------------------------------------------


def test_promoted_turn_carries_web_provenance():
    """The promoted CorpusTurn has source_model='web' and provenance metadata."""
    source_store = InMemoryWebSourceStore()
    corpus_store = StubCorpusTurnStore()
    record = _make_and_store_record(source_store, url="https://example.com/provenance")

    app = _make_app(source_store=source_store, corpus_turn_store=corpus_store)
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": record.source_id, "approval": "yes",
        })
    assert resp.status_code == 200

    turn = corpus_store.all_turns()[0]
    assert turn.source_model == "web"
    assert turn.source_account == "web_promotion"
    meta = json.loads(turn.metadata_json)
    assert meta["source_url"] == "https://example.com/provenance"
    assert meta["extraction_method"] == "html_readability"
    assert meta["content_hash"]
    assert meta["retrieved_at"]


# ---------------------------------------------------------------------------
# Custom target corpus
# ---------------------------------------------------------------------------


def test_promote_with_custom_target_corpus():
    """The target_corpus_id override is reflected in the response."""
    source_store = InMemoryWebSourceStore()
    corpus_store = StubCorpusTurnStore()
    record = _make_and_store_record(source_store)

    app = _make_app(source_store=source_store, corpus_turn_store=corpus_store)
    with _client(app) as client:
        resp = client.post("/api/v1/web/promote", json={
            "source_id": record.source_id,
            "approval": "yes",
            "target_corpus_id": "research",
        })
    assert resp.status_code == 200
    assert resp.json()["target_corpus_id"] == "research"
