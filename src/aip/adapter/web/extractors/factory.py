"""Extractor factory for Web Source Acquisition (ADR-017 WS-2).

Selects the appropriate ``ContentExtractor`` based on the response's
Content-Type.  Unknown content types fall back to ``PlainTextExtractor``
with a warning, so the pipeline never hard-fails on an unfamiliar type.
"""

from __future__ import annotations

from aip.adapter.web.extractors.html import HtmlContentExtractor
from aip.adapter.web.extractors.pdf import PdfContentExtractor
from aip.adapter.web.extractors.plain_text import PlainTextExtractor
from aip.foundation.protocols.web import ContentExtractor

# Singleton instances — extractors are stateless and safe to reuse.
_HTML = HtmlContentExtractor()
_PDF = PdfContentExtractor()
_PLAIN = PlainTextExtractor()


def select_extractor(content_type: str) -> ContentExtractor:
    """Select a ``ContentExtractor`` for the given Content-Type.

    Args:
        content_type: The Content-Type header value (may include
            parameters, e.g. ``"text/html; charset=utf-8"``).

    Returns:
        - ``HtmlContentExtractor`` for ``text/html`` and
          ``application/xhtml+xml``.
        - ``PdfContentExtractor`` for ``application/pdf``.
        - ``PlainTextExtractor`` for ``text/plain`` and any other
          text/* type, and as a fallback for unknown types.
    """
    main_type = content_type.split(";")[0].strip().lower()

    if main_type in ("text/html", "application/xhtml+xml"):
        return _HTML
    if main_type == "application/pdf":
        return _PDF
    # Everything else (text/plain, text/markdown, application/json,
    # unknown types) gets the plain-text extractor.
    return _PLAIN


__all__ = [
    "select_extractor",
]
