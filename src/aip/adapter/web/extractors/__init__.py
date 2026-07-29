"""Content extractors for Web Source Acquisition (ADR-017 WS-2).

Submodules:
    - ``html``        — HtmlContentExtractor (BeautifulSoup + lxml)
    - ``pdf``         — PdfContentExtractor (pypdf handoff)
    - ``plain_text``  — PlainTextExtractor
    - ``factory``     — select_extractor(content_type) -> ContentExtractor
"""

from __future__ import annotations

from aip.adapter.web.extractors.factory import select_extractor
from aip.adapter.web.extractors.html import HtmlContentExtractor
from aip.adapter.web.extractors.pdf import PdfContentExtractor
from aip.adapter.web.extractors.plain_text import PlainTextExtractor

__all__ = [
    "HtmlContentExtractor",
    "PdfContentExtractor",
    "PlainTextExtractor",
    "select_extractor",
]
