"""Tavily provider tests for ``aip.adapter.web.providers.tavily`` (ADR-017 WS-3).

Uses ``respx`` to mock the Tavily API so no live network is required.

Coverage:
    - Happy-path search (200 with results)
    - Empty results (200 with empty list)
    - Not-configured (no API key → WebProviderNotConfigured)
    - Rate limit (429 → WebProviderError)
    - Auth failure (401 → WebProviderNotConfigured)
    - Server error (500 → WebProviderError)
    - Timeout → WebProviderError
    - Malformed JSON → WebProviderError
    - Options mapping (limit, freshness_days, domains, topic)
    - Provider name
    - Key redaction in error messages
    - Key never cached on instance
"""

from __future__ import annotations

import httpx
import pytest
import respx

from aip.adapter.web.fake_provider import (
    WebProviderError,
    WebProviderNotConfigured,
)
from aip.adapter.web.providers.tavily import (
    DEFAULT_TAVILY_ENDPOINT,
    TavilySearchProvider,
)
from aip.foundation.schemas.web import SearchOptions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tavily_with_key() -> TavilySearchProvider:
    """A TavilySearchProvider with a fixed key (no env var needed)."""
    return TavilySearchProvider(
        api_key_env="AIP_WEB_SEARCH_API_KEY",
        key_loader=lambda: "tvly-test-key-12345",
    )


@pytest.fixture
def tavily_no_key() -> TavilySearchProvider:
    """A TavilySearchProvider with no key (simulates unset env var)."""
    return TavilySearchProvider(
        api_key_env="AIP_WEB_SEARCH_API_KEY",
        key_loader=lambda: "",
    )


TAVILY_RESPONSE_OK = {
    "results": [
        {
            "url": "https://example.com/article1",
            "title": "First Article",
            "content": "This is the first article snippet.",
            "score": 0.95,
            "published_date": "2024-03-15T10:30:00Z",
        },
        {
            "url": "https://example.com/article2",
            "title": "Second Article",
            "content": "This is the second article snippet.",
            "score": 0.82,
        },
    ],
    "answer": None,
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_returns_results(tavily_with_key):
    """A 200 response with results maps to SearchResult list."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json=TAVILY_RESPONSE_OK)
    )
    results = await tavily_with_key.search("python type hints")
    assert len(results) == 2
    assert results[0].provider == "tavily"
    assert results[0].query == "python type hints"
    assert results[0].rank == 1
    assert results[0].url == "https://example.com/article1"
    assert results[0].title == "First Article"
    assert results[0].snippet == "This is the first article snippet."
    assert results[0].provider_metadata["score"] == 0.95
    assert results[0].published_at is not None
    assert results[0].published_at.year == 2024


@respx.mock
async def test_search_empty_results(tavily_with_key):
    """A 200 response with empty results list returns []."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    results = await tavily_with_key.search("nonexistent topic")
    assert results == []


@respx.mock
async def test_search_preserves_query_in_results(tavily_with_key):
    """Each SearchResult carries the query for provenance."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json=TAVILY_RESPONSE_OK)
    )
    results = await tavily_with_key.search("my query")
    assert all(r.query == "my query" for r in results)


@respx.mock
async def test_search_ranks_sequentially(tavily_with_key):
    """Results are ranked 1..N in the order returned by Tavily."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json=TAVILY_RESPONSE_OK)
    )
    results = await tavily_with_key.search("test")
    assert [r.rank for r in results] == [1, 2]


# ---------------------------------------------------------------------------
# Not configured
# ---------------------------------------------------------------------------


async def test_search_no_key_raises_not_configured(tavily_no_key):
    """Calling search without an API key raises WebProviderNotConfigured."""
    with pytest.raises(WebProviderNotConfigured, match="AIP_WEB_SEARCH_API_KEY"):
        await tavily_no_key.search("anything")


async def test_provider_constructible_without_key(tavily_no_key):
    """A provider with no key can still be constructed (for health checks)."""
    # This should NOT raise — the provider is wired, just not configured.
    assert tavily_no_key.name == "tavily"


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_rate_limit_raises_provider_error(tavily_with_key):
    """A 429 response raises WebProviderError."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(429, text="Rate limit exceeded")
    )
    with pytest.raises(WebProviderError, match="rate limit"):
        await tavily_with_key.search("test")


@respx.mock
async def test_search_auth_failure_raises_not_configured(tavily_with_key):
    """A 401 response raises WebProviderNotConfigured (key rejected)."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(401, text="Invalid API key")
    )
    with pytest.raises(WebProviderNotConfigured, match="401"):
        await tavily_with_key.search("test")


@respx.mock
async def test_search_server_error_raises_provider_error(tavily_with_key):
    """A 500 response raises WebProviderError."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(500, text="Internal server error")
    )
    with pytest.raises(WebProviderError, match="HTTP 500"):
        await tavily_with_key.search("test")


@respx.mock
async def test_search_timeout_raises_provider_error(tavily_with_key):
    """A timeout raises WebProviderError."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    with pytest.raises(WebProviderError, match="timed out"):
        await tavily_with_key.search("test")


