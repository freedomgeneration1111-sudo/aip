"""Model Slot Resolver — real model wiring via configurable provider slots.

Resolves named slots to concrete provider + model + base_url.
Supports ci_mode for fully deterministic tests (no network).

Real dispatch supports:
  - Ollama (local /api/chat endpoint)
  - OpenAI-compatible (/v1/chat/completions endpoint)

Configuration sources (in priority order):
  1. **In-memory runtime overrides** — set via ``set_runtime_override()``.
     These are used for hot-reload of model/api-key changes from the admin
     API without writing secrets into ``os.environ``.
  2. Environment variables (startup-time only, NOT written at runtime):
       AIP_<SLOT>_BASE_URL   — e.g. AIP_SYNTHESIS_BASE_URL
       AIP_<SLOT>_MODEL      — e.g. AIP_SYNTHESIS_MODEL
       AIP_<SLOT>_API_KEY    — e.g. AIP_SYNTHESIS_API_KEY (for OpenAI-compatible)
       AIP_<SLOT>_PROVIDER   — e.g. AIP_SYNTHESIS_PROVIDER (ollama | openai_compatible)
  3. Explicit slot config dict passed to __init__ under ``models.<slot>``.
  4. Global defaults:
       AIP_OLLAMA_BASE_URL   — default base URL for all Ollama slots (default: http://localhost:11434)
       AIP_OPENAI_BASE_URL   — default base URL for all OpenAI-compatible slots
       AIP_OPENAI_API_KEY    — default API key for all OpenAI-compatible slots

Security note (Chunk 3 — credential sovereignty):
  Runtime overrides are stored in-memory ONLY. API keys are NEVER written
  to ``os.environ`` at runtime. This prevents credential leakage to child
  processes, debugging tools, and log outputs that dump environment
  variables.
"""

from __future__ import annotations

import os
import time
from typing import Any

from aip.foundation.protocols import ModelProvider
from aip.foundation.schemas import ModelSlotConfig
from aip.logging import get_logger

log = get_logger(__name__)

# Provider constants
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"

# Default Ollama base URL (local laptop-viable default)
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


