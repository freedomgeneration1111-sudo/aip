"""Provenance builder tests for ``aip.adapter.web.provenance`` (ADR-017 WS-2).

Coverage:
    - build_web_source_record: happy path, direct-URL fetch, extraction failure
    - make_source_id: stability across re-fetches, distinctness
    - redact_provider_metadata: key-based redaction, nested dict, case-insensitive
"""

from __future__ import annotations

from datetime import datetime, timezone

from aip.adapter.web.provenance import (
    build_web_source_record,
    make_source_id,
    redact_provider_metadata,
)
from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    SearchResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_search_result(**overrides) -> SearchResult:
    defaults = dict(
        provider="tavily",
        query="python type hints",
        rank=1,
        url="https://example.com/article",
        title="Article Title",
        snippet="A snippet.",
        provider_metadata={"score": 0.95},
    )
    defaults.update(overrides)
    return SearchResult(**defaults)


def _make_fetched(**overrides) -> FetchedResource:
    defaults = dict(
        requested_url="https://example.com/article",
        final_url="https://example.com/article",
        status_code=200,
        content_type="text/html",
        content_bytes_ref="ref:1",
        retrieved_at=datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc),
        response_headers={"etag": "abc"},
        content_hash="raw_hash_abc",
        truncated=False,
        redirects=("https://example.com/article",),
    )
    defaults.update(overrides)
    return FetchedResource(**defaults)


def _make_extracted(**overrides) -> ExtractedDocument:
    defaults = dict(
        source_url="https://example.com/article",
        canonical_url="https://example.com/canonical",
        title="Article Title",
        text="The article body text.",
        authors=("Jane Doe",),
        published_at=datetime(2024, 3, 15, 10, 30, 0, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc),
        content_hash="extracted_hash_def",
        extraction_method="html_readability",
        warnings=(),
        snapshot_artifact_id=None,
    )
    defaults.update(overrides)
    return ExtractedDocument(**defaults)


# ---------------------------------------------------------------------------
# build_web_source_record
# ---------------------------------------------------------------------------


def test_build_record_happy_path():
    sr = _make_search_result()
    fr = _make_fetched()
    ed = _make_extracted()
    record = build_web_source_record(
        search_result=sr, fetched=fr, extracted=ed, fetch_warnings=()
    )
    assert record.source_id.startswith("src_")
    assert record.provider == "tavily"
    assert record.content_hash == "extracted_hash_def"
    assert record.search_result is not None
    assert record.search_result.provider_metadata == {"score": 0.95}
    assert record.fetch_warnings == ()


def test_build_record_direct_fetch_no_search_result():
    """Direct URL fetches carry search_result=None and provider='direct'."""
    fr = _make_fetched()
    ed = _make_extracted()
    record = build_web_source_record(
        search_result=None, fetched=fr, extracted=ed, fetch_warnings=()
    )
    assert record.search_result is None
    assert record.provider == "direct"
    assert record.content_hash == "extracted_hash_def"


def test_build_record_extraction_failure_falls_back_to_raw_hash():
    """When extraction failed (extracted=None), use the raw-bytes hash."""
    sr = _make_search_result()
    fr = _make_fetched()
    record = build_web_source_record(
        search_result=sr, fetched=fr, extracted=None, fetch_warnings=("extraction failed",)
    )
    assert record.extracted is None
    assert record.content_hash == "raw_hash_abc"  # fell back to fetched.content_hash
    assert record.fetch_warnings == ("extraction failed",)


def test_build_record_carries_fetch_warnings():
    sr = _make_search_result()
    fr = _make_fetched()
    ed = _make_extracted()
    record = build_web_source_record(
        search_result=sr, fetched=fr, extracted=ed,
        fetch_warnings=("SSL verification skipped", "redirect loop recovered"),
    )
    assert record.fetch_warnings == ("SSL verification skipped", "redirect loop recovered")


