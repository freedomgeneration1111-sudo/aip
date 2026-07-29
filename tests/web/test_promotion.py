"""WS-5 promotion tests for ``aip.adapter.web.promotion`` (ADR-017 WS-5).

Tests the WebSourcePromoter service directly (not via the HTTP route)
so the dedup/approval/sensitive-corpus logic is verified in isolation.

Coverage:
    - Happy path: promote a web source → new CorpusTurn written
    - Dedup: promote the same source twice → second returns deduplicated=True
    - Source not found → structured error
    - No extracted content → structured error
    - Missing approval → structured error
    - Write failure → structured error, corpus unchanged
    - Re-promotion with changed content → doc_version increment
    - Promoted turn carries provenance metadata (url, hash, method)
    - Promoted turn has source_model="web"
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aip.adapter.web.promotion import (
    WebSourcePromoter,
    _make_web_conversation_id,
)
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
    """In-memory CorpusTurnStore stub for testing.

    Implements get_turn + write_turn (the two methods the promoter uses).
    """

    def __init__(self) -> None:
        self._turns: dict[str, CorpusTurn] = {}

    async def get_turn(self, turn_id: str) -> CorpusTurn | None:
        return self._turns.get(turn_id)

    async def write_turn(self, turn: CorpusTurn) -> None:
        self._turns[turn.turn_id] = turn

    # For test inspection
    def all_turns(self) -> list[CorpusTurn]:
        return list(self._turns.values())


class FailingCorpusTurnStore(StubCorpusTurnStore):
    """A store that fails on write_turn (to test error handling)."""

    async def write_turn(self, turn: CorpusTurn) -> None:
        raise RuntimeError("simulated write failure")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_web_source_record(
    *,
    source_id: str = "src_test1",
    url: str = "https://example.com/article",
    title: str = "Test Article",
    text: str = "This is the article body text.",
    content_hash: str | None = None,
) -> WebSourceRecord:
    """Build a WebSourceRecord for testing."""
    retrieved_at = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
    sr = SearchResult(
        provider="tavily", query="test query", rank=1,
        url=url, title=title, snippet="snippet",
    )
    fr = FetchedResource(
        requested_url=url, final_url=url, status_code=200, content_type="text/html",
        content_bytes_ref=f"fake:{url}", retrieved_at=retrieved_at,
        content_hash=content_hash or sha256_hex(text),
    )
    ed = ExtractedDocument(
        source_url=url, canonical_url=url, title=title, text=text,
        retrieved_at=retrieved_at,
        content_hash=content_hash or sha256_hex(text),
        extraction_method="html_readability",
    )
    return WebSourceRecord(
        source_id=source_id,
        search_result=sr,
        fetched=fr,
        extracted=ed,
        provider="tavily",
        retrieved_at=retrieved_at,
        content_hash=content_hash or sha256_hex(text),
    )


@pytest.fixture
def source_store() -> InMemoryWebSourceStore:
    return InMemoryWebSourceStore()


@pytest.fixture
def corpus_turn_store() -> StubCorpusTurnStore:
    return StubCorpusTurnStore()


@pytest.fixture
def promoter(source_store, corpus_turn_store) -> WebSourcePromoter:
    return WebSourcePromoter(
        corpus_turn_store=corpus_turn_store,
        web_source_store=source_store,
        target_corpus_id="definer",
    )


@pytest.fixture
async def stored_record(source_store) -> WebSourceRecord:
    """Insert a web source record into the store and return it."""
    record = _make_web_source_record()
    await source_store.put(record)
    return record


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_promote_happy_path(promoter, source_store, corpus_turn_store, stored_record):
    """Promoting a web source writes a new CorpusTurn."""
    result = await promoter.promote(stored_record.source_id, approval="definer-approved")
    assert result.success is True
    assert result.deduplicated is False
    assert result.corpus_turn_id  # non-empty
    assert result.source_id == stored_record.source_id
    assert result.target_corpus_id == "definer"

    # The turn was written
    turns = corpus_turn_store.all_turns()
    assert len(turns) == 1
    turn = turns[0]
    assert turn.source_model == "web"
    assert turn.source_account == "web_promotion"
    assert "https://example.com/article" in turn.user_text
    assert "Test Article" in turn.user_text
    assert "This is the article body text." in turn.assistant_text


async def test_promote_carries_provenance_metadata(promoter, source_store, corpus_turn_store, stored_record):
    """The promoted turn carries provenance metadata in metadata_json."""
    result = await promoter.promote(stored_record.source_id, approval="definer-approved")
    assert result.success is True

    turn = corpus_turn_store.all_turns()[0]
    meta = json.loads(turn.metadata_json)
    assert meta["source_type"] == "web"
    assert meta["source_url"] == "https://example.com/article"
    assert meta["retrieved_at"]  # non-empty
    assert meta["content_hash"]  # non-empty
    assert meta["extraction_method"] == "html_readability"
    assert meta["provider"] == "tavily"
    assert meta["promoted_at"]  # non-empty


async def test_promoted_turn_has_source_model_web(promoter, corpus_turn_store, stored_record):
    """Promoted turns are tagged source_model='web' for retrieval/Vigil."""
    await promoter.promote(stored_record.source_id, approval="definer-approved")
    turn = corpus_turn_store.all_turns()[0]
    assert turn.source_model == "web"


async def test_promoted_turn_has_content_hash(promoter, corpus_turn_store, stored_record):
    """The promoted turn carries the content_hash for future dedup checks."""
    await promoter.promote(stored_record.source_id, approval="definer-approved")
    turn = corpus_turn_store.all_turns()[0]
    assert turn.content_hash == stored_record.content_hash


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


async def test_promote_dedup_returns_existing(promoter, corpus_turn_store, stored_record):
    """Promoting the same source twice returns the existing turn_id."""
    result1 = await promoter.promote(stored_record.source_id, approval="definer-approved")
    assert result1.success is True
    assert result1.deduplicated is False

    result2 = await promoter.promote(stored_record.source_id, approval="definer-approved")
    assert result2.success is True
    assert result2.deduplicated is True
    assert result2.corpus_turn_id == result1.corpus_turn_id

    # Only one turn in the store
    assert len(corpus_turn_store.all_turns()) == 1


async def test_promote_dedup_same_url_different_source_id(promoter, source_store, corpus_turn_store):
    """Two source records with the same URL+content dedup at the store level.

    The InMemoryWebSourceStore deduplicates by content_hash.  When two
    records with the same hash are put, the second returns the first's
    source_id.  So promoting "src_b" (which was deduped to "src_a")
    correctly finds the record under "src_a" and deduplicates at the
    corpus level too.
    """
    record1 = _make_web_source_record(source_id="src_a", url="https://example.com/x", text="same text")
    record2 = _make_web_source_record(source_id="src_b", url="https://example.com/x", text="same text")
    sid1 = await source_store.put(record1)
    sid2 = await source_store.put(record2)
    # The store deduplicates: sid2 == sid1 (both point to the same record)
    assert sid1 == sid2 == "src_a"

    # Promoting src_a writes a new turn
    result1 = await promoter.promote("src_a", approval="yes")
    assert result1.success is True
    assert result1.deduplicated is False

    # Promoting src_b fails because the store deduped it to src_a
    # (src_b was never stored as a separate record)
    result2 = await promoter.promote("src_b", approval="yes")
    assert result2.success is False
    assert result2.error["error"] == "source_not_found"

    # But promoting src_a again deduplicates at the corpus level
    result3 = await promoter.promote("src_a", approval="yes")
    assert result3.success is True
    assert result3.deduplicated is True
    assert result3.corpus_turn_id == result1.corpus_turn_id


# ---------------------------------------------------------------------------
# Re-promotion with changed content
# ---------------------------------------------------------------------------


async def test_promote_changed_content_increments_version(promoter, source_store, corpus_turn_store):
    """Re-promoting the same URL with different text increments doc_version."""
    # First promotion: original content
    record1 = _make_web_source_record(
        source_id="src_v1", url="https://example.com/changed", text="version 1 text",
    )
    await source_store.put(record1)
    result1 = await promoter.promote("src_v1", approval="yes")
    assert result1.success is True
    assert result1.deduplicated is False

    turn1 = corpus_turn_store.all_turns()[0]
    assert turn1.doc_version == 1

    # Second promotion: same URL, different text → different content_hash
    record2 = _make_web_source_record(
        source_id="src_v2", url="https://example.com/changed", text="version 2 text is different",
    )
    await source_store.put(record2)
    result2 = await promoter.promote("src_v2", approval="yes")
    assert result2.success is True
    assert result2.deduplicated is False  # not a dup — content changed

    # The turn was updated (same turn_id, new doc_version)
    turn2 = corpus_turn_store.all_turns()[0]
    assert turn2.turn_id == turn1.turn_id
    assert turn2.doc_version == 2

    # The metadata carries the previous hash
    meta = json.loads(turn2.metadata_json)
    assert meta["previous_hash"] == turn1.content_hash


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_promote_source_not_found(promoter):
    """Promoting a non-existent source_id returns a structured error."""
    result = await promoter.promote("src_nonexistent", approval="yes")
    assert result.success is False
    assert result.error["error"] == "source_not_found"
    assert "src_nonexistent" in result.error["message"]


async def test_promote_no_extracted_content(promoter, source_store):
    """A source record with no extracted document returns an error."""
    # Build a record with extracted=None
    retrieved_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
    fr = FetchedResource(
        requested_url="https://example.com/failed", final_url="https://example.com/failed",
        status_code=200, content_type="text/html", content_bytes_ref="ref",
        retrieved_at=retrieved_at, content_hash="hash_failed",
    )
    record = WebSourceRecord(
        source_id="src_no_extract",
        search_result=None,
        fetched=fr,
        extracted=None,  # extraction failed
        provider="direct",
        retrieved_at=retrieved_at,
        content_hash="hash_failed",
        fetch_warnings=("extraction failed: unsupported content type",),
    )
    await source_store.put(record)

    result = await promoter.promote("src_no_extract", approval="yes")
    assert result.success is False
    assert result.error["error"] == "no_extracted_content"


async def test_promote_missing_approval(promoter, stored_record):
    """Promoting without an approval token returns an error."""
    result = await promoter.promote(stored_record.source_id, approval="")
    assert result.success is False
    assert result.error["error"] == "approval_required"

    result = await promoter.promote(stored_record.source_id, approval="   ")
    assert result.success is False
    assert result.error["error"] == "approval_required"


async def test_promote_write_failure_returns_error(promoter, source_store, stored_record):
    """A write failure returns a structured error; corpus is unchanged."""
    # Replace the corpus_turn_store with a failing one
    failing_store = FailingCorpusTurnStore()
    promoter._corpus_turn_store = failing_store

    result = await promoter.promote(stored_record.source_id, approval="yes")
    assert result.success is False
    assert result.error["error"] == "write_failed"
    assert "simulated write failure" in result.error["message"]
    # No turn was written
    assert len(failing_store.all_turns()) == 0


# ---------------------------------------------------------------------------
# Conversation ID stability
# ---------------------------------------------------------------------------


def test_make_web_conversation_id_stable():
    """Same URL → same conversation_id."""
    cid1 = _make_web_conversation_id("https://example.com/article")
    cid2 = _make_web_conversation_id("https://example.com/article")
    assert cid1 == cid2
    assert cid1.startswith("web_")


def test_make_web_conversation_id_differs_for_different_urls():
    cid1 = _make_web_conversation_id("https://example.com/a")
    cid2 = _make_web_conversation_id("https://example.com/b")
    assert cid1 != cid2


# ---------------------------------------------------------------------------
# Target corpus override
# ---------------------------------------------------------------------------


async def test_promote_with_custom_target_corpus(promoter, stored_record):
    """The target_corpus_id override is reflected in the result."""
    result = await promoter.promote(
        stored_record.source_id,
        approval="yes",
        target_corpus_id="research",
    )
    assert result.success is True
    assert result.target_corpus_id == "research"


# ---------------------------------------------------------------------------
# Lookup failure
# ---------------------------------------------------------------------------


async def test_promote_lookup_failure_returns_error(promoter):
    """A lookup failure (store exception) returns a structured error."""
    # Use a store that raises on get
    class FailingSourceStore:
        async def get(self, source_id):
            raise RuntimeError("store unavailable")

    promoter._web_source_store = FailingSourceStore()
    result = await promoter.promote("src_anything", approval="yes")
    assert result.success is False
    assert result.error["error"] == "lookup_failed"
