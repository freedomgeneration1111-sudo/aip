"""Provenance builder for Web Source Acquisition (ADR-017 WS-2).

Assembles ``WebSourceRecord`` objects from a ``SearchResult`` +
``FetchedResource`` + ``ExtractedDocument`` triple.  This is the
canonical provenance record stored in ``WebSourceStore`` and surfaced
via ``/api/v1/web/sources/{source_id}``.

The builder:

    - Generates a stable ``source_id`` from the content hash + source URL
      (so re-fetching the same page produces the same id).
    - Computes the composite ``content_hash`` from the extracted text
      (falls back to the raw-bytes hash if extraction failed).
    - Carries fetch warnings (SSL, truncation, redirect-loop recovery)
      separately from extraction warnings (paywall, encoding, empty text).
    - Redacts ``provider_metadata`` before returning — secrets in
      provider-specific fields must never leak into the record.

This module is stdlib-only (no network imports).  It lives in the
adapter layer because it imports foundation schemas and protocols,
but it does not perform any I/O.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    SearchResult,
    WebSourceRecord,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider-metadata redaction
# ---------------------------------------------------------------------------

#: Keys in ``provider_metadata`` that are redacted from the stored record.
#: Matched case-insensitively against the EXACT key name (not substrings)
#: so that legitimate keys like "safe_key" or "nested_key" are preserved.
_REDACTED_METADATA_KEYS = frozenset({
    "api_key",
    "key",
    "token",
    "secret",
    "authorization",
    "password",
    "credential",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "api_key_id",
})

#: Value used in place of redacted fields.
_REDACTED_PLACEHOLDER = "[redacted]"


def redact_provider_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``metadata`` with sensitive keys redacted.

    Redaction is based on EXACT key-name matching (case-insensitive)
    against a known set of sensitive names.  Substring matching is NOT
    used, so legitimate keys like ``"safe_key"`` or ``"score_key"``
    are preserved.  Nested dicts are redacted recursively.
    """
    redacted: dict[str, Any] = {}
    for key, value in metadata.items():
        key_lower = key.lower()
        if key_lower in _REDACTED_METADATA_KEYS:
            redacted[key] = _REDACTED_PLACEHOLDER
        elif isinstance(value, dict):
            redacted[key] = redact_provider_metadata(value)
        else:
            redacted[key] = value
    return redacted


# ---------------------------------------------------------------------------
# Source-id generation
# ---------------------------------------------------------------------------


def make_source_id(source_url: str, content_hash: str) -> str:
    """Generate a stable source ID from URL + content hash.

    The ID is a truncated SHA-256 of ``source_url + content_hash``,
    prefixed with ``"src_"``.  Re-fetching the same page produces the
    same ID, which supports idempotent deduplication in the store.
    """
    digest = hashlib.sha256(f"{source_url}|{content_hash}".encode("utf-8")).hexdigest()
    return f"src_{digest[:24]}"


# ---------------------------------------------------------------------------
# WebSourceRecord builder
# ---------------------------------------------------------------------------


def build_web_source_record(
    *,
    search_result: SearchResult | None,
    fetched: FetchedResource,
    extracted: ExtractedDocument | None,
    fetch_warnings: tuple[str, ...] = (),
) -> WebSourceRecord:
    """Assemble a ``WebSourceRecord`` from its components.

    Args:
        search_result: The search result that led to this fetch, or
            ``None`` for a direct URL fetch (e.g. messaging ingress).
        fetched: The raw ``FetchedResource`` from the fetcher.
        extracted: The extracted ``ExtractedDocument``, or ``None`` if
            extraction failed.
        fetch_warnings: Warnings from the fetch step (SSL, truncation,
            redirect-loop recovery).  Distinct from extraction warnings
            which are carried on ``ExtractedDocument.warnings``.

    Returns:
        A ``WebSourceRecord`` with a stable ``source_id``, redacted
        ``provider_metadata`` (if any), and composite ``content_hash``.
    """
    # Determine the effective content hash
    if extracted is not None and extracted.content_hash:
        content_hash = extracted.content_hash
    else:
        content_hash = fetched.content_hash

    source_url = fetched.final_url
    source_id = make_source_id(source_url, content_hash)

    # Determine the provider name
    if search_result is not None:
        provider = search_result.provider
    else:
        provider = "direct"

    # Redact provider metadata
    if search_result is not None and search_result.provider_metadata:
        redacted_metadata = redact_provider_metadata(search_result.provider_metadata)
    else:
        redacted_metadata = {}

    # Rebuild the search result with redacted metadata (frozen dataclass → replace)
    redacted_search_result: SearchResult | None = None
    if search_result is not None:
        redacted_search_result = SearchResult(
            provider=search_result.provider,
            query=search_result.query,
            rank=search_result.rank,
            url=search_result.url,
            title=search_result.title,
            snippet=search_result.snippet,
            published_at=search_result.published_at,
            provider_metadata=redacted_metadata,
        )

    return WebSourceRecord(
        source_id=source_id,
        search_result=redacted_search_result,
        fetched=fetched,
        extracted=extracted,
        provider=provider,
        retrieved_at=fetched.retrieved_at,
        content_hash=content_hash,
        fetch_warnings=fetch_warnings,
    )


__all__ = [
    "build_web_source_record",
    "make_source_id",
    "redact_provider_metadata",
]
