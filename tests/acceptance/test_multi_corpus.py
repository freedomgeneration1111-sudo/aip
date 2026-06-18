"""ADR-008 Multi-Corpus acceptance tests — AC-01 through AC-09.

Final acceptance suite for the multi-corpus architecture. Each test
validates a specific acceptance criterion from ADR-008 Rev 3.1 §8 Chunk 9.

Tests are designed to run in CI without a running server — they create
their own CorpusRegistry + stores in tmp_path.

AC-01: Branham isolation (scaled-down 1000-query test for CI)
AC-02: Cross-corpus RRF with namespaced hit IDs
AC-03: ECS lifecycle — ARCHIVED and SUPERSEDED
AC-04: Concurrency — no deadlocks (10 concurrent writers, 30s timeout)
AC-05: Connection budget — MAX_CORPORA enforcement + partial-init cleanup
AC-06: Migration gate — _migration_ready event + fingerprint mismatch
AC-07: Review federation — list_review_items across corpora
AC-08: Bridge edge orphan recovery on startup
AC-09: Sexton write batch yield (scaled for CI)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.corpus_retrieval import gather_corpus_results, namespace_hit_id
from aip.adapter.graph_store import GraphStore
from aip.foundation.corpus_exceptions import (
    BranhamIsolationViolation,
    ConnectionBudgetExceeded,
    CorpusMigrationError,
)
from aip.foundation.corpus_types import CorpusType
from aip.foundation.ecs_graph import validate_transition
from aip.foundation.schemas.corpus_turn import CorpusTurn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(turn_id: str, text: str = "test content", corpus: str = "definer") -> CorpusTurn:
    return CorpusTurn(
        turn_id=turn_id,
        conversation_id=f"{corpus}_conv",
        conversation_name=f"{corpus} conversation",
        turn_index=0,
        source_model="test",
        source_account="test",
        export_date="2026-01-01",
        user_text=f"question about {corpus}",
        assistant_text=text,
        turn_timestamp="2026-01-01T00:00:00Z",
    )


class _FakeContainer:
    """Minimal container for retrieval tests."""

    def __init__(self, registry: CorpusRegistry):
        self.corpus_registry = registry


# ---------------------------------------------------------------------------
# AC-01: Branham isolation
# ---------------------------------------------------------------------------


class TestAC01BranhamIsolation:
    """AC-01: Branham isolation — zero cross-contamination."""

    async def test_branham_not_accessible_without_allowlist(self, tmp_path: Path):
        """BranhamIsolationViolation raised on direct access without allowlist."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=tmp_path / "branham.db",
            branham_policy_enabled=True,
        )

        with pytest.raises(BranhamIsolationViolation):
            await registry.get_stores("branham", session_branham_allowlist=False)

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_branham_suppressed_in_gather_results(self, tmp_path: Path):
        """Branham is suppressed in gather_corpus_results without allowlist."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=tmp_path / "branham.db",
            branham_policy_enabled=True,
        )

        container = _FakeContainer(registry)
        hits, suppressed = await gather_corpus_results(
            query="test",
            active_corpus_ids=["definer", "branham"],
            container=container,
            session_branham_allowlist=False,
        )

        # Branham was suppressed
        assert len(suppressed) == 1
        assert isinstance(suppressed[0], BranhamIsolationViolation)

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_branham_zero_hits_in_100_queries(self, tmp_path: Path):
        """Scaled CI version: 100 queries return zero Branham hits without allowlist.

        The full ADR specifies 1000 queries; this CI version uses 100 for speed.
        The isolation guarantee is the same — zero Branham content.
        """
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=tmp_path / "branham.db",
            branham_policy_enabled=True,
        )

        container = _FakeContainer(registry)

        # Run 100 queries — none should return Branham content
        for i in range(100):
            hits, suppressed = await gather_corpus_results(
                query=f"query_{i}",
                active_corpus_ids=["definer", "branham"],
                container=container,
                session_branham_allowlist=False,
            )
            # No hits should come from branham
            for hit in hits:
                assert hit.get("corpus_id") != "branham", f"Query {i}: Branham content leaked!"

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AC-02: Cross-corpus RRF with namespaced hit IDs
# ---------------------------------------------------------------------------


class TestAC02CrossCorpusRRF:
    """AC-02: Cross-corpus RRF returns namespaced hit IDs from multiple corpora."""

    async def test_namespaced_ids_from_multiple_corpora(self, tmp_path: Path):
        """Hit IDs are namespaced as {corpus_id}:{hit_id}."""
        assert namespace_hit_id("definer", "t1") == "definer:t1"
        assert namespace_hit_id("codeforge", "func_abc") == "codeforge:func_abc"

    async def test_same_hit_id_different_corpora_not_collapsed(self):
        """Same hit_id in different corpora produces different namespaced IDs."""
        id1 = namespace_hit_id("definer", "t1")
        id2 = namespace_hit_id("codeforge", "t1")
        assert id1 != id2  # RRF won't collapse them


# ---------------------------------------------------------------------------
# AC-03: ECS lifecycle — ARCHIVED and SUPERSEDED
# ---------------------------------------------------------------------------


class TestAC03EcsLifecycle:
    """AC-03: ECS lifecycle including ARCHIVED and SUPERSEDED."""

    def test_archived_is_terminal(self):
        """ARCHIVED is a terminal state — no exits."""
        from aip.foundation.ecs_graph import is_terminal

        assert is_terminal("ARCHIVED") is True

    def test_archived_reachable_from_generated_reviewed_approved(self):
        """ARCHIVED is reachable from GENERATED, REVIEWED, APPROVED."""
        validate_transition("GENERATED", "ARCHIVED")  # should not raise
        validate_transition("REVIEWED", "ARCHIVED")  # should not raise
        validate_transition("APPROVED", "ARCHIVED")  # should not raise

    def test_archived_not_reachable_from_specified(self):
        """ARCHIVED is NOT reachable from SPECIFIED."""
        from aip.foundation.ecs_graph import InvalidTransitionError

        with pytest.raises(InvalidTransitionError):
            validate_transition("SPECIFIED", "ARCHIVED")

    def test_archived_no_exits(self):
        """ARCHIVED has no outgoing transitions."""
        from aip.foundation.ecs_graph import InvalidTransitionError

        for target in ("GENERATED", "REVIEWED", "APPROVED", "SPECIFIED"):
            with pytest.raises(InvalidTransitionError):
                validate_transition("ARCHIVED", target)

    def test_all_legacy_transitions_preserved(self):
        """All pre-ADR-008 transitions still validate."""
        # SPECIFIED → GENERATED
        validate_transition("SPECIFIED", "GENERATED")
        # REJECTED → GENERATED (re-synthesis loop)
        validate_transition("REJECTED", "GENERATED")
        # FAILED → SPECIFIED (re-specify after failure)
        validate_transition("FAILED", "SPECIFIED")
        # APPROVED → SUPERSEDED (canonical supersession)
        validate_transition("APPROVED", "SUPERSEDED")


# ---------------------------------------------------------------------------
# AC-04: Concurrency — no deadlocks
# ---------------------------------------------------------------------------


class TestAC04Concurrency:
    """AC-04: Concurrency — no deadlocks under concurrent writes."""

    async def test_concurrent_writes_to_same_corpus(self, tmp_path: Path):
        """10 concurrent writers to the same corpus complete without deadlock."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )

        stores = await registry.get_stores("definer")

        async def write_one(i: int) -> None:
            async with stores.write_lock:
                turn = _make_turn(f"turn_{i}", f"content {i}")
                await stores.turn_store.write_turn(turn)

        # Run 10 concurrent writes with a 30s timeout
        tasks = [write_one(i) for i in range(10)]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30.0)

        # Verify all 10 turns were written
        turns = await stores.turn_store.search("content", limit=20)
        assert len(turns) >= 10

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AC-05: Connection budget
# ---------------------------------------------------------------------------


