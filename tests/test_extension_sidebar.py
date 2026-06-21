"""Tests for ADR-014 Amendment A1 — extension sidebar health polling.

Tests the KNOWN_EXTENSIONS config loading + the _poll_extension_health
function that checks extension health endpoints and updates the
_extension_status store.

Run: pytest tests/test_extension_sidebar.py -v
"""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Suppress NiceGUI refreshable warnings in test context (the NiceGUI
# event loop is not running in pytest — the refreshable fires but has
# no loop to schedule on. The _poll_extension_health function catches
# this with try/except. The warning is cosmetic, not a failure.)
warnings.filterwarnings("ignore", message="coroutine.*was never awaited")


# ---------------------------------------------------------------------------
# Test 1: KNOWN_EXTENSIONS loaded from config
# ---------------------------------------------------------------------------


def test_known_extensions_loaded_from_config():
    """Load aip.config.toml, assert extensions.known has at least one entry
    with name, health_url, nav fields.
    """
    from gui.components.layout import _load_known_extensions

    known = _load_known_extensions()
    assert len(known) >= 1, "expected at least one known extension in config"

    ext = known[0]
    assert "name" in ext, "known extension must have a 'name' field"
    assert "health_url" in ext, "known extension must have a 'health_url' field"
    assert "nav" in ext, "known extension must have a 'nav' field"
    assert isinstance(ext["nav"], list)
    assert len(ext["nav"]) >= 1, "known extension must have at least one nav item"

    # Verify the aristotle entry specifically.
    aristotle = [e for e in known if e.get("name") == "aristotle"]
    assert len(aristotle) == 1, "expected an 'aristotle' entry"
    assert aristotle[0]["health_url"] == "http://localhost:8001/health"


# ---------------------------------------------------------------------------
# Test 2: poll sets status True on HTTP 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_sets_status_true_on_200():
    """Mock httpx.AsyncClient.get to return status_code=200.
    Call _poll_extension_health(config).
    Assert _extension_status['aristotle'] is True.
    """
    from gui.components.layout import _extension_status, _poll_extension_health

    # Reset status before test.
    _extension_status.clear()

    config = {
        "extensions": {
            "known": [
                {
                    "name": "aristotle",
                    "health_url": "http://localhost:8001/health",
                    "nav": [],
                }
            ]
        }
    }

    # Mock httpx.AsyncClient to return 200.
    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("gui.components.layout.httpx.AsyncClient", return_value=mock_client):
        await _poll_extension_health(config)

    assert _extension_status.get("aristotle") is True


# ---------------------------------------------------------------------------
# Test 3: poll sets status False on connection error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_sets_status_false_on_connection_error():
    """Mock httpx.AsyncClient.get to raise httpx.ConnectError.
    Call _poll_extension_health(config).
    Assert _extension_status['aristotle'] is False.
    """
    from gui.components.layout import _extension_status, _poll_extension_health

    _extension_status.clear()

    config = {
        "extensions": {
            "known": [
                {
                    "name": "aristotle",
                    "health_url": "http://localhost:8001/health",
                    "nav": [],
                }
            ]
        }
    }

    # Mock httpx.AsyncClient.get to raise ConnectError.
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("gui.components.layout.httpx.AsyncClient", return_value=mock_client):
        await _poll_extension_health(config)

    assert _extension_status.get("aristotle") is False


# ---------------------------------------------------------------------------
# Test 4: poll sets status False on non-200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_sets_status_false_on_non_200():
    """Mock httpx.AsyncClient.get to return status_code=503.
    Call _poll_extension_health(config).
    Assert _extension_status['aristotle'] is False.
    """
    from gui.components.layout import _extension_status, _poll_extension_health

    _extension_status.clear()

    config = {
        "extensions": {
            "known": [
                {
                    "name": "aristotle",
                    "health_url": "http://localhost:8001/health",
                    "nav": [],
                }
            ]
        }
    }

    # Mock httpx.AsyncClient to return 503.
    mock_response = MagicMock()
    mock_response.status_code = 503

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("gui.components.layout.httpx.AsyncClient", return_value=mock_client):
        await _poll_extension_health(config)

    assert _extension_status.get("aristotle") is False
