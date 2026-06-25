"""Tests for Model Slot Resolver — CI mode and real provider dispatch.

Tests verify:
  - CI mode returns deterministic fixtures (no network)
  - Real mode dispatches to Ollama and OpenAI-compatible providers
  - Environment variable overrides work correctly
  - Error handling returns structured error results
  - Configuration resolution merges dict config with env vars
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aip.adapter.model_slot_resolver import (
    ModelSlotResolver,
)

# ----------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------


@pytest.fixture
def ci_config():
    """Config with ci_mode=True (deterministic, no network)."""
    return {
        "models": {
            "ci_mode": True,
            "synthesis": {"provider": "ollama", "model": "qwen2.5:32b"},
            "embedding": {"provider": "ollama", "model": "nomic-embed-text", "dimensions": 768},
        },
    }


@pytest.fixture
def real_config():
    """Config with ci_mode=False (real provider dispatch)."""
    return {
        "models": {
            "ci_mode": False,
            "synthesis": {
                "provider": "ollama",
                "model": "qwen2.5:32b",
                "base_url": "http://localhost:11434",
            },
            "evaluation": {
                "provider": "openai_compatible",
                "model": "gpt-4o-mini",
                "base_url": "https://api.openai.com",
                "api_key": "test-key-123",
            },
        },
    }


@pytest.fixture
def minimal_real_config():
    """Config with ci_mode=False but no per-slot details (uses defaults)."""
    return {
        "models": {
            "ci_mode": False,
            "synthesis": {"provider": "ollama", "model": "qwen2.5:32b"},
        },
    }


# ----------------------------------------------------------------
# Existing CI mode tests (preserved)
# ----------------------------------------------------------------


def test_resolve_slot(ci_config):
    resolver = ModelSlotResolver(ci_config)
    cfg = resolver.resolve("synthesis")
    assert cfg.slot_name == "synthesis"
    assert cfg.provider == "ollama"


def test_ci_mode_returns_fixture(ci_config):
    import asyncio

    resolver = ModelSlotResolver(ci_config)
    result = asyncio.run(resolver.call("synthesis", [{"role": "user", "content": "hello"}]))
    assert "CI-FIXTURE" in result["content"]
    assert result["cost_usd"] == 0.0


def test_list_slots(ci_config):
    resolver = ModelSlotResolver(ci_config)
    slots = resolver.list_slots()
    assert "synthesis" in slots
    assert "embedding" in slots


# ----------------------------------------------------------------
# New CI mode tests
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_ci_mode_fixture_has_model_name(ci_config):
    """CI fixture includes the configured model name."""
    resolver = ModelSlotResolver(ci_config)
    result = await resolver.call("synthesis", [{"role": "user", "content": "test"}])
    assert result["model"] == "qwen2.5:32b"


@pytest.mark.asyncio
async def test_ci_mode_fixture_usage_tokens(ci_config):
    """CI fixture returns plausible usage stats."""
    resolver = ModelSlotResolver(ci_config)
    result = await resolver.call("synthesis", [{"role": "user", "content": "hello world"}])
    assert "prompt_tokens" in result["usage"]
    assert "completion_tokens" in result["usage"]
    assert result["usage"]["prompt_tokens"] > 0


@pytest.mark.asyncio
async def test_ci_mode_no_error_flag(ci_config):
    """CI fixture should not have error=True."""
    resolver = ModelSlotResolver(ci_config)
    result = await resolver.call("synthesis", [{"role": "user", "content": "hello"}])
    assert result.get("error") is not True


# ----------------------------------------------------------------
# Configuration resolution tests
# ----------------------------------------------------------------


def test_resolve_defaults_to_ollama_base_url(real_config):
    """When base_url is not set for ollama slot, defaults to localhost:11434."""
    resolver = ModelSlotResolver(real_config)
    resolved = resolver._resolve_slot_config("synthesis")
    assert resolved["base_url"] == "http://localhost:11434"


def test_resolve_openai_slot_config(real_config):
    """OpenAI-compatible slot reads base_url and api_key from config."""
    resolver = ModelSlotResolver(real_config)
    resolved = resolver._resolve_slot_config("evaluation")
    assert resolved["provider"] == "openai_compatible"
    assert resolved["base_url"] == "https://api.openai.com"
    assert resolved["api_key"] == "test-key-123"


def test_resolve_env_var_override_base_url(real_config):
    """Environment variable overrides config dict for base_url."""
    with patch.dict(os.environ, {"AIP_SYNTHESIS_BASE_URL": "http://custom:9999"}):
        resolver = ModelSlotResolver(real_config)
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["base_url"] == "http://custom:9999"


def test_resolve_env_var_override_model(real_config):
    """Environment variable overrides config dict for model name."""
    with patch.dict(os.environ, {"AIP_SYNTHESIS_MODEL": "llama3:8b"}):
        resolver = ModelSlotResolver(real_config)
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["model"] == "llama3:8b"


def test_resolve_env_var_override_provider(real_config):
    """Environment variable overrides provider."""
    with patch.dict(os.environ, {"AIP_SYNTHESIS_PROVIDER": "openai_compatible"}):
        resolver = ModelSlotResolver(real_config)
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["provider"] == "openai_compatible"


def test_resolve_global_ollama_default():
    """AIP_OLLAMA_BASE_URL provides default for all ollama slots."""
    config = {
        "models": {
            "ci_mode": False,
            "synthesis": {"provider": "ollama", "model": "test"},
        },
    }
    with patch.dict(os.environ, {"AIP_OLLAMA_BASE_URL": "http://ollama-server:11434"}):
        resolver = ModelSlotResolver(config)
        resolved = resolver._resolve_slot_config("synthesis")
        assert resolved["base_url"] == "http://ollama-server:11434"


def test_resolve_global_openai_default():
    """AIP_OPENAI_BASE_URL and AIP_OPENAI_API_KEY provide defaults."""
    config = {
        "models": {
            "ci_mode": False,
            "eval_slot": {"provider": "openai_compatible", "model": "gpt-4"},
        },
    }
    with patch.dict(
        os.environ,
        {
            "AIP_OPENAI_BASE_URL": "https://api.deepseek.com",
            "AIP_OPENAI_API_KEY": "sk-global-key",
        },
    ):
        resolver = ModelSlotResolver(config)
        resolved = resolver._resolve_slot_config("eval_slot")
        assert resolved["base_url"] == "https://api.deepseek.com"
        assert resolved["api_key"] == "sk-global-key"


def test_resolve_unknown_slot_raises(real_config):
    """Resolving an unknown slot raises ValueError."""
    resolver = ModelSlotResolver(real_config)
    with pytest.raises(ValueError, match="Unknown model slot"):
        resolver.resolve("nonexistent_slot")


# ----------------------------------------------------------------
# Real dispatch tests (mocked HTTP)
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_dispatch_success(real_config):
    """Ollama dispatch sends correct payload and returns parsed response."""
    resolver = ModelSlotResolver(real_config)

    # Mock httpx client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "model": "qwen2.5:32b",
        "message": {"role": "assistant", "content": "Hello from Ollama!"},
        "eval_count": 20,
        "prompt_eval_count": 10,
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
            temperature=0.7,
        )

    assert result["content"] == "Hello from Ollama!"
    assert result["model"] == "qwen2.5:32b"
    assert result["usage"]["prompt_tokens"] == 10
    assert result["usage"]["completion_tokens"] == 20
    assert result.get("error") is not True

    # Verify the payload was correct
    call_args = mock_client.post.call_args
    assert "/api/chat" in call_args[1].get("url", call_args[0][0])
    payload = call_args[1].get("json", {})
    assert payload["model"] == "qwen2.5:32b"
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_openai_compatible_dispatch_success(real_config):
    """OpenAI-compatible dispatch sends correct payload and returns parsed response."""
    resolver = ModelSlotResolver(real_config)

    # Mock httpx client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "model": "gpt-4o-mini",
        "choices": [{"message": {"role": "assistant", "content": "Hello from OpenAI!"}}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "evaluation",
            [{"role": "user", "content": "Evaluate this"}],
            temperature=0.3,
            max_tokens=256,
        )

    assert result["content"] == "Hello from OpenAI!"
    assert result["model"] == "gpt-4o-mini"
    assert result["usage"]["prompt_tokens"] == 15
    assert result["usage"]["completion_tokens"] == 8
    assert result.get("error") is not True

    # Verify the payload was correct
    call_args = mock_client.post.call_args
    url = call_args[1].get("url", call_args[0][0])
    assert "/v1/chat/completions" in url
    payload = call_args[1].get("json", {})
    assert payload["model"] == "gpt-4o-mini"
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 256
    headers = call_args[1].get("headers", {})
    assert "Bearer test-key-123" in headers.get("Authorization", "")


@pytest.mark.asyncio
async def test_ollama_dispatch_connection_error(real_config):
    """Ollama dispatch failure returns structured error result, not exception."""
    resolver = ModelSlotResolver(real_config)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=ConnectionError("Ollama not running"))

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
        )

    assert result["error"] is True
    assert "Ollama not running" in result["error_message"]
    assert result["content"] == ""


@pytest.mark.asyncio
async def test_openai_dispatch_auth_error(real_config):
    """OpenAI-compatible auth failure returns structured error result."""
    resolver = ModelSlotResolver(real_config)

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.raise_for_status = MagicMock(side_effect=Exception("401 Unauthorized"))

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "evaluation",
            [{"role": "user", "content": "Hello"}],
        )

    assert result["error"] is True
    assert "401" in result["error_message"]


@pytest.mark.asyncio
async def test_unsupported_provider_error(real_config):
    """Unsupported provider returns structured error result."""
    # Patch the config to have an unsupported provider
    real_config["models"]["synthesis"]["provider"] = "anthropic"
    resolver = ModelSlotResolver(real_config)

    result = await resolver.call(
        "synthesis",
        [{"role": "user", "content": "Hello"}],
    )

    assert result["error"] is True
    assert "Unsupported provider" in result["error_message"]


@pytest.mark.asyncio
async def test_real_call_measures_latency(real_config):
    """Real dispatch records elapsed latency."""
    resolver = ModelSlotResolver(real_config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "model": "qwen2.5:32b",
        "message": {"role": "assistant", "content": "Hi"},
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
        )

    assert result["latency_ms"] >= 0


# ----------------------------------------------------------------
# Lazy client and close tests
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_cleans_up_client(real_config):
    """close() properly cleans up the httpx client."""
    resolver = ModelSlotResolver(real_config)
    # Simulate creating the client
    mock_client = AsyncMock()
    mock_client.aclose = AsyncMock()
    resolver._http_client = mock_client

    await resolver.close()

    mock_client.aclose.assert_called_once()
    assert resolver._http_client is None


@pytest.mark.asyncio
async def test_close_idempotent(real_config):
    """close() is safe to call when no client exists."""
    resolver = ModelSlotResolver(real_config)
    await resolver.close()  # Should not raise
    assert resolver._http_client is None


def test_get_http_client_raises_without_httpx(real_config):
    """_get_http_client raises RuntimeError if httpx is not installed."""
    resolver = ModelSlotResolver(real_config)

    with patch.dict("sys.modules", {"httpx": None}):
        with pytest.raises(RuntimeError, match="httpx is required"):
            resolver._get_http_client()


# ----------------------------------------------------------------
# Ollama options mapping tests
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_maps_max_tokens_to_num_predict(real_config):
    """max_tokens kwarg is mapped to Ollama's num_predict option."""
    resolver = ModelSlotResolver(real_config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "model": "qwen2.5:32b",
        "message": {"content": "response"},
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
            max_tokens=1024,
        )

    call_args = mock_client.post.call_args
    payload = call_args[1].get("json", {})
    assert payload["options"]["num_predict"] == 1024