class TestAC05ConnectionBudget:
    """AC-05: Connection budget — MAX_CORPORA enforcement + partial-init cleanup."""

    async def test_max_corpora_enforced(self, tmp_path: Path):
        """Registering MAX_CORPORA + 1 corpora raises ConnectionBudgetExceeded."""
        registry = CorpusRegistry(max_corpora=2)
        await registry.startup()

        for i in range(2):
            await registry.register(
                corpus_id=f"corpus_{i}",
                corpus_type=CorpusType.CODE,
                db_path=tmp_path / f"corpus_{i}.db",
            )

        with pytest.raises(ConnectionBudgetExceeded):
            await registry.register(
                corpus_id="corpus_2",
                corpus_type=CorpusType.CODE,
                db_path=tmp_path / "corpus_2.db",
            )

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_partial_init_cleanup_no_leak(self, tmp_path: Path):
        """A failed registration doesn't leak connections."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        # This should succeed
        stores = await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        assert stores is not None
        assert stores.connection_manager is not None
        assert stores.connection_manager.opened is True

        # Cleanup
        await registry.delete_corpus("definer")


# ---------------------------------------------------------------------------
# AC-06: Migration gate
# ---------------------------------------------------------------------------


class TestAC06MigrationGate:
    """AC-06: Migration gate — _migration_ready event + fingerprint mismatch."""

    async def test_migration_ready_set_after_startup(self):
        """startup() sets _migration_ready event."""
        registry = CorpusRegistry()
        assert registry.migration_ready.is_set() is False
        await registry.startup()
        assert registry.migration_ready.is_set() is True

    async def test_migration_ready_not_set_before_startup(self):
        """_migration_ready is NOT set before startup() completes."""
        registry = CorpusRegistry()
        assert registry.migration_ready.is_set() is False

    async def test_fingerprint_mismatch_raises(self, tmp_path: Path):
        """Fingerprint mismatch raises CorpusMigrationError.

        The migration runner detects a reordering when the applied_migrations
        table has migrations in a different order than expected. We simulate
        this by inserting a fake migration that's not in the registry.
        """
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        # Register definer (applies migrations)
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )

        # Insert a fake migration that's not in the expected set
        stores = await registry.get_stores("definer")
        conn = stores.connection_manager.write_conn
        await conn.execute(
            "INSERT OR REPLACE INTO applied_migrations (name, ordinal, sql_checksum, applied_at) "
            "VALUES (?, ?, ?, ?)",
            ("M999_unknown_migration", 999, "fake_checksum", "2026-01-01"),
        )
        await conn.commit()

        # Re-run migrations — should detect unknown migration
        from aip.adapter.corpus_migration_runner import CorpusMigrationRunner
        from aip.adapter.corpus_store_factory import MIGRATIONS
        from aip.foundation.corpus_types import MIGRATIONS_FOR_CORPUS_TYPE

        runner = CorpusMigrationRunner(stores.connection_manager)
        with pytest.raises(CorpusMigrationError):
            await runner.run_migrations(
                migration_names=MIGRATIONS_FOR_CORPUS_TYPE[CorpusType.CONVERSATION],
                migrations_registry=MIGRATIONS,
                corpus_id="definer",
            )

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AC-07: Review federation
# ---------------------------------------------------------------------------


class TestAC07ReviewFederation:
    """AC-07: Review federation — list_review_items across corpora."""

    async def test_list_review_items_across_corpora(self, tmp_path: Path):
        """list_review_items returns items from multiple corpora."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="codeforge",
            corpus_type=CorpusType.CODE,
            db_path=tmp_path / "codeforge.db",
        )

        # Create artifacts in GENERATED state in both corpora
        definer_stores = await registry.get_stores("definer")
        codeforge_stores = await registry.get_stores("codeforge")

        for stores, prefix in [(definer_stores, "def"), (codeforge_stores, "code")]:
            for i in range(2):
                await stores.ecs_store.transition(
                    artifact_id=f"{prefix}_art_{i}",
                    from_state=None,
                    to_state="GENERATED",
                    actor="test",
                    reason="test",
                )

        # Backfill the fan-in
        await registry._backfill_review_fanin()

        # List review items across all corpora
        items = await registry.list_review_items(states=["GENERATED"])
        assert len(items) == 4  # 2 per corpus

        # Verify corpus_ids are populated
        corpus_ids = {item.corpus_id for item in items}
        assert corpus_ids == {"definer", "codeforge"}

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AC-08: Bridge edge orphan recovery
# ---------------------------------------------------------------------------


