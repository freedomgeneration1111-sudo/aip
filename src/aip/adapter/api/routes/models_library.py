"""Model library API routes — browse and manage enabled_models.

Provides endpoints for the unified chat surface's model selector:
  - GET   /models/library          — list all models in enabled_models
  - POST  /models/library/fetch    — fetch from OpenRouter + upsert cache
  - PATCH /models/library          — toggle enabled flag (body-based)

Per AIP-G-09: the OpenRouter fetch is the ONLY outbound call, and it is
explicitly user-triggered (never on startup).

Cycle 16.8A fixes:
  F-D1: Schema ensure helper guarantees enabled_models table exists before
        any route operation, so a fresh backend without prior "aip init"
        does not fail with "no such table: enabled_models".
  F-D2: Toggle route changed from PATCH /models/library/{model_id} (path
        param) to PATCH /models/library (body-based) so OpenRouter model
        IDs containing "/" (e.g. "deepseek/deepseek-v4-flash:free") are
        handled correctly.
  F-D3: List responses no longer expose raw custom_api_key. Instead, a
        boolean has_custom_api_key field is returned.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aip.adapter.api.dependencies import require_definer

router = APIRouter()
logger = logging.getLogger(__name__)

_STATE_DB = "db/state.db"

# DDL for enabled_models table — kept in sync with aip/cli/init.py.
# Used by _ensure_schema() to guarantee the table exists before route ops.
_ENABLED_MODELS_DDL = """
CREATE TABLE IF NOT EXISTS enabled_models (
    model_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'openrouter',
    cost_input_per_million REAL,
    cost_output_per_million REAL,
    context_length INTEGER,
    supports_vision INTEGER DEFAULT 0,
    supports_tools INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 0,
    is_custom INTEGER DEFAULT 0,
    custom_base_url TEXT,
    custom_api_key TEXT,
    last_fetched TEXT
)
"""


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    """Ensure the enabled_models table exists in the database.

    This is called before every route operation so that a fresh backend
    started without prior ``aip init`` can still serve model-library
    endpoints. The DDL uses IF NOT EXISTS so it is idempotent and safe
    to call on an existing database with data.

    Raises aiosqlite.Error if the DDL execution fails (e.g. permission
    issue, corrupt database) — callers should let this propagate as an
    honest 500 rather than silently swallowing it.
    """
    await conn.execute(_ENABLED_MODELS_DDL)
    await conn.commit()


class ToggleEnabledRequest(BaseModel):
    """Request body for PATCH /models/library.

    Uses a body-based route (not path parameter) so model IDs containing
    '/' (e.g. "deepseek/deepseek-v4-flash:free") are transmitted safely.
    """

    model_id: str
    enabled: int  # 0 or 1


@router.get("/models/library")
async def list_model_library() -> dict:
    """List all models in the enabled_models table.

    Returns a list of model dicts. Models are ordered by enabled (enabled
    first), then by display_name. Raw custom_api_key values are never
    exposed — only a boolean has_custom_api_key is returned.
    """
    await _ensure_schema_via_route()
    items: list[dict[str, Any]] = []
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            cursor = await conn.execute(
                """
                SELECT model_id, display_name, provider,
                       cost_input_per_million, cost_output_per_million,
                       context_length, supports_vision, supports_tools,
                       enabled, is_custom, custom_base_url, custom_api_key,
                       last_fetched
                FROM enabled_models
                ORDER BY enabled DESC, display_name ASC
                """
            )
            rows = await cursor.fetchall()
            for row in rows:
                raw_key = row["custom_api_key"]
                items.append(
                    {
                        "model_id": row["model_id"],
                        "display_name": row["display_name"],
                        "provider": row["provider"],
                        "cost_input_per_million": row["cost_input_per_million"],
                        "cost_output_per_million": row["cost_output_per_million"],
                        "context_length": row["context_length"],
                        "supports_vision": row["supports_vision"],
                        "supports_tools": row["supports_tools"],
                        "enabled": row["enabled"],
                        "is_custom": row["is_custom"],
                        "custom_base_url": row["custom_base_url"],
                        "has_custom_api_key": raw_key is not None and len(str(raw_key).strip()) > 0,
                        "last_fetched": row["last_fetched"],
                    }
                )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to list model library: %s", exc)
        return {"items": [], "total": 0}

    return {"items": items, "total": len(items)}


@router.post("/models/library/fetch")
async def fetch_model_library(
    _auth=Depends(require_definer),
) -> dict:
    """Fetch model list from OpenRouter and upsert into enabled_models.

    Per AIP-G-09: this is the ONLY outbound call, user-triggered only.
    Uses INSERT OR IGNORE so existing rows (with DEFINER-set enabled flags)
    are never overwritten. Returns count of new models added.

    Fetches from https://openrouter.ai/api/v1/models which returns a
    JSON object with a 'data' array of model objects.
    """
    await _ensure_schema_via_route()

    try:
        import httpx
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="httpx not installed — cannot fetch from OpenRouter",
        ) from None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        logger.error("OpenRouter fetch failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter fetch failed: {exc}",
        ) from exc

    models_data = body.get("data", [])
    if not isinstance(models_data, list):
        raise HTTPException(
            status_code=502,
            detail="Unexpected OpenRouter response format: 'data' is not a list",
        )

    now = datetime.now(timezone.utc).isoformat()
    new_count = 0

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        try:
            await _ensure_schema(conn)
            for model in models_data:
                if not isinstance(model, dict):
                    continue
                model_id = model.get("id", "")
                if not model_id:
                    continue

                display_name = model.get("name") or model_id.split("/")[-1]
                # Parse pricing (OpenRouter returns strings like "0.00001")
                cost_in = _parse_float(model.get("pricing", {}).get("prompt"))
                cost_out = _parse_float(model.get("pricing", {}).get("completion"))
                context_length = model.get("context_length")
                supports_vision = 1 if model.get("modality") in ("text+image", "multimodal") else 0
                supports_tools = 1 if model.get("supports_tools") else 0

                cursor = await conn.execute(
                    """
                    INSERT OR IGNORE INTO enabled_models
                        (model_id, display_name, provider,
                         cost_input_per_million, cost_output_per_million,
                         context_length, supports_vision, supports_tools,
                         enabled, is_custom, last_fetched)
                    VALUES (?, ?, 'openrouter', ?, ?, ?, ?, ?, 0, 0, ?)
                    """,
                    (
                        model_id,
                        display_name,
                        cost_in,
                        cost_out,
                        context_length,
                        supports_vision,
                        supports_tools,
                        now,
                    ),
                )
                if cursor.rowcount > 0:
                    new_count += 1

            await conn.commit()
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to upsert model library: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Database upsert failed: {exc}",
        ) from exc

    return {
        "fetched": len(models_data),
        "new_models_added": new_count,
        "last_fetched": now,
    }


@router.patch("/models/library")
async def toggle_model_enabled(
    body: ToggleEnabledRequest,
    _auth=Depends(require_definer),
) -> dict:
    """Toggle the enabled flag for a model in the library.

    Body: {"model_id": "deepseek/deepseek-v4-flash:free", "enabled": 1}

    Uses a body-based route instead of a path parameter so that model IDs
    containing '/' (and other special characters like ':', '.') are
    transmitted safely in JSON rather than being parsed as URL path
    segments.

    Returns the updated model row. Returns 404 if model_id not found.
    """
    if body.enabled not in (0, 1):
        raise HTTPException(
            status_code=400,
            detail="enabled must be 0 or 1",
        )

    await _ensure_schema_via_route()

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            await _ensure_schema(conn)

            # Check model exists
            cursor = await conn.execute(
                "SELECT model_id FROM enabled_models WHERE model_id = ?",
                (body.model_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Model not found: {body.model_id}",
                )

            # Update enabled flag
            await conn.execute(
                "UPDATE enabled_models SET enabled = ? WHERE model_id = ?",
                (body.enabled, body.model_id),
            )
            await conn.commit()

            # Return updated row
            cursor = await conn.execute(
                """
                SELECT model_id, display_name, provider, enabled
                FROM enabled_models WHERE model_id = ?
                """,
                (body.model_id,),
            )
            updated = await cursor.fetchone()
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to toggle model enabled: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Database update failed: {exc}",
        ) from exc

    return {
        "model_id": updated["model_id"],
        "display_name": updated["display_name"],
        "provider": updated["provider"],
        "enabled": updated["enabled"],
    }


async def _ensure_schema_via_route() -> None:
    """Open a short-lived connection and ensure the schema exists.

    This is a convenience wrapper used by routes that need to ensure the
    schema before opening their own main connection. It avoids duplicating
    the schema-ensure logic at every route entry point.
    """
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        try:
            await _ensure_schema(conn)
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to ensure model library schema: %s", exc)


def _parse_float(value: Any) -> float | None:
    """Parse a value that may be a string or float, returning float or None."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