@respx.mock
async def test_search_malformed_json_raises_provider_error(tavily_with_key):
    """A non-JSON response raises WebProviderError."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, text="not json at all")
    )
    with pytest.raises(WebProviderError, match="non-JSON"):
        await tavily_with_key.search("test")


@respx.mock
async def test_search_results_not_list_raises(tavily_with_key):
    """If the 'results' field is not a list, raise WebProviderError."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={"results": "not a list"})
    )
    with pytest.raises(WebProviderError, match="not a list"):
        await tavily_with_key.search("test")


# ---------------------------------------------------------------------------
# Options mapping
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_limit_capped_at_max(tavily_with_key):
    """limit > MAX_TAVILY_LIMIT (20) is capped."""
    route = respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await tavily_with_key.search("test", options=SearchOptions(limit=100))
    sent_payload = route.calls[0].request.read()
    import json
    payload = json.loads(sent_payload)
    assert payload["max_results"] == 20


@respx.mock
async def test_search_freshness_days_passed_to_provider(tavily_with_key):
    """freshness_days is sent as 'days' in the payload."""
    route = respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await tavily_with_key.search("test", options=SearchOptions(freshness_days=7))
    import json
    payload = json.loads(route.calls[0].request.read())
    assert payload["days"] == 7


@respx.mock
async def test_search_domains_passed_to_provider(tavily_with_key):
    """domains is sent as 'include_domains' in the payload."""
    route = respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await tavily_with_key.search(
        "test", options=SearchOptions(domains=("arxiv.org", "github.com"))
    )
    import json
    payload = json.loads(route.calls[0].request.read())
    assert payload["include_domains"] == ["arxiv.org", "github.com"]


@respx.mock
async def test_search_topic_passed_to_provider(tavily_with_key):
    """topic is sent in the payload when set."""
    route = respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await tavily_with_key.search("test", options=SearchOptions(topic="news"))
    import json
    payload = json.loads(route.calls[0].request.read())
    assert payload["topic"] == "news"


# ---------------------------------------------------------------------------
# Key handling
# ---------------------------------------------------------------------------


@respx.mock
async def test_key_sent_in_payload(tavily_with_key):
    """The API key is sent in the request payload (Tavily's expected auth)."""
    route = respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await tavily_with_key.search("test")
    import json
    payload = json.loads(route.calls[0].request.read())
    assert payload["api_key"] == "tvly-test-key-12345"


@respx.mock
async def test_key_redacted_from_error_message(tavily_with_key):
    """When the server echoes the key in an error, it's redacted in the exception."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(
            500,
            text='{"error": "invalid key tvly-test-key-12345 provided"}',
        )
    )
    with pytest.raises(WebProviderError) as exc_info:
        await tavily_with_key.search("test")
    # The key must NOT appear in the exception message.
    assert "tvly-test-key-12345" not in str(exc_info.value)
    assert "tvly-[redacted]" in str(exc_info.value)


def test_key_not_cached_on_instance(tavily_with_key):
    """The API key must not appear as an attribute on the provider instance."""
    # Walk the instance's __dict__ to ensure no plaintext key.
    for attr_name, attr_value in vars(tavily_with_key).items():
        assert attr_value != "tvly-test-key-12345", (
            f"attribute {attr_name!r} contains the plaintext API key"
        )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


@respx.mock
async def test_provider_metadata_carries_score_and_raw(tavily_with_key):
    """provider_metadata includes 'score' and 'raw_response' (extras)."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json=TAVILY_RESPONSE_OK)
    )
    results = await tavily_with_key.search("test")
    assert "score" in results[0].provider_metadata
    # 'raw_response' carries fields not in the standard schema
    assert isinstance(results[0].provider_metadata["raw_response"], dict)


@respx.mock
async def test_published_date_parsed(tavily_with_key):
    """Tavily's published_date string is parsed to a datetime."""
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json=TAVILY_RESPONSE_OK)
    )
    results = await tavily_with_key.search("test")
    assert results[0].published_at is not None
    assert results[0].published_at.year == 2024
    assert results[0].published_at.month == 3
    # Second result has no published_date
    assert results[1].published_at is None


@respx.mock
async def test_skips_results_without_url(tavily_with_key):
    """Results missing a URL are skipped (not included in output)."""
    response = {
        "results": [
            {"url": "https://example.com/1", "title": "T1", "content": "C1"},
            {"url": "", "title": "No URL", "content": "Skipped"},
            {"title": "No URL field", "content": "Skipped"},
            {"url": "https://example.com/2", "title": "T2", "content": "C2"},
        ]
    }
    respx.post(f"{DEFAULT_TAVILY_ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json=response)
    )
    results = await tavily_with_key.search("test")
    assert len(results) == 2
    assert results[0].url == "https://example.com/1"
    assert results[1].url == "https://example.com/2"
