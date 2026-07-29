"""Plain-text content extractor for Web Source Acquisition (ADR-017 WS-2).

For ``text/plain`` and unknown text content types.  Decodes the raw
bytes as UTF-8 (with replacement) and returns the text as-is, with
minimal cleanup (trailing-whitespace strip per line, blank-line collapse).

Extraction method identifier: ``"plain_text"``.
"""

from __future__ import annotations

import re
from typing import Any

from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    sha256_hex,
)


class PlainTextExtractor:
    """Extract text from plain-text responses.

    Stateless and safe to reuse across fetches.
    """

    EXTRACTION_METHOD = "plain_text"

    @property
    def extraction_method(self) -> str:
        return self.EXTRACTION_METHOD

    async def extract(
        self,
        resource: FetchedResource,
        *,
        bytes_loader: Any,
    ) -> ExtractedDocument:
        """Extract text from a plain-text ``FetchedResource``."""
        raw = bytes_loader(resource.content_bytes_ref)
        warnings: list[str] = []

        text = raw.decode("utf-8", errors="replace")
        # Strip trailing whitespace per line
        lines = [line.rstrip() for line in text.splitlines()]
        text = "\n".join(lines)
        # Collapse 3+ blank lines to 2
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if not text:
            warnings.append("plain-text response is empty")

        if resource.truncated:
            warnings.append("source body was truncated at policy.max_bytes; text may be incomplete")

        content_hash = sha256_hex(text)

        return ExtractedDocument(
            source_url=resource.final_url,
            canonical_url=resource.final_url,
            title=_first_line(title_source=text),
            text=text,
            authors=(),
            published_at=None,
            retrieved_at=resource.retrieved_at,
            content_hash=content_hash,
            extraction_method=self.EXTRACTION_METHOD,
            warnings=tuple(warnings),
            snapshot_artifact_id=None,
        )


def _first_line(title_source: str) -> str:
    """Use the first non-empty line as a pseudo-title, truncated to 200 chars."""
    for line in title_source.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


__all__ = ["PlainTextExtractor"]
