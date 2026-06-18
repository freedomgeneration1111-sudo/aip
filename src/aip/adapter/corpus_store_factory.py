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

# Registry: name → Migration
MIGRATIONS: dict[str, Migration] = {
    _M001_ADD_REVISION_PARENT_ID.name: _M001_ADD_REVISION_PARENT_ID,
    _M002_ADD_TARGET_CORPUS_ID.name: _M002_ADD_TARGET_CORPUS_ID,
    _M003_ADD_LATEST_ECS_STATE.name: _M003_ADD_LATEST_ECS_STATE,
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
            #
            # For Chunk 2, only CorpusTurnStore is wired. The other 5 stores
            # (lexical, vector, graph, artifact, ecs) are attached in Chunk 8
            # when ECS/ArtifactStore move per-corpus.
            stores.turn_store = await self._build_turn_store(manager, corpus_id)

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
