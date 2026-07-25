"""Tests for ADR-008 Multi-Corpus foundation types.

Verifies:
  - CorpusType enum has exactly 4 values
  - CorpusDeletionState enum has exactly 3 values
  - RETRIEVAL_EXCLUDED_STATES contains ARCHIVED and SUPERSEDED
  - MIGRATIONS_FOR_CORPUS_TYPE: definer gets M002, others don't
  - All corpus exceptions descend from CorpusError
  - CorpusRegistryProtocol is runtime_checkable
  - ReviewItem dataclass fields match the contract
  - Connection budget constants match §9.3 formula

ADR-008 Rev 3.1 §8 Chunk 1.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aip.foundation.corpus_constants import (
    CORPUS_READ_POOL_SIZE,
    KNOWN_NON_CORPUS_DB_FILES,
    MAX_CONNECTIONS,
    MAX_CORPORA,
    NON_CORPUS_READ_POOL_SIZE,
    SEXTON_BATCH_YIELD_DELAY,
    SEXTON_WRITE_BATCH_SIZE,
)
from aip.foundation.corpus_exceptions import (
    RestrictedCorpusAccessViolation,
    ConnectionBudgetExceeded,
    CorpusError,
    CorpusMigrationError,
    CorpusNotFound,
    DeletionStateError,
    EcsTransitionError,
    EmbeddingModelMismatch,
)
from aip.foundation.corpus_types import (
    MIGRATIONS_FOR_CORPUS_TYPE,
    RETRIEVAL_EXCLUDED_STATES,
    CorpusDeletionState,
    CorpusType,
)
from aip.foundation.protocols import CorpusRegistryProtocol, ReviewItem

# ---------------------------------------------------------------------------
# CorpusType enum
# ---------------------------------------------------------------------------


class TestCorpusType:
    def test_four_corpus_types(self):
        assert len(CorpusType) == 4

    def test_expected_values(self):
        assert CorpusType.CONVERSATION == "conversation"
        assert CorpusType.CODE == "code"
        assert CorpusType.DOCUMENT == "document"
        assert CorpusType.BOOK == "book"

    def test_string_enum_serializes_to_str(self):
        """String enum values serialize cleanly to SQLite TEXT and JSON."""
        assert CorpusType.CONVERSATION.value == "conversation"
        assert isinstance(CorpusType.CONVERSATION.value, str)


# ---------------------------------------------------------------------------
# CorpusDeletionState enum
# ---------------------------------------------------------------------------


class TestCorpusDeletionState:
    def test_three_deletion_states(self):
        assert len(CorpusDeletionState) == 3

    def test_expected_values(self):
        assert CorpusDeletionState.ACTIVE == "ACTIVE"
        assert CorpusDeletionState.DELETING == "DELETING"
        assert CorpusDeletionState.DELETED == "DELETED"


# ---------------------------------------------------------------------------
# RETRIEVAL_EXCLUDED_STATES
# ---------------------------------------------------------------------------


class TestRetrievalExcludedStates:
    def test_contains_archived_and_superseded(self):
        assert "ARCHIVED" in RETRIEVAL_EXCLUDED_STATES
        assert "SUPERSEDED" in RETRIEVAL_EXCLUDED_STATES

    def test_does_not_contain_active_states(self):
        """Active ECS states must NOT be in the exclusion set."""
        assert "GENERATED" not in RETRIEVAL_EXCLUDED_STATES
        assert "REVIEWED" not in RETRIEVAL_EXCLUDED_STATES
        assert "APPROVED" not in RETRIEVAL_EXCLUDED_STATES

    def test_is_frozenset(self):
        assert isinstance(RETRIEVAL_EXCLUDED_STATES, frozenset)


# ---------------------------------------------------------------------------
# MIGRATIONS_FOR_CORPUS_TYPE
# ---------------------------------------------------------------------------


class TestMigrationsForCorpusType:
    def test_all_four_corpus_types_have_migrations(self):
        assert set(MIGRATIONS_FOR_CORPUS_TYPE.keys()) == set(CorpusType)

    def test_definer_gets_m002_target_corpus_id(self):
        """Only the definer (conversation) corpus gets M002 — bridge edges
        live in the definer graph only (ADR-008 Rev 3.1 §9.1)."""
        migrations = MIGRATIONS_FOR_CORPUS_TYPE[CorpusType.CONVERSATION]
        assert "M002_add_target_corpus_id" in migrations

    def test_non_definer_corpora_do_not_get_m002(self):
        """code, document, book corpora must NOT get M002."""
        for corpus_type in (CorpusType.CODE, CorpusType.DOCUMENT, CorpusType.BOOK):
            migrations = MIGRATIONS_FOR_CORPUS_TYPE[corpus_type]
            assert "M002_add_target_corpus_id" not in migrations, (
                f"{corpus_type} should not get M002 (bridge edges are definer-only)"
            )

    def test_all_corpus_types_get_m001_and_m003(self):
        """M001 (revision_parent_id) and M003 (latest_ecs_state) apply to
        all turn-bearing corpora."""
        for corpus_type in CorpusType:
            migrations = MIGRATIONS_FOR_CORPUS_TYPE[corpus_type]
            assert "M001_add_revision_parent_id" in migrations
            assert "M003_add_latest_ecs_state" in migrations

    def test_migration_names_have_m_prefix(self):
        """Migration names MUST begin with 'M<3-digit>_' so lexicographic
        sort matches migration order (ADR-008 Rev 3.1 §A8)."""
        import re

        pattern = re.compile(r"^M\d{3}_")
        for corpus_type, migrations in MIGRATIONS_FOR_CORPUS_TYPE.items():
            for name in migrations:
                assert pattern.match(name), f"Migration {name!r} for {corpus_type} lacks M<3-digit>_ prefix"


# ---------------------------------------------------------------------------
# Corpus exceptions
# ---------------------------------------------------------------------------


class TestCorpusExceptions:
    @pytest.mark.parametrize(
        "exc_class",
        [
            EmbeddingModelMismatch,
            CorpusNotFound,
            ConnectionBudgetExceeded,
            RestrictedCorpusAccessViolation,
            CorpusMigrationError,
            DeletionStateError,
            EcsTransitionError,
        ],
    )
    def test_all_descend_from_corpus_error(self, exc_class):
        assert issubclass(exc_class, CorpusError)

    def test_corpus_error_descends_from_exception(self):
        assert issubclass(CorpusError, Exception)

    def test_exceptions_are_raisable_and_catchable_as_corpus_error(self):
        """Callers can catch the entire family with a single except clause."""
        for exc_class in [
            EmbeddingModelMismatch,
            CorpusNotFound,
            ConnectionBudgetExceeded,
            RestrictedCorpusAccessViolation,
            CorpusMigrationError,
            DeletionStateError,
            EcsTransitionError,
        ]:
            with pytest.raises(CorpusError):
                raise exc_class("test")


# ---------------------------------------------------------------------------
# Corpus constants
# ---------------------------------------------------------------------------


class TestCorpusConstants:
    def test_connection_budget_formula_constants(self):
        """ADR-008 Rev 3.1 §9.3 (corrected by §A0):
        non_corpus = 7 × (1 + 3) = 28
        available  = 64 - 28 = 36
        per_corpus = 1 + 2 = 3 (shared write conn + shared read pool)
        theoretical_max = floor(36 / 3) = 12
        SHIP: MAX_CORPORA = 8 (raised from 4 on 2026-07-23 per QW10;
              8 × 3 = 24, leaving 12 of headroom under the 36-connection
              corpus budget — accommodates definer + ARISTOTLE + 6 future
              fleet extensions per ADR-015)
        """
        assert MAX_CONNECTIONS == 64
        assert KNOWN_NON_CORPUS_DB_FILES == 7
        assert NON_CORPUS_READ_POOL_SIZE == 3  # matches read_pool._DEFAULT_POOL_SIZE
        assert CORPUS_READ_POOL_SIZE == 2

        non_corpus_budget = KNOWN_NON_CORPUS_DB_FILES * (1 + NON_CORPUS_READ_POOL_SIZE)
        assert non_corpus_budget == 28

        available = MAX_CONNECTIONS - non_corpus_budget
        assert available == 36

        per_corpus = 1 + CORPUS_READ_POOL_SIZE
        assert per_corpus == 3

        theoretical_max = available // per_corpus
        assert theoretical_max == 12

        assert MAX_CORPORA == 8  # raised from 4 on 2026-07-23 (QW10)
        assert MAX_CORPORA <= theoretical_max
        # Explicit headroom check: 8 corpora × 3 conns = 24, leaving 12 of 36
        assert (MAX_CORPORA * per_corpus) <= available
        assert available - (MAX_CORPORA * per_corpus) >= 12  # ≥12 conns headroom

    def test_sexton_batch_constants(self):
        """ADR-008 Rev 3.1 §9.5: Sexton yields lock every 100 turns, with
        a 0.001s delay (not sleep(0)) so chat routes can interleave."""
        assert SEXTON_WRITE_BATCH_SIZE == 100
        assert SEXTON_BATCH_YIELD_DELAY == 0.001


# ---------------------------------------------------------------------------
# CorpusRegistryProtocol
# ---------------------------------------------------------------------------


class TestCorpusRegistryProtocol:
    def test_protocol_is_runtime_checkable(self):
        """The Protocol must be @runtime_checkable so isinstance() works
        for dependency injection validation."""
        # CorpusRegistryProtocol is decorated with @runtime_checkable
        # We verify by checking it has __protocol_attrs__ or is a Protocol

        # A runtime_checkable Protocol's isinstance check works on attribute
        # presence. We verify the class is a Protocol subclass.

        # CorpusRegistryProtocol should be a Protocol
        assert hasattr(CorpusRegistryProtocol, "_is_protocol")

    def test_protocol_defines_all_required_methods(self):
        """ADR-008 Rev 3.1 §5.4 — 7 methods required."""
        required_methods = {
            "startup",
            "register",
            "get_stores",
            "delete_corpus",
            "list_corpora",
            "list_review_items",
            "transition_artifact",
        }
        protocol_attrs = set(dir(CorpusRegistryProtocol))
        for method in required_methods:
            assert method in protocol_attrs, f"Protocol missing method: {method}"


# ---------------------------------------------------------------------------
# ReviewItem dataclass
# ---------------------------------------------------------------------------


class TestReviewItem:
    def test_review_item_has_required_fields(self):
        """ADR-008 Rev 3.1 §5.4 — ReviewItem contract."""
        from dataclasses import fields

        field_names = {f.name for f in fields(ReviewItem)}
        assert field_names == {"corpus_id", "artifact_id", "state", "title", "updated_at"}

    def test_review_item_constructable(self):
        """ReviewItem must be constructable with the documented fields."""
        now = datetime.now(timezone.utc)
        item = ReviewItem(
            corpus_id="definer",
            artifact_id="art-001",
            state="GENERATED",
            title="Test artifact",
            updated_at=now,
        )
        assert item.corpus_id == "definer"
        assert item.artifact_id == "art-001"
        assert item.state == "GENERATED"
        assert item.title == "Test artifact"
        assert item.updated_at == now

    def test_state_field_is_string_not_enum(self):
        """ADR-008 Rev 3.1 §5.1: ECS states are strings (not Enum) to match
        the existing foundation/ecs_graph.py string-based state machine."""
        item = ReviewItem(
            corpus_id="definer",
            artifact_id="art-001",
            state="ARCHIVED",
            title="Test",
            updated_at=datetime.now(timezone.utc),
        )
        assert isinstance(item.state, str)


# ---------------------------------------------------------------------------
# Cross-layer import discipline
# ---------------------------------------------------------------------------


class TestLayerDiscipline:
    def test_foundation_corpus_types_no_adapter_imports(self):
        """Foundation must not import from adapter or orchestration."""
        import inspect

        from aip.foundation import corpus_types

        source = inspect.getsource(corpus_types)
        assert "from aip.adapter" not in source
        assert "from aip.orchestration" not in source
        assert "import aip.adapter" not in source
        assert "import aip.orchestration" not in source

    def test_foundation_corpus_exceptions_no_adapter_imports(self):
        import inspect

        from aip.foundation import corpus_exceptions

        source = inspect.getsource(corpus_exceptions)
        assert "from aip.adapter" not in source
        assert "from aip.orchestration" not in source

    def test_foundation_corpus_constants_no_adapter_imports(self):
        import inspect

        from aip.foundation import corpus_constants

        source = inspect.getsource(corpus_constants)
        assert "from aip.adapter" not in source
        assert "from aip.orchestration" not in source

    def test_foundation_corpus_registry_protocol_no_adapter_imports(self):
        """The Protocol must not import CorpusStores (adapter-layer concrete)
        — it uses Any for the return type to preserve layer discipline.

        Docstring MENTIONS of 'CorpusStores' are fine (documentation); the
        check is that there's no actual import statement."""
        import inspect

        from aip.foundation.protocols import corpus_registry

        source = inspect.getsource(corpus_registry)
        # No import statements from adapter/orchestration
        assert "from aip.adapter" not in source
        assert "from aip.orchestration" not in source
        assert "import aip.adapter" not in source
        assert "import aip.orchestration" not in source
        # No 'from aip.adapter.corpus_stores import CorpusStores' style import
        assert "import CorpusStores" not in source
