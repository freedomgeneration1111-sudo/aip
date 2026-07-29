"""HTML content extractor for Web Source Acquisition (ADR-017 WS-2).

Uses BeautifulSoup4 + lxml to extract main-content text from HTML pages.
Implements:

    - Title extraction (``<title>`` then ``<h1>`` fallback)
    - Canonical URL resolution (``<link rel="canonical">``)
    - Author extraction (``<meta name="author">``, ``article:author``)
    - Published-at extraction (``<meta>``, ``<time>``)
    - Main-content heuristic: prefer ``<article>``, ``<main>``, or
      ``role="main"``; fall back to ``<body>`` with boilerplate removal
    - Paywall / login-wall detection (heuristic, surfaces as warnings)
    - Encoding fallback (response Content-Type, then meta charset,
      then BeautifulSoup default)
    - Prompt-injection isolation: extracted text is DATA, never
      instructions.  The extractor does NOT interpret or execute
      anything in the HTML.

This module imports ``bs4`` and ``lxml`` — adapter layer, allowed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from aip.foundation.schemas.web import (
    ExtractedDocument,
    FetchedResource,
    sha256_hex,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heuristic paywall / login-wall signals
# ---------------------------------------------------------------------------

_PAYWALL_SIGNALS = [
    "subscribe to continue",
    "subscribe to read",
    "sign in to continue reading",
    "create a free account to continue",
    "this content is for subscribers",
    "premium content",
    "already a subscriber",
    "subscriber only",
]

_LOGIN_WALL_SIGNALS = [
    "please log in",
    "sign in to continue",
    "you must be logged in",
    "login required",
    "sign in to view",
]

# Boilerplate tags to remove before text extraction
_BOILERPLATE_TAGS = frozenset({
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "form", "iframe", "svg", "button",
})


class HtmlContentExtractor:
    """Extract main-content text from HTML pages.

    Uses BeautifulSoup4 with the lxml parser.  The extractor is
    stateless and safe to reuse across fetches.
    """

    EXTRACTION_METHOD = "html_readability"

    @property
    def extraction_method(self) -> str:
        return self.EXTRACTION_METHOD

    async def extract(
        self,
        resource: FetchedResource,
        *,
        bytes_loader: Any,
    ) -> ExtractedDocument:
        """Extract text and metadata from an HTML ``FetchedResource``."""
        raw = bytes_loader(resource.content_bytes_ref)
        warnings: list[str] = []

        # ---- Parse with encoding fallback ----
        # BeautifulSoup auto-detects encoding from meta tags; if the
        # response declared a charset, we decode first and pass str.
        charset = _extract_charset(resource.content_type)
        if charset:
            try:
                html_text = raw.decode(charset, errors="replace")
                soup = BeautifulSoup(html_text, "lxml")
            except (LookupError, UnicodeDecodeError):
                soup = BeautifulSoup(raw, "lxml")
                warnings.append(f"unknown charset {charset!r}; fell back to auto-detection")
        else:
            soup = BeautifulSoup(raw, "lxml")

        # ---- Title ----
        title = _extract_title(soup)

        # ---- Canonical URL ----
        canonical_url = _extract_canonical_url(soup, base_url=resource.final_url)

        # ---- Authors ----
        authors = _extract_authors(soup)

        # ---- Published-at ----
        published_at = _extract_published_at(soup)

        # ---- Main-content extraction ----
        main_element = _select_main_element(soup)
        if main_element is None:
            main_element = soup.body or soup
            warnings.append("no <article>, <main>, or role=main found; used <body>")

        # Remove boilerplate tags from the selected element
        for tag_name in _BOILERPLATE_TAGS:
            for tag in main_element.find_all(tag_name):
                tag.decompose()

        text = main_element.get_text(separator="\n", strip=True)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if not text:
            warnings.append("extracted text is empty after boilerplate removal")

        # ---- Paywall / login-wall detection ----
        text_lower = text.lower()[:5000]  # check first 5k chars
        for signal in _PAYWALL_SIGNALS:
            if signal in text_lower:
                warnings.append(f"paywall signal detected: {signal!r}")
                break
        for signal in _LOGIN_WALL_SIGNALS:
            if signal in text_lower:
                warnings.append(f"login-wall signal detected: {signal!r}")
                break

        # ---- Truncation warning ----
        if resource.truncated:
            warnings.append("source body was truncated at policy.max_bytes; extraction may be incomplete")

        content_hash = sha256_hex(text)
        retrieved_at = resource.retrieved_at

        return ExtractedDocument(
            source_url=resource.final_url,
            canonical_url=canonical_url,
            title=title,
            text=text,
            authors=tuple(authors),
            published_at=published_at,
            retrieved_at=retrieved_at,
            content_hash=content_hash,
            extraction_method=self.EXTRACTION_METHOD,
            warnings=tuple(warnings),
            snapshot_artifact_id=None,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_charset(content_type: str) -> str | None:
    """Extract charset from a Content-Type header value."""
    # e.g. "text/html; charset=utf-8"
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part[len("charset="):].strip().strip('"').strip("'")
    return None


def _extract_title(soup: BeautifulSoup) -> str:
    """Extract the page title: ``<title>`` first, then ``<h1>``."""
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True)[:500]
    h1_tag = soup.find("h1")
    if h1_tag and h1_tag.get_text(strip=True):
        return h1_tag.get_text(strip=True)[:500]
    return ""


def _extract_canonical_url(soup: BeautifulSoup, base_url: str) -> str | None:
    """Extract the canonical URL from ``<link rel="canonical">``."""
    link_tag = soup.find("link", attrs={"rel": "canonical"})
    if link_tag and link_tag.get("href"):
        href = link_tag["href"].strip()
        # Resolve relative URLs against the base
        return urljoin(base_url, href)
    return None


def _extract_authors(soup: BeautifulSoup) -> list[str]:
    """Extract author names from meta tags."""
    authors: list[str] = []
    # <meta name="author" content="...">
    for meta in soup.find_all("meta", attrs={"name": "author"}):
        content = meta.get("content", "").strip()
        if content and content not in authors:
            authors.append(content)
    # <meta property="article:author" content="...">
    for meta in soup.find_all("meta", attrs={"property": "article:author"}):
        content = meta.get("content", "").strip()
        if content and content not in authors:
            authors.append(content)
    return authors


def _extract_published_at(soup: BeautifulSoup) -> datetime | None:
    """Extract publication timestamp from meta tags or ``<time>``."""
    # <meta property="article:published_time" content="2024-01-15T...">
    for prop in ("article:published_time", "og:published_time", "datePublished"):
        meta = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if meta and meta.get("content"):
            dt = _parse_datetime(meta["content"].strip())
            if dt is not None:
                return dt
    # <time datetime="2024-01-15T..." pubdate>
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        dt = _parse_datetime(time_tag["datetime"].strip())
        if dt is not None:
            return dt
    return None


def _parse_datetime(value: str) -> datetime | None:
    """Parse an ISO 8601 datetime string, returning None on failure."""
    try:
        # Handle "Z" suffix
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _select_main_element(soup: BeautifulSoup):
    """Select the main content element using heuristics."""
    # 1. <article>
    article = soup.find("article")
    if article:
        return article
    # 2. <main>
    main = soup.find("main")
    if main:
        return main
    # 3. role="main"
    role_main = soup.find(attrs={"role": "main"})
    if role_main:
        return role_main
    # 4. Common content IDs
    for div_id in ("content", "main-content", "article-body", "post-content"):
        element = soup.find(id=div_id)
        if element:
            return element
    return None


__all__ = ["HtmlContentExtractor"]
