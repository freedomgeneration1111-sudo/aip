"""Model Council — multi-model Fusion synthesis endpoint.

Provides:
  POST /api/v1/beast/compare-models

The Model Council lets the DEFINER compare multiple model outputs for a
prompt/turn/context, then receive a Beast-style Fusion synthesis.

Phase 1 (default): The Beast analysis runs as a two-stage OpenRouter Fusion
pipeline — Judge-Beast reads the panel outputs and produces a structured
JSON comparison, then Synth-Beast reads ONLY that JSON (no panel outputs,
no retrieval) and writes the final fused answer. Per-model panel outputs
remain in ``selected_models`` for the human to compare alongside the
single ``fusion_answer``.

Reports are ADVISORY ONLY. No auto-approve, no auto-export, no wiki
mutation, no config changes, no model slot changes.

Two parallel sources of models are supported:
  - ``selected_model_slots``  — TOML-configured slot names (synthesis,
    evaluation, beast, …). Routed via ``ModelSlotResolver``.
  - ``selected_model_ids``    — OpenRouter model IDs (e.g.
    ``deepseek/deepseek-v4-flash:free``) drawn from the
    ``enabled_models`` SQLite library. Routed via direct OpenRouter
    calls using ``AIP_OPENAI_API_KEY`` (or per-row ``custom_api_key``).

If fewer than two usable models (slots + library IDs combined) are
available, returns an honest ``insufficient_models`` state. If one model
fails, returns a partial/degraded report rather than total failure. If
Beast synthesis is unavailable, returns per-model results with
conclusion status ``unavailable`` rather than a fake conclusion.

Layer discipline: This module imports ONLY from adapter and foundation.
Store access is through the container, not via direct orchestration imports.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Slots that should NOT be used for text generation comparison
# ---------------------------------------------------------------------------

_EXCLUDED_SLOTS = {"embedding"}

# Default text-generation slots to use for comparison if caller doesn't specify
_DEFAULT_COMPARISON_SLOTS = ["synthesis", "evaluation", "beast"]

# Default OpenRouter base URL for direct library-model calls.
_OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api"

# State DB path — kept in sync with models_library.py.
_STATE_DB = "db/state.db"

# ── Per-call timeouts ──────────────────────────────────────────────────
# A single hung model must not hold the entire panel hostage. Each panel
# call is wrapped in ``asyncio.wait_for`` so a slow/hung model is cut
# loose at the panel timeout (recorded as ``status="failed"`` with the
# timeout message), letting the gather complete as soon as the slowest
# *cooperative* model returns. Judge and Synth get longer timeouts
# because they process the full panel context (longer prompts, more
# reasoning work). These are upper bounds — fast models return well
# before the timeout fires.
_PANEL_CALL_TIMEOUT_S = 30.0   # single panel model call (Q&A)
_JUDGE_CALL_TIMEOUT_S = 60.0   # Judge-Beast (reads all panel outputs)
_SYNTH_CALL_TIMEOUT_S = 60.0   # Synth-Beast (reads Judge JSON)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PerModelResult(BaseModel):
    """Per-model result within a Model Council comparison."""

    model_slot: str = ""
    model_id: str = ""
    provider: str = ""
    status: str = "pending"  # pending, completed, failed, excluded
    answer: str = ""
    error: str = ""
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    # Provenance: "slot" = TOML-configured slot routed via ModelSlotResolver,
    # "library" = OpenRouter model ID routed directly from the enabled_models
    # SQLite table. Existing callers that ignore this field continue to work.
    source: str = "slot"


class ModelCouncilRequest(BaseModel):
    """Request body for Model Council comparison.

    Two parallel model sources are accepted:
      - ``selected_model_slots`` — TOML slot names (e.g. ``["synthesis",
        "beast"]``). Routed via ``ModelSlotResolver``.
      - ``selected_model_ids``   — OpenRouter model IDs from the
        ``enabled_models`` SQLite library (e.g.
        ``["deepseek/deepseek-v4-flash:free"]``). Routed via direct
        OpenRouter HTTP calls.

    Both lists are merged for the comparison; the ``≥2 usable models``
    gate counts the combined total.

    ``skip_default_slots`` (default ``False``): when ``True``, the
    resolver returns ``[]`` for ``comparison_slots`` even if
    ``selected_model_slots`` is empty — i.e. the panel is built ONLY
    from ``selected_model_ids`` (OpenRouter library IDs). This is the
    GUI's "models not tied to actor slots/roles" mode: the user picks
    N models from the unified dropdown, the backend calls those N
    models directly via OpenRouter, and the ``beast`` slot is used
    ONLY for the Judge+Synth synthesis stages. Default ``False``
    preserves the existing fallback (``_DEFAULT_COMPARISON_SLOTS``)
    for external API clients and existing tests.

    ``assemble_augmented_context`` (default ``False``): when ``True``
    AND ``turn_id`` is non-empty, the endpoint calls the shared
    ``routes/_augmented_context.py::assemble_augmented_context()``
    helper to build the augmented system messages (corpus turns + wiki
    + graph + definer profile) and PREPENDS them to each panel call's
    user prompt. This is the Phase 1 retrieval bridge — fixes the
    AIP-acronym bug where Multi-Cast panel models answered blind
    without seeing the corpus. The augmented context is computed ONCE
    per request (not N times) and is identical across panelists —
    diversity comes from the models themselves, not from differential
    context. The Judge and Synth calls do NOT receive the augmented
    prefix (the Judge reads panel outputs; the Synth reads only the
    Judge JSON). Default ``False`` preserves the existing bare-prompt
    behavior for external API clients and existing tests.
    """

    prompt: str
    turn_id: str = ""
    session_id: str = ""
    existing_answer: str = ""
    sources: list[dict] = []
    selected_model_slots: list[str] = Field(default_factory=list)
    selected_model_ids: list[str] = Field(default_factory=list)
    save_as_artifact: bool = False
    skip_default_slots: bool = False
    assemble_augmented_context: bool = False


class ModelCouncilResponse(BaseModel):
    """Response model for Model Council comparison report.

    Phase 1 (Fusion pipeline): the Beast analysis now runs as a two-stage
    Fusion pipeline — Judge-Beast produces a structured JSON comparison,
    then Synth-Beast reads that JSON and writes the final fused answer.

    Legacy fields (``convergence``, ``disagreements``,
    ``unique_contributions``, ``risks``, ``beast_conclusion``,
    ``recommended_decision``) are still populated from the Judge JSON so
    existing consumers continue to work. New consumers should prefer
    ``fusion_answer`` (the final Synth-Beast output) and
    ``judge_analysis`` (the full structured Judge JSON for audit).
    """

    id: str = ""
    status: str = "pending"  # pending, completed, partial, insufficient_models, unavailable, error
    prompt: str = ""
    turn_id: str = ""
    session_id: str = ""
    selected_models: list[PerModelResult] = []
    convergence: str = ""
    disagreements: str = ""
    unique_contributions: str = ""
    risks: str = ""
    beast_conclusion: str = ""
    recommended_decision: str = ""
    degraded_models: list[str] = []
    failed_models: list[str] = []
    artifact_id: str = ""
    created_at: str = ""
    advisory_only: bool = True
    requires_DEFINER_approval: bool = True
    error: str = ""
    # Synthesis status — separate from overall status
    synthesis_status: str = "pending"  # pending, completed, unavailable, failed
    # ── Phase 1 Fusion fields ──
    # ``fusion_answer`` is the final fused answer produced by Synth-Beast
    # after reading the Judge JSON. ``beast_conclusion`` is mirrored to
    # this value for legacy consumers.
    fusion_answer: str = ""
    # ``judge_analysis`` is the full structured JSON produced by
    # Judge-Beast: ``{status, analysis:{consensus[], contradictions[],
    # partial_coverage[], unique_insights[], blind_spots[]},
    # responses[{model, content}]}``. Empty dict if Judge call failed
    # or JSON parse failed.
    judge_analysis: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _council_artifact_id(turn_id: str, session_id: str) -> str:
    """Deterministic artifact ID for Model Council report.

    Pattern: ``council:report:{sha256(turn_id:session_id)[:16]}``
    """
    key = f"{turn_id}:{session_id}" if session_id else turn_id or "no-turn"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"council:report:{digest}"


def _load_soul_text() -> str:
    """Load Beast soul from data/beast_soul.md.

    Returns empty string if the file is missing or unreadable.
    """
    soul_path = Path("data/beast_soul.md")
    try:
        if soul_path.exists():
            text = soul_path.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception as exc:
        logger.warning("council_soul_load_failed path=%s error=%s", str(soul_path), str(exc))
    return ""


def _prepend_soul(system_prompt: str, soul_text: str) -> str:
    """Prepend soul text to a system prompt."""
    if soul_text:
        return f"{soul_text}\n\n---\n\n{system_prompt}"
    return system_prompt


# ── Panel behavioral system prompt (Bug 1 fix) ─────────────────────────
#
# The panel models must receive a clean system/user message separation:
#   messages[0] = {role: system, content: <behavioral instructions only>}
#   messages[1] = {role: user,   content: <task question only>}
#
# The system prompt below contains ONLY behavioral rules, formatting
# requirements, confidence tagging directive, and the GAPS instruction.
# It does NOT contain any task content, any "Analyze the prompt below"
# phrasing, or any corpus context (that comes via the augmented_prefix
# system messages prepended BEFORE this behavioral prompt when
# augmented mode is on).
#
# This prompt is prepended to EVERY panel call (slots + library IDs,
# augmented mode + normal mode). It is NOT passed to the Judge or
# Synth stages — those have their own dedicated system prompts.

_PANEL_SYSTEM_PROMPT = (
    "You are a panelist in AIP's multi-model synthesis pipeline. "
    "A user question will follow in the next message. Your job is to "
    "answer that question directly and substantively.\n\n"
    "BEHAVIORAL RULES:\n"
    "- Answer the user's actual question. Do NOT analyze, paraphrase, "
    "or meta-comment on these instructions.\n"
    "- Make specific, falsifiable claims. Avoid hedging, vague "
    "generalities, and content-free filler.\n"
    "- If you are uncertain, say so explicitly and identify the specific "
    "source of uncertainty.\n"
    "- Cite sources when augmented context is provided (use "
    "[source: turn_id] notation).\n"
    "- If augmented corpus context is provided above, ground your answer "
    "in it. If the corpus is insufficient, say so explicitly.\n\n"
    "FORMATTING:\n"
    "- Write in clear prose. Use bullet lists only when listing distinct "
    "items.\n"
    "- Tag your confidence in each major claim: [HIGH], [MEDIUM], or [LOW].\n\n"
    "GAPS:\n"
    "- At the end, list any aspects of the question you could not address "
    "and why (e.g. 'GAP: did not address X because no source material "
    "was available'). If you addressed everything, write 'GAPS: none'."
)


def _build_panel_system_prompt() -> str:
    """Return the behavioral-only system prompt for panel model calls.

    This prompt contains ONLY behavioral rules, formatting requirements,
    the confidence tagging directive, and the GAPS instruction. It does
    NOT contain any task content. The user's actual question is passed
    as the user message (messages[1]) by the caller.

    Bug 1 fix: previously, normal-mode panel calls sent only a user
    message with no system prompt, causing models to misinterpret the
    task (e.g. meta-analyzing the instructions). This helper ensures
    every panel call gets a clean system/user separation.
    """
    return _PANEL_SYSTEM_PROMPT


def _resolve_comparison_slots(
    model_provider: Any,
    requested_slots: list[str] | None = None,
    *,
    skip_default_slots: bool = False,
) -> list[str]:
    """Determine which slots to use for comparison.

    Filters out embedding and non-dict slots. If caller specifies slots,
    uses those (after filtering). Otherwise uses default text-generation
    slots that are actually configured.

    When ``skip_default_slots=True`` AND ``requested_slots`` is empty/None,
    returns ``[]`` immediately without falling back to
    ``_DEFAULT_COMPARISON_SLOTS``. This is the GUI's "models not tied to
    actor slots/roles" mode: the panel is built ONLY from
    ``selected_model_ids`` (OpenRouter library IDs) and the ``beast``
    slot is used ONLY for the Judge+Synth synthesis stages.
    """
    # Short-circuit: GUI explicitly opts out of default slot fallback.
    # The panel will be built entirely from ``selected_model_ids`` by
    # the caller (compare_models).
    if skip_default_slots and not requested_slots:
        return []

    try:
        available = model_provider.list_slots()
    except Exception:
        available = []

    # Filter to only dict-typed slots (exclude ci_mode flags etc.)
    configured_slots = []
    for s in available:
        try:
            cfg = model_provider._resolve_slot_config(s)
            if isinstance(cfg, dict):
                configured_slots.append(s)
        except Exception:
            logger.debug("slot_config_resolve_failed slot=%s", s)
            pass

    # Remove excluded slots
    usable = [s for s in configured_slots if s not in _EXCLUDED_SLOTS]

    if requested_slots:
        # Use caller's selection, filtered to actually configured + usable
        return [s for s in requested_slots if s in usable]

    # Default: use configured text-generation slots from our default list
    defaults_in_config = [s for s in _DEFAULT_COMPARISON_SLOTS if s in usable]
    if defaults_in_config:
        return defaults_in_config

    # Fallback: use any usable slots
    return usable


# ---------------------------------------------------------------------------
# Library model helpers — call OpenRouter directly per model ID
# ---------------------------------------------------------------------------


async def _lookup_library_model(model_id: str) -> dict[str, Any] | None:
    """Look up a model row in the ``enabled_models`` SQLite table.

    Returns the row as a dict, or ``None`` if not found / table missing /
    DB unreachable. Best-effort: callers must tolerate ``None`` and fall
    back to OpenRouter defaults.
    """
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            cursor = await conn.execute(
                """
                SELECT model_id, display_name, provider, enabled,
                       is_custom, custom_base_url, custom_api_key
                FROM enabled_models
                WHERE model_id = ?
                """,
                (model_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "model_id": row["model_id"],
                "display_name": row["display_name"],
                "provider": row["provider"],
                "enabled": row["enabled"],
                "is_custom": row["is_custom"],
                "custom_base_url": row["custom_base_url"],
                "custom_api_key": row["custom_api_key"],
            }
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("library_model_lookup_failed model_id=%s error=%s", model_id, exc)
        return None


async def _call_library_model_id(
    model_id: str,
    user_prompt: str | None = None,
    messages: list[dict] | None = None,
) -> dict:
    """Call a single OpenRouter library model directly by model ID.

    Returns a dict shaped like ``ModelSlotResolver.call()``:
    ``{content, model, usage, latency_ms, cost_usd, error, error_message}``.

    Resolution order for credentials and base URL:
      1. If the model row has ``custom_base_url`` and ``custom_api_key``
         (``is_custom=1``), use those.
      2. Otherwise use ``AIP_OPENAI_API_KEY`` env var and the default
         OpenRouter base URL.

    Message passing (one of):
      - ``messages``: a full ``[{role, content}, ...]`` list. Use this
        for calls that need a system prompt (e.g., the Judge-Beast and
        Synth-Beast stages of the Fusion pipeline when a library model
        is acting as the engine).
      - ``user_prompt``: a single user-content string. Convenience for
        the panel gather path. Converted to ``[{"role": "user",
        "content": user_prompt}]``.

    Never raises — returns an error dict on failure so the comparison
    report can degrade gracefully instead of failing entirely.
    """
    if messages is None:
        if user_prompt is None:
            return {
                "content": "",
                "model": model_id,
                "usage": {},
                "latency_ms": 0,
                "cost_usd": 0.0,
                "error": True,
                "error_message": (
                    "_call_library_model_id: either messages or "
                    "user_prompt must be provided"
                ),
            }
        messages = [{"role": "user", "content": user_prompt}]

    row = await _lookup_library_model(model_id)
    display_name = (row or {}).get("display_name") or model_id

    # Resolve base_url + api_key
    if row and row.get("is_custom") == 1 and row.get("custom_base_url") and row.get("custom_api_key"):
        base_url = row["custom_base_url"]
        api_key = row["custom_api_key"]
    else:
        base_url = _OPENROUTER_DEFAULT_BASE_URL
        api_key = os.environ.get("AIP_OPENAI_API_KEY", "")

    if not api_key:
        return {
            "content": "",
            "model": model_id,
            "usage": {},
            "latency_ms": 0,
            "cost_usd": 0.0,
            "error": True,
            "error_message": (
                f"No API key configured for library model '{model_id}'. "
                f"Set AIP_OPENAI_API_KEY in the environment."
            ),
        }

    try:
        import httpx
    except ImportError as exc:
        return {
            "content": "",
            "model": model_id,
            "usage": {},
            "latency_ms": 0,
            "cost_usd": 0.0,
            "error": True,
            "error_message": f"httpx not installed: {exc}",
        }

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model_id,
        "messages": messages,
    }

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "content": "",
            "model": model_id,
            "usage": {},
            "latency_ms": elapsed_ms,
            "cost_usd": 0.0,
            "error": True,
            "error_message": f"OpenRouter call failed for '{model_id}': {exc}",
        }

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    content = ""
    choices = data.get("choices", []) or []
    if choices:
        message = choices[0].get("message", {}) or {}
        content = message.get("content", "") or ""

    usage = data.get("usage", {}) or {}
    usage_data = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }

    # display_name is returned alongside model_id so callers can render a
    # friendly label in the per-model result card.
    return {
        "content": content,
        "model": data.get("model", model_id),
        "display_name": display_name,
        "usage": usage_data,
        "latency_ms": elapsed_ms,
        "cost_usd": 0.0,
        "error": False,
    }


# ---------------------------------------------------------------------------
# POST endpoint — run model comparison
# ---------------------------------------------------------------------------


@router.post(
    "/beast/compare-models",
    response_model=ModelCouncilResponse,
)
async def compare_models(
    request: ModelCouncilRequest,
    container: AipContainer = Depends(get_container),
):
    """Run a multi-model comparison and produce an advisory Model Council report.

    Calls multiple configured model slots with the same prompt, then uses
    the Beast/synthesis model to synthesize a structured advisory report
    covering convergence, disagreements, unique contributions, risks,
    and recommended decision.

    Returns ``insufficient_models`` if fewer than two text-generation
    model slots are configured. Returns ``partial`` if some models fail.
    Never auto-approves, auto-exports, mutates wiki, or changes config.
    """
    now = datetime.now(timezone.utc).isoformat()
    artifact_id = _council_artifact_id(request.turn_id, request.session_id)

    # --- Resolve slot-based comparison models ---
    # ``comparison_slots`` is [] when model_provider is None or when no
    # slots are configured. Library model IDs are processed independently
    # and don't require a model_provider.
    # When ``request.skip_default_slots`` is True (GUI multi-select mode),
    # the resolver returns [] instead of falling back to
    # ``_DEFAULT_COMPARISON_SLOTS`` — the panel is built ONLY from
    # ``request.selected_model_ids`` (OpenRouter library IDs).
    if container.model_provider is not None:
        comparison_slots = _resolve_comparison_slots(
            container.model_provider,
            request.selected_model_slots,
            skip_default_slots=request.skip_default_slots,
        )
    else:
        comparison_slots = []

    # Deduplicate requested library model IDs (preserve order)
    seen_ids: set[str] = set()
    comparison_model_ids: list[str] = []
    for mid in request.selected_model_ids:
        if mid and mid not in seen_ids:
            seen_ids.add(mid)
            comparison_model_ids.append(mid)

    total_usable = len(comparison_slots) + len(comparison_model_ids)

    if total_usable < 2:
        excluded_results: list[PerModelResult] = [
            PerModelResult(
                model_slot=s,
                model_id=_safe_model_id(container.model_provider, s) if container.model_provider else f"<{s}>",
                provider=_safe_provider(container.model_provider, s) if container.model_provider else "unknown",
                status="excluded",
                source="slot",
            )
            for s in comparison_slots
        ] + [
            PerModelResult(
                model_slot="",
                model_id=mid,
                provider="openrouter",
                status="excluded",
                source="library",
            )
            for mid in comparison_model_ids
        ]
        return ModelCouncilResponse(
            id=artifact_id,
            status="insufficient_models",
            prompt=request.prompt[:500],
            turn_id=request.turn_id,
            session_id=request.session_id,
            selected_models=excluded_results,
            error=(
                f"Insufficient usable models for comparison. "
                f"Found {len(comparison_slots)} slot(s) + {len(comparison_model_ids)} library ID(s) "
                f"= {total_usable} total. Need at least 2. "
                f"Embedding slot is excluded from text generation. "
                f"Enable more models on the Models page or add more [models.*] slots in config."
            ),
            created_at=now,
            synthesis_status="unavailable",
        )

    # --- Build the user prompt ---
    sources_text = ""
    if request.sources:
        sources_text = "\n\nContext/Sources:\n"
        for i, src in enumerate(request.sources[:10], 1):
            sources_text += f"  {i}. {src.get('title', src.get('id', 'unknown'))}: "
            sources_text += f"{src.get('snippet', src.get('content', ''))[:200]}\n"

    existing_answer_block = ""
    if request.existing_answer:
        existing_answer_block = f"\n\nExisting Answer:\n{request.existing_answer[:3000]}\n"

    user_prompt = f"""{request.prompt[:4000]}{sources_text}{existing_answer_block}"""

    # --- Phase 1 retrieval bridge: assemble augmented context ONCE ---
    # When ``request.assemble_augmented_context`` is True AND
    # ``request.turn_id`` is non-empty, call the shared
    # ``routes/_augmented_context.py::assemble_augmented_context()``
    # helper to build the augmented system messages (corpus turns +
    # wiki + graph + definer profile). The resulting messages are
    # PREPENDED to each panel call's user prompt — every panelist
    # sees the SAME augmented context (diversity comes from the
    # models, not from differential context).
    #
    # The Judge and Synth calls do NOT receive this prefix (the Judge
    # reads panel outputs; the Synth reads only the Judge JSON).
    #
    # When the flag is False (default) or turn_id is empty,
    # ``augmented_prefix`` is an empty list and the panel calls
    # proceed with the bare prompt (existing behavior — backward
    # compatible).
    augmented_prefix: list[dict] = []
    augmented_sources: list[dict] = []
    if request.assemble_augmented_context and request.turn_id:
        from aip.adapter.api.routes._augmented_context import assemble_augmented_context

        try:
            aug = await assemble_augmented_context(
                content=request.prompt,
                session_id=request.session_id,
                container=container,
            )
            augmented_prefix = aug.messages
            augmented_sources = aug.sources
            logger.info(
                "council_augmented_context_assembled "
                "assembled=%s messages=%d sources=%d domain=%s",
                aug.assembled,
                len(aug.messages),
                len(aug.sources),
                aug.domain,
            )
        except Exception as exc:
            # The helper itself never raises, but guard defensively.
            logger.warning(
                "council_augmented_context_failed error=%s", str(exc)
            )
            augmented_prefix = []
            augmented_sources = []

    # --- Build the panel behavioral system prompt (Bug 1 fix) ---
    # Every panel call receives a clean system/user separation:
    #   messages[0..k-1] = augmented_prefix system msgs (corpus + wiki + graph + definer)
    #   messages[k]       = {role: system, content: panel_system_prompt}  (behavioral only)
    #   messages[k+1]     = {role: user,   content: user_prompt}          (task only)
    # The behavioral prompt contains ONLY rules, formatting, confidence
    # tagging, and the GAPS instruction — no task content, no "Analyze
    # the prompt below" phrasing. This prevents panel models from
    # meta-analyzing the instructions instead of answering the question.
    panel_system_prompt = _build_panel_system_prompt()

    # --- Call each model concurrently (slots + library IDs in parallel) ---
    # Bug 2 fix: each model call is wrapped in its own try/except via
    # ``asyncio.gather(return_exceptions=True)`` AND logged individually
    # with ``[PANEL]`` markers so dispatch completeness is auditable.
    # A failure on model N does NOT affect models N+1 through end —
    # ``asyncio.gather`` runs them concurrently and ``return_exceptions=True``
    # captures per-task failures as values rather than raising.
    #
    # Bug 1 fix: every call receives ``panel_system_prompt`` so the
    # messages array has the clean [system, user] shape.
    per_model_tasks: dict[str, Any] = {}
    for slot_name in comparison_slots:
        # Log dispatch start (Bug 2)
        logger.info("[PANEL] Dispatching → slot:%s", slot_name)
        per_model_tasks[f"slot:{slot_name}"] = asyncio.wait_for(
            _call_model_slot(
                container.model_provider,
                slot_name,
                user_prompt,
                messages_prefix=augmented_prefix,
                panel_system_prompt=panel_system_prompt,
            ),
            timeout=_PANEL_CALL_TIMEOUT_S,
        )
    for model_id in comparison_model_ids:
        # Log dispatch start (Bug 2)
        logger.info("[PANEL] Dispatching → library:%s", model_id)
        # Build the full messages list for library models: augmented
        # prefix + behavioral system prompt + user prompt (Bug 1 fix).
        panel_messages: list[dict] = list(augmented_prefix) + [
            {"role": "system", "content": panel_system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        per_model_tasks[f"library:{model_id}"] = asyncio.wait_for(
            _call_library_model_id(model_id, messages=panel_messages),
            timeout=_PANEL_CALL_TIMEOUT_S,
        )

    # Run all model calls concurrently. ``return_exceptions=True``
    # ensures a single task failure does NOT cancel the gather — every
    # task gets a chance to complete (or fail), and failures are
    # captured as Exception values in the results list.
    results_map: dict[str, dict] = {}
    task_keys = list(per_model_tasks.keys())
    task_coros = [per_model_tasks[k] for k in task_keys]
    task_results = await asyncio.gather(*task_coros, return_exceptions=True)

    for task_key, result in zip(task_keys, task_results):
        if isinstance(result, Exception):
            # ``asyncio.TimeoutError`` from ``wait_for`` has an empty
            # ``str()`` — surface a clear message so the per-model card
            # shows "timed out after Ns" instead of an empty error.
            if isinstance(result, asyncio.TimeoutError):
                err_msg = f"timed out after {_PANEL_CALL_TIMEOUT_S:.0f}s"
            else:
                err_msg = str(result) or result.__class__.__name__
            # Bug 2: log the failure with [PANEL] marker
            logger.warning("[PANEL] FAILED ← %s %s", task_key, err_msg)
            results_map[task_key] = {
                "content": "",
                "model": "",
                "usage": {},
                "latency_ms": 0,
                "cost_usd": 0.0,
                "error": True,
                "error_message": err_msg,
            }
        else:
            # Bug 2: log the successful response with [PANEL] marker
            # and token count when available.
            usage = result.get("usage", {}) if isinstance(result, dict) else {}
            token_count = usage.get("total_tokens", 0)
            logger.info(
                "[PANEL] Response ← %s (%s tokens)",
                task_key,
                token_count,
            )
            results_map[task_key] = result

    # --- Build per-model results (slots first, then library IDs) ---
    per_model_results: list[PerModelResult] = []
    degraded_models: list[str] = []
    failed_models: list[str] = []
    successful_count = 0

    # Slots
    for slot_name in comparison_slots:
        r = results_map.get(f"slot:{slot_name}", {})
        model_id = r.get("model", _safe_model_id(container.model_provider, slot_name))
        provider = _safe_provider(container.model_provider, slot_name)
        usage = r.get("usage", {})
        is_error = r.get("error", False)

        if is_error:
            failed_models.append(slot_name)
            per_model_results.append(
                PerModelResult(
                    model_slot=slot_name,
                    model_id=model_id,
                    provider=provider,
                    status="failed",
                    error=r.get("error_message", "Model call failed"),
                    latency_ms=r.get("latency_ms"),
                    source="slot",
                )
            )
        else:
            successful_count += 1
            per_model_results.append(
                PerModelResult(
                    model_slot=slot_name,
                    model_id=model_id,
                    provider=provider,
                    status="completed",
                    answer=r.get("content", ""),
                    latency_ms=r.get("latency_ms"),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    cost_usd=r.get("cost_usd"),
                    source="slot",
                )
            )

    # Library model IDs
    for model_id in comparison_model_ids:
        r = results_map.get(f"library:{model_id}", {})
        # Library calls return ``display_name`` alongside ``model`` for a
        # friendly label; fall back to the model_id if absent.
        display_name = r.get("display_name") or model_id
        actual_model_id = r.get("model", model_id)
        usage = r.get("usage", {})
        is_error = r.get("error", False)
        label = display_name if display_name != model_id else model_id

        if is_error:
            failed_models.append(label)
            per_model_results.append(
                PerModelResult(
                    model_slot="",
                    model_id=actual_model_id,
                    provider="openrouter",
                    status="failed",
                    error=r.get("error_message", "Model call failed"),
                    latency_ms=r.get("latency_ms"),
                    source="library",
                )
            )
        else:
            successful_count += 1
            per_model_results.append(
                PerModelResult(
                    model_slot="",
                    model_id=actual_model_id,
                    provider="openrouter",
                    status="completed",
                    answer=r.get("content", ""),
                    latency_ms=r.get("latency_ms"),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    cost_usd=r.get("cost_usd"),
                    source="library",
                )
            )

    # --- Determine overall status ---
    if successful_count == 0:
        overall_status = "error"
    elif successful_count < total_usable:
        overall_status = "partial"
        # ``degraded_models`` lists models that succeeded but whose peers
        # failed. Use friendly labels for library models.
        successful_labels = {
            (pm.model_slot if pm.source == "slot" else pm.model_id)
            for pm in per_model_results
            if pm.status == "completed"
        }
        failed_set = set(failed_models)
        degraded_models = [m for m in successful_labels if m not in failed_set]
    else:
        overall_status = "completed"

    # --- Beast Fusion synthesis (Panel → Judge-Beast → Synth-Beast) ---
    # Phase 1: the Beast analysis now runs as a two-stage Fusion pipeline.
    # Judge-Beast reads the panel outputs and produces a structured JSON
    # comparison. Synth-Beast reads ONLY that JSON (no panel outputs, no
    # retrieval) and writes the final fused answer. Per-model panel
    # outputs remain in ``selected_models`` for the human to compare
    # alongside the fusion.
    synthesis_status = "pending"
    convergence = ""
    disagreements = ""
    unique_contributions = ""
    risks = ""
    beast_conclusion = ""
    recommended_decision = ""
    fusion_answer = ""
    judge_analysis: dict[str, Any] = {}

    # --- Pick the Fusion engine (Judge+Synth) from successful panel models ---
    # Phase 1 Fix D: previously the code always called
    # ``container.model_provider.call("beast", ...)`` for the Judge and
    # Synth stages, even when the ``beast`` slot had just failed in the
    # panel. If ``beast`` was one of the OpenRouter free models that
    # timed out, the Judge call would also time out at
    # ``_JUDGE_CALL_TIMEOUT_S`` and the entire Fusion output was lost —
    # the user saw only per-model cards and a tiny "synthesis failed"
    # system message.
    #
    # Fix: pick the engine from the SUCCESSFUL panel models. Preference
    # order: beast slot (if it succeeded) → any successful slot → any
    # successful library model. This makes the Fusion pipeline
    # resilient to individual model failures — as long as ≥2 models
    # answered, we can run Fusion on one of the answerers.
    fusion_engine_kind: str | None = None
    fusion_engine_id: str | None = None
    if successful_count >= 2:
        fusion_engine_kind, fusion_engine_id = _pick_fusion_engine(per_model_results)

    if successful_count >= 2 and fusion_engine_kind is None:
        # Defensive guard — should be unreachable (successful_count >= 2
        # implies at least one successful panel model).
        synthesis_status = "unavailable"
        beast_conclusion = (
            "Beast Fusion synthesis unavailable — no successful panel "
            "model available to act as the Judge/Synth engine. "
            "Per-model results are available for individual review."
        )
    elif successful_count >= 2:
        # Build the per-model answers block for the Judge. Use a
        # friendly label for each: "<slot_name> (<model_id>)" for slots,
        # "<display_name> (<model_id>)" for library models (model_slot=""
        # for library models).
        #
        # Bug 2 fix: the Judge MUST receive a response entry for EVERY
        # dispatched slot — completed OR failed. Failed models are
        # included as explicit error stubs so the Judge can surface them
        # in blind_spots / contradictions / partial_coverage rather than
        # silently dropping them. Previously the loop skipped failed
        # models entirely, making them invisible to the Judge.
        answers_block = ""
        for pm in per_model_results:
            if pm.source == "slot":
                label = pm.model_slot or "slot"
            else:
                # Library model — model_id IS the friendly identifier
                label = pm.model_id
            if pm.status == "completed":
                answers_block += f"\n## {label} ({pm.model_id})\n{pm.answer[:2000]}\n"
            else:
                # Bug 2: include failed models as explicit error stubs
                # so the Judge sees every dispatched slot. The stub
                # format follows the directive's contract:
                #   {"model": "{model_id}", "content": "[DISPATCH_ERROR: {msg}]"}
                err_summary = (pm.error or "unknown error")[:200]
                answers_block += (
                    f"\n## {label} ({pm.model_id})\n"
                    f"[DISPATCH_ERROR: {err_summary}]\n"
                )

        soul_text = _load_soul_text()

        logger.info(
            "council_fusion_engine_picked kind=%s id=%s beast_in_panel=%s",
            fusion_engine_kind,
            fusion_engine_id,
            any(pm.source == "slot" and pm.model_slot == "beast" for pm in per_model_results),
        )

        # ── Stage 1: Judge-Beast — structured comparison JSON ──
        judge_system_prompt = (
            "You are AIP Beast acting as the JUDGE in a Fusion pipeline. "
            "You are given multiple model responses to the same prompt. "
            "Your job is to produce a STRUCTURED JSON comparison that "
            "the Synthesizer will use to write the final fused answer.\n\n"
            "Your output is ADVISORY ONLY — it must never be treated as "
            "canonical without DEFINER review and approval.\n\n"
            "IMPORTANT CONSTRAINTS:\n"
            "- All findings are ADVISORY ONLY and require DEFINER approval\n"
            "- Never auto-approve, auto-export, mutate wiki, change config, "
            "or change model slots\n"
            "- Be honest about uncertainty — flag weak signals explicitly\n"
            "- Do not fabricate consensus, contradictions, or insights\n\n"
            "MODEL LABEL CONTRACT (CRITICAL):\n"
            "The user message contains a section per model, each starting with "
            "a markdown header in the form:\n"
            "    ## <LABEL> (<model_id>)\n"
            "For TOML slot-sourced models, <LABEL> is the slot name "
            "(e.g. 'synthesis', 'evaluation', 'beast'). For OpenRouter "
            "library models, <LABEL> is the model ID (e.g. "
            "'anthropic/claude-3-opus', 'openai/gpt-4o').\n\n"
            "In EVERY ``model`` field of your JSON output (in contradictions[].stances[].model, "
            "partial_coverage[].models[], unique_insights[].model, and responses[].model), "
            "you MUST use the EXACT <LABEL> string as it appears between '## ' and ' (' in "
            "the corresponding section header. Do NOT invent your own labels. Do NOT use "
            "generic labels like 'model_a', 'model_b', 'the first model', 'beast' (unless "
            "'beast' is actually one of the section labels). Do NOT use the model_id "
            "(the parenthesized part) — use the <LABEL>.\n\n"
            "For example, if the user message contains:\n"
            "    ## synthesis (gpt-4o)\n"
            "    ## anthropic/claude-3-opus (anthropic/claude-3-opus)\n"
            "then a contradiction stance must be written as:\n"
            "    {\"topic\": \"...\", \"stances\": [\n"
            "      {\"model\": \"synthesis\", \"stance\": \"...\"},\n"
            "      {\"model\": \"anthropic/claude-3-opus\", \"stance\": \"...\"}\n"
            "    ]}\n"
            "NOT as {\"model\": \"gpt-4o\", ...} and NOT as {\"model\": \"model_a\", ...}.\n\n"
            "Respond with a JSON object with EXACTLY this shape:\n"
            "{\n"
            '  "status": "completed" | "partial" | "insufficient",\n'
            '  "analysis": {\n'
            '    "consensus": ["points ALL successful models agree on"],\n'
            '    "contradictions": [\n'
            '      {"topic": "...", "stances": [{"model": "<LABEL>", "stance": "..."}]}\n'
            '    ],\n'
            '    "partial_coverage": [\n'
            '      {"models": ["<LABEL_A>", "<LABEL_B>"], "point": "topic only some models covered"}\n'
            '    ],\n'
            '    "unique_insights": [\n'
            '      {"model": "<LABEL>", "insight": "..."}\n'
            '    ],\n'
            '    "blind_spots": ["topics NO model addressed"]\n'
            '  },\n'
            '  "responses": [\n'
            '    {"model": "<LABEL>", "content": "brief summary of that model\'s answer"}\n'
            '  ]\n'
            "}\n\n"
            "Rules:\n"
            "- consensus[] must list points where ALL successful models agree\n"
            "- contradictions[] must attribute each stance to a specific model "
            "using the EXACT <LABEL> from the section header\n"
            "- partial_coverage[] must list which models covered a point, again "
            "using the EXACT <LABEL> strings\n"
            "- unique_insights[] must attribute each insight to its source model "
            "using the EXACT <LABEL>\n"
            "- blind_spots[] is MANDATORY — list topics NO model addressed. "
            "Use an empty array only if you can prove coverage was complete.\n"
            "- If a field has no entries, use an empty array\n"
        )
        judge_system_prompt = _prepend_soul(judge_system_prompt, soul_text)

        judge_user_prompt = f"""Compare these model responses and produce the structured JSON.