@pytest.mark.asyncio
async def test_ollama_prefers_explicit_num_predict(real_config):
    """Explicit num_predict kwarg takes precedence over max_tokens."""
    resolver = ModelSlotResolver(real_config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "model": "qwen2.5:32b",
        "message": {"content": "response"},
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
            max_tokens=1024,
            num_predict=512,
        )

    call_args = mock_client.post.call_args
    payload = call_args[1].get("json", {})
    assert payload["options"]["num_predict"] == 512


# ----------------------------------------------------------------
# Fallback model tests (Phase D — OpenRouter reliability)
# ----------------------------------------------------------------


@pytest.fixture
def fallback_config():
    """Config with a fallback model configured for the synthesis slot."""
    return {
        "models": {
            "ci_mode": False,
            "synthesis": {
                "provider": "openai_compatible",
                "model": "nvidia/nemotron-primary",
                "base_url": "https://openrouter.ai/api",
                "api_key": "test-key",
                "fallback_provider": "openai_compatible",
                "fallback_model": "meta-llama/llama-backup",
                "fallback_base_url": "https://openrouter.ai/api",
                "fallback_api_key": "test-key",
            },
        },
    }


@pytest.mark.asyncio
async def test_fallback_triggered_on_empty_content(fallback_config):
    """When primary returns empty content (OpenRouter timeout), fallback is tried."""
    resolver = ModelSlotResolver(fallback_config)

    # First call (primary) returns empty content, second call (fallback) returns real content
    empty_response = MagicMock()
    empty_response.status_code = 200
    empty_response.raise_for_status = MagicMock()
    empty_response.json.return_value = {
        "model": "nvidia/nemotron-primary",
        "choices": [{"message": {"content": ""}}],  # empty — OpenRouter timeout
    }
    fallback_response = MagicMock()
    fallback_response.status_code = 200
    fallback_response.raise_for_status = MagicMock()
    fallback_response.json.return_value = {
        "model": "meta-llama/llama-backup",
        "choices": [{"message": {"content": "Hello from fallback!"}}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[empty_response, fallback_response])

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
        )

    # Fallback content is returned
    assert result["content"] == "Hello from fallback!"
    assert result["error"] is False
    # Two HTTP calls were made (primary + fallback)
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_fallback_triggered_on_exception(fallback_config):
    """When primary raises (connection error), fallback is tried."""
    resolver = ModelSlotResolver(fallback_config)

    fallback_response = MagicMock()
    fallback_response.status_code = 200
    fallback_response.raise_for_status = MagicMock()
    fallback_response.json.return_value = {
        "model": "meta-llama/llama-backup",
        "choices": [{"message": {"content": "Fallback OK"}}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[ConnectionError("timeout"), fallback_response])

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
        )

    assert result["content"] == "Fallback OK"
    assert result["error"] is False
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_no_fallback_when_not_configured(real_config):
    """When no fallback is configured, primary failure returns error (no retry)."""
    resolver = ModelSlotResolver(real_config)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=ConnectionError("primary down"))

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
        )

    assert result["error"] is True
    # Only one call (no fallback configured)
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_fallback_also_fails_returns_error(fallback_config):
    """When both primary and fallback fail, the fallback's error is returned."""
    resolver = ModelSlotResolver(fallback_config)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[
        ConnectionError("primary timeout"),
        ConnectionError("fallback also down"),
    ])

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
        )

    assert result["error"] is True
    assert "fallback also down" in result["error_message"]
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_no_fallback_when_primary_succeeds(fallback_config):
    """When primary succeeds, fallback is NOT tried (no wasted calls)."""
    resolver = ModelSlotResolver(fallback_config)

    primary_response = MagicMock()
    primary_response.status_code = 200
    primary_response.raise_for_status = MagicMock()
    primary_response.json.return_value = {
        "model": "nvidia/nemotron-primary",
        "choices": [{"message": {"content": "Primary OK"}}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=primary_response)

    with patch.object(resolver, "_get_http_client", return_value=mock_client):
        result = await resolver.call(
            "synthesis",
            [{"role": "user", "content": "Hello"}],
        )

    assert result["content"] == "Primary OK"
    # Only one call — fallback not tried
    assert mock_client.post.call_count == 1
