"""Fake search + fetch providers for Web Source Acquisition CI (ADR-017 WS-1).

The fake providers let every web-related code path be exercised in CI
without network access.  ``tests/test_no_network.py`` forbids
``httpx``/``requests``/``aiohttp`` in the foundation and orchestration
layers, but allows them in the adapter layer.  These fakes do NOT
import any network library — they read from in-memory dicts or local
fixture files.

Determinism contract:

    - The same ``seed`` always produces the same ``SearchResult`` ranks
      and snippets.
    - The same URL always maps to the same ``FetchedResource`` bytes
      (modulo the ``retrieved_at`` timestamp, which is injected by the
      caller or defaults to ``datetime.now(timezone.utc)``).
    - ``fetch`` honors ``FetchPolicy`` exactly: it denies SSRF URLs
      via ``is_url_allowed``, truncates at ``max_bytes``, and sets
      ``truncated=True`` when it does so.

The fakes are intentionally minimal — they exist to make tests
deterministic, not to model real provider behavior.  Real provider
behavior is exercised by the WS-3 Tavily adapter (with mocked HTTP)
and by the manual dogfood smoke (with live network).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aip.adapter.web.policy import is_url_allowed
from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    FetchPolicy,
    SearchOptions,
    SearchResult,
    sha256_hex,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WebProviderNotConfigured(RuntimeError):
    """Raised when a real (non-fake) provider is invoked without an API key."""


class WebProviderError(RuntimeError):
    """Raised when a provider returns an error response."""


class WebFetchDenied(RuntimeError):
    """Raised when ``WebFetcher.fetch`` is called with a URL denied by policy."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"fetch denied for {url!r}: {reason}")
        self.url = url
        self.reason = reason


class WebFetchError(RuntimeError):
    """Raised when ``WebFetcher.fetch`` encounters a network or HTTP error."""


# ---------------------------------------------------------------------------
# FakeSearchProvider
# ---------------------------------------------------------------------------


class FakeSearchProvider:
    """In-memory ``SearchProvider`` for CI.

    Construct with a mapping of ``query -> list[SearchResult]`` (or a
    list of ``(query_substring, result)`` pairs for substring matching).
    The provider returns the pre-registered results for a query,
    honoring ``SearchOptions.limit``.

    Example::

        provider = FakeSearchProvider({
            "python type hints": [
                SearchResult(provider="fake", query="python type hints", rank=1, ...),
                SearchResult(provider="fake", query="python type hints", rank=2, ...),
            ],
        })
        results = await provider.search("python type hints")

    For fixture-driven tests, use ``FakeSearchProvider.from_yaml_file``
    or ``FakeSearchProvider.from_fixture_dir`` (WS-2 will add these;
    WS-1 ships the dict constructor only).
    """

    def __init__(
        self,
        results: dict[str, list[SearchResult]] | None = None,
        *,
        name: str = "fake",
    ) -> None:
        self._name = name
        # Normalize keys to lower for case-insensitive exact match.
        self._results: dict[str, list[SearchResult]] = {
            (k or "").lower(): list(v or []) for k, v in (results or {}).items()
        }

    @property
    def name(self) -> str:
        return self._name

    async def search(
        self,
        query: str,
        *,
        options: SearchOptions | None = None,
    ) -> list[SearchResult]:
        opts = options or SearchOptions()
        key = (query or "").lower()
        matches = self._results.get(key, [])
        # Apply limit
        limited = matches[: max(0, opts.limit)]
        # Re-rank 1..N in case the caller passed a smaller limit
        reranked: list[SearchResult] = []
        for i, r in enumerate(limited, start=1):
            reranked.append(
                SearchResult(
                    provider=r.provider,
                    query=r.query,
                    rank=i,
                    url=r.url,
                    title=r.title,
                    snippet=r.snippet,
                    published_at=r.published_at,
                    provider_metadata=dict(r.provider_metadata),
                )
            )
        return reranked


# ---------------------------------------------------------------------------
# FakeWebFetcher
# ---------------------------------------------------------------------------


