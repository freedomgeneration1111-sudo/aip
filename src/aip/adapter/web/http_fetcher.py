"""Bounded HTTP fetcher for Web Source Acquisition (ADR-017 WS-2).

Implements ``WebFetcher`` using ``httpx.AsyncClient`` with:

    - SSRF defense at every redirect hop (``is_url_allowed`` on the
      requested URL AND on each redirect target; DNS resolution check
      via ``is_ip_allowed`` on every resolved address to defeat DNS
      rebinding).
    - Streaming body with ``max_bytes`` truncation.
    - Redirect cap from ``FetchPolicy.max_redirects``.
    - Timeout from ``FetchPolicy.timeout_seconds``.
    - Content-Type allowlist from ``FetchPolicy.allowed_content_types``.
    - Sensitive response header stripping (``Set-Cookie``,
      ``Authorization``, ``Cookie``).
    - SHA-256 ``content_hash`` of the raw response bytes.
    - Lifecycle integration: every in-flight fetch registers with the
      ``BackgroundTaskRegistry`` so shutdown can cancel cleanly.
    - Bytes persistence: an optional ``bytes_sink`` callable receives
      the streamed body + the ``FetchedResource`` and returns a storage
      ref (typically a snapshot_id).  The ref is stored as
      ``content_bytes_ref`` so downstream extractors can retrieve the
      bytes from the snapshot store.  This closes the WS-3 known
      limitation where bytes were lost after the fetch returned.

This module imports ``httpx`` — it lives in the adapter layer, which
``tests/test_no_network.py`` allows.  CI tests mock the HTTP layer
via ``respx``; no live network is required.

DNS-rebinding defense:

    A malicious domain may resolve to a public IP at first resolution
    (passing the SSRF check) and then to a private IP on the actual
    connection.  To defeat this, ``HttpxWebFetcher`` resolves the
    hostname BEFORE creating the httpx request and checks every
    resolved address against ``is_ip_allowed``.  If any address is
    private/loopback/etc., the fetch is denied.

    For testability, DNS resolution is performed via a pluggable
    ``dns_resolver`` callable (default: ``socket.getaddrinfo``).
    Tests inject a fake resolver so no real DNS lookups happen.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from aip.adapter.web.lifecycle import BackgroundTaskRegistry
from aip.adapter.web.policy import (
    is_ip_allowed,
    is_url_allowed,
)
from aip.foundation.schemas.web import (
    FetchedResource,
    FetchPolicy,
    sha256_hex,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DNS resolution type
# ---------------------------------------------------------------------------

#: A DNS resolver callable: hostname -> list of IP address strings.
#: Tests inject a fake; production uses socket.getaddrinfo.
DnsResolver = Callable[[str], list[str]]

#: A bytes sink callable: (body, fetched_resource) -> storage ref string.
#: The sink persists the body and returns a ref that downstream code can
#: use to retrieve the bytes (typically a snapshot_id from
#: WebSnapshotStore.put).  If the sink fails, the fetch still succeeds —
#: the bytes are just not persisted (extractors will get bytes_unavailable).
BytesSink = Callable[[bytes, FetchedResource], Awaitable[str]]


def _default_dns_resolver(hostname: str) -> list[str]:
    """Default DNS resolver using ``socket.getaddrinfo``.

    Returns a list of IP address strings.  On failure, returns an empty
    list (the caller treats empty as a denial — we cannot verify the
    host is not private, so we deny defensively).
    """
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        return list({info[4][0] for info in infos})
    except (socket.gaierror, socket.herror, OSError):
        return []


# ---------------------------------------------------------------------------
# Sensitive headers to strip
# ---------------------------------------------------------------------------

_SENSITIVE_RESPONSE_HEADERS = frozenset({
    "set-cookie",
    "authorization",
    "cookie",
    "www-authenticate",
    "proxy-authenticate",
    "proxy-authorization",
})


# ---------------------------------------------------------------------------
# HttpxWebFetcher
# ---------------------------------------------------------------------------


class HttpxWebFetcher:
    """Production ``WebFetcher`` backed by ``httpx.AsyncClient``.

    Args:
        task_registry: Background task registry for lifecycle management.
            If provided, every in-flight fetch registers a task so
            ``registry.cancel_all()`` can cancel it on shutdown.  If
            ``None``, fetches are not registered (use only in tests
            where lifecycle is not under test).
        dns_resolver: Pluggable DNS resolver (hostname -> list of IP
            strings).  Defaults to ``socket.getaddrinfo``.  Tests
            inject a fake to avoid real DNS lookups.
        client_factory: Optional callable that returns an
            ``httpx.AsyncClient``.  Defaults to creating a new client
            per fetch.  Tests can inject a mock client.
    """

    def __init__(
        self,
        *,
        task_registry: BackgroundTaskRegistry | None = None,
        dns_resolver: DnsResolver | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        bytes_sink: BytesSink | None = None,
    ) -> None:
        self._registry = task_registry
        self._dns_resolver = dns_resolver or _default_dns_resolver
        self._client_factory = client_factory
        self._bytes_sink = bytes_sink

    async def fetch(
        self,
        url: str,
        policy: FetchPolicy,
    ) -> FetchedResource:
        """Fetch a URL with full policy enforcement.

        Raises:
            WebFetchDenied: URL denied by policy (SSRF, scheme, content
                type, size).
            WebFetchError: Network or HTTP error (timeout, DNS failure,
                5xx after retries).
        """
        from aip.adapter.web.fake_provider import WebFetchDenied

        # ---- Static policy check (scheme, host, IP-literal SSRF) ----
        allowed, reason = is_url_allowed(url, policy)
        if not allowed:
            raise WebFetchDenied(url, reason)

        # ---- DNS-rebinding defense for non-IP hosts ----
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        # is_url_allowed already verified the host is non-empty.
        # If the host is NOT an IP literal, resolve and check each address.
        if not _is_ip_literal(host):
            resolved = self._dns_resolver(host)
            if not resolved:
                raise WebFetchDenied(
                    url, f"DNS resolution failed for {host!r} (denied defensively)"
                )
            import ipaddress
            for addr_str in resolved:
                try:
                    addr = ipaddress.ip_address(addr_str)
                except ValueError:
                    continue
                ok, ip_reason = is_ip_allowed(addr, policy)
                if not ok:
                    raise WebFetchDenied(url, f"{ip_reason} (resolved from {host})")

        # ---- Execute the fetch (with lifecycle registration) ----
        if self._registry is not None:
            task_name = f"web_fetch:{url}:{id(url)}"
            current_task = asyncio.current_task()
            if current_task is not None:
                try:
                    self._registry.register(task_name, current_task)
                except ValueError:
                    pass  # duplicate name edge case — not critical
            try:
                return await self._do_fetch(url, policy)
            finally:
                if self._registry is not None:
                    self._registry.unregister(task_name)
        else:
            return await self._do_fetch(url, policy)

    async def _do_fetch(
        self,
        url: str,
        policy: FetchPolicy,
    ) -> FetchedResource:
        """Perform the actual HTTP fetch with redirect tracking."""
        from aip.adapter.web.fake_provider import WebFetchDenied, WebFetchError

        redirects_chain: list[str] = [url]
        current_url = url
        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(policy.timeout_seconds),
            "follow_redirects": False,  # we handle redirects manually for SSRF checking
            "verify": True,  # always verify TLS
        }

        client: httpx.AsyncClient
        if self._client_factory is not None:
            client = self._client_factory()
        else:
            client = httpx.AsyncClient(**client_kwargs)

        try:
            for hop in range(max(0, policy.max_redirects) + 1):
                try:
                    response = await client.get(current_url)
                except httpx.TimeoutException as exc:
                    raise WebFetchError(f"timeout fetching {current_url!r}: {exc}") from exc
                except httpx.ConnectError as exc:
                    raise WebFetchError(f"connection error fetching {current_url!r}: {exc}") from exc
                except httpx.HTTPError as exc:
                    raise WebFetchError(f"HTTP error fetching {current_url!r}: {exc}") from exc

                # ---- Handle redirects ----
                if response.is_redirect:
                    # If we've hit the redirect cap, raise instead of following.
                    if hop >= policy.max_redirects:
                        await response.aclose()
                        raise WebFetchError(
                            f"exceeded max_redirects={policy.max_redirects} for {url!r}"
                        )

                    location = response.headers.get("location", "")
                    if not location:
                        await response.aclose()
                        raise WebFetchError(f"redirect without Location header from {current_url!r}")

                    # Resolve relative redirect
                    next_url = str(httpx.URL(current_url).join(location))

                    # Re-check the redirect target (SSRF + DNS)
                    allowed, reason = is_url_allowed(next_url, policy)
                    if not allowed:
                        await response.aclose()
                        raise WebFetchDenied(next_url, f"redirect target denied: {reason}")

                    # DNS-rebinding check on redirect target
                    next_parts = urlsplit(next_url)
                    next_host = (next_parts.hostname or "").lower()
                    if not _is_ip_literal(next_host):
                        resolved = self._dns_resolver(next_host)
                        if not resolved:
                            await response.aclose()
                            raise WebFetchDenied(
                                next_url,
                                f"DNS resolution failed for redirect target {next_host!r}",
                            )
                        import ipaddress
                        for addr_str in resolved:
                            try:
                                addr = ipaddress.ip_address(addr_str)
                            except ValueError:
                                continue
                            ok, ip_reason = is_ip_allowed(addr, policy)
                            if not ok:
                                await response.aclose()
                                raise WebFetchDenied(
                                    next_url,
                                    f"redirect target {ip_reason} (resolved from {next_host})",
                                )

                    redirects_chain.append(next_url)
                    current_url = next_url
                    await response.aclose()
                    continue

                # ---- We have the final response ----
                if policy.allowed_content_types is not None:
                    content_type_raw = response.headers.get("content-type", "")
                    content_type_main = content_type_raw.split(";")[0].strip().lower()
                    allowed_cts = {ct.lower() for ct in policy.allowed_content_types}
                    if content_type_main and content_type_main not in allowed_cts:
                        raise WebFetchDenied(
                            current_url,
                            f"content type {content_type_main!r} not in allowed list",
                        )

                # ---- Stream and truncate the body ----
                body_chunks: list[bytes] = []
                total_bytes = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    if total_bytes + len(chunk) > policy.max_bytes:
                        remaining = policy.max_bytes - total_bytes
                        if remaining > 0:
                            body_chunks.append(chunk[:remaining])
                            total_bytes += remaining
                        truncated = True
                        break
                    body_chunks.append(chunk)
                    total_bytes += len(chunk)

                body = b"".join(body_chunks)
                content_hash = sha256_hex(body)

                # ---- Build safe response headers ----
                safe_headers: dict[str, str] = {}
                for k, v in response.headers.items():
                    if k.lower() not in _SENSITIVE_RESPONSE_HEADERS:
                        safe_headers[k] = v

                content_type = response.headers.get("content-type", "application/octet-stream")
                retrieved_at = datetime.now(timezone.utc)

                # ---- Build the FetchedResource with a placeholder ref ----
                fetched = FetchedResource(
                    requested_url=url,
                    final_url=current_url,
                    status_code=response.status_code,
                    content_type=content_type,
                    content_bytes_ref=f"httpx:{current_url}:{content_hash[:16]}",
                    retrieved_at=retrieved_at,
                    response_headers=safe_headers,
                    content_hash=content_hash,
                    truncated=truncated,
                    redirects=tuple(redirects_chain),
                )

                # ---- Persist bytes via the sink (if configured) ----
                # The sink stores the body in the snapshot store and
                # returns a storage ref (snapshot_id).  We replace the
                # placeholder ref with the real one so downstream
                # extractors can retrieve the bytes directly.
                if self._bytes_sink is not None:
                    try:
                        storage_ref = await self._bytes_sink(body, fetched)
                        if storage_ref:
                            fetched = dataclasses.replace(
                                fetched,
                                content_bytes_ref=storage_ref,
                            )
                    except Exception as exc:
                        logger.warning("bytes_sink_persist_failed: %s", exc)
                        # Non-fatal — the fetch still succeeds, but
                        # extractors will get bytes_unavailable if they
                        # try to read the body.

                return fetched

            # This line is unreachable: the loop either returns a
            # FetchedResource or raises WebFetchError when the redirect
            # cap is hit.  Defensive raise for exhaustiveness.
            raise WebFetchError(
                f"unexpected loop exit fetching {url!r}"
            )
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_ip_literal(host: str) -> bool:
    """Return True if ``host`` looks like an IP literal (v4 or v6)."""
    if not host:
        return False
    # IPv6 in brackets
    if host.startswith("[") and host.endswith("]"):
        return True
    # Contains ":" → IPv6
    if ":" in host:
        return True
    # Dotted decimal with 4 parts
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return True
    return False


__all__ = [
    "HttpxWebFetcher",
    "DnsResolver",
    "BytesSink",
]
