"""Corpus-layer exceptions — ADR-008 Multi-Corpus Architecture.

Pure foundation layer: exception classes only. No I/O, no imports from
orchestration or adapter.

All corpus-layer exceptions descend from CorpusError so callers can catch
the entire family with a single except clause if needed.
"""

from __future__ import annotations


class CorpusError(Exception):
    """Base class for all corpus-layer exceptions."""


class EmbeddingModelMismatch(CorpusError):
    """Raised when a corpus specifies a different embedding model than the registry.

    ADR-008 Rev 3.1 §3.3: single embedding model is enforced at registry level.
    """


class CorpusNotFound(CorpusError):
    """Raised when get_stores() is called with an unregistered corpus_id."""


class ConnectionBudgetExceeded(CorpusError):
    """Raised when registering a corpus would exceed MAX_CONNECTIONS.

    ADR-008 Rev 3.1 §9.3: budget formula accounts for both pre-existing
    non-corpus stores and per-corpus shared connections.
    """


class RestrictedCorpusAccessViolation(CorpusError):
    """Raised when a sensitive corpus is queried without session opt-in.

    ADR-008 Rev 3.1 §3.4: 4-layer defense. This exception is the Layer 3
    enforcement point — CorpusRegistry.get_stores() raises it when a
    corpus marked sensitive=True is requested and the corpus_id is not
    in the session's allowed_restricted_corpora set.

    This is the GENERIC version — any corpus can be sensitive, not just
    Branham. BranhamIsolationViolation is kept as an alias for backward
    compatibility with the 1000-query acceptance test.
    """


# Backward-compat alias — BranhamIsolationViolation is the old name.
# The 1000-query acceptance test catches this by name. New code should
# catch RestrictedCorpusAccessViolation instead.
BranhamIsolationViolation = RestrictedCorpusAccessViolation


class CorpusMigrationError(CorpusError):
    """Raised on schema fingerprint mismatch or partial migration failure.

    ADR-008 Rev 3.1 §A8: migrations run in a dedicated runner outside
    _create_tables. Fingerprint is sha256 of applied migration names in
    applied order, plus per-migration sql_checksum to detect changed
    migration bodies under the same name.
    """


class DeletionStateError(CorpusError):
    """Raised when a corpus in DELETING state is accessed for reads.

    ADR-008 Rev 3.1 §A13: two-phase delete sets DELETING (persisted to
    corpus_metadata) before any file operation. get_stores() checks
    deletion_state and raises this if DELETING.
    """


class EcsTransitionError(CorpusError):
    """Raised on an invalid ECS state transition through the corpus registry.

    This wraps foundation.ecs_graph.InvalidTransitionError for the corpus
    layer, so callers catching CorpusError also catch transition failures.
    """
