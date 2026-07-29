"""Bytes sink tests for ``HttpxWebFetcher`` (ADR-017 bytes_sink fix).

Verifies that the ``bytes_sink`` parameter on ``HttpxWebFetcher``
persists fetched bytes to the snapshot store during the streaming read,
and that the ``content_bytes_ref`` on the returned ``FetchedResource``
points to the stored bytes (so downstream extractors can retrieve them
without the ``bytes_unavailable`` 500 error).

Coverage:
    - Bytes sink persists body to the snapshot store
    - content_bytes_ref matches the snapshot_id returned by the sink
    - Extractor can retrieve bytes via the ref after the fetch
    - Sink failure is non-fatal (fetch still succeeds, ref is placeholder)
    - No sink configured → placeholder ref (backward compatible)
    - Truncated body is persisted at the truncated size
"""

from __future__ import annotations

import httpx
import pytest
import respx

from aip.adapter.web.http_fetcher import HttpxWebFetcher
from aip.adapter.web.snapshot import InMemoryWebSnapshotStore
from aip.foundation.schemas.web import FetchPolicy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_dns() -> dict:
    return {"example.com": ["93.184.216.34"]}


@pytest.fixture
def snapshot_store() -> InMemoryWebSnapshotStore:
    return InMemoryWebSnapshotStore()


@pytest.fixture
def fetcher_with_sink(fake_dns, snapshot_store):
    """An HttpxWebFetcher with a bytes_sink that persists to the snapshot store."""
    def resolver(hostname: str) -> list[str]:
        return fake_dns.get(hostname.lower(), ["93.184.216.34"])

    async def sink(body: bytes, fetched) -> str:
        sid, _ = await snapshot_store.put(
            requested_url=fetched.requested_url,
            final_url=fetched.final_url,
            retrieved_at=fetched.retrieved_at,
            content_type=fetched.content_type,
            content_hash=fetched.content_hash,
            bytes_data=body,
        )
        return sid

    return HttpxWebFetcher(dns_resolver=resolver, bytes_sink=sink)


@pytest.fixture
def fetcher_no_sink(fake_dns):
    """An HttpxWebFetcher with no bytes_sink (backward compatible)."""
    def resolver(hostname: str) -> list[str]:
        return fake_dns.get(hostname.lower(), ["93.184.216.34"])

    return HttpxWebFetcher(dns_resolver=resolver)


# ---------------------------------------------------------------------------
# Bytes sink persists body
# ---------------------------------------------------------------------------


@respx.mock
async def test_bytes_sink_persists_body(fetcher_with_sink, snapshot_store, strict_policy):
    """After a fetch, the body is in the snapshot store."""
    body = b"<html><body>Hello world</body></html>"
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    fr = await fetcher_with_sink.fetch("https://example.com/page", strict_policy)

    # The snapshot store should have the bytes
    assert fr.content_bytes_ref.startswith("snap_")
    stored_bytes = await snapshot_store.get_bytes(fr.content_bytes_ref)
    assert stored_bytes == body


@respx.mock
async def test_content_bytes_ref_matches_snapshot_id(fetcher_with_sink, snapshot_store, strict_policy):
    """The content_bytes_ref is the snapshot_id returned by the sink."""
    body = b"<html>test</html>"
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    fr = await fetcher_with_sink.fetch("https://example.com/", strict_policy)

    # The ref should be a snapshot_id, not the old placeholder format
    assert fr.content_bytes_ref.startswith("snap_")
    assert "httpx:" not in fr.content_bytes_ref

    # The snapshot store should have a record for this id
    record = await snapshot_store.get(fr.content_bytes_ref)
    assert record is not None
    assert record.content_hash == fr.content_hash


@respx.mock
async def test_extractor_can_retrieve_bytes_after_fetch(fetcher_with_sink, snapshot_store, strict_policy):
    """An extractor can retrieve the bytes via the ref after the fetch."""
    body = b"<html><head><title>Test</title></head><body><article>Content</article></body></html>"
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    fr = await fetcher_with_sink.fetch("https://example.com/article", strict_policy)

    # Simulate what the route's _load_bytes_for_extraction does
    bytes_data = await snapshot_store.get_bytes(fr.content_bytes_ref)
    assert bytes_data == body

    # And the extractor can use it
    from aip.adapter.web.extractors.html import HtmlContentExtractor

    def loader(ref: str) -> bytes:
        return bytes_data

    extractor = HtmlContentExtractor()
    ed = await extractor.extract(fr, bytes_loader=loader)
    assert "Content" in ed.text
    assert ed.title == "Test"


# ---------------------------------------------------------------------------
# Sink failure is non-fatal
# ---------------------------------------------------------------------------


@respx.mock
async def test_sink_failure_is_non_fatal(fake_dns, strict_policy):
    """If the sink raises, the fetch still succeeds with a placeholder ref."""
    body = b"<html>test</html>"
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )

    async def failing_sink(body: bytes, fetched) -> str:
        raise RuntimeError("sink unavailable")

    fetcher = HttpxWebFetcher(
        dns_resolver=lambda h: ["93.184.216.34"],
        bytes_sink=failing_sink,
    )
    fr = await fetcher.fetch("https://example.com/", strict_policy)
    # Fetch succeeded — the ref falls back to the placeholder
    assert fr.content_bytes_ref.startswith("httpx:")
    assert fr.content_hash  # hash is still computed


# ---------------------------------------------------------------------------
# No sink configured (backward compatible)
# ---------------------------------------------------------------------------


@respx.mock
async def test_no_sink_uses_placeholder_ref(fetcher_no_sink, strict_policy):
    """Without a bytes_sink, the ref is the placeholder format."""
    body = b"<html>test</html>"
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    fr = await fetcher_no_sink.fetch("https://example.com/", strict_policy)
    assert fr.content_bytes_ref.startswith("httpx:")
    assert "httpx:https://example.com/" in fr.content_bytes_ref


# ---------------------------------------------------------------------------
# Truncated body persisted at truncated size
# ---------------------------------------------------------------------------


@respx.mock
async def test_truncated_body_persisted_at_truncated_size(fetcher_with_sink, snapshot_store):
    """When the body is truncated, the snapshot stores the truncated version."""
    policy = FetchPolicy(max_bytes=100)
    big_body = b"x" * 500

    respx.get("https://example.com/big").mock(
        return_value=httpx.Response(200, content=big_body, headers={"content-type": "text/plain"})
    )
    fr = await fetcher_with_sink.fetch("https://example.com/big", policy)

    assert fr.truncated is True
    stored_bytes = await snapshot_store.get_bytes(fr.content_bytes_ref)
    assert len(stored_bytes) == 100  # truncated to max_bytes


# ---------------------------------------------------------------------------
# Dedup: same content fetched twice → same snapshot_id
# ---------------------------------------------------------------------------


@respx.mock
async def test_dedup_same_content_same_snapshot_id(fetcher_with_sink, snapshot_store, strict_policy):
    """Fetching the same content twice deduplicates at the snapshot store."""
    body = b"<html>same content</html>"
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    respx.get("https://example.com/b").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )

    fr1 = await fetcher_with_sink.fetch("https://example.com/a", strict_policy)
    fr2 = await fetcher_with_sink.fetch("https://example.com/b", strict_policy)

    # Same content_hash → same snapshot_id
    assert fr1.content_hash == fr2.content_hash
    assert fr1.content_bytes_ref == fr2.content_bytes_ref
