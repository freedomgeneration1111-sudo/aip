"""Shared fixtures for the WS-1 web test suite."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from aip.adapter.web.fake_provider import (
    FakeContentExtractor,
    FakeSearchProvider,
    FakeWebFetcher,
)
from aip.adapter.web.snapshot import InMemoryWebSnapshotStore, InMemoryWebSourceStore
from aip.foundation.schemas.web import (
    FetchPolicy,
    SearchResult,
)

# ---------------------------------------------------------------------------
# Time anchor — every test gets a fixed timestamp so hashes are stable
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_time() -> datetime:
    """Deterministic UTC timestamp for fetch/extract provenance."""
    return datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@pytest.fixture
def strict_policy() -> FetchPolicy:
    """Production-shaped policy: no private networks, http/https only."""
    return FetchPolicy(
        allowed_schemes=("http", "https"),
        max_redirects=5,
        timeout_seconds=20.0,
        max_bytes=20_000_000,
        allowed_content_types=None,
        allow_private_networks=False,
    )


@pytest.fixture
def tiny_policy() -> FetchPolicy:
    """Small-bytes policy for truncation tests."""
    return FetchPolicy(
        allowed_schemes=("http", "https"),
        max_redirects=3,
        timeout_seconds=5.0,
        max_bytes=128,
        allowed_content_types=None,
        allow_private_networks=False,
    )


@pytest.fixture
def private_allowed_policy() -> FetchPolicy:
    """Policy that allows private networks (for local-fixture tests only)."""
    return FetchPolicy(
        allowed_schemes=("http", "https"),
        max_redirects=5,
        timeout_seconds=20.0,
        max_bytes=20_000_000,
        allowed_content_types=None,
        allow_private_networks=True,
    )


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_search_results() -> list[SearchResult]:
    """Three deterministic search results for a fixed query."""
    return [
        SearchResult(
            provider="fake",
            query="python type hints",
            rank=1,
            url="https://docs.python.org/3/library/typing.html",
            title="typing — Support for gradual typing",
            snippet="The typing module supports type hints...",
            published_at=None,
            provider_metadata={"score": 0.95},
        ),
        SearchResult(
            provider="fake",
            query="python type hints",
            rank=2,
            url="https://peps.python.org/pep-0484/",
            title="PEP 484 – Type Hints",
            snippet="This PEP formalizes type hint notation...",
            published_at=None,
            provider_metadata={"score": 0.88},
        ),
        SearchResult(
            provider="fake",
            query="python type hints",
            rank=3,
            url="https://realpython.com/python-type-checking/",
            title="Python Type Checking Guide",
            snippet="A comprehensive guide to type hints...",
            published_at=None,
            provider_metadata={"score": 0.81},
        ),
    ]


# ---------------------------------------------------------------------------
# Fake providers wired to fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_search_provider(
    sample_search_results: list[SearchResult],
) -> FakeSearchProvider:
    return FakeSearchProvider(
        results={"python type hints": sample_search_results},
    )


@pytest.fixture
def fake_pages() -> dict[str, bytes]:
    """Three fixture pages keyed by URL (lowercased)."""
    return {
        "https://docs.python.org/3/library/typing.html": (
            b"<html><head><title>typing - Support for gradual typing</title></head>"
            b"<body><p>The typing module supports type hints as specified by PEP 484.</p></body></html>"
        ),
        "https://peps.python.org/pep-0484/": (
            b"<html><head><title>PEP 484</title></head>"
            b"<body><p>This PEP formalizes type hint notation for Python.</p></body></html>"
        ),
        "https://realpython.com/python-type-checking/": (
            b"<html><head><title>Python Type Checking</title></head>"
            b"<body><p>Comprehensive guide to type hints in Python.</p></body></html>"
        ),
    }


@pytest.fixture
def fake_web_fetcher(
    fake_pages: dict[str, bytes],
    fixed_time: datetime,
) -> FakeWebFetcher:
    return FakeWebFetcher(
        pages=fake_pages,
        retrieved_at=fixed_time,
    )


@pytest.fixture
def fake_content_extractor() -> FakeContentExtractor:
    return FakeContentExtractor()


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_store() -> InMemoryWebSnapshotStore:
    return InMemoryWebSnapshotStore()


@pytest.fixture
def source_store() -> InMemoryWebSourceStore:
    return InMemoryWebSourceStore()


# ---------------------------------------------------------------------------
# bytes_loader for extractor tests
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_bytes_loader(fake_web_fetcher: FakeWebFetcher) -> Any:
    return fake_web_fetcher.make_bytes_loader()
