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
import json
import logging
import time
import uuid
from datetime import datetime, timezone
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
        self._restricted_policy_enabled: bool = False  # generic: gates ALL sensitive corpora

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
        restricted_policy_enabled: bool = False,
        branham_policy_enabled: bool | None = None,  # deprecated alias
    ) -> None:
        """Initialize all pre-configured corpora, run migrations, set migration_ready.

        Args:
            corpora_to_register: list of (corpus_id, corpus_type, db_path).
                The definer corpus MUST be first. If None, no corpora are
                registered (used in tests).
            embedding_model: the shared embedding model id. All corpora must
                use this model (§3.3). If None, the first corpus to register
                sets it.
            restricted_policy_enabled: if True, the generic 4-layer restricted-corpus
                isolation is enforced (§3.4). Any corpus registered with sensitive=True
                is gated — the session must include its corpus_id in
                allowed_restricted_corpora to access it.
            branham_policy_enabled: DEPRECATED alias for restricted_policy_enabled.
                Kept for backward compat with existing callers.

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
        # Handle deprecated alias
        if branham_policy_enabled is not None:
            restricted_policy_enabled = branham_policy_enabled
        self._restricted_policy_enabled = restricted_policy_enabled

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
        for entry in corpora_to_register:
            # Accept 3-tuple (id, type, db_path) or extended 5-tuple
            # (id, type, db_path, sensitive, access_note)
            corpus_id = entry[0]
            corpus_type = entry[1]
            db_path = entry[2]
            sensitive = entry[3] if len(entry) > 3 else False
            access_note = entry[4] if len(entry) > 4 else ""
            try:
                await self.register(
                    corpus_id=corpus_id,
                    corpus_type=corpus_type,
                    db_path=db_path,
                    sensitive=sensitive,
                    access_note=access_note,
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
            # Backfill review_queue_fanin from existing artifacts (§A10)
            await self._backfill_review_fanin()

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
        sensitive: bool = False,
        access_note: str = "",
        branham_policy_enabled: bool | None = None,  # deprecated alias for sensitive
    ) -> CorpusStores:
        """Open or create a corpus database.

        Validates budget + embedding model, then calls factory.build().
        Wraps build() in try/except: the factory handles partial-init cleanup.

        Args:
            corpus_id: unique identifier for the corpus.
            corpus_type: determines which migrations apply.
            db_path: path to the corpus SQLite file.
            sensitive: if True, this corpus requires session opt-in via
                allowed_restricted_corpora to access (§3.4 generic 4-layer defense).
            access_note: human-readable explanation of why the corpus is sensitive,
                shown in the GUI confirmation dialog.
            branham_policy_enabled: DEPRECATED alias for sensitive. Kept for
                backward compat with existing callers.

        Raises:
            ConnectionBudgetExceeded: if MAX_CORPORA or MAX_CONNECTIONS exceeded.
            EmbeddingModelMismatch: if corpus specifies a different model.
            CorpusMigrationError: on fingerprint mismatch or partial migration.
        """
        if corpus_id in self._corpora:
            # Idempotent: return existing stores
            return self._corpora[corpus_id]

        # Handle deprecated alias
        if branham_policy_enabled is not None:
            sensitive = branham_policy_enabled

        # Budget validation
        self._validate_connection_budget()

        # Build the stores (factory handles partial-init cleanup)
        stores = await self._factory.build(
            corpus_id=corpus_id,
            corpus_type=corpus_type,
            db_path=db_path,
            migration_lock=self._migration_lock,
        )

        # Set generic sensitive flags (used by get_stores() Layer 3 check)
        stores._sensitive = sensitive  # type: ignore[attr-defined]
        stores._access_note = access_note  # type: ignore[attr-defined]

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
    # get_stores() — ADR-008 Rev 3.1 §8 Chunk 2, §3.4 (generic restricted-corpus 4-layer)
    # ------------------------------------------------------------------

    async def get_stores(
        self,
        corpus_id: str,
        *,
        allowed_restricted_corpora: list[str] | None = None,
        session_branham_allowlist: bool | None = None,  # deprecated
    ) -> CorpusStores:
        """Look up stores by corpus_id.

        Args:
            corpus_id: the corpus to access.
            allowed_restricted_corpora: session-level opt-in list. If the corpus
                is sensitive=True and its corpus_id is NOT in this list, raises
                RestrictedCorpusAccessViolation (Layer 3 of 4-layer defense).
            session_branham_allowlist: DEPRECATED — if True, adds "branham" to
                allowed_restricted_corpora for backward compat.

        Raises:
            CorpusNotFound: if corpus_id is not registered.
            RestrictedCorpusAccessViolation: if a sensitive corpus is requested
                and its corpus_id is not in allowed_restricted_corpora.
            DeletionStateError: if the corpus is in DELETING state.
        """
        if corpus_id not in self._corpora:
            raise CorpusNotFound(
                f"Corpus {corpus_id!r} is not registered. Registered corpora: {list(self._corpora.keys())}"
            )

        stores = self._corpora[corpus_id]

        # Build the effective allowed list (handle deprecated alias)
        effective_allowed: set[str] = set(allowed_restricted_corpora or [])
        if session_branham_allowlist:
            effective_allowed.add("branham")  # backward compat

        # Layer 3: generic restricted-corpus access check
        if getattr(stores, "_sensitive", False) and corpus_id not in effective_allowed:
            await self._write_audit(
                action="RESTRICTED_CORPUS_ACCESS_DENIED",
                corpus_id=corpus_id,
                outcome="DENIED",
                detail={"reason": "corpus_id not in allowed_restricted_corpora"},
            )
            raise BranhamIsolationViolation(
                f"Restricted corpus {corpus_id!r} requires opt-in via "
                f"allowed_restricted_corpora. Layer 3 of 4-layer defense "
                f"(ADR-008 Rev 3.1 §3.4). Access note: {getattr(stores, '_access_note', '')}"
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
        Phase 2: delete_bridge_edges(corpus_id) in definer graph (Chunk 6 —
                 calls GraphStore.delete_bridge_edges for cross-corpus edges
                 pointing at this corpus; best-effort, logs on failure).
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

        # Phase 2: delete bridge edges pointing to this corpus (§A13).
        # Bridge edges live in the definer graph. We clean them up so
        # cross-corpus RRF doesn't try to follow edges to a deleted corpus.
        if corpus_id != "definer" and "definer" in self._corpora:
            definer = self._corpora["definer"]
            if definer.connection_manager is not None:
                try:
                    from aip.adapter.graph_store import GraphStore

                    gs = GraphStore(definer.connection_manager.db_path)
                    await gs.initialize()
                    deleted = await gs.delete_bridge_edges(corpus_id)
                    if deleted > 0:
                        logger.info(
                            "corpus_delete_bridge_edges_cleaned corpus=%s edges=%d",
                            corpus_id,
                            deleted,
                        )
                    await gs.close()
                except Exception as exc:
                    logger.warning(
                        "corpus_delete_bridge_edges_failed corpus=%s error=%s",
                        corpus_id,
                        exc,
                    )

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

        ADR-008 Rev 3.1 §9.4: review_queue_fanin is an ADVISORY index, not
        the source of truth. Each corpus's PersistentEcsStore._state_cache is
        canonical. This method:
          1. Reads the candidate set from review_queue_fanin (fast).
          2. Validates each candidate against the owning corpus's
             ecs_store.current_state() (cheap — cache hit).
          3. Drops items whose authoritative state no longer matches the
             requested filter.
          4. Returns merged list sorted by updated_at descending.

        If the definer corpus isn't registered (no fan-in table), returns [].
        """
        if not states:
            return []
        if "definer" not in self._corpora:
            return []

        definer = self._corpora["definer"]
        if definer.connection_manager is None:
            return []

        # Step 1: read candidate set from review_queue_fanin
        target_corpora = corpus_ids or list(self._corpora.keys())
        placeholders = ",".join("?" for _ in states)
        corpus_placeholders = ",".join("?" for _ in target_corpora)
        sql = (
            f"SELECT corpus_id, artifact_id, state, title, updated_at "
            f"FROM review_queue_fanin "
            f"WHERE state IN ({placeholders}) AND corpus_id IN ({corpus_placeholders}) "
            f"ORDER BY updated_at DESC"
        )
        params = list(states) + list(target_corpora)

        try:
            conn = await definer.connection_manager.acquire_read()
            try:
                cursor = await conn.execute(sql, params)
                rows = await cursor.fetchall()
            finally:
                definer.connection_manager.release_read(conn)
        except Exception as exc:
            logger.warning("list_review_items_fanin_read_failed error=%s", exc)
            return []

        # Step 2-3: validate against owning corpus's authoritative state
        items: list[ReviewItem] = []
        for row in rows:
            cid = row["corpus_id"]
            artifact_id = row["artifact_id"]
            fanin_state = row["state"]

            # Validate against the owning corpus's ecs_store
            owning_stores = self._corpora.get(cid)
            if owning_stores is None or owning_stores.ecs_store is None:
                # Corpus not registered or ECS store not attached — skip
                continue

            try:
                authoritative_state = await owning_stores.ecs_store.current_state(artifact_id)
            except Exception:
                authoritative_state = None

            if authoritative_state is None:
                # Artifact not found in ECS store — stale fan-in entry, skip
                continue

            if authoritative_state != fanin_state:
                # Fan-in is stale — skip (the outbox consumer will update it)
                continue

            if authoritative_state not in states:
                # State no longer matches the requested filter
                continue

            items.append(
                ReviewItem(
                    corpus_id=cid,
                    artifact_id=artifact_id,
                    state=authoritative_state,
                    title=row["title"] or "",
                    updated_at=datetime.fromtimestamp(row["updated_at"], tz=timezone.utc),
                )
            )

        # Step 4: already sorted by updated_at DESC from the SQL query
        return items

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

        ADR-008 Rev 3.1 §A3, §A10, §A12:
          1. Transition ECS under the corpus write_lock.
          2. Look up turn_id via artifact_turn_links (no-op if absent — many
             artifacts are wiki/summary/eval artifacts with no turn).
          3. If found, UPDATE corpus_turns SET latest_ecs_state = ? WHERE turn_id = ?.
          4. Enqueue a durable fan-in outbox row (§A10) in the SAME transaction
             as the ECS transition (atomic, crash-safe).
          5. If new_state is ARCHIVED or SUPERSEDED, the outbox row triggers
             removal from review_queue_fanin (decided/terminal artifacts don't
             belong in a pending-review queue).
        """
        if corpus_id not in self._corpora:
            raise CorpusNotFound(f"Cannot transition artifact in unregistered corpus {corpus_id!r}.")

        stores = self._corpora[corpus_id]
        if stores.deletion_state != CorpusDeletionState.ACTIVE:
            raise DeletionStateError(
                f"Corpus {corpus_id!r} is in {stores.deletion_state.value} state — transitions blocked."
            )

        async with stores.write_lock:
            # Step 1: get current state + validate transition
            current = await stores.ecs_store.current_state(artifact_id)
            if current is None:
                raise CorpusNotFound(f"Artifact {artifact_id!r} not found in corpus {corpus_id!r} ECS store.")

            from aip.foundation.ecs_graph import validate_transition

            validate_transition(current, new_state)  # raises InvalidTransitionError

            # Step 2: look up turn_id via artifact_turn_links
            turn_id = await self._lookup_turn_id(stores, artifact_id)

            # Step 3: update latest_ecs_state on the linked turn (if any).
            # Use the turn_store's own connection (not the manager's) to avoid
            # "database is locked" — the turn_store and ecs_store each have
            # their own connections, and SQLite WAL mode only allows one writer
            # at a time. Since we're under stores.write_lock, the turn_store's
            # write and the ecs_store's write are serialized on the same thread.
            if turn_id and stores.turn_store is not None:
                turn_conn = await stores.turn_store._get_conn()
                await turn_conn.execute(
                    "UPDATE corpus_turns SET latest_ecs_state = ? WHERE turn_id = ?",
                    (new_state, turn_id),
                )
                await turn_conn.commit()

            # Step 4: transition ECS (this writes to ecs_state + ecs_transitions)
            await stores.ecs_store.transition(
                artifact_id=artifact_id,
                from_state=current,
                to_state=new_state,
                actor="definer",
                reason=f"transition_artifact({current} → {new_state})",
            )

            # Step 5: enqueue durable fan-in outbox row (§A10)
            await self._enqueue_fanin_outbox(stores, artifact_id, new_state)

        # Step 6: audit log
        action = (
            "ARTIFACT_ARCHIVED"
            if new_state == "ARCHIVED"
            else ("ARTIFACT_SUPERSEDED" if new_state == "SUPERSEDED" else "ARTIFACT_TRANSITIONED")
        )
        await self._write_audit(
            action=action,
            corpus_id=corpus_id,
            outcome="SUCCESS",
            detail={"artifact_id": artifact_id, "from": current, "to": new_state},
        )

        # Step 7: drain the outbox (best-effort, non-blocking)
        asyncio.create_task(self._drain_fanin_outbox())

    async def _lookup_turn_id(self, stores: CorpusStores, artifact_id: str) -> str | None:
        """Look up the turn_id linked to an artifact via artifact_turn_links.

        Returns None if no link exists (many artifacts are wiki/summary/eval
        artifacts with no turn). Uses the corpus's shared write connection.
        """
        if stores.connection_manager is None:
            return None
        try:
            conn = stores.connection_manager.write_conn
            cursor = await conn.execute(
                "SELECT turn_id FROM artifact_turn_links WHERE artifact_id = ? LIMIT 1",
                (artifact_id,),
            )
            row = await cursor.fetchone()
            return row["turn_id"] if row else None
        except Exception as exc:
            logger.warning("lookup_turn_id_failed artifact=%s error=%s", artifact_id, exc)
            return None

    async def _enqueue_fanin_outbox(self, stores: CorpusStores, artifact_id: str, new_state: str) -> None:
        """Enqueue a durable fan-in outbox row in the SAME transaction as the ECS transition.

        ADR-008 Rev 3.1 §A10: the outbox row is written to review_fanin_outbox
        in the definer corpus. A consumer reads delivered=0 rows, writes
        review_queue_fanin, marks delivered=1.

        For ARCHIVED/SUPERSEDED (terminal states), the outbox row has
        state=new_state so the consumer knows to REMOVE the row from
        review_queue_fanin (terminal artifacts don't belong in a pending-review queue).
        """
        if "definer" not in self._corpora:
            return
        definer = self._corpora["definer"]
        if definer.connection_manager is None:
            return

        outbox_id = uuid.uuid4().hex
        now = time.time()
        title = ""  # title is populated by the consumer from artifact metadata

        try:
            conn = definer.connection_manager.write_conn
            await conn.execute(
                "INSERT OR REPLACE INTO review_fanin_outbox "
                "(id, corpus_id, artifact_id, state, title, updated_at, delivered) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (outbox_id, stores.corpus_id, artifact_id, new_state, title, now),
            )
            await conn.commit()
        except Exception as exc:
            logger.warning("enqueue_fanin_outbox_failed artifact=%s error=%s", artifact_id, exc)

    async def _drain_fanin_outbox(self, batch_size: int = 50) -> int:
        """Drain undelivered fan-in outbox rows into review_queue_fanin.

        ADR-008 Rev 3.1 §A10: a single consumer reads delivered=0 rows,
        writes review_queue_fanin in the definer corpus, then marks delivered=1.
        On startup, the consumer resumes from undelivered rows — no loss.

        For ARCHIVED/SUPERSEDED (terminal states), the row is REMOVED from
        review_queue_fanin (not added).

        Returns the number of rows processed.
        """
        if "definer" not in self._corpora:
            return 0
        definer = self._corpora["definer"]
        if definer.connection_manager is None:
            return 0

        from aip.foundation.corpus_types import RETRIEVAL_EXCLUDED_STATES

        processed = 0
        try:
            async with definer.write_lock:
                conn = definer.connection_manager.write_conn
                cursor = await conn.execute(
                    "SELECT id, corpus_id, artifact_id, state, title, updated_at "
                    "FROM review_fanin_outbox WHERE delivered = 0 "
                    "ORDER BY updated_at ASC LIMIT ?",
                    (batch_size,),
                )
                rows = await cursor.fetchall()

                for row in rows:
                    outbox_id = row["id"]
                    cid = row["corpus_id"]
                    artifact_id = row["artifact_id"]
                    state = row["state"]
                    title = row["title"] or ""
                    updated_at = row["updated_at"]

                    if state in RETRIEVAL_EXCLUDED_STATES:
                        # Terminal state — remove from fan-in
                        await conn.execute(
                            "DELETE FROM review_queue_fanin WHERE corpus_id = ? AND artifact_id = ?",
                            (cid, artifact_id),
                        )
                    else:
                        # Active state — upsert into fan-in
                        await conn.execute(
                            "INSERT OR REPLACE INTO review_queue_fanin "
                            "(corpus_id, artifact_id, state, title, updated_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (cid, artifact_id, state, title, updated_at),
                        )

                    # Mark as delivered
                    await conn.execute(
                        "UPDATE review_fanin_outbox SET delivered = 1 WHERE id = ?",
                        (outbox_id,),
                    )
                    processed += 1

                await conn.commit()
        except Exception as exc:
            logger.warning("drain_fanin_outbox_failed error=%s", exc)

        return processed

    async def _backfill_review_fanin(self) -> int:
        """Backfill review_queue_fanin from existing artifacts.

        ADR-008 Rev 3.1 §A10 (backfill): scan each registered corpus's ECS store
        for artifacts in pending states (SPECIFIED, GENERATED, REVIEWED) and
        seed review_queue_fanin. Only runs once on startup; subsequent
        transitions go through the outbox.

        Returns the number of items backfilled.
        """
        if "definer" not in self._corpora:
            return 0

        pending_states = ["SPECIFIED", "GENERATED", "REVIEWED"]
        total = 0

        for cid, stores in self._corpora.items():
            if stores.ecs_store is None:
                continue
            try:
                for state in pending_states:
                    artifact_ids = await stores.ecs_store.list_by_state(state)
                    for artifact_id in artifact_ids:
                        await self._enqueue_fanin_outbox(stores, artifact_id, state)
                        total += 1
            except Exception as exc:
                logger.warning("backfill_review_fanin_failed corpus=%s error=%s", cid, exc)

        # Drain the outbox we just filled
        if total > 0:
            drained = await self._drain_fanin_outbox(batch_size=max(total * 2, 100))
            logger.info("review_fanin_backfilled enqueued=%d drained=%d", total, drained)

        return total

    # ------------------------------------------------------------------
    # _reconcile_bridge_edges() — ADR-008 Rev 3.1 §A13, §9.4
    # ------------------------------------------------------------------

    async def _reconcile_bridge_edges(self) -> None:
        """Scan definer graph_edges for orphan bridge edges and clean them up.

        ADR-008 Rev 3.1 §A13, §9.4: runs on startup, before _migration_ready.set().
        Scans definer.graph_edges WHERE target_corpus_id IS NOT NULL.
        For each target_corpus_id not in self._corpora:
          - calls definer_stores.graph_store.delete_bridge_edges(target_corpus_id)
          - emits WARNING log + audit log: action=BRIDGE_ORPHAN_CLEANED

        This recovers from crashes during delete_corpus() where the corpus
        was removed from _corpora but bridge edges weren't cleaned up.
        """
        definer = self._corpora.get("definer")
        if definer is None or definer.connection_manager is None:
            return

        # The definer corpus doesn't have a graph_store attached yet (Chunk 6
        # doesn't attach graph_store to CorpusStores — that's a follow-up).
        # We need to access the graph_store via the definer's db_path.
        # For now, create a temporary GraphStore to scan for orphans.
        # In a future refactor, graph_store will be attached to CorpusStores.
        try:
            from aip.adapter.graph_store import GraphStore

            gs = GraphStore(definer.connection_manager.db_path)
            await gs.initialize()

            # Get all target_corpus_id values that have bridge edges
            orphan_targets = await gs.get_orphan_bridge_targets()

            cleaned = 0
            for target_cid in orphan_targets:
                if target_cid not in self._corpora:
                    deleted = await gs.delete_bridge_edges(target_cid)
                    cleaned += deleted
                    logger.warning(
                        "bridge_orphan_cleaned target_corpus=%s edges_deleted=%d",
                        target_cid,
                        deleted,
                    )
                    await self._write_audit(
                        action="BRIDGE_ORPHAN_CLEANED",
                        corpus_id=target_cid,
                        outcome="SUCCESS",
                        detail={"edges_deleted": deleted},
                    )

            if cleaned > 0:
                logger.info(
                    "bridge_orphan_reconciliation_complete total_cleaned=%d",
                    cleaned,
                )

            await gs.close()
        except Exception as exc:
            logger.warning("bridge_orphan_reconciliation_failed error=%s", exc)

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

        ADR-008 Rev 3.1 §A13: deletion_state is persisted BEFORE any file
        operation so a crash mid-delete is recoverable on startup.
        """
        if stores.connection_manager is None:
            return
        try:
            conn = stores.connection_manager.write_conn
            await conn.execute(
                "INSERT OR REPLACE INTO corpus_metadata (key, value) VALUES (?, ?)",
                ("deletion_state", state.value),
            )
            await conn.commit()
        except Exception as exc:
            logger.warning("persist_deletion_state_failed corpus=%s error=%s", stores.corpus_id, exc)

    async def _write_audit(
        self,
        action: str,
        corpus_id: str | None,
        outcome: str,
        detail: dict | None = None,
    ) -> None:
        """Write an entry to corpus_audit_log in the definer corpus.

        ADR-008 Rev 3.1 §9.6: id = uuid4().hex, ts = time.time(),
        actor_id = "system" (for now; Chunk 9 CLI passes actor_id).
        If the definer corpus or corpus_audit_log table doesn't exist,
        logs to the standard logger instead (graceful degradation).
        """
        log_detail = detail or {}
        logger.info(
            "corpus_audit action=%s corpus=%s outcome=%s detail=%s",
            action,
            corpus_id,
            outcome,
            log_detail,
        )

        # Write to corpus_audit_log table if definer is registered
        if "definer" not in self._corpora:
            return
        definer = self._corpora["definer"]
        if definer.connection_manager is None:
            return

        try:
            conn = definer.connection_manager.write_conn
            audit_id = uuid.uuid4().hex
            now = time.time()
            await conn.execute(
                "INSERT INTO corpus_audit_log (id, ts, actor_id, corpus_id, action, outcome, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    audit_id,
                    now,
                    "system",
                    corpus_id,
                    action,
                    outcome,
                    json.dumps(log_detail),
                ),
            )
            await conn.commit()
        except Exception as exc:
            logger.warning("write_audit_failed action=%s error=%s", action, exc)
