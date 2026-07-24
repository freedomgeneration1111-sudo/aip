"""CorpusStoreFactory — builds CorpusStores bundles for the registry.

ADR-008 Rev 3.1 §8 Chunk 2, Amendment §A0:

Builds one CorpusConnectionManager per corpus, opens it, runs migrations
under the migration runner (§A8), and attaches all six stores. Each store
receives the shared connection manager instead of a db_path.

Layer: adapter. Imports from foundation and adapter (corpus_connection,
corpus_stores, corpus_migration_runner, and the existing store classes).

Contract (consumed by CorpusRegistry.register()):
    factory = CorpusStoreFactory()
    stores = await factory.build(
        corpus_id="definer",
        corpus_type=CorpusType.CONVERSATION,
        db_path=Path("db/definer.db"),
        migration_lock=registry._migration_lock,
    )
    # stores is a fully-initialized CorpusStores with all 6 stores attached.
    # On any failure, factory calls stores.close_all() and re-raises.

NOTE: For Chunk 2, only CorpusTurnStore is fully wired to accept the
connection manager. The other 5 stores (lexical, vector, graph, artifact,
ecs) are attached as None — they're wired in Chunk 8 when ECS/ArtifactStore
move per-corpus. This keeps Chunk 2 testable in isolation.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aip.adapter.corpus_connection import CorpusConnectionManager
from aip.adapter.corpus_migration_runner import CorpusMigrationRunner, Migration
from aip.adapter.corpus_stores import CorpusStores
from aip.foundation.corpus_constants import CORPUS_READ_POOL_SIZE
from aip.foundation.corpus_types import MIGRATIONS_FOR_CORPUS_TYPE, CorpusType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migration registry — ADR-008 Rev 3.1 Appendix B + §A8
# ---------------------------------------------------------------------------
# Each migration is registered here with its SQL and verification clauses.
# Migration names MUST begin with "M<3-digit>_" so lexicographic sort matches
# migration order. The fingerprint is order-preserving (not sorted) — see
# corpus_migration_runner.compute_fingerprint().

_M001_ADD_REVISION_PARENT_ID = Migration(
    name="M001_add_revision_parent_id",
    sql="ALTER TABLE corpus_turns ADD COLUMN revision_parent_id TEXT REFERENCES corpus_turns(turn_id)",
    verify=(("table_info(corpus_turns)", "revision_parent_id"),),
)

_M002_ADD_TARGET_CORPUS_ID = Migration(
    name="M002_add_target_corpus_id",
    # M002 alters graph_edges, which is created by GraphStore. In Chunk 2,
    # GraphStore isn't attached yet (it lands in Chunk 6). The migration
    # records a pending flag in corpus_metadata; Chunk 6's GraphStore creation
    # will read this flag and add the target_corpus_id column when it creates
    # graph_edges. This keeps the migration runner's "genuine failure" detection
    # honest — we don't swallow errors, we defer the ALTER to when the table exists.
    sql=("INSERT OR IGNORE INTO corpus_metadata (key, value) VALUES ('M002_target_corpus_id_pending', 'true')"),
    # No verification in Chunk 2 — graph_edges doesn't exist yet. Chunk 6
    # will add verification when GraphStore is attached.
    verify=(),
)

_M003_ADD_LATEST_ECS_STATE = Migration(
    name="M003_add_latest_ecs_state",
    sql="ALTER TABLE corpus_turns ADD COLUMN latest_ecs_state TEXT NOT NULL DEFAULT 'GENERATED'",
    verify=(("table_info(corpus_turns)", "latest_ecs_state"),),
)

# M004: artifact_turn_links — ADR-008 Rev 3.1 §A3.
# Explicit link table mapping artifacts to turns (many artifacts have no turn —
# wiki, summary, eval artifacts). transition_artifact() uses this to find the
# turn_id when updating latest_ecs_state on the corpus_turns row.
_M004_ADD_ARTIFACT_TURN_LINKS = Migration(
    name="M004_add_artifact_turn_links",
    sql=(
        "CREATE TABLE IF NOT EXISTS artifact_turn_links ("
        "artifact_id TEXT NOT NULL, "
        "turn_id TEXT NOT NULL, "
        "PRIMARY KEY (artifact_id, turn_id)"
        "); "
        "CREATE INDEX IF NOT EXISTS idx_atl_turn ON artifact_turn_links(turn_id)"
    ),
    verify=(("table_info(artifact_turn_links)", "artifact_id"),),
)

# M005: review_queue.corpus_id — ADR-008 Rev 3.1 §A11.
# Adds corpus_id to the existing review_queue table so DEFINER decisions can
# route back to the owning corpus. Default 'definer' for backward compat.
# This migration only applies to the definer corpus (where review_queue lives).
_M005_ADD_REVIEW_QUEUE_CORPUS_ID = Migration(
    name="M005_add_review_queue_corpus_id",
    sql="ALTER TABLE review_queue ADD COLUMN corpus_id TEXT NOT NULL DEFAULT 'definer'",
    verify=(("table_info(review_queue)", "corpus_id"),),
)

# Registry: name → Migration
MIGRATIONS: dict[str, Migration] = {
    _M001_ADD_REVISION_PARENT_ID.name: _M001_ADD_REVISION_PARENT_ID,
    _M002_ADD_TARGET_CORPUS_ID.name: _M002_ADD_TARGET_CORPUS_ID,
    _M003_ADD_LATEST_ECS_STATE.name: _M003_ADD_LATEST_ECS_STATE,
    _M004_ADD_ARTIFACT_TURN_LINKS.name: _M004_ADD_ARTIFACT_TURN_LINKS,
    _M005_ADD_REVIEW_QUEUE_CORPUS_ID.name: _M005_ADD_REVIEW_QUEUE_CORPUS_ID,
}


class CorpusStoreFactory:
    """Creates and initializes all stores for a corpus.

    Called by CorpusRegistry.register(). Builds a CorpusStores bundle:
      1. Create CorpusConnectionManager (1 write conn + N read conns).
      2. Open the manager.
      3. Run migrations under migration_lock + corpus write_lock.
      4. Create CorpusStores with the manager attached.
      5. Attach stores incrementally (turn_store first, others in Chunk 8).
      6. On any failure, call stores.close_all() and re-raise.
    """

    def __init__(self, read_pool_size: int = CORPUS_READ_POOL_SIZE) -> None:
        self._read_pool_size = read_pool_size

    async def build(
        self,
        corpus_id: str,
        corpus_type: CorpusType,
        db_path: Path,
        migration_lock: asyncio.Lock,
    ) -> CorpusStores:
        """Build a fully-initialized CorpusStores bundle.

        Args:
            corpus_id: the corpus identifier (e.g. "definer", "codeforge").
            corpus_type: determines which migrations apply.
            db_path: path to the corpus SQLite file (created if absent).
            migration_lock: registry-global lock serializing migrations across corpora.

        Returns:
            CorpusStores with connection_manager attached and migrations applied.

        Raises:
            CorpusMigrationError: on fingerprint mismatch or partial migration.
            aiosqlite.Error: on database open failure.
            Any store-init error (propagated to caller for cleanup).
        """
        # Step 1: create the connection manager
        manager = CorpusConnectionManager(
            db_path=str(db_path),
            read_pool_size=self._read_pool_size,
        )

        # Step 2: open the manager (opens 1 write + N read connections)
        await manager.open()

        # Step 3: create the CorpusStores shell with the manager attached.
        # write_lock/closed/deletion_state are set in __init__ so close_all()
        # is safe even if we fail before attaching any stores.
        stores = CorpusStores(
            corpus_id=corpus_id,
            corpus_type=corpus_type,
            connection_manager=manager,
        )

        try:
            # Step 4: attach the turn store FIRST. The turn store's _create_tables
            # creates the base corpus_turns table (and FTS5 + triggers). Migrations
            # (M001, M003) run ALTER TABLE on corpus_turns, so the base table must
            # exist before migrations run. Per §A8, migrations run OUTSIDE
            # _create_tables — but the base tables must exist first.
            stores.turn_store = await self._build_turn_store(manager, corpus_id)

            # Step 4b: for the definer corpus, ensure review_queue table exists
            # before M005 tries to ALTER it. The ReviewQueueStore creates this
            # table; we initialize it here so M005 can add the corpus_id column.
            if corpus_type == CorpusType.CONVERSATION:
                await self._ensure_review_queue_table(manager)

            # Step 5: run migrations under migration_lock + corpus write_lock.
            # Both locks required (§A8 + §3.6). Migrations run after base tables
            # exist so ALTER TABLE succeeds.
            async with migration_lock:
                async with stores.write_lock:
                    runner = CorpusMigrationRunner(manager)
                    migration_names = MIGRATIONS_FOR_CORPUS_TYPE.get(corpus_type, [])
                    await runner.run_migrations(
                        migration_names=migration_names,
                        migrations_registry=MIGRATIONS,
                        corpus_id=corpus_id,
                    )

                    # Step 5b: create definer-only tables (review_queue_fanin,
                    # corpus_audit_log, review_fanin_outbox) — ADR-008 §8 Chunk 8.
                    # These are NOT migrations (they're new tables, not ALTERs)
                    # but they're created under the same lock for atomicity.
                    if corpus_type == CorpusType.CONVERSATION:
                        await self._create_definer_only_tables(manager)

            # Step 6: attach ECS + artifact stores (ADR-008 §8 Chunk 8).
            # These use the corpus db_path (same as turn_store). In a future
            # refactor (post-Chunk-3), they'll use the shared connection manager.
            stores.ecs_store = await self._build_ecs_store(manager, corpus_id)
            stores.artifact_store = await self._build_artifact_store(manager, corpus_id)

            # Step 6b (ND3, 2026-07-23): attach lexical + graph stores.
            # These are per-corpus instances using the corpus db_path.
            # The lexical store provides a dedicated FTS5 index for
            # document-style content (complements the turn store's built-in
            # FTS5). The graph store provides per-corpus graph nodes/edges
            # (for Phase β code dependency graphs).
            # vector_store is deferred — it needs an embedding_provider
            # (container-level, not per-corpus); Phase β will wire it.
            stores.lexical_store = await self._build_lexical_store(manager, corpus_id)
            stores.graph_store = await self._build_graph_store(manager, corpus_id)

            logger.info(
                "corpus_stores_built corpus=%s type=%s db_path=%s",
                corpus_id,
                corpus_type.value,
                str(db_path),
            )
            return stores

        except Exception:
            # Partial-init cleanup — never leak connections.
            # close_all() is safe on the shell because write_lock/closed are set.
            await stores.close_all()
            raise

    async def _build_turn_store(self, manager: CorpusConnectionManager, corpus_id: str):
        """Build a CorpusTurnStore wired to the shared connection manager.

        For Chunk 2, this uses the existing CorpusTurnStore constructor with
        db_path (the store opens its own connections). In Chunk 8, this is
        refactored to inject the manager directly.

        NOTE: This is a known compromise for Chunk 2 testability. The store
        uses its own connections (1 write + N read) IN ADDITION to the
        manager's connections. This temporarily inflates the connection
        count, but Chunk 3 removes the legacy singletons and Chunk 8
        refactors all stores to use the manager. The budget formula in
        §9.3 assumes the Chunk 8 end-state.
        """
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        store = CorpusTurnStore(manager.db_path)
        await store.initialize()
        return store

    async def _build_ecs_store(self, manager: CorpusConnectionManager, corpus_id: str):
        """Build a PersistentEcsStore for this corpus.

        ADR-008 §8 Chunk 8: ECS store is per-corpus. Uses the corpus db_path.
        The ECS store creates its own tables (ecs_state, ecs_transitions) via
        its initialize() method.
        """
        from aip.adapter.ecs_store_persistent import PersistentEcsStore

        store = PersistentEcsStore(manager.db_path)
        await store.initialize()
        return store

    async def _build_artifact_store(self, manager: CorpusConnectionManager, corpus_id: str):
        """Build a VersionedArtifactStore for this corpus.

        ADR-008 §8 Chunk 8: ArtifactStore is per-corpus. Uses the corpus db_path.
        The artifact store creates its own table (artifacts) via initialize().
        """
        from aip.adapter.artifact_store_versioned import VersionedArtifactStore

        store = VersionedArtifactStore(manager.db_path)
        await store.initialize()
        return store

    async def _build_lexical_store(self, manager: CorpusConnectionManager, corpus_id: str):
        """Build a SqliteFts5Store for this corpus (ND3, 2026-07-23).

        Provides a dedicated FTS5 index for document-style content that
        isn't stored in corpus_turns (e.g. ingested markdown documents,
        book chapters). The turn store has its own built-in FTS5 for
        turn content; this store is for non-turn content.

        Uses the corpus db_path (same SQLite file as turn_store).
        """
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        store = SqliteFts5LexicalStore(manager.db_path)
        await store.initialize()
        return store

    async def _build_graph_store(self, manager: CorpusConnectionManager, corpus_id: str):
        """Build a GraphStore for this corpus (ND3, 2026-07-23).

        Provides per-corpus graph nodes/edges. For the definer corpus,
        this is the conversation knowledge graph (entities, relationships).
        For code corpora, this will hold the code dependency graph
        (functions, classes, imports — Phase β).

        Uses the corpus db_path (same SQLite file as turn_store).
        """
        from aip.adapter.graph_store import GraphStore

        store = GraphStore(manager.db_path)
        await store.initialize()
        return store

    async def _ensure_review_queue_table(self, manager: CorpusConnectionManager) -> None:
        """Ensure the review_queue table exists in the definer corpus.

        M005 (add review_queue.corpus_id) needs the base review_queue table
        to exist before it can ALTER it. The ReviewQueueStore creates this
        table; we initialize it here so M005 succeeds.
        """
        from aip.adapter.review_queue_store import ReviewQueueStore

        store = ReviewQueueStore(manager.db_path)
        await store.initialize()
        await store.close()

    async def _create_definer_only_tables(self, manager: CorpusConnectionManager) -> None:
        """Create definer-only tables: review_queue_fanin, corpus_audit_log,
        review_fanin_outbox.

        ADR-008 Rev 3.1 §8 Chunk 8, §A10, §9.6:
          - review_queue_fanin: cross-corpus review discovery index (definer only)
          - corpus_audit_log: lifecycle events (definer only)
          - review_fanin_outbox: durable outbox for fan-in updates (definer only,
            since the fan-in table lives in definer)

        These are created under the migration lock + corpus write_lock for
        atomicity. They are NOT migrations (they're new tables, not ALTERs)
        but they're idempotent (CREATE TABLE IF NOT EXISTS).
        """
        conn = manager.write_conn

        # review_queue_fanin: cross-corpus discovery index (§9.4)
        # Primary key is (corpus_id, artifact_id) — one row per artifact across
        # all corpora. Index on (state, updated_at) for list_review_items() perf.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_queue_fanin (
                corpus_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                state TEXT NOT NULL,
                title TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (corpus_id, artifact_id)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_queue_fanin_state_updated "
            "ON review_queue_fanin(state, updated_at DESC)"
        )

        # corpus_audit_log: lifecycle events (§9.6)
        # id = uuid4().hex (§A16 C-3). Records CORPUS_REGISTERED, CORPUS_DELETED,
        # BRIDGE_ORPHAN_CLEANED, RESTRICTED_CORPUS_ACCESS_DENIED, MIGRATION_APPLIED,
        # ARTIFACT_ARCHIVED, ARTIFACT_SUPERSEDED.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_audit_log (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                actor_id TEXT NOT NULL,
                corpus_id TEXT,
                action TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_corpus_audit_log_ts ON corpus_audit_log(ts DESC)")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_corpus_audit_log_corpus_action ON corpus_audit_log(corpus_id, action)"
        )

        # review_fanin_outbox: durable outbox for fan-in updates (§A10)
        # Written in the SAME transaction as the ECS transition (atomic, crash-safe).
        # A consumer reads delivered=0 rows, writes review_queue_fanin, marks delivered=1.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_fanin_outbox (
                id TEXT PRIMARY KEY,
                corpus_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                state TEXT NOT NULL,
                title TEXT,
                updated_at REAL NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_fanin_outbox_undelivered "
            "ON review_fanin_outbox(delivered, updated_at)"
        )

        await conn.commit()
        logger.debug("definer_only_tables_created db_path=%s", manager.db_path)
