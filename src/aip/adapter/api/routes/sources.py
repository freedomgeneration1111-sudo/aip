"""Sources API route — browse indexed sources and their chunks.

Provides an overview of all ingested content: conversations, artifacts,
and compiled knowledge that have been indexed into LexicalStore and
VectorStore. Unlike /memory/search which searches *within* sources,
this endpoint *browses* the source inventory.

ADR-017 WS-4: Each source record carries a ``kind`` discriminator
(``"corpus"`` or ``"web"``) so the GUI can render them distinctly.
Corpus sources are the existing inventory (conversations, artifacts,
compiled knowledge).  Web sources are ephemeral fetched records stored
in the WebSourceStore (populated by /web/ground and /web/fetch).
Filtering by ``kind=web`` returns only web sources; ``kind=corpus``
returns only corpus sources; omitting ``kind`` returns both.

"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/sources")
async def list_sources(
    domain: str | None = None,
    source_type: str | None = None,
    kind: str | None = None,
    container: AipContainer = Depends(get_container),
):
    """List indexed sources with metadata.

    Returns a summary of all indexed content organized by source type:
      - conversation: Ingested conversation chunks
      - artifact: Generated and saved artifacts
      - compiled_knowledge: Approved compiled knowledge
      - web: Ephemeral web source records (ADR-017 WS-4)

    Each source entry includes: source_id, type, kind, domain, and metadata.

    Args:
        domain: Filter by domain (corpus sources only).
        source_type: Filter by source type (corpus sources only).
        kind: Filter by kind — ``"corpus"`` or ``"web"``.  Omit to
            return both.  (ADR-017 WS-4)
    """
    sources: list[dict[str, Any]] = []

    # ---- Corpus sources (existing behavior, tagged kind="corpus") ----
    if kind is None or kind == "corpus":
        # Gather from entity store (artifact metadata)
        if container.entity_store is not None:
            try:
                entities = await container.entity_store.list_entities(
                    entity_type=source_type,
                )
                for entity in entities:
                    etype = entity.get("entity_type", entity.get("type", "artifact"))
                    if source_type and etype != source_type:
                        continue
                    entity_domain = entity.get("domain", "")
                    if domain and entity_domain != domain:
                        continue
                    sources.append(
                        {
                            "source_id": entity.get("entity_id", entity.get("id", "")),
                            "source_type": etype,
                            "kind": "corpus",
                            "domain": entity_domain,
                            "title": entity.get("name", entity.get("title", "")),
                            "metadata": entity,
                        }
                    )
            except Exception as exc:
                logger.warning("Failed to list entities for sources: %s", exc)

        # Gather from knowledge store (compiled knowledge)
        if container.knowledge_store is not None and (source_type is None or source_type == "compiled_knowledge"):
            try:
                knowledge_items = await container.knowledge_store.list_compiled(domain=domain)
                for item in knowledge_items:
                    sources.append(
                        {
                            "source_id": item.get("knowledge_id", ""),
                            "source_type": "compiled_knowledge",
                            "kind": "corpus",
                            "domain": item.get("domain", ""),
                            "title": item.get("knowledge_id", ""),
                            "state": item.get("state", ""),
                            "metadata": {
                                "source_canonical_ids": item.get("source_canonical_ids", []),
                                "created_at": item.get("created_at", ""),
                                "updated_at": item.get("updated_at", ""),
                            },
                        }
                    )
            except Exception as exc:
                logger.warning("Failed to list knowledge for sources: %s", exc)

    # ---- Web sources (ADR-017 WS-4, tagged kind="web") ----
    if kind is None or kind == "web":
        web_source_store = getattr(container, "web_source_store", None)
        if web_source_store is not None:
            try:
                # The WebSourceStore Protocol doesn't have a "list all" method,
                # but we can use list_by_query with an empty query to get the
                # most recent records.  For the sources panel, we want a
                # recent-activity view rather than an exhaustive list.
                # (A future slice can add a dedicated list_all method.)
                recent_web = await web_source_store.list_by_query("", limit=20)
                for record in recent_web:
                    extracted = record.extracted
                    sources.append({
                        "source_id": record.source_id,
                        "source_type": "web",
                        "kind": "web",
                        "domain": "",
                        "title": extracted.title if extracted else "",
                        "url": record.fetched.final_url if record.fetched else "",
                        "retrieved_at": record.retrieved_at.isoformat() if record.retrieved_at else "",
                        "content_hash": record.content_hash,
                        "extraction_method": extracted.extraction_method if extracted else "",
                        "metadata": {
                            "provider": record.provider,
                            "fetch_warnings": list(record.fetch_warnings),
                            "extraction_warnings": list(extracted.warnings) if extracted else [],
                        },
                    })
            except Exception as exc:
                logger.warning("Failed to list web sources: %s", exc)

    # Add vector store stats
    vector_stats: dict[str, Any] = {}
    if container.vector_store is not None:
        try:
            vector_count = await container.vector_store.count(domain=domain)
            vector_stats = {
                "total_vectors": vector_count,
                "domain": domain,
            }
        except Exception as exc:
            logger.warning("Failed to get vector store stats: %s", exc)

    # Add lexical store stats (approximate via search)
    lexical_stats: dict[str, Any] = {}
    if container.lexical_store is not None:
        try:
            # Try to get a count via a broad search
            # LexicalStore doesn't have a count method, so we estimate
            lexical_stats = {"available": True, "domain": domain}
        except Exception:
            lexical_stats = {"available": False}

    return {
        "sources": sources,
        "total": len(sources),
        "vector_stats": vector_stats,
        "lexical_stats": lexical_stats,
    }


@router.get("/sources/stats")
async def get_sources_stats(container: AipContainer = Depends(get_container)):
    """Get aggregate statistics about indexed content.

    Returns counts for vectors, entities, knowledge items, and
    storage health information. Useful for the Sources panel
    overview and for monitoring ingestion progress.
    """
    stats: dict[str, Any] = {
        "vector_store": {"available": False, "total_vectors": 0},
        "entity_store": {"available": False, "total_entities": 0},
        "knowledge_store": {"available": False, "total_items": 0},
        "lexical_store": {"available": False},
    }

    # Vector store stats
    if container.vector_store is not None:
        try:
            total = await container.vector_store.count()
            health = await container.vector_store.health_check()
            stats["vector_store"] = {
                "available": True,
                "total_vectors": total,
                "health": health,
            }
        except Exception as exc:
            logger.warning("Vector store stats failed: %s", exc)
            stats["vector_store"] = {"available": True, "error": str(exc)}

    # Entity store stats
    if container.entity_store is not None:
        try:
            entities = await container.entity_store.list_entities()
            stats["entity_store"] = {
                "available": True,
                "total_entities": len(entities),
            }
        except Exception as exc:
            logger.warning("Entity store stats failed: %s", exc)

    # Knowledge store stats
    if container.knowledge_store is not None:
        try:
            items = await container.knowledge_store.list_compiled()
            stats["knowledge_store"] = {
                "available": True,
                "total_items": len(items),
            }
        except Exception as exc:
            logger.warning("Knowledge store stats failed: %s", exc)

    # Lexical store availability
    stats["lexical_store"] = {"available": container.lexical_store is not None}

    return stats
