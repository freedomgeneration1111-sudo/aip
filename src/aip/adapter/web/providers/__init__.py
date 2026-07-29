"""Web search provider adapters (ADR-017 WS-3).

Submodules:
    - ``tavily``   — TavilySearchProvider (primary, shipped in WS-3)
    - ``factory``  — build_search_provider(config) -> SearchProvider | None
    - (future) ``brave`` — BraveSearchProvider (post-WS-3 fallback)
"""

from __future__ import annotations

from aip.adapter.web.providers.factory import build_search_provider
from aip.adapter.web.providers.tavily import TavilySearchProvider

__all__ = [
    "TavilySearchProvider",
    "build_search_provider",
]
