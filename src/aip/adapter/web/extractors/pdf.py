"""PDF content extractor for Web Source Acquisition (ADR-017 WS-2).

Hands off to ``pypdf`` (already a project dependency per DEBT-012) to
extract text from PDF bytes.  Writes the fetched bytes to a temporary
file because ``pypdf.PdfReader`` accepts a file path, not raw bytes
(this is a pypdf API limitation).

The extractor does NOT do any layout-aware parsing, table extraction,
or OCR — it relies on pypdf's ``page.extract_text()`` which is
sufficient for corpus grounding.  If a page produces no extractable
text (e.g. scanned PDF without OCR), a warning is recorded.

Extraction method identifier: ``"pdf_handoff"``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    sha256_hex,
)

logger = logging.getLogger(__name__)


class PdfContentExtractor:
    """Extract text from PDF bytes via pypdf.

    Stateless and safe to reuse across fetches.
    """

    EXTRACTION_METHOD = "pdf_handoff"

    @property
    def extraction_method(self) -> str:
        return self.EXTRACTION_METHOD

    async def extract(
        self,
        resource: FetchedResource,
        *,
        bytes_loader: Any,
    ) -> ExtractedDocument:
        """Extract text and metadata from a PDF ``FetchedResource``."""
        raw = bytes_loader(resource.content_bytes_ref)
        warnings: list[str] = []

        # ---- Truncation warning (added early so it survives parse failures) ----
        if resource.truncated:
            warnings.append("source body was truncated at policy.max_bytes; PDF may be incomplete")

        # ---- Write to a temp file for pypdf ----
        # pypdf.PdfReader accepts a file path or a file-like object.
        # We use a temp file to avoid loading the entire PDF into memory
        # twice (the bytes are already in memory from the fetch).
        fd, temp_path = tempfile.mkstemp(suffix=".pdf", prefix="web_pdf_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)

            try:
                from pypdf import PdfReader
            except ImportError:
                warnings.append("pypdf not installed; PDF extraction unavailable")
                return ExtractedDocument(
                    source_url=resource.final_url,
                    canonical_url=resource.final_url,
                    title="",
                    text="",
                    retrieved_at=resource.retrieved_at,
                    content_hash=sha256_hex(""),
                    extraction_method=self.EXTRACTION_METHOD,
                    warnings=tuple(warnings),
                    snapshot_artifact_id=None,
                )

            try:
                reader = PdfReader(temp_path)
            except Exception as exc:
                logger.warning("pdf_open_failed", url=resource.final_url, error=str(exc))
                warnings.append(f"failed to open PDF: {exc}")
                return ExtractedDocument(
                    source_url=resource.final_url,
                    canonical_url=resource.final_url,
                    title="",
                    text="",
                    retrieved_at=resource.retrieved_at,
                    content_hash=sha256_hex(""),
                    extraction_method=self.EXTRACTION_METHOD,
                    warnings=tuple(warnings),
                    snapshot_artifact_id=None,
                )

            # ---- Extract title from metadata ----
            title = ""
            try:
                meta = reader.metadata
                if meta and meta.title:
                    title = str(meta.title).strip()[:500]
            except Exception:
                pass  # metadata extraction is best-effort

            # ---- Extract text from each page ----
            page_texts: list[str] = []
            empty_pages = 0
            for page_num, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text() or ""
                except Exception as exc:
                    warnings.append(f"page {page_num + 1} extraction error: {exc}")
                    page_text = ""
                if not page_text.strip():
                    empty_pages += 1
                page_texts.append(page_text)

            text = "\n\n".join(pt for pt in page_texts if pt.strip())
            text = text.strip()

            if not text:
                warnings.append("PDF contains no extractable text (possibly scanned without OCR)")
            if empty_pages > 0:
                warnings.append(f"{empty_pages} page(s) produced no extractable text")

            content_hash = sha256_hex(text)

            return ExtractedDocument(
                source_url=resource.final_url,
                canonical_url=resource.final_url,
                title=title,
                text=text,
                authors=(),
                published_at=None,
                retrieved_at=resource.retrieved_at,
                content_hash=content_hash,
                extraction_method=self.EXTRACTION_METHOD,
                warnings=tuple(warnings),
                snapshot_artifact_id=None,
            )
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


__all__ = ["PdfContentExtractor"]