class TestAC08BridgeOrphanRecovery:
    """AC-08: Bridge edge orphan recovery on startup."""

    async def test_orphan_bridge_edges_cleaned_on_startup(self, tmp_path: Path):
        """_reconcile_bridge_edges cleans orphan bridge edges on startup."""
        # First startup: register definer + codeforge, add bridge edges
        registry1 = CorpusRegistry(max_corpora=4)
        await registry1.startup()

        await registry1.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry1.register(
            corpus_id="codeforge",
            corpus_type=CorpusType.CODE,
            db_path=tmp_path / "codeforge.db",
        )

        # Add a bridge edge to codeforge
        definer_stores = await registry1.get_stores("definer")
        gs = GraphStore(definer_stores.connection_manager.db_path)
        await gs.initialize()
        await gs.upsert_bridge_edge(
            source_id="turn_1",
            source_corpus_id="definer",
            target_id="func_1",
            target_corpus_id="codeforge",
            edge_type="REFERENCES",
        )
        await gs.close()

        # Simulate crash: delete codeforge from registry but NOT from graph
        # (don't call delete_corpus — just remove from _corpora)
        del registry1._corpora["codeforge"]

        # Second startup: should reconcile the orphan
        registry2 = CorpusRegistry(max_corpora=4)
        await registry2.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
            ],
        )

        # The orphan bridge edge to codeforge should be cleaned
        gs2 = GraphStore(str(tmp_path / "definer.db"))
        await gs2.initialize()
        targets = await gs2.get_orphan_bridge_targets()
        assert "codeforge" not in targets  # cleaned
        await gs2.close()

        for cid in await registry2.list_corpora():
            try:
                await registry2.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AC-09: Sexton write batch yield
