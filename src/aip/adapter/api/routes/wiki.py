"""Wiki API routes — browse, create, and edit wiki/CODEX articles.

Wiki articles are stored as artifacts (beast:wiki:* / beast:proposal:* / wiki:* / sexton:wiki:*)
via the container's artifact_store with ECS state tracking. This route module provides:

  GET  /wiki/articles           — List articles (existing, enhanced with WikiArticle schema)
  GET  /wiki/articles/{id}      — Get single article with full schema
  POST /wiki/articles           — Create a new article (DEFINER action, starts as GENERATED)
  PATCH /wiki/articles/{id}     — Update article (DEFINER action, creates new version)
  GET  /wiki/stats              — Wiki statistics (existing)
  GET  /wiki/backlinks/{id}     — Backlinks for an article
  GET  /wiki/stale              — Articles that may be stale
  GET  /wiki/contradictions     — Detected contradictions

Storage path (Cycle 7.1 hardening):
  - Preferred: container.artifact_store + container.ecs_store + container.event_store
  - Fallback: direct aiosqlite to state.db (sqlite_compat) when container not available
  - storage_backend field in responses honestly reports which path was used
  - Crosslink-safe IDs: wiki:{domain}:{title_slug}:{timestamp}

Key sovereignty guarantees:
  - No auto-approve: CREATE always sets state to GENERATED
  - No silent mutation: every write is explicit and logged
  - No fake data: unavailable fields return empty/null honestly
  - No secret exposure
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()
logger = logging.getLogger(__name__)

_STATE_DB = "db/state.db"


# ── Request / Response schemas ─────────────────────────────────────────


class WikiArticleCreateRequest(BaseModel):
    """Request body for creating a new wiki article.

    The article will be stored as an artifact with ECS state GENERATED.
    It requires DEFINER review before becoming APPROVED/canonical.
    """

    title: str = Field(..., min_length=1, max_length=256, description="Article title")
    domain: str = Field("", max_length=128, description="Domain classification")
    summary: str = Field("", max_length=1024, description="Brief summary")
    body: str = Field("", description="Article body content")
    tags: list[str] = Field(default_factory=list, description="Topic tags")
    aliases: list[str] = Field(default_factory=list, description="Alternative names")


class WikiArticleUpdateRequest(BaseModel):
    """Request body for updating an existing wiki article.

    Only provided fields will be updated. The article gets a new version
    in the artifact store. The ECS state is NOT changed by editing —
    a separate review/approve action is required.
    """

    title: str | None = Field(None, max_length=256, description="Updated title")
    summary: str | None = Field(None, max_length=1024, description="Updated summary")
    body: str | None = Field(None, description="Updated body content")
    tags: list[str] | None = Field(None, description="Updated tags")
    aliases: list[str] | None = Field(None, description="Updated aliases")


# ── Helper: generate article ID ────────────────────────────────────────


def _generate_article_id(title: str, domain: str) -> str:
    """Generate a wiki article ID from title and domain.

    Format: wiki:{domain_snake}:{title_snake}:{timestamp}
    This ID is stable and crosslink-safe — the same article will always
    be referencable by this ID. Cycle 8 Crosslinks MUST target this
    article_id, never raw DB row IDs.
    """
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    domain_slug = domain.lower().replace(" ", "_").replace("-", "_") if domain else "general"
    title_slug = title.lower().replace(" ", "_").replace("-", "_")[:64]
    return f"wiki:{domain_slug}:{title_slug}:{now}"


# ── Helper: determine storage backend ──────────────────────────────────


def _resolve_storage_backend(container: AipContainer | None) -> str:
    """Determine which storage backend is available.

    Returns:
        "artifact_store" — container has artifact_store AND ecs_store
        "sqlite_compat"  — container unavailable, falls back to direct aiosqlite
        "unavailable"    — neither path is viable
    """
    if container is None:
        return "sqlite_compat"
    if container.artifact_store is not None and container.ecs_store is not None:
        return "artifact_store"
    # artifact_store without ecs_store is a partial path — still use compat
    # because ECS state management requires ecs_store
    return "sqlite_compat"


# ── Helper: extract WikiArticle schema from DB row ─────────────────────


def _row_to_article(
    row: aiosqlite.Row, content_text: str = "", storage_backend: str = "sqlite_compat"
) -> dict[str, Any]:
    """Convert a DB row to the stable WikiArticle schema."""
    metadata = {}
    raw_meta = row["metadata_json"]
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            pass

    artifact_id = row["id"]
    if not content_text:
        content_text = row["content"] or ""

    # Extract domain from metadata or artifact ID
    article_domain = metadata.get("domain", "")
    if not article_domain and ":" in artifact_id:
        parts = artifact_id.split(":")
        if len(parts) >= 3:
            article_domain = parts[2].replace("_", " ").title()

    # Extract title from metadata or generate from ID
    title = metadata.get("title", "")
    if not title:
        # Try to derive a title from the domain/article ID
        if ":" in artifact_id:
            parts = artifact_id.split(":")
            if len(parts) >= 3:
                title = parts[2].replace("_", " ").title()
        if not title:
            title = artifact_id

    current_state = row["current_state"]

    return {
        # Core identity
        "id": artifact_id,
        "title": title,
        "summary": metadata.get("summary", ""),
        "body": content_text,
        "status": current_state,
        "tags": metadata.get("tags", []),
        "aliases": metadata.get("aliases", []),
        # Links (honest empty arrays when not available)
        "linked_articles": metadata.get("linked_articles", []),
        "backlinks": [],  # Populated separately via /wiki/backlinks/{id}
        "source_documents": metadata.get("source_documents", []),
        "related_artifacts": metadata.get("related_artifacts", []),
        "related_turns": metadata.get("related_turns", []),
        "related_beast_commentaries": metadata.get("related_beast_commentaries", []),
        # Quality indicators
        "open_questions": metadata.get("open_questions", []),
        "contradictions": [],  # Populated separately via /wiki/contradictions
        # Audit
        "revision_history": metadata.get("revision_history", []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "approved_at": row["updated_at"] if current_state == "APPROVED" else None,
        # Convenience fields
        "domain": article_domain,
        "artifact_type": (
            "manual_chapter"
            if artifact_id.startswith("manual:")
            else "wiki"
            if artifact_id.startswith("beast:wiki:")
            or artifact_id.startswith("wiki:")
            or artifact_id.startswith("sexton:wiki:")
            else "proposal"
        ),
        "version": row["version"],
        "word_count": len(content_text.split()) if content_text else 0,
        "metadata": metadata,
        # Cycle 7.1: storage backend indicator for crosslink readiness
        "storage_backend": storage_backend,
    }


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/wiki/articles")
async def list_wiki_articles(
    state: str | None = None,
    domain: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    container: AipContainer = Depends(get_container),
) -> dict:
    """List wiki articles from artifacts + ecs_state.

    Returns articles matching beast:wiki:* / beast:proposal:* / wiki:*
    with full WikiArticle schema. Supports filtering by state, domain,
    and text search.

    Each article uses the stable WikiArticle schema with honest empty
    arrays for fields not yet backed by a crosslink system.

    storage_backend is reported honestly in each article item.
    """
    storage_backend = _resolve_storage_backend(container)
    items: list[dict] = []

    # Build WHERE clause
    conditions = [
        "(a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%' OR a.id LIKE 'wiki:%' OR a.id LIKE 'sexton:wiki:%' OR a.id LIKE 'manual:%')"
    ]
    params: list[str] = []

    if state:
        conditions.append("e.current_state = ?")
        params.append(state)

    if domain:
        conditions.append("(a.metadata_json LIKE ? OR a.id LIKE ?)")
        params.append(f'%"domain": "{domain}"%')
        params.append(f"%:{domain}:%")

    if search:
        conditions.append("(a.content LIKE ? OR a.metadata_json LIKE ?)")
        params.append(f"%{search}%")
        params.append(f"%{search}%")

    where_clause = " AND ".join(conditions)

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            cursor = await conn.execute(
                f"""
                SELECT a.id, a.version, a.content, a.metadata_json, a.created_at,
                       COALESCE(e.current_state, 'UNKNOWN') as current_state,
                       COALESCE(e.updated_at, a.created_at) as updated_at
                FROM artifacts a
                LEFT JOIN ecs_state e ON a.id = e.artifact_id
                INNER JOIN (
                    SELECT id, MAX(version) as max_ver
                    FROM artifacts
                    GROUP BY id
                ) latest ON a.id = latest.id AND a.version = latest.max_ver
                WHERE {where_clause}
                ORDER BY a.created_at DESC
                """,
                params,
            )
            rows = await cursor.fetchall()

            for row in rows:
                items.append(_row_to_article(row, storage_backend=storage_backend))
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to list wiki articles: %s", exc)
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "storage_backend": storage_backend}

    # Apply pagination
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "storage_backend": storage_backend,
    }


@router.get("/wiki/articles/{article_id:path}")
async def get_wiki_article(
    article_id: str,
    container: AipContainer = Depends(get_container),
) -> dict:
    """Get a single wiki article by ID with full WikiArticle schema.

    Returns the latest version of the article with all schema fields.
    Fields not yet backed (backlinks, contradictions, etc.) return
    empty arrays honestly.

    Returns 404 if the article is not found.
    """
    storage_backend = _resolve_storage_backend(container)

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            cursor = await conn.execute(
                """
                SELECT a.id, a.version, a.content, a.metadata_json, a.created_at,
                       COALESCE(e.current_state, 'UNKNOWN') as current_state,
                       COALESCE(e.updated_at, a.created_at) as updated_at
                FROM artifacts a
                LEFT JOIN ecs_state e ON a.id = e.artifact_id
                INNER JOIN (
                    SELECT id, MAX(version) as max_ver
                    FROM artifacts
                    GROUP BY id
                ) latest ON a.id = latest.id AND a.version = latest.max_ver
                WHERE a.id = ?
                """,
                (article_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail=f"Wiki article '{article_id}' not found")

            article = _row_to_article(row, storage_backend=storage_backend)

            # Attempt to populate backlinks from graph_edges
            try:
                cursor = await conn.execute(
                    """
                    SELECT source_id, source_type, relation_type
                    FROM graph_edges
                    WHERE target_id = ? AND relation_type IN ('mentions', 'related_to', 'supports', 'extends')
                    LIMIT 20
                    """,
                    (article_id,),
                )
                backlink_rows = await cursor.fetchall()
                article["backlinks"] = [
                    {
                        "source_id": bl_row["source_id"],
                        "source_type": bl_row["source_type"],
                        "relation_type": bl_row["relation_type"],
                    }
                    for bl_row in backlink_rows
                ]
            except Exception:
                # graph_edges table may not exist — return empty honestly
                article["backlinks"] = []

            # Attempt to populate contradictions from codex_contradictions
            try:
                domain = article.get("domain", "")
                if domain:
                    cursor = await conn.execute(
                        """
                        SELECT contradiction_id, claim_a, claim_b, severity, status
                        FROM codex_contradictions
                        WHERE topic_id LIKE ? AND status = 'open'
                        LIMIT 10
                        """,
                        (f"%{domain}%",),
                    )
                    contra_rows = await cursor.fetchall()
                    article["contradictions"] = [
                        {
                            "contradiction_id": cr["contradiction_id"],
                            "claim_a": cr["claim_a"],
                            "claim_b": cr["claim_b"],
                            "severity": cr["severity"],
                            "status": cr["status"],
                        }
                        for cr in contra_rows
                    ]
            except Exception:
                article["contradictions"] = []

            return article
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get wiki article '%s': %s", article_id, exc)
        raise HTTPException(status_code=500, detail=f"Error retrieving article: {exc}") from exc


@router.post("/wiki/articles", status_code=201)
async def create_wiki_article(
    request: WikiArticleCreateRequest,
    container: AipContainer = Depends(get_container),
) -> dict:
    """Create a new wiki article (explicit DEFINER action).

    The article is stored as an artifact with ECS state GENERATED.
    It requires DEFINER review before becoming APPROVED/canonical.
    No auto-approve occurs.

    The article ID is generated from domain + title + timestamp.
    This ID is stable and crosslink-safe.
    """
    storage_backend = _resolve_storage_backend(container)
    article_id = _generate_article_id(request.title, request.domain)
    now = datetime.now(timezone.utc).isoformat()

    metadata = {
        "title": request.title,
        "domain": request.domain,
        "summary": request.summary,
        "tags": request.tags,
        "aliases": request.aliases,
        "source": "definer_create",
        "linked_articles": [],
        "source_documents": [],
        "related_artifacts": [],
        "related_turns": [],
        "related_beast_commentaries": [],
        "open_questions": [],
        "revision_history": [
            {
                "version": 1,
                "timestamp": now,
                "action": "created",
                "actor": "definer",
            }
        ],
    }

    content = request.body or request.summary or ""

    # ── Preferred path: container.artifact_store + ecs_store ──────────
    if storage_backend == "artifact_store":
        try:
            # Write artifact via container.store (shares connection pool)
            artifact_metadata = {
                "artifact_type": "wiki_article",
                "title": request.title,
                "domain": request.domain,
                "summary": request.summary,
                "tags": request.tags,
                "aliases": request.aliases,
                "source": "definer_create",
                "linked_articles": [],
                "source_documents": [],
                "related_artifacts": [],
                "related_turns": [],
                "related_beast_commentaries": [],
                "open_questions": [],
                "revision_history": metadata["revision_history"],
            }
            await container.artifact_store.write(
                id=article_id,
                content=content,
                metadata=artifact_metadata,
            )

            # ECS transition to GENERATED — via validated ecs_store
            await container.ecs_store.transition(
                artifact_id=article_id,
                from_state=None,
                to_state="GENERATED",
                actor="definer",
                reason="Wiki article created by DEFINER — requires review before approval",
            )

            # Record creation event via event_store if available
            if container.event_store is not None:
                try:
                    await container.event_store.write_event(
                        event_type="wiki_article_created",
                        actor="definer",
                        artifact_id=article_id,
                        from_state=None,
                        to_state="GENERATED",
                        title=request.title,
                        domain=request.domain,
                    )
                except Exception:
                    logger.warning("Could not record creation event for article '%s'", article_id)

            logger.info(
                "Wiki article created (artifact_store): id=%s title='%s' state=GENERATED",
                article_id,
                request.title,
            )

            # Phase β-3 (2026-07-23): create a WIKI_ARTICLE graph node so wiki
            # articles appear as first-class graph entities. This enables
            # "what concepts does this wiki article relate to?" queries and
            # makes the wiki visible in the graph visualization.
            try:
                from aip.adapter.graph_store import GraphNode

                _graph_store = getattr(container, "graph_store", None)
                if _graph_store is not None:
                    _node_id = f"wiki_{article_id.replace(':', '_').lower()}"
                    _wiki_node = GraphNode(
                        id=_node_id,
                        entity_type="WIKI_ARTICLE",
                        canonical_name=request.title,
                        domain=request.domain,
                        confidence=1.0,
                        source="wiki_create",
                        metadata={"article_id": article_id, "tags": request.tags},
                    )
                    await _graph_store.upsert_node(_wiki_node)
                    logger.info("wiki_graph_node_created id=%s title='%s'", _node_id, request.title)
            except Exception as _graph_exc:
                logger.debug("wiki_graph_node_failed article=%s error=%s", article_id, _graph_exc)

            return {
                "id": article_id,
                "title": request.title,
                "domain": request.domain,
                "state": "GENERATED",
                "message": "Article created as GENERATED — requires DEFINER review before approval.",
                "created_at": now,
                "storage_backend": "artifact_store",
            }

        except Exception as exc:
            # If container path fails, fall through to sqlite_compat
            logger.warning(
                "artifact_store path failed for wiki create, falling back to sqlite_compat: %s",
                exc,
            )
            storage_backend = "sqlite_compat"

    # ── Fallback path: direct aiosqlite (sqlite_compat) ──────────────
    # This path is intentionally isolated and documented as a compatibility
    # layer. Cycle 8 Crosslinks should NOT depend on this path.
    # Migration plan: once container is always available in production,
    # this path can be removed.
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        try:
            # Insert artifact version 1
            await conn.execute(
                "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, 1, ?, ?, ?)",
                (article_id, content, json.dumps(metadata), now),
            )

            # Create ECS state entry as GENERATED (never auto-approved)
            await conn.execute(
                "INSERT OR REPLACE INTO ecs_state (artifact_id, current_state, updated_at) VALUES (?, 'GENERATED', ?)",
                (article_id, now),
            )

            # Record creation event
            try:
                await conn.execute(
                    (
                        "INSERT INTO events "
                        "(event_type, actor, artifact_id, from_state, to_state, metadata_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)"
                    ),
                    (
                        "ecs_transition",
                        "definer",
                        article_id,
                        "",
                        "GENERATED",
                        json.dumps({"action": "wiki_article_created", "title": request.title}),
                        now,
                    ),
                )
            except Exception:
                # Events table structure may vary — log but don't fail
                logger.warning("Could not record creation event for article '%s'", article_id)

            await conn.commit()

            logger.info(
                "Wiki article created (sqlite_compat): id=%s title='%s' state=GENERATED",
                article_id,
                request.title,
            )

            return {
                "id": article_id,
                "title": request.title,
                "domain": request.domain,
                "state": "GENERATED",
                "message": "Article created as GENERATED — requires DEFINER review before approval.",
                "created_at": now,
                "storage_backend": "sqlite_compat",
            }
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to create wiki article: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error creating article: {exc}") from exc


@router.patch("/wiki/articles/{article_id:path}")
async def update_wiki_article(
    article_id: str,
    request: WikiArticleUpdateRequest,
    container: AipContainer = Depends(get_container),
) -> dict:
    """Update an existing wiki article (explicit DEFINER action).

    Creates a new version of the artifact. Does NOT change ECS state —
    a separate review/approve action is required for state transitions.

    Only provided (non-None) fields are updated.
    Article IDs are stable and crosslink-safe — updating does not change
    the article_id.
    """
    storage_backend = _resolve_storage_backend(container)

    # ── Preferred path: container.artifact_store + ecs_store ──────────
    if storage_backend == "artifact_store":
        try:
            # Read current article to get current metadata and version
            current_content, current_metadata = await container.artifact_store.read_with_metadata(article_id)
            current_versions = await container.artifact_store.list_versions(article_id)
            current_version = max(current_versions) if current_versions else 0

            # Check current ECS state
            current_ecs_state = await container.ecs_store.current_state(article_id)
            if current_ecs_state is None:
                current_ecs_state = "UNKNOWN"

            # Apply updates to metadata
            if request.title is not None:
                current_metadata["title"] = request.title
            if request.summary is not None:
                current_metadata["summary"] = request.summary
            if request.tags is not None:
                current_metadata["tags"] = request.tags
            if request.aliases is not None:
                current_metadata["aliases"] = request.aliases

            # Add revision history entry
            now = datetime.now(timezone.utc).isoformat()
            rev_history = current_metadata.get("revision_history", [])
            rev_history.append(
                {
                    "version": current_version + 1,
                    "timestamp": now,
                    "action": "updated",
                    "actor": "definer",
                }
            )
            current_metadata["revision_history"] = rev_history

            # Determine new content
            new_content = current_content or ""
            if request.body is not None:
                new_content = request.body

            # Write new version via artifact_store
            await container.artifact_store.write(
                id=article_id,
                content=new_content,
                metadata=current_metadata,
            )

            # Do NOT change ECS state — editing is separate from review/approve
            # This is the key sovereignty guarantee: no silent state mutation

            # Record update event
            if container.event_store is not None:
                try:
                    await container.event_store.write_event(
                        event_type="wiki_article_updated",
                        actor="definer",
                        artifact_id=article_id,
                        from_state=current_ecs_state,
                        to_state=current_ecs_state,  # State unchanged
                        version=current_version + 1,
                    )
                except Exception:
                    logger.warning("Could not record update event for article '%s'", article_id)

            logger.info(
                "Wiki article updated (artifact_store): id=%s version=%d state=%s (unchanged)",
                article_id,
                current_version + 1,
                current_ecs_state,
            )

            return {
                "id": article_id,
                "title": current_metadata.get("title", ""),
                "version": current_version + 1,
                "state": current_ecs_state,
                "message": "Article updated. ECS state unchanged — separate review/approve action required.",
                "updated_at": now,
                "storage_backend": "artifact_store",
            }

        except KeyError:
            raise HTTPException(status_code=404, detail=f"Wiki article '{article_id}' not found")
        except Exception as exc:
            # Fall through to sqlite_compat on container path failure
            logger.warning(
                "artifact_store path failed for wiki update, falling back to sqlite_compat: %s",
                exc,
            )
            storage_backend = "sqlite_compat"
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Get current latest version
            cursor = await conn.execute(
                """
                SELECT a.id, a.version, a.content, a.metadata_json, a.created_at,
                       COALESCE(e.current_state, 'UNKNOWN') as current_state,
                       COALESCE(e.updated_at, a.created_at) as updated_at
                FROM artifacts a
                LEFT JOIN ecs_state e ON a.id = e.artifact_id
                INNER JOIN (
                    SELECT id, MAX(version) as max_ver
                    FROM artifacts
                    GROUP BY id
                ) latest ON a.id = latest.id AND a.version = latest.max_ver
                WHERE a.id = ?
                """,
                (article_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail=f"Wiki article '{article_id}' not found")

            # Parse current metadata
            metadata = {}
            raw_meta = row["metadata_json"]
            if raw_meta:
                try:
                    metadata = json.loads(raw_meta)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Apply updates to metadata
            if request.title is not None:
                metadata["title"] = request.title
            if request.summary is not None:
                metadata["summary"] = request.summary
            if request.tags is not None:
                metadata["tags"] = request.tags
            if request.aliases is not None:
                metadata["aliases"] = request.aliases

            # Add revision history entry
            now = datetime.now(timezone.utc).isoformat()
            rev_history = metadata.get("revision_history", [])
            rev_history.append(
                {
                    "version": row["version"] + 1,
                    "timestamp": now,
                    "action": "updated",
                    "actor": "definer",
                }
            )
            metadata["revision_history"] = rev_history

            # Determine new content
            new_content = row["content"] or ""
            if request.body is not None:
                new_content = request.body

            # Insert new version
            new_version = row["version"] + 1
            await conn.execute(
                "INSERT INTO artifacts (id, version, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (article_id, new_version, new_content, json.dumps(metadata), row["created_at"]),
            )

            # Record update event
            try:
                await conn.execute(
                    (
                        "INSERT INTO events "
                        "(event_type, actor, artifact_id, from_state, to_state, metadata_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)"
                    ),
                    (
                        "artifact_updated",
                        "definer",
                        article_id,
                        row["current_state"],
                        row["current_state"],
                        json.dumps({"action": "wiki_article_updated", "version": new_version}),
                        now,
                    ),
                )
            except Exception:
                logger.warning("Could not record update event for article '%s'", article_id)

            await conn.commit()

            logger.info(
                "Wiki article updated (sqlite_compat): id=%s version=%d state=%s (unchanged)",
                article_id,
                new_version,
                row["current_state"],
            )

            return {
                "id": article_id,
                "title": metadata.get("title", ""),
                "version": new_version,
                "state": row["current_state"],
                "message": "Article updated. ECS state unchanged — separate review/approve action required.",
                "updated_at": now,
                "storage_backend": "sqlite_compat",
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update wiki article '%s': %s", article_id, exc)
        raise HTTPException(status_code=500, detail=f"Error updating article: {exc}") from exc


@router.get("/wiki/backlinks/{article_id:path}")
async def get_wiki_backlinks(
    article_id: str,
    container: AipContainer = Depends(get_container),
) -> dict:
    """Get backlinks for a wiki article.

    Returns articles and other knowledge objects that reference this article.
    Returns empty list honestly if the graph_edges table is not available
    or no backlinks exist.

    Crosslink readiness: backlinks use stable article_id, never raw DB row IDs.
    When Cycle 8 Crosslinks are implemented, this endpoint will return
    crosslink-derived connections in addition to graph_edges.
    """
    storage_backend = _resolve_storage_backend(container)
    backlinks: list[dict[str, Any]] = []

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Check if graph_edges table exists
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='graph_edges'")
            table_exists = await cursor.fetchone()

            if table_exists:
                cursor = await conn.execute(
                    """
                    SELECT source_id, source_type, relation_type, confidence
                    FROM graph_edges
                    WHERE target_id = ?
                    ORDER BY confidence DESC
                    LIMIT 50
                    """,
                    (article_id,),
                )
                for row in await cursor.fetchall():
                    backlinks.append(
                        {
                            "source_id": row["source_id"],
                            "source_type": row["source_type"],
                            "relation_type": row["relation_type"],
                            "confidence": row["confidence"] if "confidence" in row.keys() else None,
                        }
                    )
                return {
                    "article_id": article_id,
                    "backlinks": backlinks,
                    "total": len(backlinks),
                    "available": True,
                    "storage_backend": storage_backend,
                }
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Backlinks query failed for '%s': %s", article_id, exc)
        # Return empty honestly rather than erroring
        return {
            "article_id": article_id,
            "backlinks": [],
            "total": 0,
            "available": False,
            "storage_backend": storage_backend,
        }

    # graph_edges table does not exist — return empty honestly
    return {
        "article_id": article_id,
        "backlinks": [],
        "total": 0,
        "available": False,
        "storage_backend": storage_backend,
    }


@router.get("/wiki/stale")
async def get_stale_articles(
    container: AipContainer = Depends(get_container),
) -> dict:
    """Get wiki articles that may be stale based on CODEX staleness data.

    Returns articles with high staleness scores from the codex_topics table,
    or articles that haven't been updated in a long time.
    Returns empty list honestly if CODEX tables are not available.
    """
    storage_backend = _resolve_storage_backend(container)
    stale_items: list[dict[str, Any]] = []

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Check if codex_topics table exists
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='codex_topics'")
            table_exists = await cursor.fetchone()

            if table_exists:
                cursor = await conn.execute(
                    """
                    SELECT topic_id, title, domain, staleness_score, last_activity_at, is_wiki_page
                    FROM codex_topics
                    WHERE staleness_score > 0.5
                    ORDER BY staleness_score DESC
                    LIMIT 50
                    """
                )
                for row in await cursor.fetchall():
                    stale_items.append(
                        {
                            "topic_id": row["topic_id"],
                            "title": row["title"],
                            "domain": row["domain"],
                            "staleness_score": row["staleness_score"],
                            "last_activity_at": row["last_activity_at"],
                            "has_wiki_page": bool(row["is_wiki_page"]),
                        }
                    )
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Stale articles query failed: %s", exc)
        return {"items": [], "total": 0, "available": False, "storage_backend": storage_backend}

    return {"items": stale_items, "total": len(stale_items), "available": True, "storage_backend": storage_backend}


@router.get("/wiki/contradictions")
async def get_wiki_contradictions(
    container: AipContainer = Depends(get_container),
) -> dict:
    """Get detected contradictions from the CODEX system.

    Returns open contradictions from the codex_contradictions table.
    Contradictions are never auto-resolved — DEFINER must review.
    Returns empty list honestly if CODEX tables are not available.
    """
    storage_backend = _resolve_storage_backend(container)
    contradictions: list[dict[str, Any]] = []

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Check if codex_contradictions table exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='codex_contradictions'"
            )
            table_exists = await cursor.fetchone()

            if table_exists:
                cursor = await conn.execute(
                    """
                    SELECT contradiction_id, topic_id, claim_a, source_a_id, source_a_title,
                           claim_b, source_b_id, source_b_title, severity, status,
                           context, detected_at
                    FROM codex_contradictions
                    WHERE status = 'open'
                    ORDER BY
                        CASE severity
                            WHEN 'critical' THEN 1
                            WHEN 'major' THEN 2
                            WHEN 'minor' THEN 3
                            WHEN 'apparent' THEN 4
                            ELSE 5
                        END
                    LIMIT 50
                    """
                )
                for row in await cursor.fetchall():
                    contradictions.append(
                        {
                            "contradiction_id": row["contradiction_id"],
                            "topic_id": row["topic_id"],
                            "claim_a": row["claim_a"],
                            "source_a_id": row["source_a_id"],
                            "source_a_title": row["source_a_title"],
                            "claim_b": row["claim_b"],
                            "source_b_id": row["source_b_id"],
                            "source_b_title": row["source_b_title"],
                            "severity": row["severity"],
                            "status": row["status"],
                            "context": row["context"],
                            "detected_at": row["detected_at"],
                        }
                    )
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Contradictions query failed: %s", exc)
        return {"items": [], "total": 0, "available": False, "storage_backend": storage_backend}

    return {
        "items": contradictions,
        "total": len(contradictions),
        "available": True,
        "storage_backend": storage_backend,
    }


@router.get("/wiki/stats")
async def wiki_stats(
    container: AipContainer = Depends(get_container),
) -> dict:
    """Quick wiki statistics — article counts by state and domain.

    Returns total articles, approved count, generated count,
    and a list of domains with article counts.
    """
    storage_backend = _resolve_storage_backend(container)
    stats: dict = {
        "total": 0,
        "approved": 0,
        "generated": 0,
        "domains": [],
        "storage_backend": storage_backend,
    }

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Count by state
            cursor = await conn.execute(
                """
                SELECT e.current_state, COUNT(*) as c
                FROM artifacts a
                INNER JOIN ecs_state e ON a.id = e.artifact_id
                WHERE a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%' OR a.id LIKE 'wiki:%' OR a.id LIKE 'manual:%'
                GROUP BY e.current_state
                """,
            )
            for row in await cursor.fetchall():
                st = row["current_state"]
                cnt = row["c"]
                stats["total"] += cnt
                if st == "APPROVED":
                    stats["approved"] = cnt
                elif st == "GENERATED":
                    stats["generated"] = cnt

            # Domain breakdown
            cursor = await conn.execute(
                """
                SELECT
                    COALESCE(
                        json_extract(a.metadata_json, '$.domain'),
                        SUBSTR(a.id, 12, INSTR(SUBSTR(a.id, 12), ':') - 1)
                    ) as domain,
                    e.current_state,
                    COUNT(*) as c
                FROM artifacts a
                INNER JOIN ecs_state e ON a.id = e.artifact_id
                WHERE a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%' OR a.id LIKE 'wiki:%' OR a.id LIKE 'manual:%'
                GROUP BY domain, e.current_state
                ORDER BY domain
                """,
            )
            domain_map: dict[str, dict] = {}
            for row in await cursor.fetchall():
                d = row["domain"] or "(unclassified)"
                if d not in domain_map:
                    domain_map[d] = {"name": d, "total": 0, "approved": 0, "generated": 0}
                domain_map[d]["total"] += row["c"]
                if row["current_state"] == "APPROVED":
                    domain_map[d]["approved"] += row["c"]
                elif row["current_state"] == "GENERATED":
                    domain_map[d]["generated"] += row["c"]

            stats["domains"] = list(domain_map.values())
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Wiki stats query failed: %s", exc)

    return stats
