"""Crosslink System API routes — create, inspect, approve/reject, and navigate
knowledge links between first-class AIP_Brain objects.

UI Cycle 8 — Crosslink System v1.

Supported source/target object types:
  - source_document, chunk, conversation_turn, retrieval_trace,
    beast_commentary, wiki_article, artifact, review_event,
    actor_event, model_comparison_report

Supported relation types:
  - supports, contradicts, summarizes, extends, mentions,
    depends_on, implements, supersedes, related_to,
    generated_from, reviewed_by, approved_by

Sovereignty guarantees:
  - Links default to status=suggested, approved_by_definer=False
  - Approval requires explicit PATCH with approved_by_definer=True
  - No automatic mutation of linked objects
  - No automatic artifact approval/export
  - No fake links or backlinks
  - Honest unavailable/empty states when storage is unavailable
  - No secret exposure

Storage:
  - Dedicated `knowledge_links` table in state.db
  - Isolated behind adapter-layer helper (KnowledgeLinkStore)
  - Uses aiosqlite with WAL mode
  - Table created safely on first operation if missing
  - storage_backend field in responses: "knowledge_link_store" | "unavailable"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from aip.adapter.api.dependencies import AipContainer

log = logging.getLogger("aip.adapter.api.routes.links")

router = APIRouter()

# ── Constants ────────────────────────────────────────────────────────────

VALID_OBJECT_TYPES = frozenset(
    {
        "source_document",
        "chunk",
        "conversation_turn",
        "retrieval_trace",
        "beast_commentary",
        "wiki_article",
        "artifact",
        "review_event",
        "actor_event",
        "model_comparison_report",
    }
)

VALID_RELATION_TYPES = frozenset(
    {
        "supports",
        "contradicts",
        "summarizes",
        "extends",
        "mentions",
        "depends_on",
        "implements",
        "supersedes",
        "related_to",
        "generated_from",
        "reviewed_by",
        "approved_by",
        "prerequisite_of",  # Phase β-2 (2026-07-23): wiki → manual chapter ordering
    }
)

VALID_STATUSES = frozenset(
    {
        "suggested",
        "approved",
        "rejected",
        "deleted",
    }
)

# ── Pydantic models ──────────────────────────────────────────────────────


class KnowledgeLinkCreateRequest(BaseModel):
    """Request body for POST /api/v1/links."""

    source_type: str = Field(..., description="Object type of the source")
    source_id: str = Field(..., description="ID of the source object")
    target_type: str = Field(..., description="Object type of the target")
    target_id: str = Field(..., description="ID of the target object")
    relation_type: str = Field(..., description="Type of relationship between source and target")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    created_by: str = Field("definer", description="Who/what created this link")
    status: str = Field("suggested", description="Initial status — defaults to suggested")
    approved_by_definer: bool = Field(False, description="Whether the DEFINER has approved this link")
    provenance: str = Field("", description="Provenance note (e.g. 'manual', 'beast_suggestion')")
    notes: str = Field("", description="Optional freeform notes")


class KnowledgeLinkUpdateRequest(BaseModel):
    """Request body for PATCH /api/v1/links/{link_id}."""

    relation_type: str | None = Field(None, description="Updated relation type")
    confidence: float | None = Field(None, ge=0.0, le=1.0, description="Updated confidence score")
    status: str | None = Field(None, description="Updated status (suggested/approved/rejected/deleted)")
    approved_by_definer: bool | None = Field(None, description="Set to True to approve the link")
    provenance: str | None = Field(None, description="Updated provenance note")
    notes: str | None = Field(None, description="Updated freeform notes")


# ── Knowledge Link Store (adapter-layer helper) ─────────────────────────


class KnowledgeLinkStore:
    """Adapter-layer helper for knowledge_links table.

    Uses aiosqlite to a dedicated table in state.db. Isolated from
    other stores to avoid overloading graph_edges with approval/
    provenance/status metadata that it was not designed for.

    Table is created on first call if it does not exist.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def _ensure_table(self) -> None:
        """Create the knowledge_links table if it does not exist."""
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_links (
                    id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_by TEXT NOT NULL DEFAULT 'definer',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_by_definer INTEGER NOT NULL DEFAULT 0,
                    approved_at TEXT,
                    status TEXT NOT NULL DEFAULT 'suggested',
                    provenance TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT ''
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_kl_source
                ON knowledge_links(source_type, source_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_kl_target
                ON knowledge_links(target_type, target_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_kl_status
                ON knowledge_links(status)
            """)
            await db.commit()
        self._initialized = True

    async def create_link(self, link: dict[str, Any]) -> dict[str, Any]:
        """Insert a new knowledge link. Returns the full link dict."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute(
                """INSERT INTO knowledge_links
                   (id, source_type, source_id, target_type, target_id,
                    relation_type, confidence, created_by, created_at, updated_at,
                    approved_by_definer, approved_at, status, provenance, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    link["id"],
                    link["source_type"],
                    link["source_id"],
                    link["target_type"],
                    link["target_id"],
                    link["relation_type"],
                    link["confidence"],
                    link["created_by"],
                    link["created_at"],
                    link["updated_at"],
                    int(link["approved_by_definer"]),
                    link.get("approved_at"),
                    link["status"],
                    link.get("provenance", ""),
                    link.get("notes", ""),
                ),
            )
            await db.commit()
        return link

    async def get_link(self, link_id: str) -> dict[str, Any] | None:
        """Get a single link by ID. Returns None if not found."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM knowledge_links WHERE id = ?", (link_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_link(row)

    async def list_links(
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
    ) -> tuple[list[dict[str, Any]], int]:
        """List links with optional filters. Returns (links, total_count)."""
        await self._ensure_table()
        conditions: list[str] = []
        params: list[Any] = []

        if source_type is not None:
            conditions.append("source_type = ?")
            params.append(source_type)
        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)
        if target_type is not None:
            conditions.append("target_type = ?")
            params.append(target_type)
        if target_id is not None:
            conditions.append("target_id = ?")
            params.append(target_id)
        if relation_type is not None:
            conditions.append("relation_type = ?")
            params.append(relation_type)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row

            # Count
            count_cursor = await db.execute(f"SELECT COUNT(*) FROM knowledge_links {where_clause}", params)
            count_row = await count_cursor.fetchone()
            total = count_row[0]

            # Fetch
            fetch_cursor = await db.execute(
                f"SELECT * FROM knowledge_links {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            rows = await fetch_cursor.fetchall()
            links = [_row_to_link(row) for row in rows]

        return links, total

    async def get_backlinks(
        self, target_type: str, target_id: str, *, limit: int = 100
    ) -> tuple[list[dict[str, Any]], int]:
        """Get links pointing TO a given object (backlinks)."""
        return await self.list_links(target_type=target_type, target_id=target_id, limit=limit)

    async def get_forward_links(
        self, source_type: str, source_id: str, *, limit: int = 100
    ) -> tuple[list[dict[str, Any]], int]:
        """Get links pointing FROM a given object (forward links)."""
        return await self.list_links(source_type=source_type, source_id=source_id, limit=limit)

    async def update_link(self, link_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update a link. Returns updated link or None if not found."""
        await self._ensure_table()
        existing = await self.get_link(link_id)
        if existing is None:
            return None

        # Build SET clause
        set_parts: list[str] = []
        params: list[Any] = []
        for key, value in updates.items():
            if key == "approved_by_definer":
                set_parts.append("approved_by_definer = ?")
                params.append(int(value))
            elif key in ("relation_type", "confidence", "status", "provenance", "notes", "updated_at", "approved_at"):
                set_parts.append(f"{key} = ?")
                params.append(value)

        if not set_parts:
            return existing

        params.append(link_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute(
                f"UPDATE knowledge_links SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )
            await db.commit()

        return await self.get_link(link_id)

    async def delete_link(self, link_id: str) -> bool:
        """Hard-delete a link. Returns True if deleted, False if not found."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            cursor = await db.execute("DELETE FROM knowledge_links WHERE id = ?", (link_id,))
            await db.commit()
            return cursor.rowcount > 0


def _row_to_link(row: aiosqlite.Row) -> dict[str, Any]:
    """Convert a database row to a KnowledgeLink dict."""
    return {
        "id": row["id"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "relation_type": row["relation_type"],
        "confidence": row["confidence"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "approved_by_definer": bool(row["approved_by_definer"]),
        "approved_at": row["approved_at"],
        "status": row["status"],
        "provenance": row["provenance"],
        "notes": row["notes"],
    }


def _generate_link_id(source_type: str, source_id: str, target_type: str, target_id: str, relation_type: str) -> str:
    """Generate a stable, unique link ID.

    Format: link:{source_type}:{source_id}__{relation_type}__{target_type}:{target_id}
    Truncated for readability but still unique enough for v1.
    """
    # Use hash for long IDs to keep the link ID manageable
    import hashlib

    raw = f"{source_type}:{source_id}|{relation_type}|{target_type}:{target_id}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"link:{h}:{ts}"


# ── Route handlers ───────────────────────────────────────────────────────


def _get_db_path(container: AipContainer | None) -> str | None:
    """Get the state.db path from the container's store registry, or None."""
    if container is None:
        return None
    registry = getattr(container, "_store_registry", None)
    if registry and "state" in registry:
        return registry["state"]
    # Fallback: check config
    config = getattr(container, "config", {})
    db_path = config.get("db_path", config.get("database", {}).get("path"))
    if db_path:
        return db_path
    return None


@router.get("/links")
async def list_links(
    source_type: str | None = Query(None),
    source_id: str | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    relation_type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    request: Request = None,
) -> dict[str, Any]:
    """List knowledge links with optional filters.

    Returns honest empty list if no links match or storage is unavailable.
    """
    container = _get_container_from_request(request)
    db_path = _get_db_path(container)

    if not db_path:
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "storage_backend": "unavailable",
        }

    try:
        store = KnowledgeLinkStore(db_path)
        links, total = await store.list_links(
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            relation_type=relation_type,
            status=status,
            limit=limit,
            offset=offset,
        )
        return {
            "items": links,
            "total": total,
            "limit": limit,
            "offset": offset,
            "storage_backend": "knowledge_link_store",
        }
    except Exception as exc:
        log.error("list_links_failed: %s", exc)
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "storage_backend": "unavailable",
            "error": str(exc),
        }


@router.post("/links", status_code=201)
async def create_link(
    body: KnowledgeLinkCreateRequest,
    request: Request = None,
) -> dict[str, Any]:
    """Create a new knowledge link.

    Defaults:
      - status: suggested
      - approved_by_definer: False
    No linked objects are mutated. No artifacts are approved/exported.
    """
    # Validate object types
    if body.source_type not in VALID_OBJECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type: {body.source_type}. Valid types: {', '.join(sorted(VALID_OBJECT_TYPES))}",
        )
    if body.target_type not in VALID_OBJECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_type: {body.target_type}. Valid types: {', '.join(sorted(VALID_OBJECT_TYPES))}",
        )
    # Validate relation type
    if body.relation_type not in VALID_RELATION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid relation_type: {body.relation_type}. "
            f"Valid types: {', '.join(sorted(VALID_RELATION_TYPES))}",
        )
    # Prevent self-links
    if body.source_type == body.target_type and body.source_id == body.target_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot create a self-referential link (source and target are the same object).",
        )
    # Only allow 'suggested' or 'approved' status on creation
    if body.status not in ("suggested", "approved"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid initial status: {body.status}. Must be 'suggested' or 'approved'.",
        )
    # If status is 'approved', approved_by_definer must be True
    if body.status == "approved" and not body.approved_by_definer:
        raise HTTPException(
            status_code=400,
            detail="Cannot create link with status 'approved' without approved_by_definer=True.",
        )

    container = _get_container_from_request(request)
    db_path = _get_db_path(container)

    if not db_path:
        raise HTTPException(
            status_code=503,
            detail="Knowledge link storage unavailable — no database path configured.",
        )

    now = datetime.now(timezone.utc).isoformat()
    link_id = _generate_link_id(
        body.source_type,
        body.source_id,
        body.target_type,
        body.target_id,
        body.relation_type,
    )

    link = {
        "id": link_id,
        "source_type": body.source_type,
        "source_id": body.source_id,
        "target_type": body.target_type,
        "target_id": body.target_id,
        "relation_type": body.relation_type,
        "confidence": body.confidence,
        "created_by": body.created_by,
        "created_at": now,
        "updated_at": now,
        "approved_by_definer": body.approved_by_definer,
        "approved_at": now if body.approved_by_definer else None,
        "status": body.status,
        "provenance": body.provenance,
        "notes": body.notes,
    }

    try:
        store = KnowledgeLinkStore(db_path)
        result = await store.create_link(link)
        result["storage_backend"] = "knowledge_link_store"
        return result
    except Exception as exc:
        log.error("create_link_failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create link: {exc}")


@router.patch("/links/{link_id}")
async def update_link(
    link_id: str,
    body: KnowledgeLinkUpdateRequest,
    request: Request = None,
) -> dict[str, Any]:
    """Update a knowledge link.

    Approve: PATCH with approved_by_definer=True
    Reject: PATCH with status='rejected'
    Edit: PATCH with updated relation_type/confidence/notes
    """
    container = _get_container_from_request(request)
    db_path = _get_db_path(container)

    if not db_path:
        raise HTTPException(
            status_code=503,
            detail="Knowledge link storage unavailable.",
        )

    # Validate relation type if provided
    if body.relation_type is not None and body.relation_type not in VALID_RELATION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid relation_type: {body.relation_type}. "
            f"Valid types: {', '.join(sorted(VALID_RELATION_TYPES))}",
        )
    # Validate status if provided
    if body.status is not None and body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {body.status}. Valid statuses: {', '.join(sorted(VALID_STATUSES))}",
        )

    try:
        store = KnowledgeLinkStore(db_path)
        existing = await store.get_link(link_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Link not found: {link_id}")

        now = datetime.now(timezone.utc).isoformat()
        updates: dict[str, Any] = {"updated_at": now}

        if body.relation_type is not None:
            updates["relation_type"] = body.relation_type
        if body.confidence is not None:
            updates["confidence"] = body.confidence
        if body.status is not None:
            updates["status"] = body.status
        if body.provenance is not None:
            updates["provenance"] = body.provenance
        if body.notes is not None:
            updates["notes"] = body.notes

        # Approval handling
        if body.approved_by_definer is True:
            updates["approved_by_definer"] = True
            updates["approved_at"] = now
            if body.status is None:
                updates["status"] = "approved"
        elif body.approved_by_definer is False:
            updates["approved_by_definer"] = False
            updates["approved_at"] = None

        result = await store.update_link(link_id, updates)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Link not found after update: {link_id}")

        result["storage_backend"] = "knowledge_link_store"
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log.error("update_link_failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to update link: {exc}")


@router.delete("/links/{link_id}")
async def delete_link(
    link_id: str,
    request: Request = None,
) -> dict[str, Any]:
    """Delete a knowledge link.

    Uses hard delete for v1 simplicity. No linked objects are mutated.
    """
    container = _get_container_from_request(request)
    db_path = _get_db_path(container)

    if not db_path:
        raise HTTPException(
            status_code=503,
            detail="Knowledge link storage unavailable.",
        )

    try:
        store = KnowledgeLinkStore(db_path)
        deleted = await store.delete_link(link_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Link not found: {link_id}")
        return {
            "id": link_id,
            "deleted": True,
            "storage_backend": "knowledge_link_store",
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("delete_link_failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to delete link: {exc}")


@router.get("/links/backlinks/{target_type}/{target_id}")
async def get_backlinks(
    target_type: str,
    target_id: str,
    limit: int = Query(100, ge=1, le=1000),
    request: Request = None,
) -> dict[str, Any]:
    """Get backlinks (links pointing TO a given object).

    Returns honest empty list if no backlinks exist or storage is unavailable.
    """
    if target_type not in VALID_OBJECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_type: {target_type}. Valid types: {', '.join(sorted(VALID_OBJECT_TYPES))}",
        )

    container = _get_container_from_request(request)
    db_path = _get_db_path(container)

    if not db_path:
        return {
            "target_type": target_type,
            "target_id": target_id,
            "backlinks": [],
            "total": 0,
            "available": False,
            "storage_backend": "unavailable",
        }

    try:
        store = KnowledgeLinkStore(db_path)
        links, total = await store.get_backlinks(target_type, target_id, limit=limit)
        return {
            "target_type": target_type,
            "target_id": target_id,
            "backlinks": links,
            "total": total,
            "available": True,
            "storage_backend": "knowledge_link_store",
        }
    except Exception as exc:
        log.error("get_backlinks_failed: %s", exc)
        return {
            "target_type": target_type,
            "target_id": target_id,
            "backlinks": [],
            "total": 0,
            "available": False,
            "storage_backend": "unavailable",
            "error": str(exc),
        }


@router.get("/links/forward/{source_type}/{source_id}")
async def get_forward_links(
    source_type: str,
    source_id: str,
    limit: int = Query(100, ge=1, le=1000),
    request: Request = None,
) -> dict[str, Any]:
    """Get forward links (links pointing FROM a given object).

    Returns honest empty list if no forward links exist or storage is unavailable.
    """
    if source_type not in VALID_OBJECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type: {source_type}. Valid types: {', '.join(sorted(VALID_OBJECT_TYPES))}",
        )

    container = _get_container_from_request(request)
    db_path = _get_db_path(container)

    if not db_path:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "forward_links": [],
            "total": 0,
            "available": False,
            "storage_backend": "unavailable",
        }

    try:
        store = KnowledgeLinkStore(db_path)
        links, total = await store.get_forward_links(source_type, source_id, limit=limit)
        return {
            "source_type": source_type,
            "source_id": source_id,
            "forward_links": links,
            "total": total,
            "available": True,
            "storage_backend": "knowledge_link_store",
        }
    except Exception as exc:
        log.error("get_forward_links_failed: %s", exc)
        return {
            "source_type": source_type,
            "source_id": source_id,
            "forward_links": [],
            "total": 0,
            "available": False,
            "storage_backend": "unavailable",
            "error": str(exc),
        }


# ── Helper ───────────────────────────────────────────────────────────────


def _get_container_from_request(request: Any) -> AipContainer | None:
    """Extract the AipContainer from the request's app state."""
    if request is None:
        return None
    try:
        app = getattr(request, "app", None)
        if app is None:
            return None
        state = getattr(app, "state", None)
        if state is None:
            return None
        return getattr(state, "container", None)
    except Exception:
        return None
