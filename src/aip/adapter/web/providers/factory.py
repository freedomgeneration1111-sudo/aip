"""Search provider factory for Web Source Acquisition (ADR-017 WS-3).

Builds the appropriate ``SearchProvider`` from configuration.  Returns
``None`` when web search is disabled or no provider is configured —
the API routes use this to produce honest "not_configured" 503
responses.

Configuration shape (from ``[web]`` and ``[web.providers.<name>]``
in ``aip.config.toml``)::

    [web]
    enabled = false
    default_provider = "tavily"

    [web.providers.tavily]
    api_key_env = "AIP_WEB_SEARCH_API_KEY"
    # options: endpoint, timeout_seconds, topic, include_raw_content

The factory reads the env var named in ``api_key_env`` to decide
whether the provider is "configured".  A provider with no key is
still constructible (so health checks can report "not_configured"
without raising), but its ``search`` method will raise
``WebProviderNotConfigured`` when called.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.protocols.web import SearchProvider

logger = logging.getLogger(__name__)


def build_search_provider(
    web_config: dict[str, Any],
    *,
    providers_config: dict[str, dict[str, Any]] | None = None,
) -> SearchProvider | None:
    """Build a ``SearchProvider`` from configuration.

    Args:
        web_config: The ``[web]`` section of the config dict.  Must
            contain ``enabled`` (bool) and ``default_provider`` (str).
        providers_config: The ``[web.providers.<name>]`` sections,
            keyed by provider name.  Each value is a dict with
            ``api_key_env`` and optional ``options``.

    Returns:
        - A ``SearchProvider`` instance if ``enabled`` is True and
          ``default_provider`` names a known provider.
        - ``None`` if ``enabled`` is False, or ``default_provider``
          is empty/unknown.

    The returned provider may be "not configured" (no API key) —
    callers must handle ``WebProviderNotConfigured`` from ``search``.
    The ``is_provider_configured`` helper below checks this without
    calling ``search``.
    """
    if not web_config.get("enabled", False):
        logger.debug("web_search_disabled")
        return None

    provider_name = web_config.get("default_provider", "").strip()
    if not provider_name:
        logger.warning("web_search_no_default_provider")
        return None

    providers_config = providers_config or {}
    # The "fake" provider is a special case: it doesn't need a config entry
    # (it has no key, no options).  This lets CI wire it via default_provider="fake"
    # without adding a [web.providers.fake] section.
    if provider_name == "fake":
        from aip.adapter.web.fake_provider import FakeSearchProvider

        logger.info("web_search_provider_wired: fake")
        return FakeSearchProvider()

    provider_cfg = providers_config.get(provider_name)
    if provider_cfg is None:
        logger.warning("web_search_provider_not_found: %s", provider_name)
        return None

    if provider_name == "tavily":
        from aip.adapter.web.providers.tavily import (
            DEFAULT_TAVILY_ENDPOINT,
            DEFAULT_TIMEOUT_SECONDS,
            TavilySearchProvider,
        )

        api_key_env = provider_cfg.get("api_key_env", "AIP_WEB_SEARCH_API_KEY")
        options = provider_cfg.get("options", {}) or {}
        endpoint = options.get("endpoint", DEFAULT_TAVILY_ENDPOINT)
        timeout = float(options.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        return TavilySearchProvider(
            api_key_env=api_key_env,
            endpoint=endpoint,
            timeout_seconds=timeout,
        )

    # Unknown provider — return None so routes produce honest 503s.
    logger.warning("web_search_unknown_provider: %s", provider_name)
    return None


def is_provider_configured(provider: SearchProvider | None) -> bool:
    """Return True if ``provider`` is non-None AND has an API key available.

    Used by health checks and API routes to produce honest
    "not_configured" responses without actually calling ``search``.
    """
    if provider is None:
        return False
    # Tavily exposes ``_get_api_key`` for this check.
    getter = getattr(provider, "_get_api_key", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:
            return False
    # Fake providers and others without a key concept are always "configured".
    return True


def provider_status(provider: SearchProvider | None) -> str:
    """Return a status string suitable for the health endpoint.

    Returns one of:
        - ``"not_configured"`` — provider is None, or no API key.
        - ``"available"`` — provider is wired and has a key.
        - ``"degraded"`` — provider is wired but a recent call failed
          (tracked by the caller; this function does not maintain
          state, so it returns ``"available"`` for wired+key providers).
    """
    if not is_provider_configured(provider):
        return "not_configured"
    return "available"


__all__ = [
    "build_search_provider",
    "is_provider_configured",
    "provider_status",
]