class FakeWebFetcher:
    """In-memory ``WebFetcher`` for CI.

    Construct with a mapping of ``url -> bytes`` (the raw response
    body) plus optional ``url -> (status_code, content_type, headers)``.
    The fetcher honors ``FetchPolicy``:

        - SSRF denials via ``is_url_allowed`` (raises ``WebFetchDenied``).
        - Truncation at ``policy.max_bytes`` (sets ``truncated=True``).
        - Records ``redirects`` when the policy allows redirects and
          the URL has been registered as a redirect (via ``redirects``
          constructor arg).

    Unknown URLs raise ``WebFetchError`` (mirrors a real fetcher's
    DNS-failure behavior).

    The ``content_bytes_ref`` returned in ``FetchedResource`` is the
    string ``"fake:{url}"`` so tests can retrieve the bytes via the
    ``bytes_loader`` callable (provided by the fake fetcher itself;
    see ``make_bytes_loader``).
    """

    def __init__(
        self,
        pages: dict[str, bytes] | None = None,
        *,
        statuses: dict[str, tuple[int, str, dict[str, str]]] | None = None,
        redirects: dict[str, str] | None = None,
        retrieved_at: datetime | None = None,
    ) -> None:
        # Normalize page URLs to lower for case-insensitive match.
        self._pages: dict[str, bytes] = {
            (k or "").lower(): v for k, v in (pages or {}).items()
        }
        self._statuses: dict[str, tuple[int, str, dict[str, str]]] = {
            (k or "").lower(): v for k, v in (statuses or {}).items()
        }
        self._redirects: dict[str, str] = {
            (k or "").lower(): v for k, v in (redirects or {}).items()
        }
        # Post-truncation bytes keyed by content_bytes_ref. Populated by fetch().
        # The bytes_loader reads from here first, then falls back to _pages
        # so tests can also call the loader directly on a registered page.
        self._fetched_bytes: dict[str, bytes] = {}
        self._retrieved_at_factory = (
            (lambda: retrieved_at) if retrieved_at is not None else (lambda: datetime.now(timezone.utc))
        )

    def make_bytes_loader(self) -> Any:
        """Return a callable ``bytes_ref -> bytes`` for use with extractors.

        The extractor Protocol takes a ``bytes_loader`` callable so it
        does not need to know the storage backend.  This helper returns
        one bound to this fetcher's fetched-bytes cache first, falling
        back to the raw page registry (so tests can call the loader
        directly on a registered page without going through fetch()).
        """
        fetched = self._fetched_bytes
        pages = self._pages

        def loader(bytes_ref: str) -> bytes:
            if bytes_ref in fetched:
                return fetched[bytes_ref]
            if bytes_ref.startswith("fake:"):
                url = bytes_ref[len("fake:") :]
                try:
                    return pages[url.lower()]
                except KeyError as exc:
                    raise KeyError(f"no fake page registered for {url!r}") from exc
            raise KeyError(f"unknown bytes_ref: {bytes_ref!r}")

        return loader

    async def fetch(
        self,
        url: str,
        policy: FetchPolicy,
    ) -> FetchedResource:
        # Static policy check (SSRF, scheme, host)
        allowed, reason = is_url_allowed(url, policy)
        if not allowed:
            raise WebFetchDenied(url, reason)

        # Follow registered redirects (bounded by policy.max_redirects)
        redirects_chain: list[str] = [url]
        current = url
        for _ in range(max(0, policy.max_redirects)):
            target = self._redirects.get(current.lower())
            if target is None:
                break
            # Re-check the redirect target with the same policy
            allowed, reason = is_url_allowed(target, policy)
            if not allowed:
                raise WebFetchDenied(target, reason)
            redirects_chain.append(target)
            current = target
        else:
            # Hit the redirect cap — raise to mirror real fetcher behavior
            raise WebFetchError(f"exceeded max_redirects={policy.max_redirects} for {url!r}")

        final_url = current
        page_key = final_url.lower()

        if page_key not in self._pages:
            raise WebFetchError(f"no fake page registered for {final_url!r}")

        body = self._pages[page_key]
        truncated = False
        if len(body) > policy.max_bytes:
            body = body[: policy.max_bytes]
            truncated = True

        # Default status/content-type if not registered
        status_code, content_type, extra_headers = self._statuses.get(
            page_key, (200, "text/html; charset=utf-8", {})
        )

        # Strip sensitive headers (mirrors the real fetcher contract)
        safe_headers: dict[str, str] = {}
        sensitive = {"set-cookie", "authorization", "cookie"}
        for k, v in extra_headers.items():
            if k.lower() in sensitive:
                continue
            safe_headers[k] = v

        content_hash = sha256_hex(body)
        retrieved_at = self._retrieved_at_factory()

        content_bytes_ref = f"fake:{final_url}"
        # Store post-truncation bytes so the bytes_loader returns what
        # was actually fetched (not the original pre-truncation body).
        self._fetched_bytes[content_bytes_ref] = body

        return FetchedResource(
            requested_url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            content_bytes_ref=content_bytes_ref,
            retrieved_at=retrieved_at,
            response_headers=safe_headers,
            content_hash=content_hash,
            truncated=truncated,
            redirects=tuple(redirects_chain),
        )


