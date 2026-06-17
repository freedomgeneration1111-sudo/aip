"""AIP API Client — adapter layer for GUI-to-backend communication.

This module is the SOLE interface between the NiceGUI frontend and the
AIP FastAPI backend. It communicates exclusively through REST and WebSocket
endpoints — never by importing from aip.orchestration or directly accessing
AipContainer.

This follows the API-First approach agreed upon in the Phase 1 plan:
the GUI is treated like CLI/MCP surfaces, communicating via FastAPI routes
and WebSocket, not in-process container access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

# Module-level logger for the API client
log = logging.getLogger("gui.api_client")

# Default backend URL — configurable via environment variable

# Load .env file on import so AIP_OPENAI_API_KEY is available immediately.
# This MUST happen before reading any env vars.
try:
    from dotenv import load_dotenv

    _env_loaded = load_dotenv()
except ImportError:
    _env_loaded = False

AIP_BACKEND_URL = os.getenv("AIP_BACKEND_URL", "http://127.0.0.1:8000")


class AipApiClient:
    """HTTP + WebSocket client for communicating with the AIP FastAPI backend.

    All GUI components should use this client rather than making direct
    HTTP calls to Ollama or OpenRouter. The backend handles model routing,
    session management, and actor dispatch.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or AIP_BACKEND_URL).rstrip("/")
        self._http_client: httpx.AsyncClient | None = None
        self._ws_session_id: str | None = None
        self._openrouter_api_key: str | None = None

    # ------------------------------------------------------------------
    # HTTP Client Management
    # ------------------------------------------------------------------

    def _get_http_client(self) -> httpx.AsyncClient:
        """Lazily create and reuse an httpx.AsyncClient."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def check_health(self) -> dict[str, Any]:
        """Check if the AIP backend is reachable and healthy.

        Returns the health response dict, or raises on connection failure.
        Used for graceful degradation in the GUI.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/health", timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    async def is_backend_reachable(self) -> bool:
        """Quick check if the backend is reachable (non-throwing)."""
        try:
            await self.check_health()
            return True
        except Exception:
            return False

    async def check_api_key_status(self) -> dict[str, Any]:
        """Check whether the backend has a valid API key configured.

        Calls GET /api/v1/models/api_key_status which inspects the
        actual resolved configuration (env vars + TOML config) on the
        backend, not just the local process env var.

        Returns dict with 'has_any_key' (bool) and 'slots' (dict of
        slot_name -> {has_key, provider}).
        """
        client = self._get_http_client()
        resp = await client.get(
            f"{self.base_url}/api/v1/models/api_key_status",
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Model Slots
    # ------------------------------------------------------------------

    async def list_model_slots(self) -> list[dict[str, Any]]:
        """Fetch available model slots from the backend.

        Returns a list of slot info dicts with:
          slot_name, provider, model, base_url, has_fallback, ...
        This replaces the old direct read of enabled_models.json.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/models/slots", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        return data.get("slots", [])

    async def list_text_generation_slots(self) -> dict[str, Any]:
        """Fetch text-generation model slots from GET /api/v1/models/text-generation-slots.

        Returns only slots suitable for text generation (excludes embedding).
        Used by the Model Council panel to populate the slot selector.

        Returns a dict with:
          slots: list of slot info dicts with slot_name, provider, model, has_real_model
          ci_mode: bool
          sufficient_for_council: bool — True if at least 2 text-gen slots
        Never exposes secrets.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/models/text-generation-slots",
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("text_generation_slots_fetch_failed: %s", exc)
            return {"slots": [], "ci_mode": False, "sufficient_for_council": False, "error": str(exc)}

    # Compatibility alias — some callers use get_ prefix
    async def get_text_generation_slots(self) -> dict[str, Any]:
        """Alias for list_text_generation_slots()."""
        return await self.list_text_generation_slots()

    async def list_model_library(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """Fetch model library from GET /api/v1/models/library.

        Returns a list of model dicts from the enabled_models table.
        If enabled_only=True, filters to models where enabled=1.
        Each dict has: model_id, display_name, provider, cost_input/output_per_million,
        context_length, supports_vision, supports_tools, enabled, etc.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/models/library", timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if enabled_only:
                return [m for m in items if m.get("enabled") == 1]
            return items
        except Exception as exc:
            log.warning("model_library_fetch_failed: %s", exc)
            return []

    async def fetch_model_library(self) -> dict[str, Any]:
        """Fetch models from OpenRouter and upsert into the model library.

        Calls POST /api/v1/models/library/fetch (requires DEFINER auth).
        Returns a dict with fetched count, new_models_added, and last_fetched.
        Honors AIP-G-09: the OpenRouter fetch is the ONLY outbound call,
        and it is user-triggered only.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/models/library/fetch",
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.error("model_library_fetch_failed: %s", exc)
            return {"error": str(exc), "fetched": 0, "new_models_added": 0}

    async def toggle_model_enabled(self, model_id: str, enabled: bool) -> dict[str, Any]:
        """Toggle a model's enabled flag in the library.

        Calls PATCH /api/v1/models/library (requires DEFINER auth).
        Uses body-based route so model IDs containing '/' (e.g.
        "deepseek/deepseek-v4-flash:free") are transmitted safely in
        JSON rather than as URL path segments.
        Returns the updated model dict or an error dict.
        """
        client = self._get_http_client()
        try:
            resp = await client.patch(
                f"{self.base_url}/api/v1/models/library",
                json={"model_id": model_id, "enabled": 1 if enabled else 0},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.error("toggle_model_enabled_failed: %s", exc)
            return {"error": str(exc)}

    async def beast_scan(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Fire Beast corpus scan via GET /api/v1/beast/scan.

        Per AIP_UNIFIED_CHAT_SPEC §Beast Pane: non-blocking, fires AFTER
        the chat response in bare mode. Returns scan results or error dict.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/beast/scan",
                params={"query": query, "limit": limit},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("beast_scan_failed: %s", exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        role: str | None = None,
        model_slot: str = "synthesis",
        mode: str = "normal",
        project_id: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Create a new chat session via POST /api/v1/sessions.

        Returns session metadata including the session_id needed for
        WebSocket communication.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "model_slot": model_slot,
            "mode": mode,
        }
        if role:
            payload["role"] = role
        if project_id:
            payload["project_id"] = project_id
        if domain:
            payload["domain"] = domain

        resp = await client.post(f"{self.base_url}/api/v1/sessions", json=payload, timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Get session details."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_session_context(self, session_id: str) -> dict[str, Any]:
        """Get session context (turn count, context window estimate)."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/sessions/{session_id}/context")
        resp.raise_for_status()
        return resp.json()

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update session metadata via PATCH /api/v1/sessions/{session_id}.

        Used to toggle auto_save, change mode, update role, etc.
        Returns the updated session metadata.
        """
        client = self._get_http_client()
        resp = await client.patch(f"{self.base_url}/api/v1/sessions/{session_id}", json=updates)
        resp.raise_for_status()
        return resp.json()

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        """Delete a session via DELETE /api/v1/sessions/{session_id}."""
        client = self._get_http_client()
        resp = await client.delete(f"{self.base_url}/api/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    async def list_projects(self) -> list[dict[str, Any]]:
        """Fetch all projects via GET /api/v1/projects.

        Returns a list of project dicts with at least:
          project_id, name, domain
        Used by the AUGMENTED panel to resolve a valid project_name
        for the /api/v1/ask endpoint.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/projects", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        # API returns {"projects": [...]}
        if isinstance(data, dict):
            return data.get("projects", [])
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------
    # Review Queue
    # ------------------------------------------------------------------

    async def list_pending_reviews(self) -> list[dict[str, Any]]:
        """Fetch pending items from the review queue."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/reviews")
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest_conversation(
        self,
        conversation_id: str,
        turns: list[dict[str, str]],
        *,
        title: str | None = None,
        domain: str = "chat",
        source_format: str = "plaintext",
    ) -> dict[str, Any]:
        """Ingest a conversation via POST /api/v1/ingest/conversation.

        Args:
            conversation_id: Unique ID for this conversation.
            turns: List of dicts with 'role', 'content', optional 'timestamp'.
            title: Optional conversation title.
            domain: Domain for indexing (default: "chat").
            source_format: Source format hint (default: "plaintext").

        Returns an IngestionResult summary dict.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "turns": turns,
            "domain": domain,
            "source_format": source_format,
        }
        if title:
            payload["title"] = title

        resp = await client.post(f"{self.base_url}/api/v1/ingest/conversation", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def ingest_file(
        self,
        path: str,
        *,
        domain: str = "imported",
        source_format: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a conversation file via POST /api/v1/ingest/file.

        Args:
            path: File path to ingest.
            domain: Domain for indexing (default: "imported").
            source_format: Optional format override.

        Returns a dict with list of IngestionResult summaries.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "path": path,
            "domain": domain,
        }
        if source_format:
            payload["source_format"] = source_format

        resp = await client.post(f"{self.base_url}/api/v1/ingest/file", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Actor Status
    # ------------------------------------------------------------------

    async def get_actors_status(self) -> dict[str, Any]:
        """Fetch status of all actors from the backend.

        Returns dict with 'actors' key containing Beast, Vigil, Sexton status.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/actors/status")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Status Summary (UI Cycle 3 — consolidated dashboard endpoint)
    # ------------------------------------------------------------------

    async def get_status_summary(self) -> dict[str, Any]:
        """Fetch the consolidated status summary for the Operator Console Dashboard.

        Calls GET /api/v1/status/summary which aggregates all subsystem health
        into a single stable, secret-safe response. This is the primary data
        source for the dashboard cards and right rail.

        Returns a dict with keys:
          dogfood_mode, backend_health, actor_status_summary,
          retrieval_health_summary, corpus_summary,
          embedding_backfill_summary, review_queue_summary,
          wiki_summary, model_slot_summary, warnings, recent_activity

        Never exposes secrets. Missing subsystems reported honestly.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/status/summary",
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("status_summary_fetch_failed: %s", exc)
            return {}

    async def trigger_actor_cycle(self, actor_name: str) -> dict[str, Any]:
        """Manually trigger an actor cycle (admin/debug)."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/actors/{actor_name}/trigger")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # WebSocket Chat
    # ------------------------------------------------------------------

    async def chat_via_websocket(
        self,
        session_id: str,
        message: str,
        on_response: Any = None,
        on_error: Any = None,
        on_gate: Any = None,
        model_slot: str | None = None,
    ) -> None:
        """Send a chat message via WebSocket and handle the streaming response.

        Connects to ws://<backend>/api/v1/chat/<session_id> and sends
        a JSON message. Calls the appropriate callback for each response type:
          - on_response(response_dict) — normal chat response
          - on_error(error_dict) — error from the backend
          - on_gate(gate_dict) — DEFINER gate prompt requiring approval

        Args:
            session_id: The session ID obtained from create_session().
            message: The user's chat message text.
            on_response: Callback for normal responses.
            on_error: Callback for error responses.
            on_gate: Callback for gate (DEFINER review) prompts.
            model_slot: Optional per-message model slot override.
        """
        # Build WebSocket URL
        ws_base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_base}/api/v1/chat/{session_id}"
        log.info("chat_ws: connecting to %s (slot=%s)", ws_url, model_slot)

        try:
            import websockets

            async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
                # Send the user message
                msg_payload: dict[str, Any] = {
                    "type": "message",
                    "content": message,
                }
                if model_slot:
                    msg_payload["model_slot"] = model_slot

                await ws.send(json.dumps(msg_payload))
                log.info("chat_ws: sent message (len=%d)", len(message))

                # Wait for response(s) with a timeout for the model call
                # The backend may send multiple messages (gate then response)
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    except asyncio.TimeoutError:
                        log.error("chat_ws: timed out waiting for response (120s)")
                        if on_error:
                            on_error(
                                {
                                    "type": "error",
                                    "content": "Model call timed out (120s). The model may be unavailable.",
                                }
                            )
                        break
                    try:
                        resp = json.loads(raw)
                    except json.JSONDecodeError:
                        log.error("chat_ws: invalid JSON from backend")
                        if on_error:
                            on_error({"type": "error", "content": "Invalid JSON from backend"})
                        break

                    resp_type = resp.get("type", "")
                    log.info("chat_ws: received type=%s", resp_type)

                    if resp_type == "response":
                        if on_response:
                            on_response(resp)
                        break  # Got the final response — done with this message

                    elif resp_type == "gate":
                        if on_gate:
                            on_gate(resp)
                        # Don't break — wait for the gate_response flow
                        # The GUI will handle this separately

                    elif resp_type == "error":
                        if on_error:
                            on_error(resp)
                        break

                    elif resp_type == "pong":
                        # Keepalive response — ignore
                        continue

                    else:
                        log.warning("chat_ws: unknown response type=%s", resp_type)
                        if on_error:
                            on_error(resp)
                        break

        except ImportError:
            # websockets is a required dependency — fail honestly
            log.error("chat_ws: 'websockets' package is not installed; cannot connect")
            if on_error:
                on_error(
                    {
                        "type": "error",
                        "content": "WebSocket chat unavailable: 'websockets' package is not installed. "
                        "Install it with: pip install websockets",
                    }
                )
        except Exception as exc:
            log.error("chat_ws: connection failed: %s", exc)
            if on_error:
                on_error({"type": "error", "content": f"WebSocket connection failed: {exc}"})

    async def send_gate_response(
        self,
        session_id: str,
        approved: bool,
    ) -> dict[str, Any]:
        """Send a gate approval/rejection response via WebSocket.

        Called when the user responds to a DEFINER gate prompt.
        """
        ws_base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_base}/api/v1/chat/{session_id}"

        try:
            import websockets

            async with websockets.connect(ws_url) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "gate_response",
                            "approved": approved,
                        }
                    )
                )
                # Wait for the resumed response
                raw = await ws.recv()
                return json.loads(raw)
        except ImportError:
            # websockets is a required dependency — fail honestly
            log.error("send_gate_response: 'websockets' package is not installed")
            return {
                "type": "error",
                "content": "Gate response unavailable: 'websockets' package is not installed. "
                "Install it with: pip install websockets",
            }
        except Exception as exc:
            return {"type": "error", "content": f"Gate response failed: {exc}"}

    # ------------------------------------------------------------------
    # Ask Pipeline (Knowledge Augmented Queries)
    # ------------------------------------------------------------------

    async def ask(
        self,
        question: str,
        project_name: str,
        *,
        source: str = "all",
        max_sources: int = 10,
        save_artifact: bool = False,
        model_slot: str = "synthesis",
    ) -> dict[str, Any]:
        """Submit a source-grounded ask query via POST /api/v1/ask.

        Returns AskResult dict with status, answer, sources, and metadata.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "question": question,
            "project_name": project_name,
            "source": source,
            "max_sources": max_sources,
            "save_artifact": save_artifact,
            "model_slot": model_slot,
        }
        resp = await client.post(f"{self.base_url}/api/v1/ask", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def augmented_ask(
        self,
        query: str,
        *,
        project_name: str = "default",
        source: str = "all",
        max_sources: int = 10,
        model_slot: str = "synthesis",
        system_prompt_modifier: str = "",
    ) -> dict[str, Any]:
        """Submit a knowledge-augmented query via POST /api/v1/ask.

        Convenience wrapper for the ask endpoint used by the AUGMENTED
        chat panel.  The backend requires ``question`` and ``project_name``
        as required fields; this method maps the GUI's ``query`` param
        to the backend's ``question`` field and defaults project_name
        to "default" so callers don't need to know the schema.

        The system_prompt_modifier is prepended to the synthesis system
        prompt by the backend (per AIP_UNIFIED_CHAT_SPEC §Chat Mode Picker).

        Returns the raw AskResult dict: status, answer, sources (list of
        {source_id, source_type, title, score, content_snippet, domain,
        metadata}), model_slot, model_provider, artifact_id, errors, etc.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "question": query,
            "project_name": project_name,
            "source": source,
            "max_sources": max_sources,
            "model_slot": model_slot,
        }
        if system_prompt_modifier:
            payload["system_prompt_modifier"] = system_prompt_modifier
        resp = await client.post(
            f"{self.base_url}/api/v1/ask",
            json=payload,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def ask_retrieve(
        self,
        question: str,
        *,
        domain: str | None = None,
        project_name: str | None = None,
        source: str = "all",
        max_sources: int = 20,
    ) -> dict[str, Any]:
        """Retrieve sources for a query without generating an answer.

        Uses POST /api/v1/ask/retrieve. Returns matching sources from
        LexicalStore + VectorStore without dispatching to a model.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "question": question,
            "source": source,
            "max_sources": max_sources,
        }
        if domain:
            payload["domain"] = domain
        if project_name:
            payload["project_name"] = project_name
        resp = await client.post(f"{self.base_url}/api/v1/ask/retrieve", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Memory Search (Vector + Lexical)
    # ------------------------------------------------------------------

    async def search_memory(self, q: str) -> dict[str, Any]:
        """Hybrid search via GET /api/v1/memory/search.

        Returns lexical + vector results for the query.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/memory/search", params={"q": q})
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Knowledge Browser
    # ------------------------------------------------------------------

    async def list_knowledge(
        self,
        *,
        domain: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """List compiled knowledge items via GET /api/v1/knowledge."""
        client = self._get_http_client()
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        if state:
            params["state"] = state
        resp = await client.get(f"{self.base_url}/api/v1/knowledge", params=params)
        resp.raise_for_status()
        return resp.json()

    async def list_wiki_articles(
        self,
        *,
        state: str | None = None,
        domain: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """List wiki articles from artifacts + ecs_state via GET /api/v1/wiki/articles.

        UI Cycle 7: Enhanced with search filter support.
        """
        client = self._get_http_client()
        params: dict[str, str] = {}
        if state:
            params["state"] = state
        if domain:
            params["domain"] = domain
        if search:
            params["search"] = search
        resp = await client.get(f"{self.base_url}/api/v1/wiki/articles", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_wiki_article(self, article_id: str) -> dict[str, Any]:
        """Get a single wiki article by ID via GET /api/v1/wiki/articles/{id}.

        UI Cycle 7: Returns full WikiArticle schema with backlinks and
        contradictions populated when available.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/wiki/articles/{article_id}")
        resp.raise_for_status()
        return resp.json()

    async def create_wiki_article(
        self,
        *,
        title: str,
        domain: str = "",
        summary: str = "",
        body: str = "",
        tags: list[str] | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new wiki article via POST /api/v1/wiki/articles.

        UI Cycle 7: Explicit DEFINER action. Article is created as GENERATED
        state — never auto-approved. Requires separate review/approve.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "title": title,
            "domain": domain,
            "summary": summary,
            "body": body,
        }
        if tags:
            payload["tags"] = tags
        if aliases:
            payload["aliases"] = aliases
        resp = await client.post(f"{self.base_url}/api/v1/wiki/articles", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def update_wiki_article(
        self,
        article_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update a wiki article via PATCH /api/v1/wiki/articles/{id}.

        UI Cycle 7: Explicit DEFINER action. Creates a new version but does
        NOT change ECS state. Separate review/approve required.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if summary is not None:
            payload["summary"] = summary
        if body is not None:
            payload["body"] = body
        if tags is not None:
            payload["tags"] = tags
        if aliases is not None:
            payload["aliases"] = aliases
        resp = await client.patch(f"{self.base_url}/api/v1/wiki/articles/{article_id}", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_wiki_backlinks(self, article_id: str) -> dict[str, Any]:
        """Get backlinks for a wiki article via GET /api/v1/wiki/backlinks/{id}.

        UI Cycle 7: Returns empty list honestly if graph_edges not available.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/wiki/backlinks/{article_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_wiki_stats(self) -> dict[str, Any]:
        """Get wiki statistics via GET /api/v1/wiki/stats."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/wiki/stats")
        resp.raise_for_status()
        return resp.json()

    async def get_wiki_stale(self) -> dict[str, Any]:
        """Get stale wiki articles via GET /api/v1/wiki/stale.

        UI Cycle 7: Returns empty list honestly if CODEX tables not available.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/wiki/stale")
        resp.raise_for_status()
        return resp.json()

    async def get_wiki_contradictions(self) -> dict[str, Any]:
        """Get wiki contradictions via GET /api/v1/wiki/contradictions.

        UI Cycle 7: Contradictions are never auto-resolved. Returns empty
        list honestly if CODEX tables not available.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/wiki/contradictions")
        resp.raise_for_status()
        return resp.json()

    async def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        """Get a specific knowledge item via GET /api/v1/knowledge/{id}."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/knowledge/{knowledge_id}")
        resp.raise_for_status()
        return resp.json()

    async def search_knowledge(self, q: str, *, domain: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Search compiled knowledge via GET /api/v1/knowledge/search."""
        client = self._get_http_client()
        params: dict[str, Any] = {"q": q, "limit": limit}
        if domain:
            params["domain"] = domain
        resp = await client.get(f"{self.base_url}/api/v1/knowledge/search", params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # ECS Graph
    # ------------------------------------------------------------------

    async def get_ecs_graph(self) -> dict[str, Any]:
        """Get ECS state graph + artifact distribution via GET /api/v1/ecs/graph."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/ecs/graph")
        resp.raise_for_status()
        return resp.json()

    async def get_ecs_artifact(self, artifact_id: str) -> dict[str, Any]:
        """Get ECS state + history for an artifact via GET /api/v1/ecs/artifacts/{id}."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/ecs/artifacts/{artifact_id}")
        resp.raise_for_status()
        return resp.json()

    async def list_ecs_artifacts(self, *, state: str | None = None) -> dict[str, Any]:
        """List ECS artifacts via GET /api/v1/ecs/artifacts."""
        client = self._get_http_client()
        params: dict[str, str] = {}
        if state:
            params["state"] = state
        resp = await client.get(f"{self.base_url}/api/v1/ecs/artifacts", params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Sources Browser
    # ------------------------------------------------------------------

    async def list_sources(
        self,
        *,
        domain: str | None = None,
        source_type: str | None = None,
    ) -> dict[str, Any]:
        """List indexed sources via GET /api/v1/sources."""
        client = self._get_http_client()
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        if source_type:
            params["source_type"] = source_type
        resp = await client.get(f"{self.base_url}/api/v1/sources", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_sources_stats(self) -> dict[str, Any]:
        """Get aggregate source statistics via GET /api/v1/sources/stats."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/sources/stats")
        resp.raise_for_status()
        return resp.json()

    async def get_corpus_stats(self) -> dict[str, Any]:
        """Get corpus turn statistics via GET /api/v1/corpus/stats."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/corpus/stats")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Budget Status
    # ------------------------------------------------------------------

    async def get_budget_status(self, scope: str = "session", scope_id: str = "default") -> dict[str, Any]:
        """Get budget status for a scope via GET /api/v1/admin/budget.

        Scopes: session, project, daily. The scope_id identifies the specific
        session, project, or day (ISO date string for daily).
        Returns consumed_tokens, limit, remaining, fraction_used, etc.
        """
        client = self._get_http_client()
        resp = await client.get(
            f"{self.base_url}/api/v1/admin/budget",
            params={"scope": scope, "scope_id": scope_id},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Review Queue
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Direct OpenRouter Chat (fallback when backend is unreachable)
    # ------------------------------------------------------------------

    async def chat_direct_openrouter(
        self,
        model: str,
        messages: list[dict[str, str]],
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat completion directly to OpenRouter API.

        This is used as a fallback when the AIP backend is not running.
        The GUI still prefers the backend (which handles sessions, auto-save,
        actors, etc.) but can fall back to direct OpenRouter calls for basic chat.

        Returns a dict with: content, model, tokens_used, latency_ms.
        """
        key = api_key or self.get_openrouter_api_key()
        if not key:
            return {"error": True, "content": "No OpenRouter API key set. Go to Models page to enter one."}

        log.info("direct_openrouter: calling model=%s", model)
        client = self._get_http_client()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8080",
            "X-Title": "AIP_Brain",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
        }

        import time

        start = time.monotonic()
        try:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60.0,
            )
            latency_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code != 200:
                error_detail = resp.text[:500]
                log.error("direct_openrouter: API error status=%d detail=%s", resp.status_code, error_detail[:200])
                return {"error": True, "content": f"OpenRouter API error ({resp.status_code}): {error_detail}"}

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            usage = data.get("usage", {})
            model_used = data.get("model", model)
            tokens_used = usage.get("total_tokens", 0)

            log.info("direct_openrouter: success model=%s tokens=%d latency=%dms", model_used, tokens_used, latency_ms)

            return {
                "error": False,
                "content": content,
                "model": model_used,
                "tokens_used": tokens_used,
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            log.error("direct_openrouter: request failed: %s", exc)
            return {"error": True, "content": f"OpenRouter request failed: {exc}"}

    # ------------------------------------------------------------------
    # OpenRouter Model Catalog
    # ------------------------------------------------------------------

    async def list_openrouter_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Fetch available text models from OpenRouter API.

        Calls GET https://openrouter.ai/api/v1/models with optional auth.
        Returns a list of model dicts with id, name, pricing, context_length, etc.
        Filters to chat/completion models only (excludes embedding/image).
        """
        client = self._get_http_client()
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        models = data.get("data", [])
        # Filter to text/chat models only
        text_models = []
        for m in models:
            mid = m.get("id", "")
            # Skip non-text models (image gen, TTS, etc.)
            if any(
                skip in mid.lower() for skip in ["image", "dall", "tts", "whisper", "stable-diffusion", "midjourney"]
            ):
                continue
            # Only include models with text output modality
            arch = m.get("architecture", {})
            modality = arch.get("modality", "")
            if modality == "text->image" or modality == "image->image":
                continue
            text_models.append(m)
        return text_models

    async def list_openrouter_embedding_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Fetch embedding models from OpenRouter API.

        Calls GET https://openrouter.ai/api/v1/models?output_modalities=embeddings
        Returns a list of model dicts with id, name, pricing, context_length, etc.
        """
        client = self._get_http_client()
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            params={"output_modalities": "embeddings"},
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])

    # ------------------------------------------------------------------
    # OpenRouter API Key Management
    # ------------------------------------------------------------------

    def get_openrouter_api_key(self) -> str | None:
        """Get the stored OpenRouter API key.

        Priority: 1) in-memory key, 2) AIP_OPENAI_API_KEY env var.
        """
        if self._openrouter_api_key:
            return self._openrouter_api_key
        return os.environ.get("AIP_OPENAI_API_KEY")

    def set_openrouter_api_key(self, key: str) -> None:
        """Store the OpenRouter API key in memory only.

        The key is passed to the backend via the PATCH /models/slots API
        (which uses in-memory runtime overrides), so there is no need
        to write it to os.environ. Keeping secrets out of the process
        environment prevents credential leakage to child processes and
        debugging tools.
        """
        self._openrouter_api_key = key

    def has_openrouter_api_key(self) -> bool:
        """Check if an OpenRouter API key is available."""
        key = self.get_openrouter_api_key()
        return key is not None and len(key.strip()) > 0

    async def update_slot_model(self, slot_name: str, model: str, api_key: str | None = None) -> dict[str, Any]:
        """Update the model for a slot at runtime via PATCH /api/v1/models/slots/{slot_name}/model.

        The backend stores the override in-memory (not in os.environ),
        which has the highest priority in ModelSlotResolver._resolve_slot_config().
        The change persists until the server restarts.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {"model": model}
        if api_key:
            payload["api_key"] = api_key
        resp = await client.patch(
            f"{self.base_url}/api/v1/models/slots/{slot_name}/model",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # UI Cycle 5 — Beast Counsel: Commentary endpoints
    # ------------------------------------------------------------------

    async def get_beast_commentary(self, turn_id: str, mode: str = "continuity") -> dict[str, Any]:
        """Fetch existing Beast commentary for a turn + mode.

        Calls GET /api/v1/turns/{turn_id}/beast-commentary?mode={mode}.
        Returns commentary if available, or an honest not_available/unavailable status.
        The mode parameter ensures commentary is retrieved for the correct
        mode — different modes produce distinct artifacts per turn.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/turns/{turn_id}/beast-commentary",
                params={"mode": mode},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("beast_commentary_get_failed: %s", exc)
            return {"status": "unavailable", "error": str(exc), "turn_id": turn_id, "mode": mode}

    async def run_beast_commentary(
        self,
        turn_id: str,
        *,
        session_id: str = "",
        mode: str = "continuity",
        question_text: str = "",
        answer_text: str = "",
        sources: list[dict] | None = None,
        trace_available: bool = False,
        lexical_only: bool = False,
        vector_contributed: bool = False,
    ) -> dict[str, Any]:
        """Generate Beast commentary for a turn.

        Calls POST /api/v1/turns/{turn_id}/beast-commentary/run.
        Returns commentary if generated, or an honest not_wired/error status.
        Commentary is ADVISORY ONLY — never auto-approved or auto-executed.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "session_id": session_id,
            "mode": mode,
            "question_text": question_text,
            "answer_text": answer_text,
            "sources": sources or [],
            "trace_available": trace_available,
            "lexical_only": lexical_only,
            "vector_contributed": vector_contributed,
        }
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/turns/{turn_id}/beast-commentary/run",
                json=payload,
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("beast_commentary_run_failed: %s", exc)
            return {"status": "error", "error": str(exc), "turn_id": turn_id, "mode": mode}

    # ------------------------------------------------------------------
    # UI Cycle 4 — Ask Workbench: Turn-level inspection endpoints
    # ------------------------------------------------------------------

    async def get_retrieval_trace_by_session(self, session_id: str) -> dict[str, Any]:
        """Fetch the retrieval trace for a session via GET /api/v1/retrieval/traces/session/{session_id}.

        Returns the most recent retrieval trace for the given session,
        including channel details, latency, degradation flags, and warnings.
        If no trace is found, returns {"status": "not_found", "trace": null}.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/retrieval/traces/session/{session_id}",
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trace_by_session_fetch_failed: %s", exc)
            return {"status": "error", "trace": None, "error": str(exc)}

    async def save_turn_as_artifact(
        self,
        session_id: str,
        content: str,
        *,
        title: str | None = None,
        domain: str = "chat",
    ) -> dict[str, Any]:
        """Save a chat turn as a versioned artifact via POST /api/v1/turns/save-artifact.

        Creates an artifact in GENERATED state (NOT APPROVED — requires DEFINER review).
        Returns artifact_id, ecs_state, and a message noting review is required.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "session_id": session_id,
            "content": content,
            "domain": domain,
        }
        if title:
            payload["title"] = title
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/turns/save-artifact",
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("save_turn_artifact_failed: %s", exc)
            return {"artifact_id": None, "ecs_state": None, "error": str(exc)}

    async def approve_review(self, artifact_id: str) -> dict[str, Any]:
        """Approve a review item via POST /api/v1/reviews/{id}/approve."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/reviews/{artifact_id}/approve")
        resp.raise_for_status()
        return resp.json()

    async def approve_all_reviews(self) -> dict[str, Any]:
        """Bulk approve all pending beast artifacts via POST /api/v1/reviews/approve-all."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/reviews/approve-all")
        resp.raise_for_status()
        return resp.json()

    async def trigger_backfill(
        self, domain: str | None = None, limit: int = 500, batch_size: int = 50, dry_run: bool = False
    ) -> dict[str, Any]:
        """Trigger backfill via POST /api/v1/admin/embeddings/backfill (now non-blocking)."""
        client = self._get_http_client()
        payload: dict[str, Any] = {"limit": limit, "batch_size": batch_size, "dry_run": dry_run}
        if domain:
            payload["domain"] = domain
        resp = await client.post(f"{self.base_url}/api/v1/admin/embeddings/backfill", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_backfill_status(self) -> dict[str, Any]:
        """Get backfill status via GET /api/v1/admin/embeddings/backfill/status."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/admin/embeddings/backfill/status")
        resp.raise_for_status()
        return resp.json()

    async def reject_review(self, artifact_id: str) -> dict[str, Any]:
        """Reject a review item via POST /api/v1/reviews/{id}/reject."""
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/reviews/{artifact_id}/reject")
        resp.raise_for_status()
        return resp.json()

    async def get_graph_stats(self) -> dict[str, Any]:
        """Get knowledge graph node/edge statistics via GET /api/v1/graph/stats."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/graph/stats")
        resp.raise_for_status()
        return resp.json()

    async def get_graph_node(self, node_id: str) -> dict[str, Any]:
        """Get graph node neighbors via GET /api/v1/graph/neighbors/{node_id}."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/graph/neighbors/{node_id}")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # UI Cycle 6 — Model Council: Multi-model comparison
    # ------------------------------------------------------------------

    async def run_model_council(
        self,
        prompt: str,
        *,
        turn_id: str = "",
        session_id: str = "",
        existing_answer: str = "",
        sources: list[dict] | None = None,
        selected_model_slots: list[str] | None = None,
        selected_model_ids: list[str] | None = None,
        save_as_artifact: bool = False,
        skip_default_slots: bool = False,
    ) -> dict[str, Any]:
        """Run a Model Council multi-model comparison report.

        Calls POST /api/v1/beast/compare-models.
        Returns an advisory comparison report with per-model results,
        convergence, disagreements, risks, and Beast synthesis.
        Reports are ADVISORY ONLY — never auto-approved.

        Two parallel model sources are accepted:
          - ``selected_model_slots`` — TOML slot names routed via
            ModelSlotResolver (synthesis, evaluation, beast, …)
          - ``selected_model_ids``   — OpenRouter model IDs from the
            enabled_models SQLite library (e.g.
            ``deepseek/deepseek-v4-flash:free``), routed via direct
            OpenRouter calls.
        Both lists are merged; the backend requires ≥2 usable models
        total (slots + library IDs combined).

        ``skip_default_slots`` (default ``False``): when ``True``, the
        backend will NOT fall back to ``_DEFAULT_COMPARISON_SLOTS``
        (synthesis/evaluation/beast) when ``selected_model_slots`` is
        empty — the panel is built ONLY from ``selected_model_ids``.
        This is the GUI's "models not tied to actor slots/roles" mode:
        the ``beast`` slot is used ONLY for the Judge+Synth synthesis
        stages, not as a panel model.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "prompt": prompt,
            "turn_id": turn_id,
            "session_id": session_id,
            "existing_answer": existing_answer,
            "sources": sources or [],
            "selected_model_slots": selected_model_slots or [],
            "selected_model_ids": selected_model_ids or [],
            "save_as_artifact": save_as_artifact,
            "skip_default_slots": skip_default_slots,
        }
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/beast/compare-models",
                json=payload,
                timeout=120.0,  # Multiple model calls may take time
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("model_council_run_failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # UI Cycle 8 — Crosslink System: Knowledge Links
    # ------------------------------------------------------------------

    async def list_knowledge_links(
        self,
        *,
        source_type: str | None = None,
        source_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        relation_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List knowledge links with optional filters.

        Calls GET /api/v1/links.
        Returns honest empty list if no links match or storage unavailable.
        """
        client = self._get_http_client()
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if source_type:
            params["source_type"] = source_type
        if source_id:
            params["source_id"] = source_id
        if target_type:
            params["target_type"] = target_type
        if target_id:
            params["target_id"] = target_id
        if relation_type:
            params["relation_type"] = relation_type
        if status:
            params["status"] = status
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/links",
                params=params,
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("list_knowledge_links_failed: %s", exc)
            return {
                "items": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "storage_backend": "unavailable",
                "error": str(exc),
            }

    async def create_knowledge_link(
        self,
        *,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        relation_type: str,
        confidence: float = 1.0,
        created_by: str = "definer",
        status: str = "suggested",
        approved_by_definer: bool = False,
        provenance: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a new knowledge link.

        Calls POST /api/v1/links.
        Default status is 'suggested', approved_by_definer is False.
        No linked objects are mutated. No artifacts are approved/exported.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            "relation_type": relation_type,
            "confidence": confidence,
            "created_by": created_by,
            "status": status,
            "approved_by_definer": approved_by_definer,
            "provenance": provenance,
            "notes": notes,
        }
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/links",
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("create_knowledge_link_failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def update_knowledge_link(
        self,
        link_id: str,
        *,
        relation_type: str | None = None,
        confidence: float | None = None,
        status: str | None = None,
        approved_by_definer: bool | None = None,
        provenance: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Update a knowledge link (approve, reject, edit).

        Calls PATCH /api/v1/links/{link_id}.
        Approval requires explicit approved_by_definer=True.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {}
        if relation_type is not None:
            payload["relation_type"] = relation_type
        if confidence is not None:
            payload["confidence"] = confidence
        if status is not None:
            payload["status"] = status
        if approved_by_definer is not None:
            payload["approved_by_definer"] = approved_by_definer
        if provenance is not None:
            payload["provenance"] = provenance
        if notes is not None:
            payload["notes"] = notes
        try:
            resp = await client.patch(
                f"{self.base_url}/api/v1/links/{link_id}",
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("update_knowledge_link_failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def delete_knowledge_link(self, link_id: str) -> dict[str, Any]:
        """Delete a knowledge link.

        Calls DELETE /api/v1/links/{link_id}.
        No linked objects are mutated.
        """
        client = self._get_http_client()
        try:
            resp = await client.delete(
                f"{self.base_url}/api/v1/links/{link_id}",
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("delete_knowledge_link_failed: %s", exc)
            return {"deleted": False, "error": str(exc)}

    async def get_link_backlinks(
        self,
        target_type: str,
        target_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get backlinks (links pointing TO a given object).

        Calls GET /api/v1/links/backlinks/{target_type}/{target_id}.
        Returns honest empty list if no backlinks exist or storage unavailable.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/links/backlinks/{target_type}/{target_id}",
                params={"limit": limit},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_link_backlinks_failed: %s", exc)
            return {
                "target_type": target_type,
                "target_id": target_id,
                "backlinks": [],
                "total": 0,
                "available": False,
                "storage_backend": "unavailable",
                "error": str(exc),
            }

    # ── Artifact Workbench methods (UI Cycle 9) ────────────────

    async def list_artifacts(
        self,
        *,
        ecs_state: str | None = None,
        artifact_type: str | None = None,
        created_by: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List artifacts via GET /api/v1/artifacts.

        Supports filtering by state, type, source, and search query.
        Returns honest empty list if backend unavailable.
        """
        client = self._get_http_client()
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if ecs_state is not None:
            params["ecs_state"] = ecs_state
        if artifact_type is not None:
            params["artifact_type"] = artifact_type
        if created_by is not None:
            params["created_by"] = created_by
        if search is not None:
            params["search"] = search
        resp = await client.get(f"{self.base_url}/api/v1/artifacts", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_artifact_detail(self, artifact_id: str) -> dict[str, Any]:
        """Get artifact detail via GET /api/v1/artifacts/{artifact_id}.

        Returns content, metadata, state, sources, review history, export eligibility.
        """
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/artifacts/{artifact_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_artifact_sources(self, artifact_id: str) -> dict[str, Any]:
        """Get artifact sources via GET /api/v1/artifacts/{artifact_id}/sources."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/artifacts/{artifact_id}/sources")
        resp.raise_for_status()
        return resp.json()

    async def get_artifact_reviews(self, artifact_id: str) -> dict[str, Any]:
        """Get artifact review history via GET /api/v1/artifacts/{artifact_id}/reviews."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/artifacts/{artifact_id}/reviews")
        resp.raise_for_status()
        return resp.json()

    async def approve_artifact(self, artifact_id: str) -> dict[str, Any]:
        """Approve artifact via POST /api/v1/artifacts/{artifact_id}/approve.

        Explicit DEFINER action. No auto-approve.
        """
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/artifacts/{artifact_id}/approve")
        resp.raise_for_status()
        return resp.json()

    async def reject_artifact(self, artifact_id: str, note: str = "") -> dict[str, Any]:
        """Reject artifact via POST /api/v1/artifacts/{artifact_id}/reject.

        Explicit DEFINER action. No auto-reject.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {}
        if note:
            payload["note"] = note
        resp = await client.post(
            f"{self.base_url}/api/v1/artifacts/{artifact_id}/reject",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def needs_revision_artifact(self, artifact_id: str, instruction: str = "") -> dict[str, Any]:
        """Mark artifact as needs-revision via POST /api/v1/artifacts/{artifact_id}/needs-revision.

        Explicit DEFINER action. No ECS transition — NEEDS_REVISION is a verdict.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {}
        if instruction:
            payload["instruction"] = instruction
        resp = await client.post(
            f"{self.base_url}/api/v1/artifacts/{artifact_id}/needs-revision",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def export_artifact(self, artifact_id: str) -> dict[str, Any]:
        """Export APPROVED artifact via POST /api/v1/artifacts/{artifact_id}/export.

        Only APPROVED artifacts can be exported normally.
        """
        client = self._get_http_client()
        resp = await client.post(f"{self.base_url}/api/v1/artifacts/{artifact_id}/export")
        resp.raise_for_status()
        return resp.json()

    async def force_export_artifact(self, artifact_id: str, reason: str = "") -> dict[str, Any]:
        """Force-export artifact via POST /api/v1/artifacts/{artifact_id}/force-export.

        SOVEREIGN OVERRIDE — visibly exceptional and audited.
        Requires explicit reason.
        """
        client = self._get_http_client()
        resp = await client.post(
            f"{self.base_url}/api/v1/artifacts/{artifact_id}/force-export",
            json={"force": True, "reason": reason},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_artifact_dashboard(self) -> dict[str, Any]:
        """Get artifact dashboard summary via GET /api/v1/artifacts/dashboard."""
        client = self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/v1/artifacts/dashboard")
        resp.raise_for_status()
        return resp.json()

    async def get_link_forward_links(
        self,
        source_type: str,
        source_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get forward links (links pointing FROM a given object).

        Calls GET /api/v1/links/forward/{source_type}/{source_id}.
        Returns honest empty list if no forward links exist or storage unavailable.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/links/forward/{source_type}/{source_id}",
                params={"limit": limit},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_link_forward_links_failed: %s", exc)
            return {
                "source_type": source_type,
                "source_id": source_id,
                "forward_links": [],
                "total": 0,
                "available": False,
                "storage_backend": "unavailable",
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # UI Cycle 10: Corpus Workbench
    # ------------------------------------------------------------------

    async def get_corpus_status(self) -> dict[str, Any]:
        """Get corpus status via GET /api/v1/corpus/status.

        Returns total_turns, embedded, tagged, documents, conversations,
        embed_failures, needs_reembed, embed_coverage, tag_coverage.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/corpus/status", timeout=8.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_corpus_status_failed: %s", exc)
            return {"total_turns": 0, "embedded": 0, "tagged": 0, "error": str(exc)}

    async def get_corpus_embedding_progress(self) -> dict[str, Any]:
        """Get embedding progress via GET /api/v1/corpus/embedding-progress.

        Returns total, embedded, unembedded, needs_reembed, percentage,
        last_embed_at, embedding_models, sexton_pass.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/corpus/embedding-progress", timeout=8.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_corpus_embedding_progress_failed: %s", exc)
            return {"total": 0, "embedded": 0, "unembedded": 0, "percentage": 0.0, "error": str(exc)}

    async def list_corpus_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
    ) -> dict[str, Any]:
        """List corpus documents via GET /api/v1/corpus/documents.

        Returns items (list of document summaries), total, limit, offset.
        Each item has source_path, source_model, turn_count, embedded_count,
        unembedded_count, embed_fail_count, needs_reembed_count,
        primary_domains, last_updated, conversation_count.
        """
        client = self._get_http_client()
        try:
            params: dict[str, Any] = {"limit": limit, "offset": offset}
            if search:
                params["search"] = search
            resp = await client.get(
                f"{self.base_url}/api/v1/corpus/documents",
                params=params,
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("list_corpus_documents_failed: %s", exc)
            return {"items": [], "total": 0, "limit": limit, "offset": offset, "error": str(exc)}

    async def get_corpus_document_detail(self, source_path: str) -> dict[str, Any]:
        """Get document detail via GET /api/v1/corpus/documents/{source_path}.

        Returns metadata, chunk summary, embedding status, errors, sample_turns.
        Returns not_found=True honestly if document doesn't exist.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/corpus/documents/{source_path}",
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_corpus_document_detail_failed: %s", exc)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 404:
                return {"not_found": True, "source_path": source_path}
            return {"not_found": True, "source_path": source_path, "error": str(exc)}

    async def get_corpus_problems(self) -> dict[str, Any]:
        """Get corpus problems via GET /api/v1/corpus/problems.

        Returns failed_ingest_jobs, unembedded_count, needs_reembed_count,
        duplicate_hashes, stale_docs.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/corpus/problems", timeout=8.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_corpus_problems_failed: %s", exc)
            return {
                "failed_ingest_jobs": [],
                "unembedded_count": 0,
                "needs_reembed_count": 0,
                "duplicate_hashes": [],
                "stale_docs": [],
                "available": False,
                "error": str(exc),
            }

    async def get_corpus_unembedded(self, limit: int = 100) -> dict[str, Any]:
        """Get unembedded chunks via GET /api/v1/corpus/unembedded.

        Returns items (list of unembedded turns), count, available.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/corpus/unembedded",
                params={"limit": limit},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_corpus_unembedded_failed: %s", exc)
            return {"items": [], "count": 0, "available": False, "error": str(exc)}

    async def trigger_corpus_backfill(
        self,
        limit: int = 500,
        batch_size: int = 20,
        dry_run: bool = False,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Trigger embedding backfill via POST /api/v1/corpus/backfill.

        Explicit DEFINER action. Returns status: accepted, not_wired,
        already_running, or error.
        """
        client = self._get_http_client()
        try:
            payload: dict[str, Any] = {
                "limit": limit,
                "batch_size": batch_size,
                "dry_run": dry_run,
            }
            if domain:
                payload["domain"] = domain
            resp = await client.post(
                f"{self.base_url}/api/v1/corpus/backfill",
                json=payload,
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_corpus_backfill_failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def retry_failed_embeds(self, limit: int = 50) -> dict[str, Any]:
        """Retry failed embeds via POST /api/v1/corpus/retry-failed.

        Explicit DEFINER action. Returns status: accepted, no_failed,
        not_wired, or error.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/corpus/retry-failed",
                json={"limit": limit},
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("retry_failed_embeds_failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def ingest_to_corpus(
        self,
        path: str,
        source_model: str = "",
        source_account: str = "gui_ingest",
        recursive: bool = False,
    ) -> dict[str, Any]:
        """Ingest file/directory via POST /api/v1/corpus/ingest.

        Explicit DEFINER action. Must not silently overwrite existing documents.
        Returns type, source_path, turns_ingested, turns_skipped,
        turns_updated, turns_failed, warnings, errors honestly.
        """
        client = self._get_http_client()
        try:
            payload = {
                "path": path,
                "source_model": source_model,
                "source_account": source_account,
                "recursive": recursive,
            }
            resp = await client.post(
                f"{self.base_url}/api/v1/corpus/ingest",
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("ingest_to_corpus_failed: %s", exc)
            return {"type": "error", "error": str(exc)}

    async def get_corpus_duplicates(self) -> dict[str, Any]:
        """Get duplicate documents via GET /api/v1/corpus/duplicates.

        Returns items (list of duplicate hash entries), total, available.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/corpus/duplicates", timeout=8.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_corpus_duplicates_failed: %s", exc)
            return {"items": [], "total": 0, "available": False, "error": str(exc)}

    async def get_corpus_stale(self) -> dict[str, Any]:
        """Get stale documents via GET /api/v1/corpus/stale.

        Returns items (list of stale document entries), total, available.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/corpus/stale", timeout=8.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_corpus_stale_failed: %s", exc)
            return {"items": [], "total": 0, "available": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Retrieval Lab (UI Cycle 11)
    # ------------------------------------------------------------------

    async def retrieval_test(
        self,
        query: str,
        selected_channels: list[str] | None = None,
        limit: int = 20,
        include_trace: bool = True,
    ) -> dict[str, Any]:
        """Run a standalone retrieval test via POST /api/v1/retrieval/test.

        Returns per-channel results, health, latency, fusion/ranking,
        selected context, degraded/failed channels, warnings, and
        lexical_only/vector_contributed flags. No answer synthesis.
        """
        client = self._get_http_client()
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "include_trace": include_trace,
        }
        if selected_channels is not None:
            payload["selected_channels"] = selected_channels
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/retrieval/test",
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("retrieval_test_failed: %s", exc)
            return {
                "status": "error",
                "message": f"Retrieval test failed: {exc}",
                "query": query,
                "selected_channels": selected_channels or [],
                "channel_results": {},
                "channel_health": {},
                "latency_ms": 0,
                "per_channel_latency_ms": {},
                "scores": {},
                "fusion_results": [],
                "selected_context": [],
                "degraded_channels": [],
                "failed_channels": [],
                "warnings": [f"Backend unavailable: {exc}"],
                "trace": None,
                "lexical_only": False,
                "vector_contributed": False,
            }

    async def retrieval_health(self) -> dict[str, Any]:
        """Get retrieval channel health via GET /api/v1/retrieval/health.

        Returns per-channel health state, vector backend type and
        degradation status, embedding provider configuration, and
        reasons for unavailable/degraded channels.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/retrieval/health",
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("retrieval_health_failed: %s", exc)
            return {
                "status": "error",
                "message": f"Retrieval health unavailable: {exc}",
                "channels": {},
                "embedding_coverage": {"status": "unavailable"},
                "vector_fallback_chain": [],
                "summary": {"total_channels": 0, "active": 0, "degraded": 0, "unavailable": 0},
            }

    async def get_retrieval_recent_traces(self, limit: int = 10) -> dict[str, Any]:
        """Get recent retrieval traces via GET /api/v1/retrieval/traces.

        Returns list of recent traces with per-channel timing and verdict.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/retrieval/traces",
                params={"limit": limit},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_retrieval_recent_traces_failed: %s", exc)
            return {"status": "error", "traces": [], "count": 0}

    # ── UI Cycle 12: Maintenance Center ─────────────────────────────────

    async def get_maintenance_status(self) -> dict[str, Any]:
        """Get aggregated maintenance status via GET /api/v1/maintenance/status.

        Returns actor states, backfill state, capability availability,
        and warnings. Honest about unavailable/not_wired states.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/maintenance/status",
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_maintenance_status_failed: %s", exc)
            return {
                "actors": {},
                "backfill": {"state": "unavailable", "running": False, "progress": {}, "last_result": None},
                "capabilities": {},
                "warnings": [f"Backend unavailable: {exc}"],
            }

    async def get_actor_runs(self, actor_name: str, limit: int = 20) -> dict[str, Any]:
        """Get recent run history for an actor via GET /api/v1/actors/{actor}/runs.

        Returns honest empty list if event store is unavailable or no events exist.
        Never fakes run history.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/actors/{actor_name}/runs",
                params={"limit": limit},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_actor_runs_failed: %s", exc)
            return {
                "actor": actor_name,
                "runs": [],
                "available": False,
                "count": 0,
                "message": f"Backend unavailable: {exc}",
            }

    async def get_maintenance_logs(self, limit: int = 50) -> dict[str, Any]:
        """Get recent maintenance logs via GET /api/v1/maintenance/logs.

        Returns honest empty list if event store is unavailable. Never fakes logs.
        """
        client = self._get_http_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/maintenance/logs",
                params={"limit": limit},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("get_maintenance_logs_failed: %s", exc)
            return {
                "logs": [],
                "available": False,
                "count": 0,
                "message": f"Backend unavailable: {exc}",
            }

    async def trigger_maintenance_backfill(
        self, limit: int = 500, batch_size: int = 20, dry_run: bool = False
    ) -> dict[str, Any]:
        """Trigger embedding backfill via POST /api/v1/maintenance/backfill-embeddings.

        Explicit DEFINER action. Uses the same runtime path as
        POST /corpus/backfill. Reports honest unavailable if not wired.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/maintenance/backfill-embeddings",
                json={"limit": limit, "batch_size": batch_size, "dry_run": dry_run},
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_maintenance_backfill_failed: %s", exc)
            return {"status": "error", "message": f"Backfill request failed: {exc}"}

    async def trigger_maintenance_rebuild_graph(self) -> dict[str, Any]:
        """Trigger graph rebuild via POST /api/v1/maintenance/rebuild-graph.

        Explicit DEFINER action. Returns not_wired/scheduled_only honestly
        if no standalone endpoint exists.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/maintenance/rebuild-graph",
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_maintenance_rebuild_graph_failed: %s", exc)
            return {"status": "error", "message": f"Graph rebuild request failed: {exc}"}

    async def trigger_maintenance_rebuild_codex(self) -> dict[str, Any]:
        """Trigger CODEX/wiki rebuild via POST /api/v1/maintenance/rebuild-codex.

        Explicit DEFINER action. Returns not_wired/scheduled_only honestly
        if no standalone endpoint exists.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/maintenance/rebuild-codex",
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_maintenance_rebuild_codex_failed: %s", exc)
            return {"status": "error", "message": f"CODEX rebuild request failed: {exc}"}

    async def trigger_maintenance_retrieval_eval(self) -> dict[str, Any]:
        """Trigger retrieval eval via POST /api/v1/maintenance/run-retrieval-eval.

        Explicit DEFINER action. Returns not_wired honestly if CLI-only.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/maintenance/run-retrieval-eval",
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_maintenance_retrieval_eval_failed: %s", exc)
            return {"status": "error", "message": f"Retrieval eval request failed: {exc}"}

    async def trigger_maintenance_check_stale_docs(self) -> dict[str, Any]:
        """Check for stale documents via POST /api/v1/maintenance/check-stale-docs.

        Explicit DEFINER action. Delegates to existing corpus stale logic.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/maintenance/check-stale-docs",
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_maintenance_check_stale_docs_failed: %s", exc)
            return {"status": "error", "message": f"Stale docs check failed: {exc}"}

    async def trigger_maintenance_check_contradictions(self) -> dict[str, Any]:
        """Check for contradictions via POST /api/v1/maintenance/check-contradictions.

        Explicit DEFINER action. Returns not_wired honestly if not available.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/maintenance/check-contradictions",
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_maintenance_check_contradictions_failed: %s", exc)
            return {"status": "error", "message": f"Contradiction check failed: {exc}"}

    async def trigger_actor_run(self, actor_name: str) -> dict[str, Any]:
        """Trigger an actor cycle via POST /api/v1/actors/{actor}/trigger.

        Explicit DEFINER action. Uses the existing actor trigger endpoint.
        Returns unavailable if actor not initialized.
        """
        client = self._get_http_client()
        try:
            resp = await client.post(
                f"{self.base_url}/api/v1/actors/{actor_name}/trigger",
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("trigger_actor_run_failed: %s", exc)
            return {"actor": actor_name, "triggered": False, "error": f"Backend unavailable: {exc}"}


# Module-level singleton for the GUI to use
_api_client: AipApiClient | None = None


def get_api_client() -> AipApiClient:
    """Get or create the shared AipApiClient singleton."""
    global _api_client
    if _api_client is None:
        _api_client = AipApiClient()
    return _api_client
