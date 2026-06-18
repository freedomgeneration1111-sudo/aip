"""CorpusTurnStore — SQLite + FTS5 store for turn-level corpus.

Persistent storage for CorpusTurn objects using the shared state.db.
FTS5 virtual table with triggers for automatic search-index sync.

Constructor is lightweight (stores path only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.adapter.read_pool import ReadPoolMixin
from aip.adapter.store_health import StoreHealthMixin
from aip.foundation.schemas.corpus_turn import CorpusTurn

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_CORPUS_TURNS = """
    CREATE TABLE IF NOT EXISTS corpus_turns (
        turn_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        conversation_name TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        source_model TEXT NOT NULL,
        source_account TEXT NOT NULL,
        export_date TEXT NOT NULL,
        content_hash TEXT NOT NULL DEFAULT '',
        source_path TEXT NOT NULL DEFAULT '',
        doc_version INTEGER NOT NULL DEFAULT 0,
        user_text TEXT NOT NULL,
        assistant_text TEXT NOT NULL,
        turn_timestamp TEXT NOT NULL,
        thinking_text TEXT NOT NULL DEFAULT '',
        domains TEXT NOT NULL DEFAULT '[]',
        primary_domain TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        importance REAL NOT NULL DEFAULT 0.0,
        bridges TEXT NOT NULL DEFAULT '[]',
        beast_confidence REAL NOT NULL DEFAULT 0.0,
        tagging_version INTEGER NOT NULL DEFAULT 0,
        searchable_text TEXT NOT NULL,
        word_count INTEGER NOT NULL DEFAULT 0,
        embedded INTEGER NOT NULL DEFAULT 0,
        embedding_model TEXT DEFAULT '',
        needs_reembed INTEGER NOT NULL DEFAULT 0,
        last_embed_at TEXT DEFAULT NULL,
        metadata_json TEXT DEFAULT '{}',
        embed_fail_count INTEGER NOT NULL DEFAULT 0,
        last_embed_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_turns_conversation ON corpus_turns(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_turns_source_model ON corpus_turns(source_model)",
    "CREATE INDEX IF NOT EXISTS idx_turns_primary_domain ON corpus_turns(primary_domain)",
    "CREATE INDEX IF NOT EXISTS idx_turns_tagging_version ON corpus_turns(tagging_version)",
    "CREATE INDEX IF NOT EXISTS idx_turns_importance ON corpus_turns(importance DESC)",
    "CREATE INDEX IF NOT EXISTS idx_turns_embedded ON corpus_turns(embedded)",
    "CREATE INDEX IF NOT EXISTS idx_turns_needs_reembed ON corpus_turns(needs_reembed)",
    "CREATE INDEX IF NOT EXISTS idx_turns_content_hash ON corpus_turns(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_turns_source_path ON corpus_turns(source_path)",
    "CREATE INDEX IF NOT EXISTS idx_turns_embed_fail ON corpus_turns(embed_fail_count)",
]

_DDL_MIGRATIONS = [
    "ALTER TABLE corpus_turns ADD COLUMN embedded INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE corpus_turns ADD COLUMN metadata_json TEXT DEFAULT '{}'",
    "ALTER TABLE corpus_turns ADD COLUMN embedding_model TEXT DEFAULT ''",
    "ALTER TABLE corpus_turns ADD COLUMN needs_reembed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE corpus_turns ADD COLUMN last_embed_at TEXT DEFAULT NULL",
    "ALTER TABLE corpus_turns ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE corpus_turns ADD COLUMN source_path TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE corpus_turns ADD COLUMN doc_version INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE corpus_turns ADD COLUMN embed_fail_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE corpus_turns ADD COLUMN last_embed_error TEXT NOT NULL DEFAULT ''",
    # ADR-008 Rev 3.1 M001 + M003 — also added here so the store's own
    # _create_tables handles them on fresh databases. The migration runner
    # (§A8) tracks these via fingerprint for crash recovery, but the store
    # must also create them for direct-construction tests and legacy paths.
    "ALTER TABLE corpus_turns ADD COLUMN revision_parent_id TEXT",
    "ALTER TABLE corpus_turns ADD COLUMN latest_ecs_state TEXT NOT NULL DEFAULT 'GENERATED'",
]

_DDL_FTS = """
    CREATE VIRTUAL TABLE IF NOT EXISTS corpus_turns_fts USING fts5(
        turn_id UNINDEXED,
        conversation_name,
        searchable_text,
        primary_domain UNINDEXED,
        content='corpus_turns',
        content_rowid='rowid'
    )
"""

_DDL_TRIGGER_INSERT = """
    CREATE TRIGGER IF NOT EXISTS corpus_turns_ai
    AFTER INSERT ON corpus_turns BEGIN
        INSERT INTO corpus_turns_fts(
            rowid, turn_id, conversation_name, searchable_text, primary_domain
        ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                  new.searchable_text, new.primary_domain);
    END
"""

_DDL_TRIGGER_DELETE = """
    CREATE TRIGGER IF NOT EXISTS corpus_turns_ad
    AFTER DELETE ON corpus_turns BEGIN
        INSERT INTO corpus_turns_fts(
            corpus_turns_fts, rowid, turn_id, conversation_name,
            searchable_text, primary_domain
        ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                  old.searchable_text, old.primary_domain);
    END
"""

_DDL_TRIGGER_UPDATE = """
    CREATE TRIGGER IF NOT EXISTS corpus_turns_au
    AFTER UPDATE ON corpus_turns BEGIN
        INSERT INTO corpus_turns_fts(
            corpus_turns_fts, rowid, turn_id, conversation_name,
            searchable_text, primary_domain
        ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                  old.searchable_text, old.primary_domain);
        INSERT INTO corpus_turns_fts(
            rowid, turn_id, conversation_name, searchable_text, primary_domain
        ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                  new.searchable_text, new.primary_domain);
    END
"""


class CorpusTurnStore(StoreHealthMixin, ReadPoolMixin):
    """SQLite-backed store for CorpusTurn objects with FTS5 search.

    All turns live in the main state.db alongside artifacts/events.
    FTS5 virtual table + triggers keep search index in sync automatically.
    Uses a persistent aiosqlite connection per instance with error recovery.
    """

    def __init__(self, db_path: str, config: dict | None = None) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tables_ready = False
        self._read_pool_config = config
        from aip.adapter.read_pool import resolve_pool_size

        self._init_read_pool(pool_size=resolve_pool_size("corpus_turn_store", config))

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a persistent connection, creating one if needed.

        Lazily ensures tables on first connection so that callers
        who bypass ``initialize()`` still get a working schema.
        """
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            # Sprint 6.3: busy_timeout to handle concurrent write contention
            await self._conn.execute("PRAGMA busy_timeout=5000")
            self._health_track_connect()
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create tables, indexes, FTS virtual table, and triggers."""
        await conn.execute(_DDL_CORPUS_TURNS)

        for idx_ddl in _DDL_INDEXES:
            await conn.execute(idx_ddl)

        for mig_ddl in _DDL_MIGRATIONS:
            try:
                await conn.execute(mig_ddl)
            except sqlite3.OperationalError:
                pass  # column already exists

        await conn.execute(_DDL_FTS)
        await conn.execute(_DDL_TRIGGER_INSERT)
        await conn.execute(_DDL_TRIGGER_DELETE)
        await conn.execute(_DDL_TRIGGER_UPDATE)
        await conn.commit()

    async def initialize(self) -> None:
        """Idempotent table creation (called by lifespan / DI container).

        Uses a short-lived connection to create tables, then discards it.
        Subsequent operations use the persistent connection from _get_conn().
        """
        if self._tables_ready:
            return
        conn = await aiosqlite.connect(self._db_path)
        try:
            await self._create_tables(conn)
            self._tables_ready = True
        finally:
            await conn.close()

    async def close(self) -> None:
        """Close the persistent connection and read pool."""
        await self._close_read_pool()
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def _reset_conn(self) -> None:
        """Reset the persistent connection (called on errors)."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._health_track_reset()

    async def write_turn(self, turn: CorpusTurn) -> None:
        """Insert or replace a turn. Sets timestamps. Serializes lists as JSON.

        Sprint 9: Handles content_hash, source_path, doc_version,
        embed_fail_count, and last_embed_error fields.
        """
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"

            cursor = await conn.execute(
                "SELECT created_at, doc_version FROM corpus_turns WHERE turn_id = ?", (turn.turn_id,)
            )
            row = await cursor.fetchone()
            created_at = row["created_at"] if row and row["created_at"] else now
            # Preserve existing doc_version if not explicitly set on the new turn
            existing_doc_version = int(row["doc_version"] or 0) if row else 0

            await conn.execute(
                """
                INSERT OR REPLACE INTO corpus_turns (
                    turn_id, conversation_id, conversation_name, turn_index,
                    source_model, source_account, export_date,
                    content_hash, source_path, doc_version,
                    user_text, assistant_text, turn_timestamp, thinking_text,
                    domains, primary_domain, tags, importance, bridges,
                    beast_confidence, tagging_version,
                    searchable_text, word_count,
                    embedded, metadata_json, embedding_model, needs_reembed, last_embed_at,
                    embed_fail_count, last_embed_error,
                    revision_parent_id,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    turn.turn_id,
                    turn.conversation_id,
                    turn.conversation_name,
                    turn.turn_index,
                    turn.source_model,
                    turn.source_account,
                    turn.export_date,
                    getattr(turn, "content_hash", "") or "",
                    getattr(turn, "source_path", "") or "",
                    int(getattr(turn, "doc_version", 0) or 0) or existing_doc_version,
                    turn.user_text,
                    turn.assistant_text,
                    turn.turn_timestamp,
                    turn.thinking_text or "",
                    json.dumps(turn.domains or []),
                    turn.primary_domain or "",
                    json.dumps(turn.tags or []),
                    float(turn.importance or 0.0),
                    json.dumps(turn.bridges or []),
                    float(turn.beast_confidence or 0.0),
                    int(turn.tagging_version or 0),
                    turn.searchable_text or "",
                    int(turn.word_count or 0),
                    int(getattr(turn, "embedded", 0) or 0),
                    getattr(turn, "metadata_json", "{}") or "{}",
                    getattr(turn, "embedding_model", "") or "",
                    int(getattr(turn, "needs_reembed", 0) or 0),
                    getattr(turn, "last_embed_at", None),
                    int(getattr(turn, "embed_fail_count", 0) or 0),
                    getattr(turn, "last_embed_error", "") or "",
                    getattr(turn, "revision_parent_id", None),
                    created_at,
                    now,
                ),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_turn(self, turn_id: str) -> CorpusTurn | None:
        """Return CorpusTurn or None (not KeyError — turns are optional)."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute("SELECT * FROM corpus_turns WHERE turn_id = ?", (turn_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_turn(row)
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def update_beast_tags(
        self,
        turn_id: str,
        domains: list[str],
        primary_domain: str,
        tags: list[str],
        importance: float,
        bridges: list[str],
        beast_confidence: float,
    ) -> None:
        """Beast-only update path for tagging metadata. Increments tagging_version."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                """
                UPDATE corpus_turns
                SET domains = ?,
                    primary_domain = ?,
                    tags = ?,
                    importance = ?,
                    bridges = ?,
                    beast_confidence = ?,
                    tagging_version = tagging_version + 1,
                    updated_at = ?
                WHERE turn_id = ?
                """,
                (
                    json.dumps(domains or []),
                    primary_domain or "",
                    json.dumps(tags or []),
                    float(importance or 0.0),
                    json.dumps(bridges or []),
                    float(beast_confidence or 0.0),
                    now,
                    turn_id,
                ),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def mark_embedded(self, turn_id: str, embedding_model: str = "") -> None:
        """Mark a turn as embedded (embedded=1). Called by Sexton after vector upsert.

        Args:
            turn_id: The turn to mark.
            embedding_model: Name of the model used for embedding (for re-embed detection).
        """
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "UPDATE corpus_turns SET embedded = 1, embedding_model = ?, "
                "needs_reembed = 0, last_embed_at = ?, updated_at = ? WHERE turn_id = ?",
                (embedding_model, now, now, turn_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def batch_mark_embedded(
        self,
        turn_ids: list[str],
        embedding_model: str = "",
    ) -> int:
        """Sprint 6.3: Mark multiple turns as embedded in a single transaction.

        This reduces write contention by batching multiple mark_embedded
        operations into one transaction instead of committing each one
        individually. Returns the number of turns updated.

        Args:
            turn_ids: List of turn IDs to mark as embedded.
            embedding_model: Name of the model used for embedding.
        """
        if not turn_ids:
            return 0
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            updated = 0
            # Process in chunks of 50 to avoid SQL variable limit
            chunk_size = 50
            for i in range(0, len(turn_ids), chunk_size):
                chunk = turn_ids[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cursor = await conn.execute(
                    f"UPDATE corpus_turns SET embedded = 1, embedding_model = ?, "
                    f"needs_reembed = 0, last_embed_at = ?, updated_at = ? "
                    f"WHERE turn_id IN ({placeholders})",
                    [embedding_model, now, now] + chunk,
                )
                updated += cursor.rowcount
            await conn.commit()
            return updated
        except Exception:
            await self._reset_conn()
            raise

    async def search(
        self,
        query: str,
        primary_domain: str | None = None,
        source_model: str | None = None,
        min_importance: float = 0.0,
        limit: int = 10,
        include_archived: bool = False,
    ) -> list[CorpusTurn]:
        """FTS5 search with optional filters. Returns [] on no results.

        ADR-008 Rev 3.1 §6: by default, excludes turns whose latest_ecs_state
        is in RETRIEVAL_EXCLUDED_STATES (ARCHIVED, SUPERSEDED). Pass
        include_archived=True to include them (for history queries).
        """
        from aip.foundation.corpus_types import RETRIEVAL_EXCLUDED_STATES

        conn = await self._checkout_read_conn()
        try:
            sql = """
                SELECT t.*
                FROM corpus_turns_fts f
                JOIN corpus_turns t ON f.rowid = t.rowid
                WHERE corpus_turns_fts MATCH ?
            """
            params: list[Any] = [query]

            # ADR-008 §6: exclude ARCHIVED/SUPERSEDED by default
            if not include_archived:
                excluded = "','".join(sorted(RETRIEVAL_EXCLUDED_STATES))
                sql += f" AND (t.latest_ecs_state IS NULL OR t.latest_ecs_state NOT IN ('{excluded}'))"

            if primary_domain:
                sql += " AND t.primary_domain = ?"
                params.append(primary_domain)
            if source_model:
                sql += " AND t.source_model = ?"
                params.append(source_model)
            if min_importance > 0:
                sql += " AND t.importance >= ?"
                params.append(float(min_importance))

            sql += " ORDER BY rank LIMIT ?"
            params.append(int(limit))

            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def delete_turn(self, turn_id: str) -> bool:
        """Delete a turn and its FTS5 entry (via corpus_turns_ad trigger).

        ADR-008 Rev 3.1 §A4: opt-in GC for ARCHIVED turns. The corpus_turns_ad
        trigger automatically removes the FTS5 row when the base row is deleted.
        Returns True if a row was deleted, False if the turn didn't exist.

        NOTE: Callers MUST also call vector_store.delete(turn_id) to remove
        the vector entry, and clean up any artifact_turn_links / bridge edges.
        This method only handles the corpus_turns + FTS5 cleanup.
        """
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("DELETE FROM corpus_turns WHERE turn_id = ?", (turn_id,))
            await conn.commit()
            return cursor.rowcount > 0
        except Exception:
            await self._reset_conn()
            raise

    async def states_for(self, turn_ids: list[str]) -> dict[str, str]:
        """Batch lookup of latest_ecs_state for a list of turn_ids.

        ADR-008 Rev 3.1 §A2: used by the fusion-layer ECS filter in
        assemble_augmented_context() to exclude ARCHIVED/SUPERSEDED turns
        from lexical and vector channel results (which don't join corpus_turns
        and would otherwise leak archived content).

        Returns dict[turn_id → latest_ecs_state]. Turns not found are omitted.
        If the latest_ecs_state column doesn't exist (pre-M003), returns {}.
        """
        if not turn_ids:
            return {}
        conn = await self._checkout_read_conn()
        try:
            # Check if latest_ecs_state column exists (pre-M003 compatibility)
            cursor = await conn.execute("PRAGMA table_info(corpus_turns)")
            cols = {row[1] for row in await cursor.fetchall()}
            if "latest_ecs_state" not in cols:
                return {}

            placeholders = ",".join("?" for _ in turn_ids)
            cursor = await conn.execute(
                f"SELECT turn_id, latest_ecs_state FROM corpus_turns WHERE turn_id IN ({placeholders})",
                turn_ids,
            )
            rows = await cursor.fetchall()
            return {row["turn_id"]: row["latest_ecs_state"] for row in rows}
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_untagged_turns(self, limit: int = 50) -> list[CorpusTurn]:
        """Turns with tagging_version == 0 (never tagged by Beast)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns WHERE tagging_version = 0 ORDER BY created_at ASC LIMIT ?",
                (int(limit),),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def get_unembedded_turns(self, limit: int = 50) -> list[CorpusTurn]:
        """Turns needing embedding: embedded == 0 OR needs_reembed == 1.

        Prioritises needs_reembed (model-change re-embedding) over first-time embeds,
        then sorts by importance DESC so high-value content is embedded first.
        """
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns WHERE embedded = 0 OR needs_reembed = 1 "
                "ORDER BY needs_reembed DESC, importance DESC, created_at ASC LIMIT ?",
                (int(limit),),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def count_unembedded(self) -> int:
        """Count of turns not yet embedded (embedded == 0)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embedded = 0")
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def count_tagged(self) -> int:
        """Count of turns with a non-empty primary_domain (domain-assigned)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                'SELECT COUNT(*) as c FROM corpus_turns WHERE primary_domain IS NOT NULL AND primary_domain != ""'
            )
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def count_needs_reembed(self) -> int:
        """Count of turns flagged for re-embedding (needs_reembed == 1)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE needs_reembed = 1")
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def get_embedding_progress(self) -> dict:
        """Return embedding progress statistics for the /corpus/embedding-progress endpoint.

        Returns dict with: total, embedded, unembedded, needs_reembed, percentage,
        last_embed_at, embedding_model distribution.
        """
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns")
            row = await cursor.fetchone()
            total = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embedded = 1")
            row = await cursor.fetchone()
            embedded = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE needs_reembed = 1")
            row = await cursor.fetchone()
            needs_reembed = int(row["c"]) if row else 0

            last_embed_at = None
            cursor = await conn.execute(
                "SELECT MAX(last_embed_at) as le FROM corpus_turns WHERE last_embed_at IS NOT NULL"
            )
            row = await cursor.fetchone()
            if row and row["le"]:
                last_embed_at = row["le"]

            # Embedding model distribution
            cursor = await conn.execute(
                "SELECT embedding_model, COUNT(*) as c FROM corpus_turns "
                "WHERE embedded = 1 GROUP BY embedding_model ORDER BY c DESC"
            )
            rows = await cursor.fetchall()
            model_distribution = {row["embedding_model"] or "unknown": int(row["c"]) for row in rows}

            percentage = round(embedded / total * 100, 2) if total > 0 else 0.0

            return {
                "total": total,
                "embedded": embedded,
                "unembedded": total - embedded,
                "needs_reembed": needs_reembed,
                "percentage": percentage,
                "last_embed_at": last_embed_at,
                "embedding_models": model_distribution,
            }
        except Exception:
            await self._reset_conn()
            raise

    async def mark_all_for_reembed(self, except_model: str = "") -> int:
        """Flag all currently-embedded turns for re-embedding.

        Called when the embedding model changes. Turns embedded with a different
        model are marked needs_reembed=1 and embedded=0 so the Sexton embedding
        pass will re-process them.

        Args:
            except_model: If provided, only mark turns whose embedding_model
                differs from this value. Empty string marks all.

        Returns:
            Number of turns marked for re-embedding.
        """
        conn = await self._get_conn()
        try:
            if except_model:
                cursor = await conn.execute(
                    "UPDATE corpus_turns SET needs_reembed = 1, embedded = 0 "
                    "WHERE embedded = 1 AND (embedding_model IS NULL OR embedding_model = '' "
                    "OR embedding_model != ?)",
                    (except_model,),
                )
            else:
                cursor = await conn.execute(
                    "UPDATE corpus_turns SET needs_reembed = 1, embedded = 0 WHERE embedded = 1"
                )
            await conn.commit()
            return cursor.rowcount
        except Exception:
            await self._reset_conn()
            raise

    async def get_turns_for_retagging(self, max_tagging_version: int, limit: int = 20) -> list[CorpusTurn]:
        """Already-tagged turns eligible for re-evaluation (tagging_version <= max)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM corpus_turns
                WHERE tagging_version > 0 AND tagging_version <= ?
                ORDER BY importance ASC LIMIT ?
                """,
                (int(max_tagging_version), int(limit)),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def count_by_domain(self) -> dict[str, int]:
        """Count of turns per primary_domain (descending by count)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT primary_domain, COUNT(*) as c
                FROM corpus_turns
                WHERE primary_domain IS NOT NULL AND primary_domain != ""
                GROUP BY primary_domain
                ORDER BY c DESC
                """
            )
            rows = await cursor.fetchall()
            return {row["primary_domain"]: int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def count_by_source(self) -> dict[str, int]:
        """Count of turns per source_model (descending by count)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT source_model, COUNT(*) as c
                FROM corpus_turns
                GROUP BY source_model
                ORDER BY c DESC
                """
            )
            rows = await cursor.fetchall()
            return {row["source_model"] or "": int(row["c"]) for row in rows}
        except Exception:
            await self._reset_conn()
            raise

    async def total_turns(self) -> int:
        """Total number of turns in the corpus."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns")
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def top_turns_by_importance(self, limit: int = 10) -> list[dict]:
        """Top corpus turns ranked by importance score."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT turn_id, user_text, primary_domain, source_model, importance
                FROM corpus_turns
                WHERE primary_domain IS NOT NULL AND primary_domain != ""
                ORDER BY importance DESC NULLS LAST
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "turn_id": row["turn_id"],
                    "user_text": row["user_text"] or "",
                    "primary_domain": row["primary_domain"],
                    "source_model": row["source_model"] or "",
                    "importance": float(row["importance"] or 0),
                }
                for row in rows
            ]
        except Exception:
            await self._reset_conn()
            raise

    async def get_augmented_turns_since(self, since: float | None = None, limit: int = 100) -> list[CorpusTurn]:
        """Return augmented chat turns created since a given timestamp."""
        conn = await self._get_conn()
        try:
            sql = """
                SELECT * FROM corpus_turns
                WHERE source_model = 'aip_chat'
                  AND metadata_json LIKE '%"augmented": true%'
            """
            params: list[Any] = []
            if since is not None:
                since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
                sql += " AND created_at > ?"
                params.append(since_iso)
            sql += " ORDER BY created_at ASC LIMIT ?"
            params.append(int(limit))
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise

    async def update_metadata_json(self, turn_id: str, metadata_json: str) -> None:
        """Update only the metadata_json column for a turn."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "UPDATE corpus_turns SET metadata_json = ?, updated_at = ? WHERE turn_id = ?",
                (metadata_json, now, turn_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def has_bridge_tagged_turns(self) -> bool:
        """Check if any turns with bridge tags exist in the corpus."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM corpus_turns "
                "WHERE bridges IS NOT NULL AND bridges != '[]' AND tagging_version > 0 LIMIT 1"
            )
            row = await cursor.fetchone()
            return bool(row and row[0] > 0)
        except Exception:
            return False

    async def count_domain_words_since(self, domain_id: str, since: str | None) -> int:
        """Count word_count for turns in a domain updated since a timestamp."""
        conn = await self._get_conn()
        try:
            if since:
                cursor = await conn.execute(
                    "SELECT COALESCE(SUM(word_count), 0) FROM corpus_turns "
                    "WHERE primary_domain = ? AND tagging_version > 0 AND updated_at >= ?",
                    (domain_id, since),
                )
            else:
                cursor = await conn.execute(
                    "SELECT COALESCE(SUM(word_count), 0) FROM corpus_turns "
                    "WHERE primary_domain = ? AND tagging_version > 0",
                    (domain_id,),
                )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def get_domain_stats(self, domain_id: str, sample_limit: int = 20, tag_limit: int = 200) -> dict:
        """Gather domain statistics and sample turns for wiki generation.

        Returns a dict with keys: total_turns, avg_importance, top_tags,
        bridge_connectors, sample_turns, max_tagging_version.
        """
        data: dict = {
            "total_turns": 0,
            "avg_importance": 0.0,
            "top_tags": [],
            "bridge_connectors": [],
            "sample_turns": [],
            "max_tagging_version": 0,
        }
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT COUNT(*) as c, COALESCE(AVG(importance), 0) as ai, "
                "COALESCE(MAX(tagging_version), 0) as mv "
                "FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0",
                (domain_id,),
            )
            row = await cursor.fetchone()
            if row:
                data["total_turns"] = int(row["c"])
                data["avg_importance"] = round(float(row["ai"]), 4)
                data["max_tagging_version"] = int(row["mv"])

            cursor = await conn.execute(
                "SELECT tags FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0 LIMIT ?",
                (domain_id, tag_limit),
            )
            tag_rows = await cursor.fetchall()
            tag_counts: dict[str, int] = {}
            for row in tag_rows:
                try:
                    for t in json.loads(row["tags"] or "[]"):
                        tag_counts[t] = tag_counts.get(t, 0) + 1
                except Exception:
                    pass
            data["top_tags"] = [t for t, _ in sorted(tag_counts.items(), key=lambda kv: -kv[1])[:10]]

            cursor = await conn.execute(
                "SELECT bridges FROM corpus_turns WHERE primary_domain = ? "
                "AND bridges != '[]' AND tagging_version > 0 LIMIT 100",
                (domain_id,),
            )
            bridge_rows = await cursor.fetchall()
            bridge_counts: dict[str, int] = {}
            for row in bridge_rows:
                try:
                    for b in json.loads(row["bridges"] or "[]"):
                        bridge_counts[b] = bridge_counts.get(b, 0) + 1
                except Exception:
                    pass
            data["bridge_connectors"] = sorted(bridge_counts.keys())

            cursor = await conn.execute(
                "SELECT turn_id, importance, tags, bridges, user_text, assistant_text "
                "FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0 "
                "ORDER BY importance DESC LIMIT ?",
                (domain_id, sample_limit),
            )
            turn_rows = await cursor.fetchall()
            sample_turns = []
            for row in turn_rows:
                try:
                    sample_turns.append(
                        {
                            "turn_id": row["turn_id"],
                            "importance": float(row["importance"]),
                            "tags": json.loads(row["tags"] or "[]"),
                            "bridges": json.loads(row["bridges"] or "[]"),
                            "user_text": (row["user_text"] or "")[:300],
                            "assistant_text": (row["assistant_text"] or "")[:500],
                        }
                    )
                except Exception:
                    pass
            data["sample_turns"] = sample_turns
        except Exception:
            await self._reset_conn()
            raise
        return data

    def _row_to_turn(self, row: sqlite3.Row) -> CorpusTurn:
        """Rehydrate a CorpusTurn from a DB row (JSON fields -> lists)."""
        return CorpusTurn(
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            conversation_name=row["conversation_name"],
            turn_index=int(row["turn_index"]),
            source_model=row["source_model"],
            source_account=row["source_account"],
            export_date=row["export_date"],
            content_hash=row["content_hash"] if "content_hash" in row.keys() else "",
            source_path=row["source_path"] if "source_path" in row.keys() else "",
            doc_version=int(row["doc_version"] or 0) if "doc_version" in row.keys() else 0,
            user_text=row["user_text"],
            assistant_text=row["assistant_text"],
            turn_timestamp=row["turn_timestamp"],
            thinking_text=row["thinking_text"] or "",
            domains=json.loads(row["domains"]) if row["domains"] else [],
            primary_domain=row["primary_domain"] or "",
            tags=json.loads(row["tags"]) if row["tags"] else [],
            importance=float(row["importance"] or 0.0),
            bridges=json.loads(row["bridges"]) if row["bridges"] else [],
            beast_confidence=float(row["beast_confidence"] or 0.0),
            tagging_version=int(row["tagging_version"] or 0),
            embedded=int(row["embedded"] or 0) if "embedded" in row.keys() else 0,
            metadata_json=row["metadata_json"] if "metadata_json" in row.keys() else "{}",
            embedding_model=row["embedding_model"] if "embedding_model" in row.keys() else "",
            needs_reembed=int(row["needs_reembed"] or 0) if "needs_reembed" in row.keys() else 0,
            last_embed_at=row["last_embed_at"] if "last_embed_at" in row.keys() else None,
            embed_fail_count=int(row["embed_fail_count"] or 0) if "embed_fail_count" in row.keys() else 0,
            last_embed_error=row["last_embed_error"] if "last_embed_error" in row.keys() else "",
            revision_parent_id=row["revision_parent_id"] if "revision_parent_id" in row.keys() else None,
            searchable_text=row["searchable_text"] or "",
            word_count=int(row["word_count"] or 0),
        )

    # ------------------------------------------------------------------
    # Sprint 9: Document identity, dedup, provenance, backfill, audit
    # ------------------------------------------------------------------

    async def check_content_hash(self, content_hash: str) -> CorpusTurn | None:
        """Find a turn by content_hash. Returns the first match or None.

        Used for dedup detection: if a turn with the same content_hash already
        exists, the content hasn't changed and re-ingest can skip it.
        """
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            )
            row = await cursor.fetchone()
            return self._row_to_turn(row) if row else None
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def find_by_source_path(self, source_path: str, limit: int = 5000) -> list[CorpusTurn]:
        """Find all turns from a given source path. Used for re-ingest detection."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns WHERE source_path = ? ORDER BY turn_index ASC LIMIT ?",
                (source_path, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def increment_doc_version(self, conversation_id: str) -> int:
        """Increment doc_version for all turns in a conversation. Returns max version."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "UPDATE corpus_turns SET doc_version = doc_version + 1, updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT MAX(doc_version) as mv FROM corpus_turns WHERE conversation_id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            return int(row["mv"] or 0) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def record_embed_failure(self, turn_id: str, error_message: str) -> None:
        """Record an embedding failure for a turn. Increments fail count.

        Called by the embedding pipeline when an individual turn fails to embed.
        The turn remains in the unembedded/backfill queue.
        """
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "UPDATE corpus_turns SET embed_fail_count = embed_fail_count + 1, "
                "last_embed_error = ?, updated_at = ? WHERE turn_id = ?",
                (error_message[:500], now, turn_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def clear_embed_failure(self, turn_id: str) -> None:
        """Clear embedding failure state. Called after successful embed."""
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            await conn.execute(
                "UPDATE corpus_turns SET embed_fail_count = 0, last_embed_error = '', updated_at = ? WHERE turn_id = ?",
                (now, turn_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

    async def get_backfill_queue(self, limit: int = 100) -> list[CorpusTurn]:
        """Get turns needing embedding backfill.

        Prioritizes: embed_fail_count > 0 first (retry failures), then
        needs_reembed, then first-time unembedded, ordered by importance.
        """
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM corpus_turns "
                "WHERE embedded = 0 OR needs_reembed = 1 OR embed_fail_count > 0 "
                "ORDER BY embed_fail_count DESC, needs_reembed DESC, importance DESC, created_at ASC "
                "LIMIT ?",
                (int(limit),),
            )
            rows = await cursor.fetchall()
            return [self._row_to_turn(row) for row in rows]
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def count_embed_failures(self) -> int:
        """Count of turns with embedding failures."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embed_fail_count > 0")
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def get_corpus_audit(self) -> dict:
        """Comprehensive corpus audit for the 'aip corpus audit' command.

        Returns a dict with integrity checks, coverage stats, and issue lists.
        """
        conn = await self._get_conn()
        try:
            result: dict[str, Any] = {}

            # Basic counts
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns")
            row = await cursor.fetchone()
            result["total_turns"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embedded = 1")
            row = await cursor.fetchone()
            result["embedded"] = int(row["c"]) if row else 0

            result["unembedded"] = result["total_turns"] - result["embedded"]

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE needs_reembed = 1")
            row = await cursor.fetchone()
            result["needs_reembed"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embed_fail_count > 0")
            row = await cursor.fetchone()
            result["embed_failures"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE content_hash = ''")
            row = await cursor.fetchone()
            result["missing_content_hash"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE tagging_version = 0")
            row = await cursor.fetchone()
            result["untagged"] = int(row["c"]) if row else 0

            # Source model distribution
            cursor = await conn.execute(
                "SELECT source_model, COUNT(*) as c FROM corpus_turns GROUP BY source_model ORDER BY c DESC"
            )
            rows = await cursor.fetchall()
            result["by_source_model"] = {row["source_model"]: int(row["c"]) for row in rows}

            # Domain distribution
            cursor = await conn.execute(
                "SELECT primary_domain, COUNT(*) as c FROM corpus_turns "
                "WHERE primary_domain IS NOT NULL AND primary_domain != '' "
                "GROUP BY primary_domain ORDER BY c DESC"
            )
            rows = await cursor.fetchall()
            result["by_domain"] = {row["primary_domain"]: int(row["c"]) for row in rows}

            # Source path distribution (for documents)
            cursor = await conn.execute(
                "SELECT source_path, COUNT(*) as c FROM corpus_turns "
                "WHERE source_path IS NOT NULL AND source_path != '' "
                "GROUP BY source_path ORDER BY c DESC LIMIT 20"
            )
            rows = await cursor.fetchall()
            result["by_source_path"] = {row["source_path"]: int(row["c"]) for row in rows}

            # Duplicate content hashes (potential duplicates)
            cursor = await conn.execute(
                "SELECT content_hash, COUNT(*) as c FROM corpus_turns "
                "WHERE content_hash != '' GROUP BY content_hash HAVING c > 1 "
                "ORDER BY c DESC LIMIT 10"
            )
            rows = await cursor.fetchall()
            result["duplicate_hashes"] = [{"content_hash": row["content_hash"], "count": int(row["c"])} for row in rows]

            # Embedding model distribution
            cursor = await conn.execute(
                "SELECT embedding_model, COUNT(*) as c FROM corpus_turns "
                "WHERE embedded = 1 GROUP BY embedding_model ORDER BY c DESC"
            )
            rows = await cursor.fetchall()
            result["embedding_models"] = {row["embedding_model"] or "unknown": int(row["c"]) for row in rows}

            # Recent embed failures
            cursor = await conn.execute(
                "SELECT turn_id, source_path, embed_fail_count, last_embed_error "
                "FROM corpus_turns WHERE embed_fail_count > 0 "
                "ORDER BY embed_fail_count DESC, updated_at DESC LIMIT 10"
            )
            rows = await cursor.fetchall()
            result["recent_embed_failures"] = [
                {
                    "turn_id": row["turn_id"],
                    "source_path": row["source_path"],
                    "fail_count": int(row["embed_fail_count"]),
                    "last_error": row["last_embed_error"][:200],
                }
                for row in rows
            ]

            # Computed health
            issues = []
            if result["missing_content_hash"] > 0:
                issues.append(f"{result['missing_content_hash']} turns missing content_hash")
            if result["embed_failures"] > 0:
                issues.append(f"{result['embed_failures']} turns with embedding failures")
            if result["duplicate_hashes"]:
                issues.append(f"{len(result['duplicate_hashes'])} duplicate content hashes detected")
            if result["unembedded"] > 0:
                pct = round(result["unembedded"] / max(result["total_turns"], 1) * 100, 1)
                issues.append(f"{result['unembedded']} turns unembedded ({pct}%)")

            result["issues"] = issues
            result["healthy"] = len(issues) == 0

            return result
        except Exception:
            await self._reset_conn()
            raise

    async def get_corpus_status(self) -> dict:
        """Quick corpus status summary for 'aip corpus status'.

        Lighter than full audit — just the key numbers.
        """
        conn = await self._checkout_read_conn()
        try:
            result: dict[str, Any] = {}

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns")
            row = await cursor.fetchone()
            result["total_turns"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embedded = 1")
            row = await cursor.fetchone()
            result["embedded"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE tagging_version > 0")
            row = await cursor.fetchone()
            result["tagged"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embed_fail_count > 0")
            row = await cursor.fetchone()
            result["embed_failures"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE needs_reembed = 1")
            row = await cursor.fetchone()
            result["needs_reembed"] = int(row["c"]) if row else 0

            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT source_path) as c FROM corpus_turns WHERE source_path != ''"
            )
            row = await cursor.fetchone()
            result["documents"] = int(row["c"]) if row else 0

            cursor = await conn.execute("SELECT COUNT(DISTINCT conversation_id) as c FROM corpus_turns")
            row = await cursor.fetchone()
            result["conversations"] = int(row["c"]) if row else 0

            total = result["total_turns"]
            result["embed_coverage"] = round(result["embedded"] / total * 100, 1) if total > 0 else 0.0
            result["tag_coverage"] = round(result["tagged"] / total * 100, 1) if total > 0 else 0.0

            return result
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    # ------------------------------------------------------------------
    # UI Cycle 10: Corpus Workbench document-level queries
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        limit: int = 50,
        offset: int = 0,
        source_path_filter: str = "",
    ) -> list[dict[str, Any]]:
        """List documents (distinct source_paths) with per-document stats.

        Each document row includes:
          - source_path, source_model, turn_count, embedded_count,
            unembedded_count, embed_fail_count, needs_reembed_count,
            primary_domains, last_updated, conversation_count

        Returns empty list honestly if no documents exist.
        """
        conn = await self._checkout_read_conn()
        try:
            where = "WHERE source_path IS NOT NULL AND source_path != ''"
            params: list[Any] = []
            if source_path_filter:
                where += " AND source_path LIKE ?"
                params.append(f"%{source_path_filter}%")

            sql = f"""
                SELECT
                    source_path,
                    source_model,
                    COUNT(*) as turn_count,
                    SUM(CASE WHEN embedded = 1 THEN 1 ELSE 0 END) as embedded_count,
                    SUM(CASE WHEN embedded = 0 THEN 1 ELSE 0 END) as unembedded_count,
                    SUM(CASE WHEN embed_fail_count > 0 THEN 1 ELSE 0 END) as embed_fail_count,
                    SUM(CASE WHEN needs_reembed = 1 THEN 1 ELSE 0 END) as needs_reembed_count,
                    GROUP_CONCAT(DISTINCT CASE WHEN primary_domain != ''
                        THEN primary_domain ELSE NULL END) as primary_domains,
                    MAX(updated_at) as last_updated,
                    COUNT(DISTINCT conversation_id) as conversation_count
                FROM corpus_turns
                {where}
                GROUP BY source_path
                ORDER BY turn_count DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

            docs = []
            for row in rows:
                domains_str = row["primary_domains"] or ""
                docs.append(
                    {
                        "source_path": row["source_path"],
                        "source_model": row["source_model"],
                        "turn_count": int(row["turn_count"]),
                        "embedded_count": int(row["embedded_count"]),
                        "unembedded_count": int(row["unembedded_count"]),
                        "embed_fail_count": int(row["embed_fail_count"]),
                        "needs_reembed_count": int(row["needs_reembed_count"]),
                        "primary_domains": [d.strip() for d in domains_str.split(",") if d.strip()],
                        "last_updated": row["last_updated"],
                        "conversation_count": int(row["conversation_count"]),
                    }
                )
            return docs
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def count_documents(self) -> int:
        """Count distinct source_paths (documents) in the corpus."""
        conn = await self._checkout_read_conn()
        try:
            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT source_path) as c FROM corpus_turns "
                "WHERE source_path IS NOT NULL AND source_path != ''"
            )
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_document_detail(self, source_path: str) -> dict[str, Any]:
        """Get detailed information about a single document (source_path).

        Returns metadata, chunk summary, embedding status, errors, and
        sample turns. Returns empty dict with not_found=True if no turns
        match the source_path.
        """
        conn = await self._checkout_read_conn()
        try:
            # Aggregate stats
            cursor = await conn.execute(
                """
                SELECT
                    source_path,
                    source_model,
                    source_account,
                    COUNT(*) as turn_count,
                    SUM(CASE WHEN embedded = 1 THEN 1 ELSE 0 END) as embedded_count,
                    SUM(CASE WHEN embedded = 0 THEN 1 ELSE 0 END) as unembedded_count,
                    SUM(CASE WHEN embed_fail_count > 0 THEN 1 ELSE 0 END) as embed_fail_count,
                    SUM(CASE WHEN needs_reembed = 1 THEN 1 ELSE 0 END) as needs_reembed_count,
                    GROUP_CONCAT(DISTINCT CASE WHEN primary_domain != ''
                        THEN primary_domain ELSE NULL END) as primary_domains,
                    MIN(created_at) as first_turn_at,
                    MAX(updated_at) as last_updated,
                    COUNT(DISTINCT conversation_id) as conversation_count,
                    GROUP_CONCAT(DISTINCT embedding_model) as embedding_models,
                    SUM(word_count) as total_word_count
                FROM corpus_turns
                WHERE source_path = ?
                GROUP BY source_path
                """,
                [source_path],
            )
            row = await cursor.fetchone()
            if row is None or int(row["turn_count"]) == 0:
                return {"not_found": True, "source_path": source_path}

            domains_str = row["primary_domains"] or ""
            models_str = row["embedding_models"] or ""
            turn_count = int(row["turn_count"])
            embedded_count = int(row["embedded_count"])

            result: dict[str, Any] = {
                "not_found": False,
                "source_path": row["source_path"],
                "source_model": row["source_model"],
                "source_account": row["source_account"],
                "turn_count": turn_count,
                "embedded_count": embedded_count,
                "unembedded_count": int(row["unembedded_count"]),
                "embed_fail_count": int(row["embed_fail_count"]),
                "needs_reembed_count": int(row["needs_reembed_count"]),
                "embed_coverage": round(embedded_count / turn_count * 100, 1) if turn_count > 0 else 0.0,
                "primary_domains": [d.strip() for d in domains_str.split(",") if d.strip()],
                "embedding_models": [m.strip() for m in models_str.split(",") if m.strip()],
                "first_turn_at": row["first_turn_at"],
                "last_updated": row["last_updated"],
                "conversation_count": int(row["conversation_count"]),
                "total_word_count": int(row["total_word_count"]),
                "errors": [],
                "sample_turns": [],
            }

            # Collect embed errors
            cursor = await conn.execute(
                """
                SELECT turn_id, embed_fail_count, last_embed_error
                FROM corpus_turns
                WHERE source_path = ? AND embed_fail_count > 0
                ORDER BY embed_fail_count DESC
                LIMIT 10
                """,
                [source_path],
            )
            error_rows = await cursor.fetchall()
            result["errors"] = [
                {
                    "turn_id": r["turn_id"],
                    "fail_count": int(r["embed_fail_count"]),
                    "last_error": r["last_embed_error"][:200] if r["last_embed_error"] else "",
                }
                for r in error_rows
            ]

            # Sample turns (first few)
            cursor = await conn.execute(
                """
                SELECT turn_id, turn_index, primary_domain, embedded,
                       importance, word_count
                FROM corpus_turns
                WHERE source_path = ?
                ORDER BY turn_index ASC
                LIMIT 5
                """,
                [source_path],
            )
            sample_rows = await cursor.fetchall()
            result["sample_turns"] = [
                {
                    "turn_id": r["turn_id"],
                    "turn_index": int(r["turn_index"]),
                    "primary_domain": r["primary_domain"],
                    "embedded": bool(r["embedded"]),
                    "importance": float(r["importance"]),
                    "word_count": int(r["word_count"]),
                }
                for r in sample_rows
            ]

            return result
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)

    async def get_corpus_problems(self) -> dict[str, Any]:
        """Get corpus problems summary for the Corpus Workbench.

        Returns:
          - failed_ingest_jobs: turns with embed_fail_count > 0
          - unembedded_chunks: turns without embeddings
          - stale_docs: documents with no recent activity
          - duplicate_hashes: content hash collisions
        """
        conn = await self._checkout_read_conn()
        try:
            result: dict[str, Any] = {}

            # Failed embed jobs
            cursor = await conn.execute(
                """
                SELECT turn_id, source_path, embed_fail_count, last_embed_error,
                       source_model, primary_domain
                FROM corpus_turns
                WHERE embed_fail_count > 0
                ORDER BY embed_fail_count DESC
                LIMIT 50
                """
            )
            rows = await cursor.fetchall()
            result["failed_ingest_jobs"] = [
                {
                    "turn_id": r["turn_id"],
                    "source_path": r["source_path"],
                    "fail_count": int(r["embed_fail_count"]),
                    "last_error": r["last_embed_error"][:200] if r["last_embed_error"] else "",
                    "source_model": r["source_model"],
                    "primary_domain": r["primary_domain"],
                }
                for r in rows
            ]

            # Unembedded count
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE embedded = 0")
            row = await cursor.fetchone()
            result["unembedded_count"] = int(row["c"]) if row else 0

            # Needs reembed count
            cursor = await conn.execute("SELECT COUNT(*) as c FROM corpus_turns WHERE needs_reembed = 1")
            row = await cursor.fetchone()
            result["needs_reembed_count"] = int(row["c"]) if row else 0

            # Duplicate hashes
            cursor = await conn.execute(
                """
                SELECT content_hash, COUNT(*) as c
                FROM corpus_turns
                WHERE content_hash != ''
                GROUP BY content_hash
                HAVING c > 1
                ORDER BY c DESC
                LIMIT 20
                """
            )
            rows = await cursor.fetchall()
            result["duplicate_hashes"] = [{"content_hash": r["content_hash"], "count": int(r["c"])} for r in rows]

            # Stale docs: documents not updated in the last 30 days
            cursor = await conn.execute(
                """
                SELECT source_path, MAX(updated_at) as last_updated,
                       COUNT(*) as turn_count
                FROM corpus_turns
                WHERE source_path IS NOT NULL AND source_path != ''
                GROUP BY source_path
                HAVING last_updated < datetime('now', '-30 days')
                ORDER BY last_updated ASC
                LIMIT 20
                """
            )
            rows = await cursor.fetchall()
            result["stale_docs"] = [
                {
                    "source_path": r["source_path"],
                    "last_updated": r["last_updated"],
                    "turn_count": int(r["turn_count"]),
                }
                for r in rows
            ]

            return result
        except Exception:
            await self._reset_conn()
            raise
        finally:
            self._return_read_conn(conn)