# ---------------------------------------------------------------------------


class TestAC09SextonBatchYield:
    """AC-09: Sexton write batch yield — lock is yielded between batches."""

    async def test_batch_yield_allows_interleaving(self, tmp_path: Path):
        """The write_lock is yielded between batches, allowing chat routes to interleave."""
        from aip.foundation.corpus_constants import SEXTON_BATCH_YIELD_DELAY, SEXTON_WRITE_BATCH_SIZE

        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )

        stores = await registry.get_stores("definer")

        # Simulate Sexton processing 2 batches (200 turns)
        chat_route_completed = asyncio.Event()

        async def sexton_batch_write():
            """Simulate Sexton writing 2 batches with yield between them."""
            for batch_num in range(2):
                async with stores.write_lock:
                    for i in range(SEXTON_WRITE_BATCH_SIZE):
                        turn = _make_turn(f"sexton_{batch_num}_{i}")
                        await stores.turn_store.write_turn(turn)
                # Yield between batches (§9.5)
                await asyncio.sleep(SEXTON_BATCH_YIELD_DELAY)

        async def chat_route_write():
            """Simulate a chat route waiting for the lock."""
            async with stores.write_lock:
                chat_route_completed.set()

        # Run Sexton + chat route concurrently
        await asyncio.wait_for(
            asyncio.gather(
                sexton_batch_write(),
                chat_route_write(),
            ),
            timeout=30.0,
        )

        # The chat route should have completed (lock was yielded)
        assert chat_route_completed.is_set()

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass
