"""Tavily search provider for Web Source Acquisition (ADR-017 WS-3).

Implements ``SearchProvider`` against the Tavily REST API
(https://docs.tavily.com).  Tavily is purpose-built for LLM grounding
and returns clean markdown + title + URL + score, which maps cleanly
to our ``SearchResult`` schema.

Secret handling:

    The API key is read from the environment variable named in
    ``WebProviderConfig.api_key_env`` (typically ``AIP_WEB_SEARCH_API_KEY``).
    The key is NEVER stored on the provider instance in plaintext form —
    it is read on demand via ``os.environ`` and redacted from all logs,
    exceptions, and ``provider_metadata``.  Tests inject the key via a
    pluggable ``key_loader`` callable so no real env var is needed.

Mapping (Tavily → SearchResult):

    Tavily result field   →   SearchResult field
    ---------------------     ------------------
    (provider name)           provider = "tavily"
    query                     query
    (rank by position)        rank (1-based)
    url                       url
    title                     title
    content                   snippet
    published_date            published_at (parsed to datetime)
    score                     provider_metadata["score"]
    raw_response              provider_metadata["raw_response"]

Rate limiting and errors:

    - Tavily returns 429 on rate limit.  We raise ``WebProviderError``
      with the status code; the caller (the API route) returns 503 to
      the client.
    - Network errors are wrapped in ``WebProviderError``.
    - Malformed JSON responses raise ``WebProviderError``.
    - Empty results (no "results" key or empty list) return ``[]`` —
      this is a valid result, not an error.

This module imports ``httpx`` — adapter layer, allowed by
``tests/test_no_network.py``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Callable

import httpx

from aip.adapter.web.fake_provider import (
    WebProviderError,
    WebProviderNotConfigured,
)
from aip.foundation.schemas.web import (
    SearchOptions,
    SearchResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Tavily API endpoint.  Override via ``WebProviderConfig.options["endpoint"]``.
DEFAULT_TAVILY_ENDPOINT = "https://api.tavily.com"

#: Default request timeout (seconds).  Override via ``options["timeout_seconds"]``.
DEFAULT_TIMEOUT_SECONDS = 20.0

#: Maximum results Tavily will return per call (their hard cap is 20).
MAX_TAVILY_LIMIT = 20


# ---------------------------------------------------------------------------
# Key loader type
# ---------------------------------------------------------------------------

#: A callable that returns the API key string, or "" if not set.
#: Defaults to ``lambda: os.environ.get(env_var, "")``.
KeyLoader = Callable[[], str]


# ---------------------------------------------------------------------------
# TavilySearchProvider
# ---------------------------------------------------------------------------


class TavilySearchProvider:
    """``SearchProvider`` backed by the Tavily REST API.

    Args:
        api_key_env: Environment variable name to read the API key from
            on each ``search`` call.  The key is never cached on the
            instance in plaintext.
        endpoint: Tavily API endpoint (default: ``https://api.tavily.com``).
        timeout_seconds: Per-request timeout.
        key_loader: Pluggable key loader for testing.  When ``None``,
            the provider reads from ``os.environ[api_key_env]`` on each
            call.  Tests inject a fake loader that returns a fixed key
            (or "" to test not-configured behavior).
        client_factory: Optional callable returning an ``httpx.AsyncClient``.
            Tests inject a mock client (typically via ``respx``) so no
            live network is required.
    """

    PROVIDER_NAME = "tavily"

    def __init__(
        self,
        *,
        api_key_env: str = "AIP_WEB_SEARCH_API_KEY",
        endpoint: str = DEFAULT_TAVILY_ENDPOINT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        key_loader: KeyLoader | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._api_key_env = api_key_env
        self._endpoint = endpoint.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._key_loader = key_loader
        self._client_factory = client_factory

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    # ------------------------------------------------------------------
    # Key access (with redaction)
    # ------------------------------------------------------------------

    def _get_api_key(self) -> str:
        """Return the API key, or "" if not configured.

        Raises ``WebProviderNotConfigured`` only when ``search`` is
        actually called without a key — construction never raises so
        the provider can be instantiated eagerly for health checks.
        """
        if self._key_loader is not None:
            return self._key_loader()
        return os.environ.get(self._api_key_env, "")

    def _require_api_key(self) -> str:
        """Return the API key or raise ``WebProviderNotConfigured``."""
        key = self._get_api_key()
        if not key:
            raise WebProviderNotConfigured(
                f"Tavily API key not set: environment variable {self._api_key_env!r} is empty. "
                f"Set it to enable web search."
            )
        return key

    # ------------------------------------------------------------------
    # SearchProvider Protocol
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        options: SearchOptions | None = None,
    ) -> list[SearchResult]:
        """Run a Tavily search and return ranked results."""
        opts = options or SearchOptions()
        key = self._require_api_key()

        # Build the request payload per Tavily API docs.
        payload: dict[str, Any] = {
            "api_key": key,
            "query": query,
            "max_results": min(max(1, opts.limit), MAX_TAVILY_LIMIT),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        if opts.topic:
            payload["topic"] = opts.topic  # "general" or "news"
        if opts.freshness_days is not None:
            # Tavily expects days as an integer under "days" (only valid with topic=news).
            payload["days"] = opts.freshness_days
        if opts.domains:
            # Tavily accepts "include_domains" as a list.
            payload["include_domains"] = list(opts.domains)

        # Execute the request.
        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self._timeout_seconds),
        }
        client: httpx.AsyncClient
        if self._client_factory is not None:
            client = self._client_factory()
        else:
            client = httpx.AsyncClient(**client_kwargs)

        try:
            try:
                response = await client.post(
                    f"{self._endpoint}/search",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            except httpx.TimeoutException as exc:
                raise WebProviderError(
                    f"Tavily request timed out after {self._timeout_seconds}s: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                raise WebProviderError(
                    f"Tavily HTTP error: {exc}"
                ) from exc

            # Handle HTTP error status codes.
            if response.status_code == 429:
                raise WebProviderError("Tavily rate limit exceeded (HTTP 429)")
            if response.status_code == 401:
                raise WebProviderNotConfigured(
                    "Tavily API key rejected (HTTP 401) — check AIP_WEB_SEARCH_API_KEY"
                )
            if response.status_code >= 400:
                # Redact the key from any error body before raising.
                body_preview = _redact_key_from_text(response.text[:500])
                raise WebProviderError(
                    f"Tavily returned HTTP {response.status_code}: {body_preview}"
                )

            # Parse JSON.
            try:
                data = response.json()
            except ValueError as exc:
                raise WebProviderError(
                    f"Tavily returned non-JSON response: {exc}"
                ) from exc

            # Map results.
            raw_results = data.get("results", [])
            if not isinstance(raw_results, list):
                raise WebProviderError(
                    f"Tavily 'results' field is not a list: {type(raw_results).__name__}"
                )

            search_results: list[SearchResult] = []
            for index, item in enumerate(raw_results, start=1):
                if not isinstance(item, dict):
                    logger.warning("tavily_result_not_dict index=%s", index)
                    continue
                url = str(item.get("url", "")).strip()
                if not url:
                    continue
                title = str(item.get("title", "")).strip()
                snippet = str(item.get("content", "")).strip()
                published_at = _parse_tavily_date(item.get("published_date"))
                score = item.get("score")
                provider_metadata: dict[str, Any] = {}
                if score is not None:
                    provider_metadata["score"] = score
                # Preserve the raw item under "raw_response" for debugging,
                # but the API boundary will redact sensitive subkeys.
                provider_metadata["raw_response"] = {
                    k: v for k, v in item.items()
                    if k not in ("url", "title", "content", "published_date", "score")
                }

                search_results.append(
                    SearchResult(
                        provider=self.PROVIDER_NAME,
                        query=query,
                        rank=index,
                        url=url,
                        title=title,
                        snippet=snippet,
                        published_at=published_at,
                        provider_metadata=provider_metadata,
                    )
                )

            return search_results
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_tavily_date(value: Any) -> datetime | None:
    """Parse a Tavily ``published_date`` value into a ``datetime``.

    Tavily returns ISO 8601 strings (e.g. "2024-03-15T10:30:00Z") for
    news searches, or ``None`` for general searches.  Returns ``None``
    for any unparseable value.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _redact_key_from_text(text: str) -> str:
    """Best-effort redaction of an API key from an error response body.

    Replaces any occurrence of a Tavily-style key prefix (``tvly-``)
    followed by alphanumeric characters with ``tvly-[redacted]``.
    Also redacts any ``"api_key": "..."`` JSON-style occurrences.
    """
    import re
    # Redact tvly-* keys
    redacted = re.sub(r"tvly-[A-Za-z0-9]+", "tvly-[redacted]", text)
    # Redact "api_key":"..." patterns
    redacted = re.sub(
        r'("api_key"\s*:\s*")[^"]+(")',
        r'\1[redacted]\2',
        redacted,
    )
    return redacted


__all__ = [
    "TavilySearchProvider",
    "DEFAULT_TAVILY_ENDPOINT",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TAVILY_LIMIT",
]
