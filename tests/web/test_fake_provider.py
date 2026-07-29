"""Tests for ``aip.adapter.web.fake_provider`` (ADR-017 WS-1).

Covers:
    - FakeSearchProvider determinism, limit enforcement, re-ranking
    - FakeWebFetcher policy enforcement (SSRF denials, truncation, redirects)
    - FakeWebFetcher sensitive-header stripping
    - FakeContentExtractor basic extraction
    - WebFetchDenied / WebFetchError exception classes
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aip.adapter.web.fake_provider import (
    FakeContentExtractor,
    FakeSearchProvider,
    FakeWebFetcher,
    WebFetchDenied,
    WebFetchError,
)
from aip.foundation.schemas.web import (
    SearchOptions,
    SearchResult,
    sha256_hex,
)

# ---------------------------------------------------------------------------
# FakeSearchProvider
# ---------------------------------------------------------------------------


async def test_search_returns_registered_results(fake_search_provider):
    results = await fake_search_provider.search("python type hints")
    assert len(results) == 3
    assert results[0].rank == 1
    assert results[0].url == "https://docs.python.org/3/library/typing.html"
    assert results[1].rank == 2
    assert results[2].rank == 3


async def test_search_returns_empty_for_unknown_query(fake_search_provider):
    results = await fake_search_provider.search("nonexistent topic")
    assert results == []


async def test_search_honors_limit(fake_search_provider):
    results = await fake_search_provider.search(
        "python type hints",
        options=SearchOptions(limit=2),
    )
    assert len(results) == 2
    assert results[0].rank == 1
    assert results[1].rank == 2


async def test_search_reranks_after_limit(fake_search_provider):
    """When limit < registered count, results are re-ranked 1..N."""
    results = await fake_search_provider.search(
        "python type hints",
        options=SearchOptions(limit=1),
    )
    assert len(results) == 1
    assert results[0].rank == 1


async def test_search_is_case_insensitive():
    """Query keys are stored lowercased; lookups should match case-insensitively."""
    provider = FakeSearchProvider(
        results={
            "Python Type Hints": [
                SearchResult(
                    provider="fake", query="Python Type Hints", rank=1,
                    url="u", title="t", snippet="s",
                ),
            ],
        },
    )
    results = await provider.search("python type hints")
    assert len(results) == 1


async def test_search_is_deterministic(fake_search_provider):
    """Two calls with the same query produce identical results."""
    r1 = await fake_search_provider.search("python type hints")
    r2 = await fake_search_provider.search("python type hints")
    assert r1 == r2


async def test_search_preserves_provider_metadata(fake_search_provider):
    results = await fake_search_provider.search("python type hints")
    assert results[0].provider_metadata == {"score": 0.95}


async def test_search_does_not_mutate_registered_results(fake_search_provider):
    """The fake must not mutate the registered results (frozen dataclasses anyway)."""
    original = await fake_search_provider.search("python type hints")
    # Try to mutate the returned list — should not affect future calls
    original.clear()
    second = await fake_search_provider.search("python type hints")
    assert len(second) == 3


# ---------------------------------------------------------------------------
# FakeWebFetcher — happy path
# ---------------------------------------------------------------------------


async def test_fetch_returns_registered_page(fake_web_fetcher, strict_policy, fixed_time):
    fr = await fake_web_fetcher.fetch("https://docs.python.org/3/library/typing.html", strict_policy)
    assert fr.status_code == 200
    assert fr.content_type == "text/html; charset=utf-8"
    assert fr.final_url == "https://docs.python.org/3/library/typing.html"
    assert fr.requested_url == "https://docs.python.org/3/library/typing.html"
    assert fr.retrieved_at == fixed_time
    assert fr.content_bytes_ref == "fake:https://docs.python.org/3/library/typing.html"
    assert fr.truncated is False
    assert fr.redirects == ("https://docs.python.org/3/library/typing.html",)


async def test_fetch_computes_content_hash(fake_web_fetcher, strict_policy, fake_pages):
    url = "https://docs.python.org/3/library/typing.html"
    fr = await fake_web_fetcher.fetch(url, strict_policy)
    expected = sha256_hex(fake_pages[url])
    assert fr.content_hash == expected


async def test_fetch_is_case_insensitive_on_url(fake_web_fetcher, strict_policy):
    """URLs are normalized to lower for fixture lookup."""
    fr = await fake_web_fetcher.fetch("HTTPS://Docs.Python.Org/3/Library/typing.html", strict_policy)
    assert fr.status_code == 200


# ---------------------------------------------------------------------------
# FakeWebFetcher — SSRF denials
# ---------------------------------------------------------------------------


async def test_fetch_denies_loopback(fake_web_fetcher, strict_policy):
    with pytest.raises(WebFetchDenied) as exc_info:
        await fake_web_fetcher.fetch("http://127.0.0.1/", strict_policy)
    assert "127.0.0.1" in str(exc_info.value)
    assert exc_info.value.reason != ""


async def test_fetch_denies_aws_metadata(fake_web_fetcher, strict_policy):
    with pytest.raises(WebFetchDenied):
        await fake_web_fetcher.fetch("http://169.254.169.254/latest/meta-data/", strict_policy)


async def test_fetch_denies_ipv6_loopback(fake_web_fetcher, strict_policy):
    with pytest.raises(WebFetchDenied):
        await fake_web_fetcher.fetch("http://[::1]/", strict_policy)


async def test_fetch_denies_obfuscated_loopback(fake_web_fetcher, strict_policy):
    with pytest.raises(WebFetchDenied):
        await fake_web_fetcher.fetch("http://2130706433/", strict_policy)


async def test_fetch_denies_file_scheme(fake_web_fetcher, strict_policy):
    with pytest.raises(WebFetchDenied):
        await fake_web_fetcher.fetch("file:///etc/passwd", strict_policy)


# ---------------------------------------------------------------------------
# FakeWebFetcher — truncation
# ---------------------------------------------------------------------------


async def test_fetch_truncates_at_max_bytes(tiny_policy):
    """When body exceeds max_bytes, fetcher truncates and sets truncated=True."""
    big_body = b"x" * 1024
    fetcher = FakeWebFetcher(
        pages={"https://example.com/big": big_body},
        retrieved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    fr = await fetcher.fetch("https://example.com/big", tiny_policy)
    assert fr.truncated is True
    # The fetcher should have cut at max_bytes=128
    # (We can verify via the bytes_loader)
    loader = fetcher.make_bytes_loader()
    bytes_returned = loader(fr.content_bytes_ref)
    assert len(bytes_returned) == 128


async def test_fetch_no_truncation_under_limit(fake_web_fetcher, strict_policy):
    fr = await fake_web_fetcher.fetch("https://docs.python.org/3/library/typing.html", strict_policy)
    assert fr.truncated is False


# ---------------------------------------------------------------------------
# FakeWebFetcher — redirects
# ---------------------------------------------------------------------------


async def test_fetch_follows_registered_redirect(fake_web_fetcher, strict_policy):
    """When a URL is registered as a redirect, fetcher follows it."""
    # Add a redirect from /old to /new
    fake_web_fetcher._redirects["https://example.com/old"] = "https://example.com/new"
    fake_web_fetcher._pages["https://example.com/new"] = b"<html><body>new page</body></html>"

    fr = await fake_web_fetcher.fetch("https://example.com/old", strict_policy)
    assert fr.final_url == "https://example.com/new"
    assert fr.requested_url == "https://example.com/old"
    assert fr.redirects == ("https://example.com/old", "https://example.com/new")


async def test_fetch_denies_redirect_to_private_ip(fake_web_fetcher, strict_policy):
    """Redirect targets are re-checked against the policy."""
    fake_web_fetcher._redirects["https://example.com/redirect"] = "http://127.0.0.1/"
    with pytest.raises(WebFetchDenied):
        await fake_web_fetcher.fetch("https://example.com/redirect", strict_policy)


async def test_fetch_raises_on_redirect_loop(fake_web_fetcher, strict_policy):
    """A redirect loop should hit max_redirects and raise WebFetchError."""
    fake_web_fetcher._redirects["https://example.com/a"] = "https://example.com/b"
    fake_web_fetcher._redirects["https://example.com/b"] = "https://example.com/a"
    fake_web_fetcher._pages["https://example.com/a"] = b"page a"
    fake_web_fetcher._pages["https://example.com/b"] = b"page b"

    with pytest.raises(WebFetchError):
        await fake_web_fetcher.fetch("https://example.com/a", strict_policy)


# ---------------------------------------------------------------------------
# FakeWebFetcher — sensitive header stripping
# ---------------------------------------------------------------------------


async def test_fetch_strips_set_cookie_header(strict_policy, fixed_time):
    fetcher = FakeWebFetcher(
        pages={"https://example.com/": b"ok"},
        statuses={
            "https://example.com/": (
                200,
                "text/html",
                {"Set-Cookie": "session=abc; HttpOnly", "Etag": "xyz"},
            ),
        },
        retrieved_at=fixed_time,
    )
    fr = await fetcher.fetch("https://example.com/", strict_policy)
    assert "Set-Cookie" not in fr.response_headers
    assert "set-cookie" not in fr.response_headers
    assert fr.response_headers.get("Etag") == "xyz"


async def test_fetch_strips_authorization_header(strict_policy, fixed_time):
    fetcher = FakeWebFetcher(
        pages={"https://example.com/": b"ok"},
        statuses={
            "https://example.com/": (
                200,
                "text/html",
                {"Authorization": "Bearer secret", "Server": "nginx"},
            ),
        },
        retrieved_at=fixed_time,
    )
    fr = await fetcher.fetch("https://example.com/", strict_policy)
    assert "Authorization" not in fr.response_headers
    assert fr.response_headers.get("Server") == "nginx"


# ---------------------------------------------------------------------------
# FakeWebFetcher — unknown URLs
# ---------------------------------------------------------------------------


async def test_fetch_unknown_url_raises_web_fetch_error(fake_web_fetcher, strict_policy):
    with pytest.raises(WebFetchError):
        await fake_web_fetcher.fetch("https://unknown.example.com/", strict_policy)


# ---------------------------------------------------------------------------
# FakeContentExtractor
# ---------------------------------------------------------------------------


async def test_extractor_returns_text_from_html(
    fake_web_fetcher, fake_content_extractor, strict_policy, fake_bytes_loader,
):
    fr = await fake_web_fetcher.fetch("https://docs.python.org/3/library/typing.html", strict_policy)
    ed = await fake_content_extractor.extract(fr, bytes_loader=fake_bytes_loader)
    assert ed.source_url == "https://docs.python.org/3/library/typing.html"
    assert "typing" in ed.text.lower()
    assert ed.extraction_method == "fake_utf8"
    assert ed.content_hash != ""
    assert ed.snapshot_artifact_id is None


async def test_extractor_reports_truncation_warning(
    tiny_policy, fixed_time,
):
    fetcher = FakeWebFetcher(
        pages={"https://example.com/": b"<html>" + b"x" * 1024 + b"</html>"},
        retrieved_at=fixed_time,
    )
    extractor = FakeContentExtractor()
    fr = await fetcher.fetch("https://example.com/", tiny_policy)
    assert fr.truncated is True
    ed = await extractor.extract(fr, bytes_loader=fetcher.make_bytes_loader())
    assert any("truncated" in w for w in ed.warnings)


async def test_extractor_computes_distinct_hash_from_raw(
    fake_web_fetcher, fake_content_extractor, strict_policy, fake_bytes_loader,
):
    """The extracted-text hash differs from the raw-bytes hash (per ADR-017)."""
    fr = await fake_web_fetcher.fetch("https://docs.python.org/3/library/typing.html", strict_policy)
    ed = await fake_content_extractor.extract(fr, bytes_loader=fake_bytes_loader)
    assert ed.content_hash != fr.content_hash


async def test_extractor_extracts_title(fake_web_fetcher, fake_content_extractor, strict_policy, fake_bytes_loader):
    fr = await fake_web_fetcher.fetch("https://docs.python.org/3/library/typing.html", strict_policy)
    ed = await fake_content_extractor.extract(fr, bytes_loader=fake_bytes_loader)
    assert "typing" in ed.title.lower()


# ---------------------------------------------------------------------------
# bytes_loader
# ---------------------------------------------------------------------------


def test_bytes_loader_unknown_ref_raises_keyerror(fake_web_fetcher):
    loader = fake_web_fetcher.make_bytes_loader()
    with pytest.raises(KeyError):
        loader("fake:https://unknown.example.com/")


def test_bytes_loader_non_fake_ref_raises_keyerror(fake_web_fetcher):
    loader = fake_web_fetcher.make_bytes_loader()
    with pytest.raises(KeyError):
        loader("memory:snap_1")
