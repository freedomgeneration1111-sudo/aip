"""CorpusRegistry Protocol — ADR-008 Multi-Corpus Architecture.

Pure foundation layer: Protocol interface + ReviewItem dataclass only.
The concrete CorpusRegistry implementation lives in the adapter layer
(adapter/corpus_registry.py). Orchestration consumes this Protocol
through dependency injection.

Contract (ADR-008 Rev 3.1 §5.4):
  - startup(): initialize all pre-configured corpora, run migrations,
    set _migration_ready so actors can begin writing.
  - register(): open or create a corpus database. Raises
    ConnectionBudgetExceeded, EmbeddingModelMismatch, CorpusMigrationError.
  - get_stores(): look up stores by corpus_id. Raises CorpusNotFound,
    BranhamIsolationViolation, DeletionStateError.
  - delete_corpus(): two-phase deletion (DELETING → cleanup → DELETED).
  - list_corpora(): return list of registered corpus_ids.
  - list_review_items(): fan out across corpus artifact_stores, merge
    by updated_at descending. Validates each candidate against the owning
    corpus's authoritative ECS state (§9.4 — advisory fan-in + validation).
  - transition_artifact(): transition ECS state under the corpus write_lock,
    update latest_ecs_state on the linked turn (if any), enqueue durable
    fan-in outbox row (§A10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from aip.foundation.corpus_types import CorpusType


@dataclass
class ReviewItem:
    """A single item in the cross-corpus review queue.

    Produced by CorpusRegistry.list_review_items(). The state field is a
    STRING (not enum) matching the foundation/ecs_graph.py string-based
    state machine — GENERATED, REVIEWED, APPROVED, ARCHIVED, SUPERSEDED, etc.

    list_review_items() validates each candidate against the owning corpus's
    authoritative current_state() before returning, so the state shown here
    is always current (ADR-008 Rev 3.1 §9.4).
    """

    corpus_id: str
    artifact_id: str
    state: str  # ECS state string — see foundation/ecs_graph.py
    title: str
    updated_at: datetime


@runtime_checkable
class CorpusRegistryProtocol(Protocol):
    """Primary store-access interface for the multi-corpus architecture.

    ADR-008 Rev 3.1 §3.1: CorpusRegistry is the primary container interface.
    Stores are reached via get_stores(corpus_id). The legacy singletons
    (container.corpus_turn_store, etc.) are removed in Chunk 3.

    Concrete implementation: adapter/corpus_registry.py::CorpusRegistry.
    """

    async def startup(self) -> None:
        """Initialize all pre-configured corpora, run migrations, set migration_ready.

        Steps (ADR-008 Rev 3.1 §8 Chunk 2):
          1. Measure actual non-corpus connection budget from pre-existing stores.
          2. Register the definer corpus first.
          3. Register any other pre-configured corpora.
          4. Run _reconcile_bridge_edges() to clean orphan edges from crashed deletes.
          5. Set _migration_ready so actors can begin writing.

        If the definer corpus fails to register, startup() raises and the
        app does not start (§A16 C-5).
        """
        ...

    async def register(
        self,
        corpus_id: str,
        corpus_type: CorpusType,
        db_path: Path,
        branham_policy_enabled: bool = False,
    ) -> Any:
        """Open or create a corpus database. Returns the CorpusStores bundle.

        Raises:
            ConnectionBudgetExceeded: if MAX_CORPORA or MAX_CONNECTIONS would be exceeded.
            EmbeddingModelMismatch: if corpus specifies a different embedding model.
            CorpusMigrationError: on schema fingerprint mismatch or partial migration.
        """
        ...

    async def get_stores(
        self,
        corpus_id: str,
        *,
        session_branham_allowlist: bool = False,
    ) -> Any:
        """Look up stores by corpus_id. Returns the CorpusStores bundle.

        Raises:
            CorpusNotFound: if corpus_id is not registered.
            BranhamIsolationViolation: if Branham corpus is requested without
                session_branham_allowlist=True (Layer 3 of 4-layer defense).
            DeletionStateError: if the corpus is in DELETING state.
        """
        ...

    async def delete_corpus(self, corpus_id: str) -> None:
        """Two-phase deletion — ADR-008 Rev 3.1 §A13.

        Phase 1: Set deletion_state=DELETING (persisted to corpus_metadata
                 before any file op, so a crash mid-delete is recoverable).
        Phase 2: delete_bridge_edges(corpus_id) in definer graph.
        Phase 3: signal per-corpus actor work to stop / drain the outbox.
        Phase 4: PRAGMA wal_checkpoint(TRUNCATE) on the corpus db.
        Phase 5: close the shared connection manager.
        Phase 6: rename all three files (.db, .db-wal, .db-shm) via
                 with_name(name + ".deleted").
        Phase 7: pop from _corpora.
        Phase 8: audit-log CORPUS_DELETED.
        """
        ...

    async def list_corpora(self) -> list[str]:
        """Return list of registered corpus_ids."""
        ...

    async def list_review_items(
        self,
        states: list[str],
        corpus_ids: list[str] | None = None,
    ) -> list[ReviewItem]:
        """Fan out across all (or specified) corpus artifact_stores.

        ADR-008 Rev 3.1 §9.4: reads the candidate set from review_queue_fanin
        (fast), validates each candidate against the owning corpus's
        authoritative current_state() (cheap, cache hit), drops items whose
        state no longer matches the requested filter. Returns merged list
        sorted by updated_at descending.
        """
        ...

    async def transition_artifact(
        self,
        corpus_id: str,
        artifact_id: str,
        new_state: str,
    ) -> None:
        """Transition artifact ECS state — ADR-008 Rev 3.1 §A3, §A10.

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
        ...
