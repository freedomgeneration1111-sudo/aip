"""Tests for ``aip.foundation.protocols.web`` (ADR-017 WS-1).

Verifies that the WS-1 fake implementations satisfy the Protocols via
``runtime_checkable`` ``isinstance`` checks.  This is the contract
that the WS-2/WS-3 real implementations must also satisfy.
"""

from __future__ import annotations

from aip.adapter.web.fake_provider import (
    FakeContentExtractor,
    FakeSearchProvider,
    FakeWebFetcher,
)
from aip.adapter.web.snapshot import (
    InMemoryWebSnapshotStore,
    InMemoryWebSourceStore,
)
from aip.foundation.protocols.web import (
    ContentExtractor,
    SearchProvider,
    WebFetcher,
    WebSnapshotStore,
    WebSourceStore,
)


def test_fake_search_provider_satisfies_protocol():
    assert isinstance(FakeSearchProvider({}), SearchProvider)


def test_fake_web_fetcher_satisfies_protocol():
    assert isinstance(FakeWebFetcher({}), WebFetcher)


def test_fake_content_extractor_satisfies_protocol():
    assert isinstance(FakeContentExtractor(), ContentExtractor)


def test_in_memory_snapshot_store_satisfies_protocol():
    assert isinstance(InMemoryWebSnapshotStore(), WebSnapshotStore)


def test_in_memory_source_store_satisfies_protocol():
    assert isinstance(InMemoryWebSourceStore(), WebSourceStore)


def test_non_implementing_class_does_not_satisfy():
    """A bare object must NOT satisfy any web Protocol — guards against
    over-permissive Protocol definitions that would accept anything."""

    class NotAProvider:
        pass

    assert not isinstance(NotAProvider(), SearchProvider)
    assert not isinstance(NotAProvider(), WebFetcher)
    assert not isinstance(NotAProvider(), ContentExtractor)
    assert not isinstance(NotAProvider(), WebSnapshotStore)
    assert not isinstance(NotAProvider(), WebSourceStore)