# ---------------------------------------------------------------------------
# FakeContentExtractor (minimal — used in WS-1 tests; real extractors land in WS-2)
# ---------------------------------------------------------------------------


class FakeContentExtractor:
    """Minimal ``ContentExtractor`` for CI.

    Returns the raw bytes decoded as UTF-8 (with replacement) as the
    extracted text.  Useful for WS-1 tests that need an end-to-end
    fake search → fetch → extract → store flow without depending on
    the WS-2 HTML/PDF extractors.
    """

    def __init__(self, *, extraction_method: str = "fake_utf8") -> None:
        self._method = extraction_method

    @property
    def extraction_method(self) -> str:
        return self._method

    async def extract(
        self,
        resource: FetchedResource,
        *,
        bytes_loader: Any,
    ) -> ExtractedDocument:
        raw = bytes_loader(resource.content_bytes_ref)
        text = raw.decode("utf-8", errors="replace")
        # Strip HTML naively if the content type looks like HTML —
        # this is just enough to keep WS-1 tests honest without
        # pulling in a real HTML parser (which is WS-2's job).
        if "html" in resource.content_type.lower():
            text = _strip_tags_naive(text)
        text_hash = sha256_hex(text)
        return ExtractedDocument(
            source_url=resource.final_url,
            canonical_url=resource.final_url,
            title=_extract_title_naive(text),
            text=text,
            authors=(),
            published_at=None,
            retrieved_at=resource.retrieved_at,
            content_hash=text_hash,
            extraction_method=self._method,
            warnings=() if not resource.truncated else ("truncated at policy.max_bytes",),
            snapshot_artifact_id=None,
        )


# ---------------------------------------------------------------------------
# Tiny HTML helpers (NOT a real extractor — see WS-2 for that)
# ---------------------------------------------------------------------------


def _strip_tags_naive(html: str) -> str:
    """Strip HTML tags with a regex-free state machine.

    This is NOT a real HTML parser.  It exists only so the fake
    extractor can produce reasonable plain text for WS-1 tests; the
    real ``HtmlContentExtractor`` in WS-2 will use a proper parser
    (e.g. ``html.parser`` from stdlib or a third-party readability
    library).
    """
    out: list[str] = []
    in_tag = False
    for ch in html:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    text = "".join(out)
    # Collapse whitespace
    return " ".join(text.split())


def _extract_title_naive(text: str) -> str:
    """Best-effort title extraction: first non-empty line, truncated."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


__all__ = [
    "WebProviderNotConfigured",
    "WebProviderError",
    "WebFetchDenied",
    "WebFetchError",
    "FakeSearchProvider",
    "FakeWebFetcher",
    "FakeContentExtractor",
]