class ModelSlotResolver(ModelProvider):
    """Resolves model slots and executes calls (with ci_mode support).

    In ``ci_mode=True`` (default), returns deterministic fixtures with no network.
    In ``ci_mode=False``, dispatches to the real provider (Ollama or OpenAI-compatible)
    using httpx for async HTTP calls.

    Provider selection is per-slot via ``models.<slot>.provider`` or the
    ``AIP_<SLOT>_PROVIDER`` environment variable.
    """

    # Known slot names used to detect when a config dict IS the models sub-dict
    _KNOWN_SLOT_NAMES = frozenset({"synthesis", "evaluation", "sexton", "embedding", "beast"})

    def __init__(self, config: dict | Any) -> None:
        if hasattr(config, "model_dump"):
            self._cfg = config.model_dump()
        elif isinstance(config, dict):
            self._cfg = config
        else:
            self._cfg = {}

        self._models = self._cfg.get("models", {})

        # Fallback 1: if no "models" key, check if the config itself IS the models dict
        # (i.e. has known slot names like "synthesis", "evaluation" as top-level keys)
        if not self._models and isinstance(self._cfg, dict):
            if self._KNOWN_SLOT_NAMES & set(self._cfg.keys()):
                self._models = self._cfg
                log.info("slot_resolver_fallback", reason="config_is_models_dict", slots=list(self._models.keys()))

        # Fallback 2: if still empty, try loading from the TOML config file
        if not self._models:
            toml_models = self._try_load_toml_models()
            if toml_models:
                self._models = toml_models
                log.info("slot_resolver_fallback", reason="loaded_from_toml", slots=list(toml_models.keys()))

        # ci_mode defaults to True unless explicitly set to False
        self._ci_mode = self._models.get("ci_mode", True)

        # In-memory runtime overrides — highest priority, never written to os.environ.
        # Keys are "<SLOT_NAME>.<FIELD>" (e.g. "synthesis.model", "embedding.api_key").
        # This is the credential-sovereign alternative to setting os.environ at runtime.
        self._runtime_overrides: dict[str, str] = {}

        # Lazy httpx client — created on first real call, reused thereafter
        self._http_client: Any = None

    @staticmethod
    def _try_load_toml_models() -> dict:
        """Attempt to load the models section from the default TOML config file.

        Searches the same paths as app.py's _load_toml_config():
          1. AIP_CONFIG_PATH env var
          2. config/aip.config.toml relative to CWD
          3. config/aip.config.toml relative to this source file

        Returns the models sub-dict, or {} if not found / no models section.
        """
        from pathlib import Path

        config_path = os.environ.get("AIP_CONFIG_PATH", "")
        candidates: list[Path] = []
        if config_path:
            candidates.append(Path(config_path))
        else:
            candidates.append(Path.cwd() / "config" / "aip.config.toml")
            # Relative to this source file: src/aip/adapter/ → project root
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            candidates.append(project_root / "config" / "aip.config.toml")

        for path in candidates:
            if path.is_file():
                try:
                    try:
                        import tomllib
                    except ImportError:
                        import tomli as tomllib  # type: ignore[no-redef]
                    with open(path, "rb") as f:
                        cfg = tomllib.load(f)
                    models = cfg.get("models", {})
                    if models:
                        log.info("slot_resolver_toml_loaded", path=str(path))
                        return models
                except Exception as exc:
                    log.warning("slot_resolver_toml_failed", path=str(path), error=str(exc))
        return {}

    def _get_http_client(self) -> Any:
        """Lazily create an httpx.AsyncClient for real provider calls.

        Returns the client; callers must use it as an async context manager
        or call aclose() when done.
        """
        if self._http_client is None:
            try:
                import httpx
            except ImportError:
                raise RuntimeError(
                    "httpx is required for real model calls but is not installed. Install it with: pip install httpx",
                )
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    async def close(self) -> None:
        """Close the httpx client if it was created."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Configuration resolution with environment variable fallbacks
    # ------------------------------------------------------------------

    def set_runtime_override(self, slot_name: str, field: str, value: str) -> None:
        """Set an in-memory runtime override for a slot field.

        Runtime overrides have the **highest** priority in ``_resolve_slot_config``,
        superseding both environment variables and TOML config. They are never
        written to ``os.environ``, keeping API keys out of the process environment.

        Args:
            slot_name: e.g. "synthesis", "embedding"
            field: one of "model", "api_key", "base_url", "provider"
            value: the override value
        """
        key = f"{slot_name}.{field}"
        self._runtime_overrides[key] = value
        log.info(
            "slot_runtime_override_set",
            slot=slot_name,
            field=field,
            has_value=bool(value),
        )

    def get_runtime_override(self, slot_name: str, field: str) -> str | None:
        """Get the current runtime override for a slot field, or None."""
        return self._runtime_overrides.get(f"{slot_name}.{field}")

    def clear_runtime_overrides(self, slot_name: str | None = None) -> None:
        """Clear runtime overrides, either for one slot or all slots.

        Args:
            slot_name: if given, clear only overrides for that slot; otherwise clear all.
        """
        if slot_name is None:
            self._runtime_overrides.clear()
        else:
            prefix = f"{slot_name}."
            self._runtime_overrides = {k: v for k, v in self._runtime_overrides.items() if not k.startswith(prefix)}

    def _resolve_slot_config(self, slot_name: str) -> dict:
        """Resolve slot config with priority: runtime overrides > env vars > TOML config.

        Runtime overrides (in-memory) have the highest priority, ensuring
        that API keys set via the admin API are never leaked to os.environ.
        Environment variables are read (not written) for startup-time config.
        """
        slot_cfg = self._models.get(slot_name, {})
        if not isinstance(slot_cfg, dict):
            slot_cfg = {}

        log.debug(
            "slot_config_resolved",
            slot=slot_name,
            has_cfg=bool(slot_cfg),
            cfg_keys=list(slot_cfg.keys()) if isinstance(slot_cfg, dict) else [],
        )

        # 1. Runtime overrides (highest priority — in-memory, never in os.environ)
        rt_model = self._runtime_overrides.get(f"{slot_name}.model")
        rt_api_key = self._runtime_overrides.get(f"{slot_name}.api_key")
        rt_base_url = self._runtime_overrides.get(f"{slot_name}.base_url")
        rt_provider = self._runtime_overrides.get(f"{slot_name}.provider")

        # 2. Environment variables (read-only — set before process start)
        env_prefix = f"AIP_{slot_name.upper()}_"
        env_base_url = os.environ.get(f"{env_prefix}BASE_URL")
        env_model = os.environ.get(f"{env_prefix}MODEL")
        env_provider = os.environ.get(f"{env_prefix}PROVIDER")
        env_api_key = os.environ.get(f"{env_prefix}API_KEY")

        # 3. Merge: runtime > env > TOML config
        resolved = {
            "provider": rt_provider or env_provider or slot_cfg.get("provider", PROVIDER_OPENAI_COMPATIBLE),
            "model": rt_model or env_model or slot_cfg.get("model", f"<{slot_name}>"),
            "base_url": rt_base_url or env_base_url or slot_cfg.get("base_url"),
            "api_key": rt_api_key or env_api_key or slot_cfg.get("api_key"),
            "fallback_provider": slot_cfg.get("fallback_provider"),
            "fallback_model": slot_cfg.get("fallback_model"),
            "dimensions": slot_cfg.get("dimensions"),
        }

        # Apply global defaults if still missing
        if not resolved["base_url"]:
            if resolved["provider"] == PROVIDER_OLLAMA:
                resolved["base_url"] = os.environ.get("AIP_OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL)
            elif resolved["provider"] == PROVIDER_OPENAI_COMPATIBLE:
                resolved["base_url"] = os.environ.get("AIP_OPENAI_BASE_URL", "https://api.openai.com")

        if not resolved["api_key"] and resolved["provider"] == PROVIDER_OPENAI_COMPATIBLE:
            resolved["api_key"] = os.environ.get("AIP_OPENAI_API_KEY")

        return resolved

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, slot_name: str) -> ModelSlotConfig:
        """Resolve a slot name to a ModelSlotConfig."""
        if slot_name not in self._models:
            raise ValueError(f"Unknown model slot: {slot_name}")

        slot_cfg = self._models[slot_name]
        # Filter: only process dict values (skip non-dict like ci_mode flag)
        if not isinstance(slot_cfg, dict):
            raise ValueError(f"Unknown model slot: {slot_name}")

        # Merge with env var overrides for the returned config
        resolved = self._resolve_slot_config(slot_name)

        log.info(
            f"slot {slot_name}: provider={resolved['provider']} "
            f"model={resolved.get('model')} base_url={resolved.get('base_url')}"
        )

        return ModelSlotConfig(
            slot_name=slot_name,
            provider=resolved["provider"],
            model=resolved["model"],
            base_url=resolved["base_url"],
            fallback_provider=resolved.get("fallback_provider"),
            fallback_model=resolved.get("fallback_model"),
            dimensions=resolved.get("dimensions"),
        )

    def list_slots(self) -> list[str]:
        """List all configured slot names (excluding non-dict entries like ci_mode)."""
        return [k for k, v in self._models.items() if isinstance(v, dict)]

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        """Execute a model call for the given slot.

        In ci_mode: returns a deterministic fixture (no network).
        Otherwise: dispatches to the appropriate provider via HTTP.

        Returns a dict with: content, model, usage, latency_ms, cost_usd.
        On provider failure, returns an error dict with content set to an
        error message and error=True, rather than raising.
        """
        # Guard: reject unknown slot names early with a clear error.
        # A slot name like "meta-llama/llama-4-maverick" is a model ID,
        # not a slot — the caller likely passed the model ID by mistake.
        if slot_name not in self._models or not isinstance(self._models.get(slot_name), dict):
            known = [k for k, v in self._models.items() if isinstance(v, dict)]
            return {
                "content": "",
                "model": slot_name,
                "usage": {},
                "latency_ms": 0,
                "cost_usd": 0.0,
                "error": True,
                "error_message": (
                    f"Unknown model slot '{slot_name}'. "
                    f"Did you pass a model ID instead of a slot name? "
                    f"Known slots: {known}"
                ),
            }

        resolved = self._resolve_slot_config(slot_name)
        provider = resolved["provider"]
        model = resolved["model"]
        base_url = resolved["base_url"]

        if self._ci_mode:
            # Deterministic fixture — content is a hash of the input for reproducibility.
            # Slot-specific fixtures: some slots have a documented JSON output contract
            # that callers parse with json.loads(). Returning plain text for those slots
            # breaks the contract and forces the caller into a fallback path.
            # Currently slot-specific: "evaluation" → Aristotle EXAMINER's evaluate()
            # expects {"score": float, "mastery_achieved": bool, "feedback": str,
            # "diagnosis": dict | None}. Phase B.5 (ADR-002 §3) added the diagnosis
            # field — it's a dict when the answer is wrong, None when correct.
            # The CI fixture returns the mastered/correct case (score=0.9,
            # mastery_achieved=True, diagnosis=None) so CI-mode dogfood runs
            # exercise the mastered=True branch (without this, ARISTOTLE-DEBT-010 —
            # the learner can never master anything in CI mode). Other slots keep
            # the plain-text fixture (their callers don't parse JSON, or they have
            # robust extraction like Sexton/Beast's find-first-bracket logic).
            prompt = messages[-1]["content"] if messages else ""
            if slot_name == "evaluation":
                import json as _json
                content = _json.dumps({
                    "score": 0.9,
                    "mastery_achieved": True,
                    "feedback": (
                        f"[CI-FIXTURE for {slot_name}] {prompt[:60]}..."
                    ),
                    # Phase B.5: diagnosis is None when mastery_achieved is True.
                    # The CI fixture is deterministic (always the mastered case),
                    # so diagnosis is always None here. Unit tests that need the
                    # wrong-answer path use _FakeModelProvider with explicit
                    # JSON responses including a diagnosis dict.
                    "diagnosis": None,
                })
            else:
                content = f"[CI-FIXTURE for {slot_name}] {prompt[:80]}..."
            log.debug("ci_fixture_response", slot=slot_name)
            return {
                "content": content,
                "model": model,
                "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 50},
                "latency_ms": 5,
                "cost_usd": 0.0,
            }

        # Guard: refuse to send sentinel model names (e.g. "<synthesis>") to any API.
        # These are placeholder defaults from _resolve_slot_config when a slot has
        # no model configured — they are never valid API model identifiers.
        if model.startswith("<") and model.endswith(">"):
            return {
                "content": "",
                "model": model,
                "usage": {},
                "latency_ms": 0,
                "cost_usd": 0.0,
                "error": True,
                "error_message": (
                    f"Slot '{slot_name}' has no model configured (resolved to sentinel '{model}'). "
                    f"Set a model via config/aip.config.toml [models.{slot_name}] or "
                    f"the AIP_{slot_name.upper()}_MODEL environment variable."
                ),
            }

        # Real dispatch
        start = time.perf_counter()
        try:
            if provider == PROVIDER_OLLAMA:
                result = await self._call_ollama(base_url, model, messages, **kwargs)
            elif provider == PROVIDER_OPENAI_COMPATIBLE:
                result = await self._call_openai_compatible(
                    base_url,
                    model,
                    resolved.get("api_key"),
                    messages,
                    **kwargs,
                )
            else:
                raise ValueError(
                    f"Unsupported provider '{provider}' for slot '{slot_name}'. "
                    f"Supported providers: {PROVIDER_OLLAMA}, {PROVIDER_OPENAI_COMPATIBLE}",
                )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log.error(
                "model_call_failed",
                slot=slot_name,
                provider=provider,
                model=model,
                error=str(exc),
                exc_info=True,
            )
            # Return a structured error result instead of raising
            return {
                "content": "",
                "model": model,
                "usage": {},
                "latency_ms": elapsed_ms,
                "cost_usd": 0.0,
                "error": True,
                "error_message": (
                    f"Model call failed for slot '{slot_name}' (provider={provider}, model={model}): {exc}"
                ),
            }

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # Ensure result has required keys
        result.setdefault("model", model)
        result.setdefault("usage", {})
        result.setdefault("latency_ms", elapsed_ms)
        result.setdefault("cost_usd", 0.0)
        result.setdefault("error", False)

        usage = result.get("usage", {})
        log.info(
            "model_call_complete",
            slot=slot_name,
            provider=provider,
            model=model,
            latency_ms=elapsed_ms,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cost_usd=result.get("cost_usd", 0.0),
        )

        return result

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    async def _call_ollama(
        self,
        base_url: str,
        model: str,
        messages: list[dict],
        **kwargs: Any,
    ) -> dict:
        """Call an Ollama /api/chat endpoint.

        Ollama chat API: POST /api/chat with {model, messages, stream: false, options: {...}}.
        Returns the assistant message content and usage stats.
        """
        client = self._get_http_client()

        url = f"{base_url.rstrip('/')}/api/chat"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        # Map common kwargs to Ollama options
        options: dict[str, Any] = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs["temperature"]
        if "num_predict" in kwargs:
            options["num_predict"] = kwargs["num_predict"]
        elif "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        if "top_p" in kwargs:
            options["top_p"] = kwargs["top_p"]
        if options:
            payload["options"] = options

        log.debug("provider_dispatch", provider="ollama", url=url, model=model)
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        # Extract content from Ollama response
        content = ""
        message = data.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
        elif isinstance(message, str):
            content = message

        # Ollama returns eval_count / prompt_eval_count for usage
        eval_count = data.get("eval_count", 0) or 0
        prompt_eval_count = data.get("prompt_eval_count", 0) or 0

        return {
            "content": content,
            "model": data.get("model", model),
            "usage": {
                "prompt_tokens": prompt_eval_count,
                "completion_tokens": eval_count,
                "total_tokens": prompt_eval_count + eval_count,
            },
        }

    async def _call_openai_compatible(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        messages: list[dict],
        **kwargs: Any,
    ) -> dict:
        """Call an OpenAI-compatible /v1/chat/completions endpoint.

        Works with OpenAI, DeepSeek, Together, Fireworks, and any provider
        that implements the OpenAI chat completions API.
        """
        client = self._get_http_client()

        url = f"{base_url.rstrip('/')}/v1/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]

        log.debug("provider_dispatch", provider="openai_compatible", url=url, model=model)
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # Extract content from OpenAI-format response
        content = ""
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")

        # Extract usage from OpenAI-format response
        usage = data.get("usage", {})
        usage_data = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

        return {
            "content": content,
            "model": data.get("model", model),
            "usage": usage_data,
        }
