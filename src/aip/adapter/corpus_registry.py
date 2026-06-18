"""CorpusRegistry — concrete implementation of CorpusRegistryProtocol.

ADR-008 Rev 3.1 §8 Chunk 2, Amendment §A0, §A5, §A8, §A13:

Central registry for all corpora. Holds dict[corpus_id → CorpusStores].
The primary store-access interface — all store access goes through
get_stores(corpus_id), not through legacy container singletons.

Layer: adapter. Imports from foundation and adapter. Wired into AipContainer
in Chunk 3 (container.corpus_registry = CorpusRegistry(...)).

Contract (consumed by AipContainer, routes, actors):
    registry = CorpusRegistry()
    await registry.startup()  # registers definer + pre-configured corpora
    stores = await registry.get_stores("definer")
    # stores.turn_store.search(...)
    await registry.transition_artifact("definer", "art-001", "ARCHIVED")

Concurrency model:
  - _migration_lock: serializes migrations ACROSS corpora (one migration at a time).
  - _migration_ready: Event set after startup() completes. Actors MUST await
    this before their first write (§A5 — 5 schedulers in app.py).
  - Per-corpus write_lock (on CorpusStores): serializes writes WITHIN a corpus.
  - get_stores() is lock-free for reads (the dict is only mutated under
    _migration_lock during register/delete).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aip.adapter.corpus_store_factory import CorpusStoreFactory
from aip.adapter.corpus_stores import CorpusStores
from aip.foundation.corpus_constants import (
    CORPUS_READ_POOL_SIZE,
    KNOWN_NON_CORPUS_DB_FILES,
    MAX_CONNECTIONS,
    MAX_CORPORA,
    NON_CORPUS_READ_POOL_SIZE,
)
from aip.foundation.corpus_exceptions import (
    BranhamIsolationViolation,
    ConnectionBudgetExceeded,
    CorpusNotFound,
    DeletionStateError,
)
from aip.foundation.corpus_types import CorpusDeletionState, CorpusType
from aip.foundation.protocols.corpus_registry import ReviewItem

logger = logging.getLogger(__name__)


class CorpusRegistry:
    """Concrete CorpusRegistry — implements CorpusRegistryProtocol.

    Holds the corpus_id → CorpusStores mapping, the migration gate
    (_migration_ready Event + _migration_lock), and the connection budget.
    """

    def __init__(
        self,
        max_connections: int = MAX_CONNECTIONS,
        max_corpora: int = MAX_CORPORA,
        read_pool_size_corpus: int = CORPUS_READ_POOL_SIZE,
    ) -> None:
        self._corpora: dict[str, CorpusStores] = {}
        self._migration_ready: asyncio.Event = asyncio.Event()
        self._migration_lock: asyncio.Lock = asyncio.Lock()
        self._non_corpus_connection_budget: int = 0  # set in startup()
        self._embedding_model: str | None = None  # set on first register()
        self._branham_policy_enabled: bool = False

        self._max_connections = max_connections
        self._max_corpora = max_corpora
        self._read_pool_size_corpus = read_pool_size_corpus

        self._factory = CorpusStoreFactory(read_pool_size=read_pool_size_corpus)
        self._definer_stores: CorpusStores | None = None

    # ------------------------------------------------------------------
    # Properties for external access (read-only)
    # ------------------------------------------------------------------

    @property
    def migration_ready(self) -> asyncio.Event:
        """Event that actors await before their first write (§A5)."""
        return self._migration_ready

    @property
    def corpora(self) -> dict[str, CorpusStores]:
        """Read-only view of the registered corpora dict."""
        return dict(self._corpora)  # shallow copy

    # ------------------------------------------------------------------
    # startup() — ADR-008 Rev 3.1 §8 Chunk 2, §A5
    # ------------------------------------------------------------------

    async def startup(
        self,
        corpora_to_register: list[tuple[str, CorpusType, Path]] | None = None,
        embedding_model: str | None = None,
        branham_policy_enabled: bool = False,
    ) -> None:
        """Initialize all pre-configured corpora, run migrations, set migration_ready.

        Args:
            corpora_to_register: list of (corpus_id, corpus_type, db_path).
                The definer corpus MUST be first. If None, no corpora are
                registered (used in tests).
            embedding_model: the shared embedding model id. All corpora must
                use this model (§3.3). If None, the first corpus to register
                sets it.
            branham_policy_enabled: if True, Branham 4-layer isolation is
                enforced (§3.4).

        Raises:
            ConnectionBudgetExceeded: if MAX_CORPORA or MAX_CONNECTIONS exceeded.
            EmbeddingModelMismatch: if a corpus specifies a different model.
            CorpusMigrationError: on fingerprint mismatch or partial migration.
            Any store-init error (propagated from factory.build()).

        If the definer corpus fails to register, startup() raises and the
        app does not start (§A16 C-5). _reconcile_bridge_edges() is skipped
        (nothing to reconcile).
        """
        self._embedding_model = embedding_model
        self._branham_policy_enabled = branham_policy_enabled

        # Measure non-corpus connection budget (conservative default if unavailable)
        self._non_corpus_connection_budget = KNOWN_NON_CORPUS_DB_FILES * (1 + NON_CORPUS_READ_POOL_SIZE)
        logger.info(
            "corpus_registry_startup non_corpus_budget=%d max_corpora=%d",
            self._non_corpus_connection_budget,
            self._max_corpora,
        )

        if not corpora_to_register:
            # No corpora to register — set migration_ready and return.
            # Used in tests where the test registers corpora manually.
            self._migration_ready.set()
            return

        # Register definer first (it must be in self._corpora before
        # _reconcile_bridge_edges can run).
        definer_registered = False
        for corpus_id, corpus_type, db_path in corpora_to_register:
            try:
                await self.register(
                    corpus_id=corpus_id,
                    corpus_type=corpus_type,
                    db_path=db_path,
                    branham_policy_enabled=(branham_policy_enabled and corpus_id == "branham"),
                )
                if corpus_id == "definer":
                    definer_registered = True
            except Exception:
                if corpus_id == "definer":
                    # Definer registration failure is fatal (§A16 C-5)
                    logger.error("corpus_registry_definer_registration_failed")
                    raise
                # Non-definer failure: log and continue (degraded mode)
                logger.warning(
                    "corpus_registry_non_definer_registration_failed corpus=%s — continuing in degraded mode",
                    corpus_id,
                    exc_info=True,
                )

        if definer_registered:
            # Cache definer stores for the sync container.definer_stores property
            self._definer_stores = self._corpora.get("definer")
            # Reconcile orphan bridge edges from crashed deletes (§A13, §9.4)
            await self._reconcile_bridge_edges()

        # Signal actors that they can begin writing
        self._migration_ready.set()
        logger.info(
            "corpus_registry_startup_complete registered=%d",
            len(self._corpora),
        )

    # ------------------------------------------------------------------
    # register() — ADR-008 Rev 3.1 §8 Chunk 2
    # ------------------------------------------------------------------

    async def register(
        self,
        corpus_id: str,
        corpus_type: CorpusType,
        db_path: Path,
        branham_policy_enabled: bool = False,
    ) -> CorpusStores:
        """Open or create a corpus database.

        Validates budget + embedding model, then calls factory.build().
        Wraps build() in try/except: the factory handles partial-init cleanup.

        Raises:
            ConnectionBudgetExceeded: if MAX_CORPORA or MAX_CONNECTIONS exceeded.
            EmbeddingModelMismatch: if corpus specifies a different model.
            CorpusMigrationError: on fingerprint mismatch or partial migration.
        """
        if corpus_id in self._corpora:
            # Idempotent: return existing stores
            return self._corpora[corpus_id]

        # Budget validation
        self._validate_connection_budget()

        # Build the stores (factory handles partial-init cleanup)
        stores = await self._factory.build(
            corpus_id=corpus_id,
            corpus_type=corpus_type,
            db_path=db_path,
            migration_lock=self._migration_lock,
        )

        # Set Branham policy flag (used by get_stores() Layer 3 check)
        stores._branham_policy_enabled = branham_policy_enabled  # type: ignore[attr-defined]

        # Persist deletion_state = ACTIVE in corpus_metadata
        await self._persist_deletion_state(stores, CorpusDeletionState.ACTIVE)

        # Write audit log entry
        await self._write_audit(
            action="CORPUS_REGISTERED",
            corpus_id=corpus_id,
            outcome="SUCCESS",
            detail={"corpus_type": corpus_type.value, "db_path": str(db_path)},
        )

        self._corpora[corpus_id] = stores
        logger.info(
            "corpus_registered corpus=%s type=%s — total_registered=%d",
            corpus_id,
            corpus_type.value,
            len(self._corpora),
        )
        return stores

    # ------------------------------------------------------------------
    # get_stores() — ADR-008 Rev 3.1 §8 Chunk 2, §3.4 (Branham 4-layer)
    # ------------------------------------------------------------------

    async def get_stores(
        self,
        corpus_id: str,
        *,
        session_branham_allowlist: bool = False,
    ) -> CorpusStores:
        """Look up stores by corpus_id.

        Raises:
            CorpusNotFound: if corpus_id is not registered.
            BranhamIsolationViolation: if Branham corpus is requested without
                session_branham_allowlist=True (Layer 3 of 4-layer defense).
            DeletionStateError: if the corpus is in DELETING state.
        """
        if corpus_id not in self._corpora:
            raise CorpusNotFound(
                f"Corpus {corpus_id!r} is not registered. Registered corpora: {list(self._corpora.keys())}"
            )

        stores = self._corpora[corpus_id]

        # Layer 3: Branham isolation check
        if getattr(stores, "_branham_policy_enabled", False) and not session_branham_allowlist:
            await self._write_audit(
                action="BRANHAM_POLICY_TRIGGERED",
                corpus_id=corpus_id,
                outcome="DENIED",
                detail={"reason": "session_branham_allowlist=False"},
            )
            raise BranhamIsolationViolation(
                f"Branham corpus {corpus_id!r} requires session_branham_allowlist=True. "
                f"Layer 3 of 4-layer defense (ADR-008 Rev 3.1 §3.4)."
            )

        # Deletion state check
        if stores.deletion_state == CorpusDeletionState.DELETING:
            raise DeletionStateError(
                f"Corpus {corpus_id!r} is in DELETING state — reads are blocked. "
                f"Wait for delete_corpus() to complete or restart the process."
            )

        return stores

    # ------------------------------------------------------------------
    # delete_corpus() — ADR-008 Rev 3.1 §A13 (two-phase + WAL sidecars)
    # ------------------------------------------------------------------

    async def delete_corpus(self, corpus_id: str) -> None:
        """Two-phase deletion — ADR-008 Rev 3.1 §A13.

        Phase 1: Set deletion_state=DELETING (persisted to corpus_metadata
                 before any file op, so a crash mid-delete is recoverable).
        Phase 2: delete_bridge_edges(corpus_id) in definer graph. (Stub —
                 implemented in Chunk 6 when bridge edges exist.)
        Phase 3: PRAGMA wal_checkpoint(TRUNCATE) on the corpus db.
        Phase 4: close the shared connection manager (close_all()).
        Phase 5: rename all three files (.db, .db-wal, .db-shm) via
                 with_name(name + ".deleted").
        Phase 6: pop from _corpora.
        Phase 7: audit-log CORPUS_DELETED.
        """
        if corpus_id not in self._corpora:
            raise CorpusNotFound(f"Cannot delete unregistered corpus {corpus_id!r}.")

        stores = self._corpora[corpus_id]
        if stores.deletion_state == CorpusDeletionState.DELETING:
            raise DeletionStateError(f"Corpus {corpus_id!r} is already in DELETING state.")

        # Phase 1: set DELETING (persisted before any file op)
        stores.deletion_state = CorpusDeletionState.DELETING
        await self._persist_deletion_state(stores, CorpusDeletionState.DELETING)
        logger.info("corpus_deletion_started corpus=%s", corpus_id)

        # Capture db_path BEFORE close_all() (which sets connection_manager=None)
        db_path_str = stores.connection_manager.db_path if stores.connection_manager else ""
        db_path = Path(db_path_str) if db_path_str else None

        # Phase 2: delete bridge edges (stub — Chunk 6 implements this)
        # await self._delete_bridge_edges_for(corpus_id)

        # Phase 3: WAL checkpoint (flush WAL sidecar before rename)
        if stores.connection_manager is not None:
            try:
                async with stores.write_lock:
                    await stores.connection_manager.wal_checkpoint()
            except Exception as exc:
                logger.warning(
                    "corpus_delete_wal_checkpoint_failed corpus=%s error=%s",
                    corpus_id,
                    exc,
                )

        # Phase 4: close all stores + connection manager
        await stores.close_all()

        # Phase 5: rename .db, .db-wal, .db-shm to *.deleted
        if db_path is not None and db_path.exists():
            for suffix in ("", "-wal", "-shm"):
                sidecar = db_path.with_name(db_path.name + suffix)
                if sidecar.exists():
                    target = sidecar.with_name(sidecar.name + ".deleted")
                    try:
                        sidecar.rename(target)
                        logger.debug(
                            "corpus_file_renamed source=%s target=%s",
                            str(sidecar),
                            str(target),
                        )
                    except Exception as exc:
                        logger.warning(
                            "corpus_file_rename_failed source=%s error=%s",
                            str(sidecar),
                            exc,
                        )

        # Phase 6: pop from _corpora
        del self._corpora[corpus_id]
        if self._definer_stores is stores:
            self._definer_stores = None

        # Phase 7: audit log
        await self._write_audit(
            action="CORPUS_DELETED",
            corpus_id=corpus_id,
            outcome="SUCCESS",
        )
        logger.info("corpus_deleted corpus=%s", corpus_id)

    # ------------------------------------------------------------------
    # list_corpora() — ADR-008 Rev 3.1 §8 Chunk 2
    # ------------------------------------------------------------------

    async def list_corpora(self) -> list[str]:
        """Return list of registered corpus_ids."""
        return list(self._corpora.keys())

    # ------------------------------------------------------------------
    # list_review_items() — ADR-008 Rev 3.1 §9.4 (advisory fan-in + validation)
    # ------------------------------------------------------------------

    async def list_review_items(
        self,
        states: list[str],
        corpus_ids: list[str] | None = None,
    ) -> list[ReviewItem]:
        """Fan out across corpus artifact_stores, merge by updated_at desc.

        NOTE: This is a stub for Chunk 2. Full implementation lands in
        Chunk 8 when ECS/ArtifactStore move per-corpus and review_queue_fanin
        is created. For now, returns an empty list.
        """
        # Chunk 8 deliverable: read from review_queue_fanin, validate against
        # owning corpus ecs_store.current_state(), merge and sort.
        return []

    # ------------------------------------------------------------------
    # transition_artifact() — ADR-008 Rev 3.1 §A3, §A10
    # ------------------------------------------------------------------

    async def transition_artifact(
        self,
        corpus_id: str,
        artifact_id: str,
        new_state: str,
    ) -> None:
        """Transition artifact ECS state under the corpus write_lock.

        NOTE: This is a stub for Chunk 2. Full implementation lands in
        Chunk 8 when ECS/ArtifactStore move per-corpus. For now, raises
        NotImplementedError to prevent accidental use.
        """
        raise NotImplementedError("transition_artifact() is implemented in Chunk 8 (ECS/ArtifactStore per corpus).")

    # ------------------------------------------------------------------
    # _reconcile_bridge_edges() — ADR-008 Rev 3.1 §A13, §9.4
    # ------------------------------------------------------------------

    async def _reconcile_bridge_edges(self) -> None:
        """Scan definer graph_edges for orphan bridge edges. Stub for Chunk 2.

        Full implementation in Chunk 6 when bridge edges exist:
          - Scans definer.graph_edges WHERE target_corpus_id IS NOT NULL.
          - For each target_corpus_id not in self._corpora:
            - calls definer_stores.graph_store.delete_bridge_edges(target_corpus_id)
            - emits WARNING log + audit log: action=BRIDGE_ORPHAN_CLEANED
        """
        # Chunk 6 deliverable
        pass

    # ------------------------------------------------------------------
    # Budget validation — ADR-008 Rev 3.1 §9.3
    # ------------------------------------------------------------------

    def _validate_connection_budget(self) -> None:
        """Validate that registering one more corpus fits the budget.

        available = _max_connections - _non_corpus_connection_budget
        needed = (len(self._corpora) + 1) * (1 + self._read_pool_size_corpus)
        if needed > available: raise ConnectionBudgetExceeded
        if len(self._corpora) >= self._max_corpora: raise ConnectionBudgetExceeded
        """
        available = self._max_connections - self._non_corpus_connection_budget
        needed = (len(self._corpora) + 1) * (1 + self._read_pool_size_corpus)
        if needed > available:
            raise ConnectionBudgetExceeded(
                f"Registering another corpus would exceed MAX_CONNECTIONS. "
                f"available={available}, needed={needed} "
                f"(corpora={len(self._corpora) + 1} × per_corpus={1 + self._read_pool_size_corpus}). "
                f"Non-corpus budget={self._non_corpus_connection_budget}, "
                f"MAX_CONNECTIONS={self._max_connections}."
            )
        if len(self._corpora) >= self._max_corpora:
            raise ConnectionBudgetExceeded(
                f"MAX_CORPORA={self._max_corpora} reached. "
                f"Currently registered: {len(self._corpora)}. "
                f"Cannot register another corpus."
            )

    # ------------------------------------------------------------------
    # Helpers — audit log + deletion state persistence
    # ------------------------------------------------------------------

    async def _persist_deletion_state(self, stores: CorpusStores, state: CorpusDeletionState) -> None:
        """Persist deletion_state to corpus_metadata table.

        NOTE: For Chunk 2, this is a no-op (the corpus_metadata table is
        created by the migration runner, but the deletion_state write
        requires a working store). Full implementation in Chunk 8.
        """
        # Chunk 8 deliverable: write to corpus_metadata
        pass

    async def _write_audit(
        self,
        action: str,
        corpus_id: str | None,
        outcome: str,
        detail: dict | None = None,
    ) -> None:
        """Write an entry to corpus_audit_log in the definer corpus.

        NOTE: For Chunk 2, this is a no-op (the corpus_audit_log table is
        created in Chunk 8). Logs to the standard logger instead.
        """
        logger.info(
            "corpus_audit action=%s corpus=%s outcome=%s detail=%s",
            action,
            corpus_id,
            outcome,
            detail or {},
        )
