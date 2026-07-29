"""AIP Web Source Acquisition adapter (ADR-017).

Subpackages:
    - ``policy``          — SSRF guard and URL policy (WS-1)
    - ``fake_provider``   — fake search/fetch for CI (WS-1)
    - ``snapshot``        — in-memory snapshot/source stores (WS-1)
    - ``http_fetcher``    — bounded HTTP fetcher (WS-2)
    - ``extractors``      — HTML/PDF/plain-text extractors (WS-2)
    - ``provenance``      — WebSourceRecord builder (WS-2)
    - ``providers``       — real provider adapters: Tavily (WS-3), Brave (post-WS-3)
    - ``promotion``       — explicit corpus promotion (WS-5)

This package is the only place in the codebase allowed to import
network libraries (``httpx``, etc.) — per ``tests/test_no_network.py``,
the foundation and orchestration layers must not.
"""

from __future__ import annotations

__all__: list[str] = []