def test_build_record_redacts_provider_metadata():
    """Sensitive keys in provider_metadata are redacted."""
    sr = _make_search_result(
        provider_metadata={
            "score": 0.95,
            "api_key": "sk-secret-123",
            "token": "tok-abc",
            "raw_response": {"nested_data": "ok", "authorization": "Bearer xyz"},
        }
    )
    fr = _make_fetched()
    ed = _make_extracted()
    record = build_web_source_record(
        search_result=sr, fetched=fr, extracted=ed, fetch_warnings=()
    )
    assert record.search_result is not None
    meta = record.search_result.provider_metadata
    assert meta["score"] == 0.95  # not redacted
    assert meta["api_key"] == "[redacted]"
    assert meta["token"] == "[redacted]"
    assert meta["raw_response"]["nested_data"] == "ok"
    assert meta["raw_response"]["authorization"] == "[redacted]"


def test_build_record_retrieved_at_from_fetched():
    sr = _make_search_result()
    ts = datetime(2026, 7, 29, 8, 30, 0, tzinfo=timezone.utc)
    fr = _make_fetched(retrieved_at=ts)
    ed = _make_extracted()
    record = build_web_source_record(
        search_result=sr, fetched=fr, extracted=ed, fetch_warnings=()
    )
    assert record.retrieved_at == ts


# ---------------------------------------------------------------------------
# make_source_id
# ---------------------------------------------------------------------------


def test_make_source_id_stable():
    """Same URL + content_hash → same source_id."""
    sid1 = make_source_id("https://example.com/page", "hash_abc")
    sid2 = make_source_id("https://example.com/page", "hash_abc")
    assert sid1 == sid2


def test_make_source_id_differs_for_different_url():
    sid1 = make_source_id("https://example.com/page1", "hash_abc")
    sid2 = make_source_id("https://example.com/page2", "hash_abc")
    assert sid1 != sid2


def test_make_source_id_differs_for_different_hash():
    sid1 = make_source_id("https://example.com/page", "hash_abc")
    sid2 = make_source_id("https://example.com/page", "hash_def")
    assert sid1 != sid2


def test_make_source_id_format():
    sid = make_source_id("https://example.com/page", "hash_abc")
    assert sid.startswith("src_")
    assert len(sid) == 4 + 24  # "src_" + 24 hex chars


def test_build_record_produces_consistent_source_id():
    """Re-fetching the same page (same URL + same extracted hash) produces
    the same source_id — this is the dedup contract."""
    sr = _make_search_result()
    fr = _make_fetched()
    ed = _make_extracted()
    r1 = build_web_source_record(search_result=sr, fetched=fr, extracted=ed)
    r2 = build_web_source_record(search_result=sr, fetched=fr, extracted=ed)
    assert r1.source_id == r2.source_id


# ---------------------------------------------------------------------------
# redact_provider_metadata
# ---------------------------------------------------------------------------


def test_redact_basic_keys():
    meta = {"score": 0.9, "api_key": "secret", "token": "tok"}
    redacted = redact_provider_metadata(meta)
    assert redacted["score"] == 0.9
    assert redacted["api_key"] == "[redacted]"
    assert redacted["token"] == "[redacted]"


def test_redact_case_insensitive():
    meta = {"API_KEY": "secret", "Token": "tok", "Authorization": "auth"}
    redacted = redact_provider_metadata(meta)
    assert redacted["API_KEY"] == "[redacted]"
    assert redacted["Token"] == "[redacted]"
    assert redacted["Authorization"] == "[redacted]"


def test_redact_nested_dict():
    meta = {"raw": {"nested_token": "tok", "safe_field": "ok"}}
    redacted = redact_provider_metadata(meta)
    # "nested_token" is NOT an exact match for "token" — preserved
    assert redacted["raw"]["nested_token"] == "tok"
    assert redacted["raw"]["safe_field"] == "ok"


def test_redact_does_not_mutate_input():
    meta = {"api_key": "secret", "score": 0.9}
    redacted = redact_provider_metadata(meta)
    assert meta["api_key"] == "secret"  # original unchanged
    assert redacted["api_key"] == "[redacted]"


def test_redact_empty_dict():
    assert redact_provider_metadata({}) == {}


def test_redact_partial_key_match():
    """Only EXACT key-name matches are redacted, not substrings.

    Keys like 'my_api_key' or 'access_token' that are common variants
    are in the redaction set; but 'safe_data' containing no exact match
    is preserved.
    """
    meta = {"access_token": "tok", "safe_data": "ok", "refresh_token": "rt"}
    redacted = redact_provider_metadata(meta)
    assert redacted["access_token"] == "[redacted]"
    assert redacted["refresh_token"] == "[redacted]"
    assert redacted["safe_data"] == "ok"