Original Prompt:
{request.prompt[:2000]}
{answers_block}
Return the JSON object now."""

        judge_messages = [
            {"role": "system", "content": judge_system_prompt},
            {"role": "user", "content": judge_user_prompt},
        ]

        judge_succeeded = False
        try:
            judge_result = await _call_fusion_engine(
                fusion_engine_kind,  # type: ignore[arg-type]
                fusion_engine_id,    # type: ignore[arg-type]
                judge_messages,
                container,
                _JUDGE_CALL_TIMEOUT_S,
            )
            judge_content = judge_result.get("content", "").strip()

            if judge_result.get("error"):
                logger.error(
                    "council_judge_provider_error error=%s",
                    judge_result.get("error_message", "unknown"),
                )
            elif judge_content:
                json_str = judge_content
                if "```json" in json_str:
                    json_str = json_str.split("```json", 1)[-1].split("```", 1)[0]
                elif "```" in json_str:
                    json_str = json_str.split("```", 1)[-1].split("```", 1)[0]

                try:
                    judge_data = json.loads(json_str.strip())
                    if isinstance(judge_data, dict):
                        judge_analysis = judge_data
                        judge_succeeded = True
                        # Populate legacy fields from the new structured
                        # schema (best-effort — tolerate missing keys).
                        analysis = (
                            judge_data.get("analysis", {})
                            if isinstance(judge_data.get("analysis"), dict)
                            else {}
                        )
                        consensus = analysis.get("consensus", [])
                        if isinstance(consensus, list) and consensus:
                            convergence = "; ".join(str(c) for c in consensus)

                        contradictions = analysis.get("contradictions", [])
                        if isinstance(contradictions, list) and contradictions:
                            parts = []
                            for c in contradictions:
                                if not isinstance(c, dict):
                                    continue
                                topic = c.get("topic", "?")
                                stances = c.get("stances", [])
                                if isinstance(stances, list) and stances:
                                    stance_str = ", ".join(
                                        f"{s.get('model', '?')}={s.get('stance', '?')}"
                                        for s in stances
                                        if isinstance(s, dict)
                                    )
                                else:
                                    stance_str = ""
                                parts.append(f"{topic}: {stance_str}" if stance_str else str(topic))
                            if parts:
                                disagreements = "; ".join(parts)

                        unique = analysis.get("unique_insights", [])
                        if isinstance(unique, list) and unique:
                            parts = []
                            for u in unique:
                                if not isinstance(u, dict):
                                    continue
                                parts.append(f"{u.get('model', '?')}: {u.get('insight', '?')}")
                            if parts:
                                unique_contributions = "; ".join(parts)

                        blind = analysis.get("blind_spots", [])
                        if isinstance(blind, list) and blind:
                            risks = "; ".join(str(b) for b in blind)

                        # Backward-compat: accept old-schema top-level keys
                        # too (the test mock and older Beast models may
                        # return these instead of the new analysis.* shape).
                        if not convergence and judge_data.get("convergence"):
                            convergence = str(judge_data.get("convergence"))
                        if not disagreements and judge_data.get("disagreements"):
                            disagreements = str(judge_data.get("disagreements"))
                        if not unique_contributions and judge_data.get("unique_contributions"):
                            unique_contributions = str(judge_data.get("unique_contributions"))
                        if not risks and judge_data.get("risks"):
                            risks = str(judge_data.get("risks"))
                        if judge_data.get("recommended_decision"):
                            recommended_decision = str(judge_data.get("recommended_decision"))
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "council_judge_json_parse_failed content_preview=%s",
                        judge_content[:200],
                    )
                    # Fall back: treat judge_content as raw advisory text
                    beast_conclusion = judge_content[:500]
        except asyncio.TimeoutError:
            logger.error(
                "council_judge_call_timed_out timeout=%ss", _JUDGE_CALL_TIMEOUT_S,
            )
        except Exception as exc:
            logger.error(
                "council_judge_call_failed error=%s", str(exc), exc_info=True,
            )

        # ── Stage 2: Synth-Beast — read JSON only, write final answer ──
        # The Synthesizer sees ONLY the Judge JSON — never the raw panel
        # outputs, never retrieval, never external sources. This is the
        # asymmetric information design that lets the fusion step give
        # lift independently of model diversity.
        if judge_succeeded and judge_analysis:
            synth_system_prompt = (
                "You are AIP Beast acting as the SYNTHESIZER in a Fusion "
                "pipeline. You are given a structured JSON comparison of "
                "multiple model responses produced by the Judge. Your job "
                "is to write the final fused answer to the original prompt.\n\n"
                "Your output is ADVISORY ONLY — it must never be treated as "
                "canonical without DEFINER review and approval.\n\n"
                "CONSTRAINTS:\n"
                "- You may NOT call any tools, perform retrieval, or consult "
                "external sources. Work only from the Judge JSON and your "
                "own knowledge.\n"
                "- All recommendations are ADVISORY ONLY and require DEFINER approval\n"
                "- Address the original prompt directly with a clear, useful answer\n"
                "- Lean on consensus points; flag contradictions explicitly\n"
                "- Cover blind_spots honestly — do not fabricate\n"
                "- Attribute unique insights to their source model where relevant\n"
                "- Be concise but complete\n\n"
                "Respond with the final fused answer as plain text (NOT JSON)."
            )
            synth_system_prompt = _prepend_soul(synth_system_prompt, soul_text)

            judge_json_str = json.dumps(judge_analysis, ensure_ascii=False, indent=2)
            synth_user_prompt = f"""Write the final fused answer.

