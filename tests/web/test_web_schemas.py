"""Tests for ``aip.foundation.schemas.web`` (ADR-017 WS-1).

Covers:
    - Frozen dataclass immutability
    - Round-trip serialization via ``dataclasses.asdict``
    - Hash stability: same input → same hash, different input → different hash
    - ``sha256_hex`` and ``normalize_text_for_hash`` helpers
    - Default factory fields produce independent copies
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    FetchPolicy,
    SearchOptions,
    SearchResult,
    WebProviderConfig,
    WebSnapshotRecord,
    WebSourceRecord,
    normalize_text_for_hash,
    sha256_hex,
)

# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_search_result_is_frozen():
    """SearchResult must be immutable (frozen dataclass)."""
    r = SearchResult(
        provider="fake",
        query="q",
        rank=1,
        url="https://example.com",
        title="t",
        snippet="s",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.rank = 2  # type: ignore[misc]


def test_fetched_resource_is_frozen():
    fr = FetchedResource(
        requested_url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        content_type="text/html",
        content_bytes_ref="fake:https://example.com",
        retrieved_at=datetime.now(timezone.utc),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        fr.status_code = 404  # type: ignore[misc]


def test_extracted_document_is_frozen():
    ed = ExtractedDocument(
        source_url="https://example.com",
        canonical_url=None,
        title="t",
        text="body",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ed.title = "other"  # type: ignore[misc]


def test_fetch_policy_is_frozen():
    p = FetchPolicy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.timeout_seconds = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Default factory fields produce independent copies
# ---------------------------------------------------------------------------


def test_search_result_provider_metadata_default_is_independent():
    """Two SearchResults with default provider_metadata must not share the dict."""
    r1 = SearchResult(provider="p", query="q", rank=1, url="u", title="t", snippet="s")
    r2 = SearchResult(provider="p", query="q", rank=1, url="u", title="t", snippet="s")
    r1.provider_metadata["x"] = 1
    assert "x" not in r2.provider_metadata


def test_fetch_policy_defaults():
    """Default FetchPolicy matches ADR-017 §Provider policy."""
    p = FetchPolicy()
    assert p.allowed_schemes == ("http", "https")
    assert p.max_redirects == 5
    assert p.timeout_seconds == 20.0
    assert p.max_bytes == 20_000_000
    assert p.allowed_content_types is None
    assert p.allow_private_networks is False


def test_search_options_defaults():
    so = SearchOptions()
    assert so.limit == 8
    assert so.freshness_days is None
    assert so.domains is None
    assert so.topic is None


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------


def test_search_result_asdict_round_trip():
    """asdict() produces a serializable dict (the API layer relies on this)."""
    r = SearchResult(
        provider="tavily",
        query="hello",
        rank=1,
        url="https://example.com",
        title="Example",
        snippet="A snippet.",
        published_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        provider_metadata={"score": 0.9, "raw": {"x": 1}},
    )
    d = dataclasses.asdict(r)
    assert d["provider"] == "tavily"
    assert d["rank"] == 1
    assert d["published_at"].year == 2026
    assert d["provider_metadata"]["raw"]["x"] == 1


def test_fetched_resource_asdict_includes_all_fields():
    fr = FetchedResource(
        requested_url="https://example.com",
        final_url="https://example.com/final",
        status_code=200,
        content_type="text/html",
        content_bytes_ref="ref:1",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        response_headers={"Etag": "abc"},
        content_hash="deadbeef",
        truncated=False,
        redirects=("https://example.com", "https://example.com/final"),
    )
    d = dataclasses.asdict(fr)
    assert d["final_url"] == "https://example.com/final"
    assert d["redirects"] == ("https://example.com", "https://example.com/final")
    assert d["response_headers"]["Etag"] == "abc"


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def test_sha256_hex_stable_for_bytes():
    assert sha256_hex(b"hello") == sha256_hex(b"hello")
    assert sha256_hex(b"hello") != sha256_hex(b"world")


def test_sha256_hex_encodes_str_as_utf8():
    """str input is UTF-8 encoded before hashing."""
    assert sha256_hex("hello") == sha256_hex(b"hello")


def test_sha256_hex_known_value():
    """Verify against a known SHA-256 to catch algorithm regressions."""
    # SHA-256("hello") = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    assert (
        sha256_hex("hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_normalize_text_for_hash_strips_trailing_whitespace_per_line():
    """Trailing whitespace differences should not affect the hash."""
    a = normalize_text_for_hash("line one   \nline two\n")
    b = normalize_text_for_hash("line one\nline two\n")
    assert a == b


def test_normalize_text_for_hash_collapses_blank_lines():
    a = normalize_text_for_hash("para one\n\n\n\npara two")
    b = normalize_text_for_hash("para one\n\npara two")
    assert a == b


def test_normalize_text_for_hash_lowercases():
    a = normalize_text_for_hash("Hello World")
    b = normalize_text_for_hash("hello world")
    assert a == b


def test_normalize_text_for_hash_distinct_content_differs():
    assert normalize_text_for_hash("alpha") != normalize_text_for_hash("beta")


# ---------------------------------------------------------------------------
# WebSourceRecord composite
# ---------------------------------------------------------------------------


def test_web_source_record_carries_fetch_and_extract():
    sr = SearchResult(provider="tavily", query="q", rank=1, url="u", title="t", snippet="s")
    fr = FetchedResource(
        requested_url="u",
        final_url="u",
        status_code=200,
        content_type="text/html",
        content_bytes_ref="ref",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_hash="abc",
    )
    ed = ExtractedDocument(
        source_url="u",
        canonical_url="u",
        title="t",
        text="body",
        content_hash="def",
    )
    wsr = WebSourceRecord(
        source_id="src_1",
        search_result=sr,
        fetched=fr,
        extracted=ed,
        provider="tavily",
        retrieved_at=fr.retrieved_at,
        content_hash=ed.content_hash,
    )
    assert wsr.search_result.query == "q"
    assert wsr.fetched.status_code == 200
    assert wsr.extracted.text == "body"
    assert wsr.content_hash == "def"


def test_web_source_record_allows_direct_fetch_no_search_result():
    """Direct URL fetches (e.g. messaging ingress) carry search_result=None."""
    fr = FetchedResource(
        requested_url="u",
        final_url="u",
        status_code=200,
        content_type="text/html",
        content_bytes_ref="ref",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_hash="abc",
    )
    wsr = WebSourceRecord(
        source_id="src_direct",
        search_result=None,
        fetched=fr,
        extracted=None,
        provider="direct",
        retrieved_at=fr.retrieved_at,
        content_hash="abc",
        fetch_warnings=("extraction failed: unsupported content type",),
    )
    assert wsr.search_result is None
    assert wsr.extracted is None
    assert wsr.fetch_warnings == ("extraction failed: unsupported content type",)


# ---------------------------------------------------------------------------
# WebSnapshotRecord
# ---------------------------------------------------------------------------


def test_web_snapshot_record_round_trip():
    r = WebSnapshotRecord(
        snapshot_id="snap_1",
        requested_url="https://example.com",
        final_url="https://example.com/final",
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        content_type="text/html",
        content_hash="abc",
        bytes_ref="memory:snap_1",
        bytes_size=1234,
    )
    d = dataclasses.asdict(r)
    assert d["snapshot_id"] == "snap_1"
    assert d["bytes_size"] == 1234


# ---------------------------------------------------------------------------
# WebProviderConfig
# ---------------------------------------------------------------------------


def test_web_provider_config_fake_flag():
    c = WebProviderConfig(name="fake", is_fake=True)
    assert c.is_fake is True
    assert c.api_key_env == ""


def test_web_provider_config_real_carries_env_name():
    c = WebProviderConfig(name="tavily", api_key_env="AIP_WEB_SEARCH_API_KEY")
    assert c.is_fake is False
    assert c.api_key_env == "AIP_WEB_SEARCH_API_KEY"
