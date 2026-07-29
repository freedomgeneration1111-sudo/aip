"""Web Source Acquisition schemas (ADR-017).

Frozen value objects for search, fetch, extraction, provenance, and
snapshot recording.  These types are consumed by the Protocols in
``aip.foundation.protocols.web`` and by the adapter-layer web service.

Design rules (per ADR-017):

- All remote content is **untrusted data**.  These schemas carry
  provenance fields (URL, retrieved_at, content_hash, extraction_method,
  warnings) but they do not encode, execute, or interpret the content.
- ``content_hash`` is SHA-256 of the canonical byte stream
  (``FetchedResource``) or the normalized extracted text
  (``ExtractedDocument``).  Two resources with the same hash are
  considered duplicates for storage and promotion.
- ``provider_metadata`` carries provider-specific fields that core code
  must NOT depend on.  It is redacted at the API boundary.
- Frozen dataclasses are used so that provenance records are immutable
  once written; this is the audit-trail contract.

This module imports **only** stdlib (``dataclasses``, ``datetime``,
``hashlib``, ``typing``).  It must not import any network library —
``tests/test_no_network.py`` enforces this for the foundation layer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchOptions:
    """Options passed to ``SearchProvider.search``.

    Attributes:
        limit: Maximum number of results to return (provider may return fewer).
        freshness_days: If set, restrict to results published within the
            last N days.  ``None`` means no freshness filter.
        domains: If set, restrict to results from these domains
            (provider-side domain allowlist, e.g. ``["arxiv.org"]``).
        topic: Provider-specific topic hint, e.g. ``"general"`` or
            ``"news"``.  ``None`` lets the provider pick its default.
    """

    limit: int = 8
    freshness_days: int | None = None
    domains: tuple[str, ...] | None = None
    topic: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """A single search hit returned by a ``SearchProvider``.

    Attributes:
        provider: Name of the provider that produced this result
            (e.g. ``"tavily"``, ``"brave"``, ``"fake"``).
        query: The query that produced this result.  Carried for
            provenance so a downstream consumer can correlate without
            threading the query separately.
        rank: 1-based rank within the provider's result list.
        url: Canonical URL of the result.  Must be a valid ``http`` or
            ``https`` URL; the fetcher is responsible for resolving
            redirects and recording the ``final_url``.
        title: Title as reported by the provider.  May be empty.
        snippet: Short text snippet as reported by the provider.
            Snippets are discovery metadata, not authoritative evidence.
        published_at: Publication timestamp if known; ``None`` if the
            provider did not report one.
        provider_metadata: Provider-specific fields (score, raw JSON,
            etc.).  Core code must not depend on keys in this dict.
            The API boundary redacts sensitive values.
    """

    provider: str
    query: str
    rank: int
    url: str
    title: str
    snippet: str
    published_at: datetime | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchPolicy:
    """Policy governing a single ``WebFetcher.fetch`` call.

    The fetcher MUST enforce every field.  Violations are denied with
    a structured error, not silently relaxed.

    Attributes:
        allowed_schemes: URL schemes the fetcher will follow.
            Default: ``("http", "https")``.  ``file`` and ``ftp`` are
            forbidden by default.
        max_redirects: Maximum number of HTTP redirects to follow.
            Each redirect hop is re-checked against ``allowed_schemes``
            and the SSRF guard.
        timeout_seconds: Per-request timeout, including redirects.
        max_bytes: Maximum response body size.  The fetcher streams
            and truncates at this limit; the resulting
            ``FetchedResource`` carries a ``truncated`` warning.
        allowed_content_types: If set, only these content types are
            accepted.  ``None`` means accept any content type (the
            extractor handles unsupported types with a warning).
        allow_private_networks: If ``False`` (the default and the only
            safe production value), URLs whose host resolves to a
            private/loopback/link-local/multicast address are denied.
            Setting this to ``True`` is recorded in the fetch trace
            and is intended only for local-fixture test modes.
        max_response_header_bytes: Cap on the size of the response
            headers, as a defense against header-flooding attacks.
    """

    allowed_schemes: tuple[str, ...] = ("http", "https")
    max_redirects: int = 5
    timeout_seconds: float = 20.0
    max_bytes: int = 20_000_000
    allowed_content_types: tuple[str, ...] | None = None
    allow_private_networks: bool = False
    max_response_header_bytes: int = 64_000


@dataclass(frozen=True)
class FetchedResource:
    """The raw result of fetching a URL.

    Attributes:
        requested_url: The URL passed to ``fetch``.
        final_url: The URL after all redirects.  Equal to
            ``requested_url`` if no redirects occurred.
        status_code: HTTP status code of the final response.
        content_type: Content-Type header value (may include
            parameters, e.g. ``"text/html; charset=utf-8"``).
        content_bytes_ref: Reference to the stored bytes.  This is an
            opaque string (artifact id, file path, or memory key) —
            not the bytes themselves — so the dataclass stays small
            and serializable.
        retrieved_at: UTC timestamp of the fetch.
        response_headers: Selected response headers preserved for
            provenance (e.g. ``ETag``, ``Last-Modified``, ``Server``).
            Sensitive headers (``Set-Cookie``, ``Authorization``) are
            stripped by the fetcher.
        content_hash: SHA-256 of the raw response bytes (hex).
        truncated: ``True`` if the response was cut off at
            ``FetchPolicy.max_bytes``.
        redirects: Ordered list of URLs visited, including the
            requested URL and ending with the final URL.  Empty list
            means the fetcher did not record the chain (treat as
            ``[requested_url, final_url]``).
    """

    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    content_bytes_ref: str
    retrieved_at: datetime
    response_headers: dict[str, str] = field(default_factory=dict)
    content_hash: str = ""
    truncated: bool = False
    redirects: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedDocument:
    """The extracted text content of a ``FetchedResource``.

    Attributes:
        source_url: The ``final_url`` of the resource this document
            was extracted from.
        canonical_url: Canonical URL if the page declared one (via
            ``<link rel="canonical">`` or equivalent).  ``None`` if
            not declared; falls back to ``source_url``.
        title: Extracted title.  May differ from the search-result
            title if the page's ``<title>`` or ``<h1>`` disagrees.
        text: Extracted main-content text.  Plain text, no HTML.
            This is the untrusted-data payload that gets enclosed in
            source-block markers by the augmented-context builder.
        authors: Authors extracted from page metadata.  May be empty.
        published_at: Publication timestamp extracted from page
            metadata.  ``None`` if not found.
        retrieved_at: UTC timestamp of the fetch (copied from the
            ``FetchedResource`` for standalone provenance).
        content_hash: SHA-256 of the normalized extracted text (hex).
            Distinct from ``FetchedResource.content_hash`` (which is
            the hash of the raw bytes) so that re-extraction with a
            better extractor produces a different hash even if the
            underlying bytes are unchanged.
        extraction_method: Identifier of the extractor used, e.g.
            ``"html_readability"``, ``"pdf_handoff"``,
            ``"plain_text"``.
        warnings: Non-fatal issues encountered during extraction:
            paywall hints, login walls, truncation, encoding issues,
            missing fields.  These are surfaced to the user via the
            answer status strip.
        snapshot_artifact_id: If snapshotting is enabled, the artifact
            id of the stored raw bytes.  ``None`` if not snapshotted.
    """

    source_url: str
    canonical_url: str | None
    title: str
    text: str
    authors: tuple[str, ...] = field(default_factory=tuple)
    published_at: datetime | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""
    extraction_method: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    snapshot_artifact_id: str | None = None


# ---------------------------------------------------------------------------
# Provenance + snapshot records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebSourceRecord:
    """Composite provenance record: fetch + extract + snapshot refs.

    This is the canonical object stored in the ``WebSourceStore`` and
    surfaced via ``/api/v1/web/sources/{source_id}``.  It is the
    unit of ephemeral grounding and the input to explicit corpus
    promotion (WS-5).

    Attributes:
        source_id: Stable identifier (typically a UUID or a hash of
            ``content_hash + source_url``).  Used as the path
            parameter in the sources API.
        search_result: The ``SearchResult`` that led to this fetch, if
            any.  ``None`` for direct URL fetches (e.g. from messaging
            ingress URL handoff).
        fetched: The raw ``FetchedResource``.
        extracted: The extracted ``ExtractedDocument``.  May be ``None``
            if extraction failed; in that case ``fetch_warnings``
            carries the reason.
        provider: Provider name (mirrors ``search_result.provider`` if
            present, else ``"direct"``).
        retrieved_at: UTC timestamp of the fetch (mirrors
            ``fetched.retrieved_at`` for convenience).
        content_hash: SHA-256 of the extracted text (mirrors
            ``extracted.content_hash``).  Used for deduplication.
        fetch_warnings: Warnings from the fetch step (distinct from
            extraction warnings) — e.g. SSL verification skipped,
            truncation, redirect-loop recovery.
    """

    source_id: str
    search_result: SearchResult | None
    fetched: FetchedResource
    extracted: ExtractedDocument | None
    provider: str
    retrieved_at: datetime
    content_hash: str
    fetch_warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class WebSnapshotRecord:
    """A stored snapshot of a fetched resource's raw bytes.

    Snapshots are stored separately from the ``WebSourceRecord`` so
    that the record can be serialized cheaply and the bytes can be
    garbage-collected on a retention policy independent of the
    provenance record.

    Attributes:
        snapshot_id: Stable identifier (typically a UUID).
        requested_url: URL passed to the fetcher.
        final_url: URL after redirects.
        retrieved_at: UTC timestamp of the fetch.
        content_type: Content-Type of the response.
        content_hash: SHA-256 of the stored bytes (mirrors
            ``FetchedResource.content_hash``).
        bytes_ref: Reference to the stored bytes (artifact id, file
            path, or memory key).
        bytes_size: Size of the stored bytes in bytes.
    """

    snapshot_id: str
    requested_url: str
    final_url: str
    retrieved_at: datetime
    content_type: str
    content_hash: str
    bytes_ref: str
    bytes_size: int


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebProviderConfig:
    """Configuration for a single search provider adapter.

    Attributes:
        name: Provider identifier (e.g. ``"tavily"``, ``"brave"``,
            ``"fake"``).  Used as the key in ``[web.providers.<name>]``.
        api_key_env: Environment variable name from which the API key
            is read.  The key itself is NEVER stored in this config or
            in any record; it is read on demand and redacted from logs.
        options: Provider-specific options (e.g. ``topic``,
            ``include_raw_content``).  Core code must not depend on
            keys in this dict.
        is_fake: ``True`` for the fake provider used in CI.  When
            ``True``, ``api_key_env`` is ignored.
    """

    name: str
    api_key_env: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    is_fake: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_hex(data: bytes | str) -> str:
    """SHA-256 hex digest of the input.

    Accepts ``bytes`` or ``str`` (UTF-8 encoded).  Provided as a
    canonical hashing helper so that all ``content_hash`` values are
    computed consistently.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def normalize_text_for_hash(text: str) -> str:
    """Normalize text before hashing for deduplication.

    Strips trailing whitespace per line, collapses blank lines, and
    lowercases.  This makes re-extraction with cosmetic differences
    (whitespace, case folding) produce the same hash, while still
    distinguishing genuinely different content.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank
    return "\n".join(collapsed).strip().lower()


__all__ = [
    "SearchOptions",
    "SearchResult",
    "FetchPolicy",
    "FetchedResource",
    "ExtractedDocument",
    "WebSourceRecord",
    "WebSnapshotRecord",
    "WebProviderConfig",
    "sha256_hex",
    "normalize_text_for_hash",
]
