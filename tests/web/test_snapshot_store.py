"""Tests for ``aip.adapter.web.snapshot`` (ADR-017 WS-1).

Covers both in-memory stores:
    - ``InMemoryWebSnapshotStore``: put dedup by hash, get/get_bytes/
      get_by_hash, delete_expired
    - ``InMemoryWebSourceStore``: put dedup by hash, get/get_by_hash,
      list_by_query (most-recent first), delete
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    SearchResult,
    WebSourceRecord,
)

# ---------------------------------------------------------------------------
# InMemoryWebSnapshotStore
# ---------------------------------------------------------------------------


async def test_snapshot_put_returns_new_id(snapshot_store):
    sid, dedup = await snapshot_store.put(
        requested_url="https://example.com",
        final_url="https://example.com",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="hash_a",
        bytes_data=b"page a",
    )
    assert sid.startswith("snap_")
    assert dedup is False


async def test_snapshot_put_deduplicates_by_hash(snapshot_store):
    sid1, dedup1 = await snapshot_store.put(
        requested_url="https://example.com",
        final_url="https://example.com",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="hash_a",
        bytes_data=b"page a",
    )
    sid2, dedup2 = await snapshot_store.put(
        requested_url="https://other.example.com",  # different URL, same hash
        final_url="https://other.example.com",
        retrieved_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="hash_a",
        bytes_data=b"page a",  # same bytes → same hash
    )
    assert sid1 == sid2
    assert dedup1 is False
    assert dedup2 is True


async def test_snapshot_get_returns_record(snapshot_store):
    sid, _ = await snapshot_store.put(
        requested_url="https://example.com",
        final_url="https://example.com/final",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="hash_a",
        bytes_data=b"page a",
    )
    record = await snapshot_store.get(sid)
    assert record is not None
    assert record.snapshot_id == sid
    assert record.final_url == "https://example.com/final"
    assert record.content_hash == "hash_a"
    assert record.bytes_size == len(b"page a")


async def test_snapshot_get_returns_none_for_unknown_id(snapshot_store):
    assert await snapshot_store.get("snap_99999999") is None


async def test_snapshot_get_bytes_returns_bytes(snapshot_store):
    sid, _ = await snapshot_store.put(
        requested_url="https://example.com",
        final_url="https://example.com",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="hash_a",
        bytes_data=b"page a",
    )
    assert await snapshot_store.get_bytes(sid) == b"page a"


async def test_snapshot_get_bytes_returns_none_for_unknown(snapshot_store):
    assert await snapshot_store.get_bytes("snap_unknown") is None


async def test_snapshot_get_by_hash(snapshot_store):
    await snapshot_store.put(
        requested_url="https://example.com",
        final_url="https://example.com",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="hash_a",
        bytes_data=b"page a",
    )
    record = await snapshot_store.get_by_hash("hash_a")
    assert record is not None
    assert record.content_hash == "hash_a"


async def test_snapshot_get_by_hash_unknown_returns_none(snapshot_store):
    assert await snapshot_store.get_by_hash("nonexistent_hash") is None


async def test_snapshot_delete_expired(snapshot_store):
    cutoff = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
    # Old snapshot
    await snapshot_store.put(
        requested_url="https://old.example.com",
        final_url="https://old.example.com",
        retrieved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="old_hash",
        bytes_data=b"old",
    )
    # New snapshot
    await snapshot_store.put(
        requested_url="https://new.example.com",
        final_url="https://new.example.com",
        retrieved_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="new_hash",
        bytes_data=b"new",
    )
    deleted_count = await snapshot_store.delete_expired(cutoff)
    assert deleted_count == 1
    # Old gone, new remains
    assert await snapshot_store.get_by_hash("old_hash") is None
    assert await snapshot_store.get_by_hash("new_hash") is not None


async def test_snapshot_delete_expired_rejects_non_datetime(snapshot_store):
    with pytest.raises(TypeError):
        await snapshot_store.delete_expired("2026-07-28")


# ---------------------------------------------------------------------------
# InMemoryWebSourceStore
# ---------------------------------------------------------------------------


def _make_source_record(
    source_id: str,
    query: str,
    content_hash: str,
    retrieved_at: datetime,
    url: str = "https://example.com",
) -> WebSourceRecord:
    sr = SearchResult(
        provider="fake",
        query=query,
        rank=1,
        url=url,
        title="t",
        snippet="s",
    )
    fr = FetchedResource(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        content_bytes_ref=f"fake:{url}",
        retrieved_at=retrieved_at,
        content_hash=content_hash,
    )
    ed = ExtractedDocument(
        source_url=url,
        canonical_url=url,
        title="t",
        text="body",
        retrieved_at=retrieved_at,
        content_hash=content_hash,
    )
    return WebSourceRecord(
        source_id=source_id,
        search_result=sr,
        fetched=fr,
        extracted=ed,
        provider="fake",
        retrieved_at=retrieved_at,
        content_hash=content_hash,
    )


async def test_source_put_returns_id(source_store):
    rec = _make_source_record("src_1", "query a", "hash_a", datetime(2026, 7, 28, tzinfo=timezone.utc))
    sid = await source_store.put(rec)
    assert sid == "src_1"


async def test_source_put_deduplicates_by_hash(source_store):
    rec1 = _make_source_record("src_1", "query a", "hash_a", datetime(2026, 7, 28, tzinfo=timezone.utc))
    rec2 = _make_source_record("src_2", "query a", "hash_a", datetime(2026, 7, 29, tzinfo=timezone.utc))
    sid1 = await source_store.put(rec1)
    sid2 = await source_store.put(rec2)
    assert sid1 == "src_1"
    assert sid2 == "src_1"  # dedup → returns existing id


async def test_source_get_returns_record(source_store):
    rec = _make_source_record("src_1", "query a", "hash_a", datetime(2026, 7, 28, tzinfo=timezone.utc))
    await source_store.put(rec)
    fetched = await source_store.get("src_1")
    assert fetched is not None
    assert fetched.source_id == "src_1"
    assert fetched.content_hash == "hash_a"


async def test_source_get_returns_none_for_unknown(source_store):
    assert await source_store.get("src_unknown") is None


async def test_source_get_by_hash(source_store):
    rec = _make_source_record("src_1", "query a", "hash_a", datetime(2026, 7, 28, tzinfo=timezone.utc))
    await source_store.put(rec)
    fetched = await source_store.get_by_hash("hash_a")
    assert fetched is not None
    assert fetched.source_id == "src_1"


async def test_source_get_by_hash_unknown(source_store):
    assert await source_store.get_by_hash("nonexistent") is None


async def test_source_list_by_query_most_recent_first(source_store):
    """list_by_query returns records most-recent-first (reverse insertion order)."""
    rec1 = _make_source_record("src_1", "q", "h1", datetime(2026, 7, 1, tzinfo=timezone.utc), url="https://a.example.com")
    rec2 = _make_source_record("src_2", "q", "h2", datetime(2026, 7, 2, tzinfo=timezone.utc), url="https://b.example.com")
    rec3 = _make_source_record("src_3", "q", "h3", datetime(2026, 7, 3, tzinfo=timezone.utc), url="https://c.example.com")
    await source_store.put(rec1)
    await source_store.put(rec2)
    await source_store.put(rec3)

    results = await source_store.list_by_query("q")
    assert len(results) == 3
    assert results[0].source_id == "src_3"  # most recent first
    assert results[1].source_id == "src_2"
    assert results[2].source_id == "src_1"


async def test_source_list_by_query_respects_limit(source_store):
    for i in range(5):
        rec = _make_source_record(
            f"src_{i}", "q", f"h{i}",
            datetime(2026, 7, i + 1, tzinfo=timezone.utc),
            url=f"https://example.com/{i}",
        )
        await source_store.put(rec)
    results = await source_store.list_by_query("q", limit=2)
    assert len(results) == 2


async def test_source_list_by_query_empty_for_unknown(source_store):
    assert await source_store.list_by_query("nonexistent") == []


async def test_source_delete(source_store):
    rec = _make_source_record("src_1", "q", "h1", datetime(2026, 7, 1, tzinfo=timezone.utc))
    await source_store.put(rec)
    deleted = await source_store.delete("src_1")
    assert deleted is True
    assert await source_store.get("src_1") is None
    assert await source_store.get_by_hash("h1") is None


async def test_source_delete_unknown_returns_false(source_store):
    deleted = await source_store.delete("src_unknown")
    assert deleted is False


async def test_source_delete_removes_from_query_index(source_store):
    """After delete, the source must not appear in list_by_query."""
    rec = _make_source_record("src_1", "q", "h1", datetime(2026, 7, 1, tzinfo=timezone.utc))
    await source_store.put(rec)
    await source_store.delete("src_1")
    results = await source_store.list_by_query("q")
    assert results == []


async def test_source_supports_direct_fetch_no_query(source_store):
    """Direct-URL fetches (search_result=None) should be storable."""
    fr = FetchedResource(
        requested_url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        content_type="text/html",
        content_bytes_ref="fake:https://example.com",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_hash="hash_direct",
    )
    rec = WebSourceRecord(
        source_id="src_direct",
        search_result=None,
        fetched=fr,
        extracted=None,
        provider="direct",
        retrieved_at=fr.retrieved_at,
        content_hash="hash_direct",
    )
    sid = await source_store.put(rec)
    assert sid == "src_direct"
    fetched = await source_store.get(sid)
    assert fetched is not None
    assert fetched.search_result is None