Original Prompt:
{request.prompt[:2000]}

Judge JSON (your only input besides your own knowledge):
```json
{judge_json_str}
```

Write the final fused answer now."""

            synth_messages = [
                {"role": "system", "content": synth_system_prompt},
                {"role": "user", "content": synth_user_prompt},
            ]

            try:
                synth_result = await _call_fusion_engine(
                    fusion_engine_kind,  # type: ignore[arg-type]
                    fusion_engine_id,    # type: ignore[arg-type]
                    synth_messages,
                    container,
                    _SYNTH_CALL_TIMEOUT_S,
                )
                synth_content = synth_result.get("content", "").strip()

                if synth_result.get("error"):
                    synthesis_status = "failed"
                    logger.error(
                        "council_synth_provider_error error=%s",
                        synth_result.get("error_message", "unknown"),
                    )
                elif synth_content:
                    # The Synth output is the final fused answer (free
                    # text in production). Some models may accidentally
                    # wrap in JSON or markdown — extract clean text.
                    fusion_answer = synth_content
                    try:
                        synth_json_str = synth_content
                        if "```json" in synth_json_str:
                            synth_json_str = synth_json_str.split("```json", 1)[-1].split("```", 1)[0]
                        elif "```" in synth_json_str:
                            synth_json_str = synth_json_str.split("```", 1)[-1].split("```", 1)[0]
                        synth_data = json.loads(synth_json_str.strip())
                        if isinstance(synth_data, dict):
                            for key in (
                                "fusion_answer", "answer", "fused_answer",
                                "synthesis", "beast_conclusion",
                            ):
                                val = synth_data.get(key)
                                if val:
                                    fusion_answer = str(val)
                                    break
                    except (json.JSONDecodeError, TypeError):
                        pass  # Use the raw synth_content as the fused answer
                    beast_conclusion = fusion_answer  # legacy mirror
                    synthesis_status = "completed"
                else:
                    synthesis_status = "failed"
            except asyncio.TimeoutError:
                logger.error(
                    "council_synth_call_timed_out timeout=%ss", _SYNTH_CALL_TIMEOUT_S,
                )
                synthesis_status = "failed"
            except Exception as exc:
                logger.error(
                    "council_synth_call_failed error=%s", str(exc), exc_info=True,
                )
                synthesis_status = "failed"
        elif judge_succeeded is False and beast_conclusion:
            # Judge call produced a raw text fallback (JSON parse failed)
            # — treat the synthesis as completed with whatever text we got.
            synthesis_status = "completed"
            fusion_answer = beast_conclusion
        else:
            # Judge call errored entirely — synthesis cannot proceed.
            synthesis_status = "failed"
            beast_conclusion = (
                "Beast Fusion synthesis failed — Judge call did not produce "
                "a structured comparison. Per-model results are available "
                "for individual review."
            )
    elif successful_count == 1:
        # Only one model succeeded — can't really compare
        synthesis_status = "unavailable"
        beast_conclusion = (
            "Only one model responded successfully. "
            "Comparison requires at least two successful model responses. "
            "Per-model results are available for individual review."
        )
    else:
        synthesis_status = "unavailable"

    # --- Build full response ---
    response = ModelCouncilResponse(
        id=artifact_id,
        status=overall_status,
        prompt=request.prompt[:500],
        turn_id=request.turn_id,
        session_id=request.session_id,
        selected_models=per_model_results,
        convergence=convergence,
        disagreements=disagreements,
        unique_contributions=unique_contributions,
        risks=risks,
        beast_conclusion=beast_conclusion,
        recommended_decision=recommended_decision,
        degraded_models=degraded_models,
        failed_models=failed_models,
        created_at=now,
        advisory_only=True,
        requires_DEFINER_approval=True,
        synthesis_status=synthesis_status,
        fusion_answer=fusion_answer,
        judge_analysis=judge_analysis,
    )

    # --- Save as artifact if requested ---
    if request.save_as_artifact and container.artifact_store is not None:
        try:
            report_data = json.dumps(response.model_dump(), ensure_ascii=False, default=str)
            artifact_metadata = {
                "artifact_type": "model_council_report",
                "turn_id": request.turn_id,
                "session_id": request.session_id,
                "comparison_slots": ",".join(comparison_slots),
                "comparison_model_ids": ",".join(comparison_model_ids),
                "status": overall_status,
            }
            await container.artifact_store.write(
                id=artifact_id,
                content=report_data,
                metadata=artifact_metadata,
            )
            response.artifact_id = artifact_id

            # ECS transition to GENERATED (NOT APPROVED — never auto-approve)
            if container.ecs_store is not None:
                try:
                    await container.ecs_store.transition(
                        artifact_id=artifact_id,
                        from_state=None,
                        to_state="GENERATED",
                        actor="model_council",
                        reason="Model Council report generated — requires DEFINER review",
                    )
                except Exception as exc:
                    logger.warning(
                        "council_ecs_transition_failed artifact_id=%s error=%s",
                        artifact_id,
                        str(exc),
                    )

            logger.info(
                "council_artifact_saved artifact_id=%s status=%s",
                artifact_id,
                overall_status,
            )
        except Exception as exc:
            logger.error(
                "council_artifact_write_failed artifact_id=%s error=%s",
                artifact_id,
                str(exc),
                exc_info=True,
            )
            # Don't fail the whole response — just note the save failure
            response.error = f"Report generated but artifact save failed: {exc}"

    return response


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _call_model_slot(
    model_provider: Any,
    slot_name: str,
    user_prompt: str,
    messages_prefix: list[dict] | None = None,
    *,
    panel_system_prompt: str | None = None,
) -> dict:
    """Call a single model slot with the given prompt.

    Returns the raw result dict from model_provider.call().

    ``messages_prefix`` (optional): list of system-message dicts to
    PREPEND to the user message. Used by the Phase 1 retrieval bridge
    to inject augmented context (corpus turns + wiki + graph + definer
    profile) into each panel call.

    ``panel_system_prompt`` (optional, Bug 1 fix): the behavioral-only
    system prompt. When provided, it is appended AFTER any
    ``messages_prefix`` augmented context and BEFORE the user message,
    producing a clean ``[augmented system msgs..., behavioral system
    msg, user msg]`` shape. When None, the call uses only
    ``messages_prefix`` + the user message (legacy behavior — kept for
    backward compat with any caller that doesn't pass the panel prompt).

    Bug 1 contract: when ``panel_system_prompt`` is provided, the final
    messages list is guaranteed to have:
        messages[-2] = {role: system, content: panel_system_prompt}
        messages[-1] = {role: user,   content: user_prompt}
    i.e. the LAST system message is the behavioral prompt and the LAST
    message is the user's task. This prevents the model from
    misinterpreting the instructions as the document to analyze.
    """
    messages: list[dict] = list(messages_prefix or [])
    if panel_system_prompt is not None:
        messages.append({"role": "system", "content": panel_system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return await model_provider.call(slot_name, messages)


def _safe_model_id(model_provider: Any, slot_name: str) -> str:
    """Safely resolve model ID for a slot without raising."""
    try:
        resolved = model_provider._resolve_slot_config(slot_name)
        return resolved.get("model", f"<{slot_name}>")
    except Exception:
        return f"<{slot_name}>"


def _safe_provider(model_provider: Any, slot_name: str) -> str:
    """Safely resolve provider name for a slot without raising."""
    try:
        resolved = model_provider._resolve_slot_config(slot_name)
        return resolved.get("provider", "unknown")
    except Exception:
        return "unknown"


def _pick_fusion_engine(
    per_model_results: list[PerModelResult],
) -> tuple[str | None, str | None]:
    """Pick the model to use as the Judge+Synth engine for the Fusion pipeline.

    Preference order (returns the first match):
      1. The ``beast`` slot IF it was in the panel AND completed successfully.
      2. ANY other successful slot (in panel order).
      3. ANY successful library model (in panel order).

    Returns ``(engine_kind, engine_id)`` where ``engine_kind`` is
    ``"slot"`` or ``"library"`` and ``engine_id`` is the slot name or
    library model ID. Returns ``(None, None)`` if no successful panel
    model exists (which should not happen when ``successful_count >= 2``
    — this is a defensive guard).

    Rationale: the prior implementation always called
    ``container.model_provider.call("beast", ...)`` for the Judge+Synth
    stages, even when the ``beast`` slot had just failed in the panel.
    If ``beast`` was one of the timing-out OpenRouter free models, the
    Judge call would also time out at ``_JUDGE_CALL_TIMEOUT_S`` and the
    entire Fusion output was lost — the user saw only per-model cards.
    Picking from successful panel models guarantees the engine is
    responsive (it just answered).
    """
    # Preference 1: beast slot, if it succeeded.
    for pm in per_model_results:
        if (
            pm.source == "slot"
            and pm.model_slot == "beast"
            and pm.status == "completed"
        ):
            return ("slot", "beast")

    # Preference 2: any other successful slot.
    for pm in per_model_results:
        if pm.source == "slot" and pm.status == "completed":
            return ("slot", pm.model_slot)

    # Preference 3: any successful library model.
    for pm in per_model_results:
        if pm.source == "library" and pm.status == "completed":
            return ("library", pm.model_id)

    return (None, None)


async def _call_fusion_engine(
    engine_kind: str,
    engine_id: str,
    messages: list[dict],
    container: Any,
    timeout: float,
) -> dict:
    """Call the picked Fusion engine (slot or library) with a messages list.

    Wraps the call in ``asyncio.wait_for`` so a hung engine is cut loose
    at ``timeout`` seconds (raises ``asyncio.TimeoutError``).

    - ``engine_kind == "slot"``: routes via ``container.model_provider.call``.
    - ``engine_kind == "library"``: routes via ``_call_library_model_id``
      with the ``messages=`` parameter (supports system+user messages).

    Returns the raw result dict from the underlying call.
    """
    if engine_kind == "slot":
        if container.model_provider is None:
            return {
                "content": "",
                "model": engine_id,
                "usage": {},
                "latency_ms": 0,
                "cost_usd": 0.0,
                "error": True,
                "error_message": (
                    f"Cannot call slot '{engine_id}' — model_provider is None"
                ),
            }
        return await asyncio.wait_for(
            container.model_provider.call(engine_id, messages),
            timeout=timeout,
        )
    elif engine_kind == "library":
        return await asyncio.wait_for(
            _call_library_model_id(engine_id, messages=messages),
            timeout=timeout,
        )
    else:
        return {
            "content": "",
            "model": engine_id or "unknown",
            "usage": {},
            "latency_ms": 0,
            "cost_usd": 0.0,
            "error": True,
            "error_message": f"Unknown engine_kind: {engine_kind!r}",
        }
