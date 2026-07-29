"""HTTP fetcher tests for ``aip.adapter.web.http_fetcher`` (ADR-017 WS-2).

Uses ``respx`` to mock httpx so no live network is required.

Coverage:
    - Happy-path fetch (200, text/html)
    - SSRF denials (loopback, private IP, DNS-rebinding-to-loopback)
    - Redirect following + redirect-to-private-IP denial
    - Redirect cap exhaustion
    - Content-type allowlist enforcement
    - max_bytes truncation
    - Sensitive response header stripping (Set-Cookie, Authorization)
    - content_hash computation
    - Lifecycle registration (task registered with BackgroundTaskRegistry)
    - DNS resolution failure denial
    - Timeout and connection error → WebFetchError
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from aip.adapter.web.fake_provider import WebFetchDenied, WebFetchError
from aip.adapter.web.http_fetcher import HttpxWebFetcher
from aip.adapter.web.lifecycle import BackgroundTaskRegistry
from aip.foundation.schemas.web import FetchPolicy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_dns() -> dict:
    """A fake DNS table mapping hostname → list of IP strings."""
    return {
        "example.com": ["93.184.216.34"],
        "safe.example.com": ["8.8.8.8"],
        "rebinding.example.com": ["127.0.0.1"],  # DNS-rebinding-to-loopback
        "private.example.com": ["10.0.0.1"],
        "fail.example.com": [],  # DNS failure
    }


@pytest.fixture
def fetcher(fake_dns) -> HttpxWebFetcher:
    """An HttpxWebFetcher with a fake DNS resolver (no real DNS lookups)."""
    def resolver(hostname: str) -> list[str]:
        return fake_dns.get(hostname.lower(), ["93.184.216.34"])

    return HttpxWebFetcher(
        dns_resolver=resolver,
        task_registry=None,  # lifecycle tested separately
    )


@pytest.fixture
def fetcher_with_registry(fake_dns) -> tuple[HttpxWebFetcher, BackgroundTaskRegistry]:
    """An HttpxWebFetcher + its registry, for lifecycle tests."""
    registry = BackgroundTaskRegistry()

    def resolver(hostname: str) -> list[str]:
        return fake_dns.get(hostname.lower(), ["93.184.216.34"])

    f = HttpxWebFetcher(dns_resolver=resolver, task_registry=registry)
    return f, registry


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_happy_path(fetcher, strict_policy):
    """A normal 200 text/html fetch returns a FetchedResource."""
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(
            200,
            content=b"<html><body><p>Hello world</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    fr = await fetcher.fetch("https://example.com/page", strict_policy)
    assert fr.status_code == 200
    assert fr.content_type == "text/html; charset=utf-8"
    assert fr.final_url == "https://example.com/page"
    assert fr.requested_url == "https://example.com/page"
    assert fr.truncated is False
    assert fr.redirects == ("https://example.com/page",)
    assert len(fr.content_hash) == 64  # SHA-256 hex
    assert "content-type" in fr.response_headers


@respx.mock
async def test_fetch_computes_content_hash(fetcher, strict_policy):
    """content_hash is SHA-256 of the raw response bytes."""
    body = b"<html><body>hash me</body></html>"
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    import hashlib
    expected = hashlib.sha256(body).hexdigest()
    fr = await fetcher.fetch("https://example.com/", strict_policy)
    assert fr.content_hash == expected


# ---------------------------------------------------------------------------
# SSRF denials
# ---------------------------------------------------------------------------


async def test_fetch_denies_loopback_ip(fetcher, strict_policy):
    """A direct loopback IP URL is denied before any network call."""
    with pytest.raises(WebFetchDenied) as exc_info:
        await fetcher.fetch("http://127.0.0.1/", strict_policy)
    assert "127.0.0.1" in str(exc_info.value)


async def test_fetch_denies_private_ip(fetcher, strict_policy):
    """A direct private-IP URL is denied."""
    with pytest.raises(WebFetchDenied):
        await fetcher.fetch("http://10.0.0.1/", strict_policy)


async def test_fetch_denies_dns_rebinding_to_loopback(fetcher, strict_policy):
    """If DNS resolves to a loopback IP, the fetch is denied (DNS-rebinding defense)."""
    # rebinding.example.com → 127.0.0.1 (per fake_dns fixture)
    with pytest.raises(WebFetchDenied) as exc_info:
        await fetcher.fetch("https://rebinding.example.com/", strict_policy)
    assert "private" in exc_info.value.reason.lower() or "loopback" in exc_info.value.reason.lower()


async def test_fetch_denies_dns_rebinding_to_private(fetcher, strict_policy):
    """If DNS resolves to a private IP, the fetch is denied."""
    with pytest.raises(WebFetchDenied):
        await fetcher.fetch("https://private.example.com/", strict_policy)


async def test_fetch_denies_dns_failure(fetcher, strict_policy):
    """If DNS returns no addresses, the fetch is denied defensively."""
    with pytest.raises(WebFetchDenied) as exc_info:
        await fetcher.fetch("https://fail.example.com/", strict_policy)
    assert "DNS" in exc_info.value.reason or "denied" in exc_info.value.reason


async def test_fetch_denies_file_scheme(fetcher, strict_policy):
    with pytest.raises(WebFetchDenied):
        await fetcher.fetch("file:///etc/passwd", strict_policy)


# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_follows_redirect(fetcher, strict_policy):
    """A redirect to a safe URL is followed; redirects chain is recorded."""
    respx.get("https://example.com/old").mock(
        return_value=httpx.Response(
            301,
            headers={"location": "https://example.com/new"},
        )
    )
    respx.get("https://example.com/new").mock(
        return_value=httpx.Response(
            200,
            content=b"<html><body>new page</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    fr = await fetcher.fetch("https://example.com/old", strict_policy)
    assert fr.final_url == "https://example.com/new"
    assert fr.requested_url == "https://example.com/old"
    assert fr.redirects == ("https://example.com/old", "https://example.com/new")


@respx.mock
async def test_fetch_denies_redirect_to_loopback(fetcher, strict_policy):
    """A redirect to a loopback IP is denied at the redirect hop."""
    respx.get("https://example.com/redirect").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/"},
        )
    )
    with pytest.raises(WebFetchDenied):
        await fetcher.fetch("https://example.com/redirect", strict_policy)


@respx.mock
async def test_fetch_denies_redirect_to_dns_rebinding(fetcher, strict_policy):
    """A redirect to a hostname that resolves to a private IP is denied."""
    respx.get("https://example.com/redirect").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "https://rebinding.example.com/"},
        )
    )
    with pytest.raises(WebFetchDenied):
        await fetcher.fetch("https://example.com/redirect", strict_policy)


@respx.mock
async def test_fetch_exhausts_redirect_cap(fetcher):
    """Exceeding max_redirects raises WebFetchError."""
    policy = FetchPolicy(max_redirects=2)
    # Create a redirect loop: a → b → a → b → ...
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.com/b"})
    )
    respx.get("https://example.com/b").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.com/a"})
    )
    with pytest.raises(WebFetchError, match="max_redirects"):
        await fetcher.fetch("https://example.com/a", policy)


@respx.mock
async def test_fetch_redirect_without_location_raises(fetcher, strict_policy):
    """A redirect response without a Location header raises WebFetchError."""
    respx.get("https://example.com/bad").mock(
        return_value=httpx.Response(302, headers={})
    )
    with pytest.raises(WebFetchError, match="Location"):
        await fetcher.fetch("https://example.com/bad", strict_policy)


# ---------------------------------------------------------------------------
# Content-type allowlist
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_denies_disallowed_content_type():
    """When allowed_content_types is set, non-matching responses are denied."""
    policy = FetchPolicy(allowed_content_types=("text/html",))
    # Fake DNS resolver that returns a public IP
    fetcher = HttpxWebFetcher(dns_resolver=lambda h: ["8.8.8.8"])

    respx.get("https://example.com/file").mock(
        return_value=httpx.Response(
            200,
            content=b"binary data",
            headers={"content-type": "application/octet-stream"},
        )
    )
    with pytest.raises(WebFetchDenied, match="content type"):
        await fetcher.fetch("https://example.com/file", policy)


@respx.mock
async def test_fetch_allows_matching_content_type():
    """When allowed_content_types is set, matching responses succeed."""
    policy = FetchPolicy(allowed_content_types=("text/html",))
    fetcher = HttpxWebFetcher(dns_resolver=lambda h: ["8.8.8.8"])

    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(
            200,
            content=b"<html></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    fr = await fetcher.fetch("https://example.com/page", policy)
    assert fr.status_code == 200


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_truncates_at_max_bytes():
    """Response body is truncated at max_bytes; truncated=True."""
    policy = FetchPolicy(max_bytes=100)
    fetcher = HttpxWebFetcher(dns_resolver=lambda h: ["8.8.8.8"])
    big_body = b"x" * 500

    respx.get("https://example.com/big").mock(
        return_value=httpx.Response(200, content=big_body, headers={"content-type": "text/plain"})
    )
    fr = await fetcher.fetch("https://example.com/big", policy)
    assert fr.truncated is True
    # The content_hash is of the truncated body (100 bytes), not the original
    import hashlib
    assert fr.content_hash == hashlib.sha256(b"x" * 100).hexdigest()


@respx.mock
async def test_fetch_no_truncation_under_limit(fetcher, strict_policy):
    """Response under max_bytes is not truncated."""
    respx.get("https://example.com/small").mock(
        return_value=httpx.Response(200, content=b"small", headers={"content-type": "text/plain"})
    )
    fr = await fetcher.fetch("https://example.com/small", strict_policy)
    assert fr.truncated is False


# ---------------------------------------------------------------------------
# Sensitive header stripping
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_strips_set_cookie(fetcher, strict_policy):
    """Set-Cookie response headers are stripped from the FetchedResource."""
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            content=b"ok",
            headers={
                "content-type": "text/html",
                "set-cookie": "session=abc123; HttpOnly",
                "etag": "xyz789",
            },
        )
    )
    fr = await fetcher.fetch("https://example.com/", strict_policy)
    # Set-Cookie must not appear (case-insensitive check)
    assert not any(k.lower() == "set-cookie" for k in fr.response_headers)
    # Etag should be preserved
    assert fr.response_headers.get("etag") == "xyz789"


@respx.mock
async def test_fetch_strips_authorization_header(fetcher, strict_policy):
    """WWW-Authenticate response headers are stripped."""
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            content=b"ok",
            headers={
                "content-type": "text/html",
                "www-authenticate": 'Basic realm="secure"',
                "server": "nginx",
            },
        )
    )
    fr = await fetcher.fetch("https://example.com/", strict_policy)
    assert not any(k.lower() == "www-authenticate" for k in fr.response_headers)
    assert fr.response_headers.get("server") == "nginx"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_timeout_raises_web_fetch_error(fetcher, strict_policy):
    """A timeout produces WebFetchError, not a raw httpx exception."""
    respx.get("https://example.com/slow").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(WebFetchError, match="timeout"):
        await fetcher.fetch("https://example.com/slow", strict_policy)


@respx.mock
async def test_fetch_connection_error_raises_web_fetch_error(fetcher, strict_policy):
    """A connection error produces WebFetchError."""
    respx.get("https://example.com/down").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(WebFetchError, match="connection error"):
        await fetcher.fetch("https://example.com/down", strict_policy)


# ---------------------------------------------------------------------------
# Lifecycle integration
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_registers_with_task_registry(fetcher_with_registry, strict_policy):
    """During a fetch, the current task is registered with the registry."""
    fetcher, registry = fetcher_with_registry
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, content=b"ok", headers={"content-type": "text/html"})
    )
    # The fetch runs in the current task; the fetcher registers it.
    # After the fetch completes, the task is unregistered.
    assert registry.names() == []
    await fetcher.fetch("https://example.com/", strict_policy)
    # After fetch completes, the task is unregistered.
    assert registry.names() == []


@respx.mock
async def test_fetch_cancellable_via_registry(fetcher_with_registry, strict_policy):
    """An in-flight fetch can be cancelled via the registry."""
    fetcher, registry = fetcher_with_registry

    # Mock a slow response
    async def slow_handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, content=b"slow", headers={"content-type": "text/html"})

    respx.get("https://example.com/slow").mock(side_effect=slow_handler)

    # Start the fetch in a background task
    fetch_task = asyncio.create_task(fetcher.fetch("https://example.com/slow", strict_policy))

    # Give it a moment to register
    await asyncio.sleep(0.1)

    # The fetch task should be registered
    # (it registers itself as the current task)
    assert len(registry.names()) > 0

    # Cancel via registry
    await registry.cancel_all(timeout_per_task=2.0)

    # The fetch task should have been cancelled
    with pytest.raises(asyncio.CancelledError):
        await fetch_task
