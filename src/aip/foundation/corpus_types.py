"""Corpus type definitions — ADR-008 Multi-Corpus Architecture.

Pure foundation layer: enums and constants only. No I/O, no imports from
orchestration or adapter.

These types are consumed by:
  - adapter/corpus_registry.py (implementation)
  - adapter/corpus_stores.py (live connection bundle)
  - adapter/corpus_store_factory.py (construction)
  - orchestration (through Protocol injection)

Contracts (verified against ADR-008 Rev 3.1 §5.1, §5.2, §8 Chunk 1):
  - CorpusType: 4 corpus types (conversation, code, document, book)
  - CorpusDeletionState: 3 deletion phases (ACTIVE, DELETING, DELETED)
  - RETRIEVAL_EXCLUDED_STATES: ECS states hidden from default retrieval
  - MIGRATIONS_FOR_CORPUS_TYPE: which migrations apply to which corpus type
"""

from __future__ import annotations

from enum import Enum


class CorpusType(str, Enum):
    """The four corpus types — ADR-008 Rev 3.1 §3.2.

    String enum so values serialize cleanly to SQLite TEXT columns and
    JSON metadata without custom codecs.
    """

    CONVERSATION = "conversation"  # definer corpus — AI conversation turns
    CODE = "code"  # codeforge — AIP Brain Python codebase
    DOCUMENT = "document"  # branham — sensitive research documents
    BOOK = "book"  # sparkle_thirst — manuscript chapters


class CorpusDeletionState(str, Enum):
    """Two-phase deletion lifecycle — ADR-008 Rev 3.1 §5.2, A13.

    ACTIVE → DELETING → DELETED (persisted as tombstone).

    get_stores() raises DeletionStateError if the corpus is in DELETING
    state — this prevents reads during the delete critical section.
    DELETED is only recorded in the audit log (the db file is renamed
    to *.deleted and the in-memory entry is popped).
    """

    ACTIVE = "ACTIVE"
    DELETING = "DELETING"  # set atomically before edges are removed
    DELETED = "DELETED"  # set after all cleanup completes


# ---------------------------------------------------------------------------
# Retrieval filtering — ADR-008 Rev 3.1 §5.2, §6
# ---------------------------------------------------------------------------

# Turns whose latest ECS state is in this set are excluded from default
# retrieval. Override with include_archived=True on CorpusTurnStore.search()
# for history queries. These are STRING states (not Enum) to match the
# existing foundation/ecs_graph.py string-based state machine.
RETRIEVAL_EXCLUDED_STATES: frozenset[str] = frozenset({"ARCHIVED", "SUPERSEDED"})


# ---------------------------------------------------------------------------
# Migration registry — ADR-008 Rev 3.1 §9.1, Appendix B
# ---------------------------------------------------------------------------

# Which migrations apply to which corpus type. Migration names MUST begin
# with "M<3-digit>_" so lexicographic sort matches migration order.
# Fingerprint = sha256("|".join(names_in_applied_order)) — order-preserving,
# not sorted (ADR-008 Rev 3.1 §A8).
MIGRATIONS_FOR_CORPUS_TYPE: dict["CorpusType", list[str]] = {
    CorpusType.CONVERSATION: [
        "M001_add_revision_parent_id",
        "M002_add_target_corpus_id",  # definer only — bridge edges live here
        "M003_add_latest_ecs_state",
    ],
    CorpusType.CODE: [
        "M001_add_revision_parent_id",
        "M003_add_latest_ecs_state",
    ],
    CorpusType.DOCUMENT: [
        "M001_add_revision_parent_id",
        "M003_add_latest_ecs_state",
    ],
    CorpusType.BOOK: [
        "M001_add_revision_parent_id",
        "M003_add_latest_ecs_state",
    ],
}
