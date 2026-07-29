"""Web Source Acquisition protocols (ADR-017).

Defines the contracts for the four core web-capability roles:

- ``SearchProvider``      — discovery (query → result metadata)
- ``WebFetcher``          — bounded HTTP fetch (URL → raw bytes)
- ``ContentExtractor``    — text extraction (raw bytes → text)
- ``WebSnapshotStore``    — snapshot persistence (raw bytes → artifact ref)
- ``WebSourceStore``      — provenance record persistence

These Protocols live in the foundation layer so that adapter-layer
implementations (``aip.adapter.web.http_fetcher.HttpxWebFetcher``,
``aip.adapter.web.providers.tavily.TavilySearchProvider``, etc.) can
be wired through the DI container without routes importing concrete
classes.

The Protocols are ``runtime_checkable`` so that tests can assert a
fake satisfies the contract via ``isinstance``.

Layering note: this module imports only ``typing`` and the schema
module.  It must not import any network library —
``tests/test_no_network.py`` enforces this for the foundation layer.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    FetchPolicy,
    SearchOptions,
    SearchResult,
    WebSnapshotRecord,
    WebSourceRecord,
)

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@runtime_checkable
class SearchProvider(Protocol):
    """Discovery provider: query → ranked result metadata.

    Implementations:
        - ``aip.adapter.web.fake_provider.FakeSearchProvider`` (CI)
        - ``aip.adapter.web.providers.tavily.TavilySearchProvider`` (WS-3)
        - ``aip.adapter.web.providers.brave.BraveSearchProvider`` (post-WS-3)

    Contract:
        - ``search`` MUST NOT raise on empty results; return ``[]``.
        - ``search`` MUST raise ``WebProviderNotConfigured`` if the
          provider's API key is missing and ``is_fake`` is False.
        - ``search`` MUST NOT fetch result bodies — that is the
          ``WebFetcher``'s job.  Returning snippets only.
        - ``search`` MUST NOT write to any corpus.  Ephemeral only.
        - ``provider_metadata`` MAY contain provider-specific fields;
          core code must not depend on them.
    """

    @property
    def name(self) -> str:
        """Provider identifier (e.g. ``"tavily"``, ``"fake"``)."""
        ...

    async def search(
        self,
        query: str,
        *,
        options: SearchOptions | None = None,
    ) -> list[SearchResult]:
        """Run a search and return ranked results.

        Args:
            query: Search query string.
            options: Optional ``SearchOptions`` (limit, freshness,
                domains, topic).  If ``None``, provider defaults apply.

        Returns:
            List of ``SearchResult`` ranked 1..N.  Empty list is a
            valid result (no hits), not an error.

        Raises:
            WebProviderNotConfigured: API key missing and provider is
                not the fake.
            WebProviderError: Provider returned an error response.
        """
        ...


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


@runtime_checkable
class WebFetcher(Protocol):
    """Bounded HTTP fetcher: URL → raw bytes + provenance.

    Implementations:
        - ``aip.adapter.web.fake_provider.FakeWebFetcher`` (CI; reads
          local fixtures, no network)
        - ``aip.adapter.web.http_fetcher.HttpxWebFetcher`` (WS-2)

    Contract:
        - ``fetch`` MUST enforce every field of ``FetchPolicy``.
        - ``fetch`` MUST deny SSRF targets (private/loopback/link-local
          IPs) at every redirect hop when
          ``policy.allow_private_networks`` is False.
        - ``fetch`` MUST stream the response body and truncate at
          ``policy.max_bytes``, setting ``truncated=True`` on the
          returned ``FetchedResource``.
        - ``fetch`` MUST strip sensitive response headers
          (``Set-Cookie``, ``Authorization``, ``Cookie``) before
          building ``response_headers``.
        - ``fetch`` MUST compute ``content_hash`` as SHA-256 of the
          raw response bytes (hex).
        - ``fetch`` MUST register with the app task registry so that
          shutdown can cancel in-flight fetches (W5 lifecycle contract).
    """

    async def fetch(
        self,
        url: str,
        policy: FetchPolicy,
    ) -> FetchedResource:
        """Fetch a URL and return the raw resource.

        Args:
            url: URL to fetch.  Must use a scheme in
                ``policy.allowed_schemes``.
            policy: Fetch policy governing redirects, timeout, size,
                SSRF, content types.

        Returns:
            ``FetchedResource`` with raw bytes reference and provenance.

        Raises:
            WebFetchDenied: URL denied by policy (SSRF, scheme, content
                type, size).
            WebFetchError: Network or HTTP error (timeout, DNS failure,
                5xx after retries).
        """
        ...


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------


@runtime_checkable
class ContentExtractor(Protocol):
    """Text extractor: raw bytes → extracted text + metadata.

    Implementations:
        - ``aip.adapter.web.extractors.html.HtmlContentExtractor`` (WS-2)
        - ``aip.adapter.web.extractors.pdf.PdfContentExtractor`` (WS-2)
        - ``aip.adapter.web.extractors.plain_text.PlainTextExtractor`` (WS-2)

    Contract:
        - ``extract`` MUST NOT raise on unsupported content types;
          return an ``ExtractedDocument`` with empty ``text`` and a
          ``warnings`` entry explaining the issue.
        - ``extract`` MUST treat the raw bytes as untrusted data.
          Instructions inside the bytes MUST NOT affect extractor
          state or policy.
        - ``extract`` MUST populate ``warnings`` for: paywall hints,
          login walls, truncation, encoding issues, missing title,
          extraction-fallback-to-raw-text.
        - ``extract`` MUST compute ``content_hash`` as SHA-256 of the
          normalized extracted text (hex), distinct from the raw-bytes
          hash on ``FetchedResource``.
    """

    @property
    def extraction_method(self) -> str:
        """Identifier of this extractor (e.g. ``"html_readability"``)."""
        ...

    async def extract(
        self,
        resource: FetchedResource,
        *,
        bytes_loader: Any,
    ) -> ExtractedDocument:
        """Extract text and metadata from a fetched resource.

        Args:
            resource: The ``FetchedResource`` to extract from.
            bytes_loader: A callable returning the raw bytes for
                ``resource.content_bytes_ref``.  Passed as a callable
                so the extractor does not need to know the storage
                backend (artifact store, file system, in-memory).

        Returns:
            ``ExtractedDocument`` with extracted text and provenance.
        """
        ...


# ---------------------------------------------------------------------------
# Snapshot store
# ---------------------------------------------------------------------------


@runtime_checkable
class WebSnapshotStore(Protocol):
    """Persistence for raw fetched bytes (snapshots).

    Implementations:
        - ``aip.adapter.web.snapshot.InMemoryWebSnapshotStore`` (WS-1, CI)
        - ``aip.adapter.web.snapshot.SqliteWebSnapshotStore`` (WS-2)

    Contract:
        - ``put`` MUST deduplicate by ``content_hash``.  A second
          ``put`` with an existing hash returns the existing
          ``snapshot_id`` and ``deduplicated=True``.
        - ``get`` MUST return ``None`` for unknown ``snapshot_id``.
        - ``get_by_hash`` MUST return ``None`` for unknown
          ``content_hash``.
        - ``delete_expired`` MUST remove snapshots older than the
          given cutoff and return the count deleted.
        - The store MUST NOT interpret the bytes; it is a pure blob
          store with provenance metadata.
    """

    async def put(
        self,
        *,
        requested_url: str,
        final_url: str,
        retrieved_at: Any,
        content_type: str,
        content_hash: str,
        bytes_data: bytes,
    ) -> tuple[str, bool]:
        """Store a snapshot.

        Returns:
            Tuple of ``(snapshot_id, deduplicated)``.  ``deduplicated``
            is ``True`` if a snapshot with the same ``content_hash``
            already existed and no new bytes were stored.
        """
        ...

    async def get(self, snapshot_id: str) -> WebSnapshotRecord | None:
        """Retrieve a snapshot record by id (without bytes)."""
        ...

    async def get_bytes(self, snapshot_id: str) -> bytes | None:
        """Retrieve the raw bytes for a snapshot, or ``None``."""
        ...

    async def get_by_hash(self, content_hash: str) -> WebSnapshotRecord | None:
        """Retrieve a snapshot record by content hash."""
        ...

    async def delete_expired(self, cutoff: Any) -> int:
        """Delete snapshots older than ``cutoff`` (datetime).

        Returns the count of deleted snapshots.
        """
        ...


# ---------------------------------------------------------------------------
# Source store (provenance records)
# ---------------------------------------------------------------------------


@runtime_checkable
class WebSourceStore(Protocol):
    """Persistence for ``WebSourceRecord`` (provenance records).

    Implementations:
        - ``aip.adapter.web.snapshot.InMemoryWebSourceStore`` (WS-1, CI)
        - ``aip.adapter.web.snapshot.SqliteWebSourceStore`` (WS-3)

    Contract:
        - ``put`` MUST deduplicate by ``content_hash``.  A second
          ``put`` with an existing hash returns the existing
          ``source_id``.
        - ``get`` MUST return ``None`` for unknown ``source_id``.
        - ``list_by_query`` returns source records produced by a given
          search query (for the source-panel UI), most-recent first.
        - The store MUST NOT fetch URLs or extract text; it stores
          already-built records only.
    """

    async def put(self, record: WebSourceRecord) -> str:
        """Store a source record. Returns the source_id."""
        ...

    async def get(self, source_id: str) -> WebSourceRecord | None:
        """Retrieve a source record by id."""
        ...

    async def get_by_hash(self, content_hash: str) -> WebSourceRecord | None:
        """Retrieve a source record by content hash (for dedup checks)."""
        ...

    async def list_by_query(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[WebSourceRecord]:
        """List source records produced by a given search query."""
        ...

    async def delete(self, source_id: str) -> bool:
        """Delete a source record. Returns True if a record was deleted."""
        ...


__all__ = [
    "SearchProvider",
    "WebFetcher",
    "ContentExtractor",
    "WebSnapshotStore",
    "WebSourceStore",
]
