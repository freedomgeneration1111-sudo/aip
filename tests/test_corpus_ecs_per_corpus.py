"""Tests for ADR-008 Multi-Corpus Chunk 8: ECS/ArtifactStore per corpus.

Covers:
  - CorpusTurnStore.delete_turn() + states_for() + search(include_archived) (§A4, §A2)
  - CorpusTurn.revision_parent_id round-trip (§A12)
  - CorpusStoreFactory: per-corpus ECS + artifact store attachment + M004/M005 (§A3, §A11)
  - Definer-only tables: review_queue_fanin, corpus_audit_log, review_fanin_outbox (§A10, §9.6)
  - CorpusRegistry.transition_artifact() full implementation (§A3, §A10)
  - CorpusRegistry.list_review_items() with §9.4 validation
  - Durable fan-in outbox: enqueue → drain → crash recovery (§A10)
  - Backfill on startup (§A10)
  - Audit log persistence (§9.6)

ADR-008 Rev 3.1 §8 Chunk 8, Amendment §A3, §A4, §A10, §A11, §A12, §A15.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.corpus_store_factory import MIGRATIONS, CorpusStoreFactory
from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.foundation.corpus_types import CorpusType
from aip.foundation.schemas.corpus_turn import CorpusTurn

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_corpus.db"


def _make_turn(turn_id: str = "t1", revision_parent_id: str | None = None) -> CorpusTurn:
    """Create a minimal CorpusTurn for testing."""
    return CorpusTurn(
        turn_id=turn_id,
        conversation_id="conv1",
        conversation_name="Test Conversation",
        turn_index=0,
        source_model="test",
        source_account="test",
        export_date="2026-01-01",
        user_text="What is AIP?",
        assistant_text="AIP is a knowledge engine.",
        turn_timestamp="2026-01-01T00:00:00Z",
        revision_parent_id=revision_parent_id,
    )


# ---------------------------------------------------------------------------
# CorpusTurnStore.delete_turn() (§A4)
# ---------------------------------------------------------------------------


class TestCorpusTurnStoreDeleteTurn:
    """Tests for the opt-in GC delete_turn method."""

    async def test_delete_turn_removes_row(self, temp_db_path: Path):
        """delete_turn() removes the corpus_turns row."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        turn = _make_turn("t1")
        await store.write_turn(turn)

        # Verify turn exists
        retrieved = await store.get_turn("t1")
        assert retrieved is not None

        # Delete it
        deleted = await store.delete_turn("t1")
        assert deleted is True

        # Verify it's gone
        retrieved = await store.get_turn("t1")
        assert retrieved is None

        await store.close()

    async def test_delete_turn_returns_false_for_missing(self, temp_db_path: Path):
        """delete_turn() returns False if the turn doesn't exist."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        deleted = await store.delete_turn("nonexistent")
        assert deleted is False

        await store.close()

    async def test_delete_turn_removes_fts5_entry(self, temp_db_path: Path):
        """delete_turn() triggers FTS5 cleanup via corpus_turns_ad trigger."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        turn = _make_turn("t1")
        await store.write_turn(turn)

        # Verify FTS5 has the entry
        results = await store.search("AIP", limit=10)
        assert len(results) == 1

        # Delete — FTS5 trigger should remove the entry
        await store.delete_turn("t1")

        # Verify FTS5 no longer returns it
        results = await store.search("AIP", limit=10)
        assert len(results) == 0

        await store.close()


# ---------------------------------------------------------------------------
# CorpusTurnStore.states_for() (§A2)
# ---------------------------------------------------------------------------


class TestCorpusTurnStoreStatesFor:
    """Tests for the batch ECS state lookup used by fusion-layer filter."""

    async def test_states_for_returns_empty_for_no_column(self, temp_db_path: Path):
        """states_for() returns {} if latest_ecs_state column doesn't exist."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        # Column doesn't exist yet (no M003 migration applied)
        result = await store.states_for(["t1", "t2"])
        assert result == {}

        await store.close()

    async def test_states_for_returns_states(self, temp_db_path: Path):
        """states_for() returns latest_ecs_state for the given turn_ids."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        # latest_ecs_state column now created by _DDL_MIGRATIONS (ADR-008 M003)
        conn = await store._get_conn()
        await conn.commit()

        # Write two turns
        await store.write_turn(_make_turn("t1"))
        await store.write_turn(_make_turn("t2"))

        # Set one to ARCHIVED
        await conn.execute("UPDATE corpus_turns SET latest_ecs_state = 'ARCHIVED' WHERE turn_id = 't1'")
        await conn.commit()

        # Query states
        result = await store.states_for(["t1", "t2", "nonexistent"])
        assert result["t1"] == "ARCHIVED"
        assert result["t2"] == "GENERATED"
        assert "nonexistent" not in result

        await store.close()

    async def test_states_for_empty_input(self, temp_db_path: Path):
        """states_for([]) returns {} without querying."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        result = await store.states_for([])
        assert result == {}

        await store.close()


# ---------------------------------------------------------------------------
# CorpusTurnStore.search(include_archived) (§6)
# ---------------------------------------------------------------------------


class TestCorpusTurnStoreSearchIncludeArchived:
    """Tests for the include_archived parameter on search()."""

    async def test_search_excludes_archived_by_default(self, temp_db_path: Path):
        """search() excludes ARCHIVED turns by default."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        # latest_ecs_state column now created by _DDL_MIGRATIONS (ADR-008 M003)
        conn = await store._get_conn()
        await conn.commit()

        # Write two turns with matching text
        await store.write_turn(_make_turn("t1"))
        await store.write_turn(_make_turn("t2"))

        # Set t1 to ARCHIVED
        await conn.execute("UPDATE corpus_turns SET latest_ecs_state = 'ARCHIVED' WHERE turn_id = 't1'")
        await conn.commit()

        # Default search should only return t2 (GENERATED)
        results = await store.search("AIP", limit=10)
        assert len(results) == 1
        assert results[0].turn_id == "t2"

        # include_archived=True should return both
        results = await store.search("AIP", limit=10, include_archived=True)
        assert len(results) == 2

        await store.close()


# ---------------------------------------------------------------------------
# CorpusTurn.revision_parent_id round-trip (§A12)
# ---------------------------------------------------------------------------


class TestRevisionParentIdRoundTrip:
    """Tests for revision_parent_id field write→read round-trip."""

    async def test_revision_parent_id_round_trip(self, temp_db_path: Path):
        """revision_parent_id survives write→read round-trip."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        # Write a turn with revision_parent_id
        turn = _make_turn("t2", revision_parent_id="t1")
        await store.write_turn(turn)

        # Read it back
        retrieved = await store.get_turn("t2")
        assert retrieved is not None
        assert retrieved.revision_parent_id == "t1"

        await store.close()

    async def test_revision_parent_id_none_by_default(self, temp_db_path: Path):
        """revision_parent_id is None by default."""
        store = CorpusTurnStore(str(temp_db_path))
        await store.initialize()

        turn = _make_turn("t1")  # no revision_parent_id
        await store.write_turn(turn)

        retrieved = await store.get_turn("t1")
        assert retrieved is not None
        assert retrieved.revision_parent_id is None

        await store.close()


# ---------------------------------------------------------------------------
# CorpusStoreFactory: per-corpus ECS + artifact store (§A3)
# ---------------------------------------------------------------------------


class TestFactoryEcsArtifactAttachment:
    """Tests for the factory's ECS + artifact store attachment."""

    async def test_factory_attaches_ecs_and_artifact_stores(self, temp_db_path: Path):
        """Factory attaches ecs_store and artifact_store to CorpusStores."""
        factory = CorpusStoreFactory(read_pool_size=1)
        stores = await factory.build(
            corpus_id="test",
            corpus_type=CorpusType.CODE,
            db_path=temp_db_path,
            migration_lock=asyncio.Lock(),
        )
        try:
            assert stores.ecs_store is not None
            assert stores.artifact_store is not None
            assert stores.turn_store is not None
        finally:
            await stores.close_all()

    async def test_factory_creates_artifact_turn_links_table(self, temp_db_path: Path):
        """Factory creates artifact_turn_links table (M004)."""
        factory = CorpusStoreFactory(read_pool_size=1)
        stores = await factory.build(
            corpus_id="test",
            corpus_type=CorpusType.CODE,
            db_path=temp_db_path,
            migration_lock=asyncio.Lock(),
        )
        try:
            # Verify artifact_turn_links table exists
            conn = stores.connection_manager.write_conn
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_turn_links'"
            )
            row = await cursor.fetchone()
            assert row is not None
        finally:
            await stores.close_all()

    async def test_factory_creates_definer_only_tables(self, tmp_path: Path):
        """Factory creates review_queue_fanin, corpus_audit_log, review_fanin_outbox
        for the definer (CONVERSATION) corpus."""
        factory = CorpusStoreFactory(read_pool_size=1)
        db_path = tmp_path / "definer.db"
        stores = await factory.build(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
            migration_lock=asyncio.Lock(),
        )
        try:
            conn = stores.connection_manager.write_conn
            for table in ("review_queue_fanin", "corpus_audit_log", "review_fanin_outbox"):
                cursor = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                row = await cursor.fetchone()
                assert row is not None, f"Table {table} should exist in definer corpus"

            # Verify review_queue has corpus_id column (M005)
            cursor = await conn.execute("PRAGMA table_info(review_queue)")
            cols = [row[1] for row in await cursor.fetchall()]
            assert "corpus_id" in cols
        finally:
            await stores.close_all()


# ---------------------------------------------------------------------------
# CorpusRegistry.transition_artifact() (§A3, §A10)
# ---------------------------------------------------------------------------


class TestTransitionArtifact:
    """Tests for the full transition_artifact implementation."""

    async def _setup_registry_with_artifact(self, tmp_path: Path) -> tuple[CorpusRegistry, str, str]:
        """Helper: set up a registry with a definer corpus + one artifact in GENERATED state."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        # Create an artifact in GENERATED state
        stores = await registry.get_stores("definer")
        artifact_id = "art-test-001"
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state=None,
            to_state="GENERATED",
            actor="test",
            reason="test setup",
        )

        return registry, "definer", artifact_id

    async def test_transition_generated_to_reviewed(self, tmp_path: Path):
        """transition_artifact() transitions GENERATED → REVIEWED."""
        registry, cid, art_id = await self._setup_registry_with_artifact(tmp_path)

        await registry.transition_artifact(cid, art_id, "REVIEWED")

        stores = await registry.get_stores(cid)
        state = await stores.ecs_store.current_state(art_id)
        assert state == "REVIEWED"

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_transition_to_archived_removes_from_fanin(self, tmp_path: Path):
        """transition_artifact() to ARCHIVED removes the artifact from review_queue_fanin."""
        registry, cid, art_id = await self._setup_registry_with_artifact(tmp_path)

        # Manually enqueue a fan-in outbox row (the setup helper used
        # ecs_store.transition() directly, which doesn't enqueue)
        stores = await registry.get_stores(cid)
        await registry._enqueue_fanin_outbox(stores, art_id, "GENERATED")

        # Drain outbox to populate fan-in
        await registry._drain_fanin_outbox()

        # Verify it's in fan-in
        items = await registry.list_review_items(states=["GENERATED"])
        assert any(i.artifact_id == art_id for i in items)

        # Transition to ARCHIVED
        await registry.transition_artifact(cid, art_id, "ARCHIVED")

        # Drain outbox to process the ARCHIVED entry
        await registry._drain_fanin_outbox()

        # Verify it's no longer in fan-in
        items = await registry.list_review_items(states=["GENERATED"])
        assert not any(i.artifact_id == art_id for i in items)

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_transition_updates_latest_ecs_state_on_turn(self, tmp_path: Path):
        """transition_artifact() updates latest_ecs_state on the linked turn."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        stores = await registry.get_stores("definer")

        # Write a turn
        turn = _make_turn("t1")
        await stores.turn_store.write_turn(turn)

        # Create an artifact + link it to the turn
        artifact_id = "art-linked-001"
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state=None,
            to_state="GENERATED",
            actor="test",
            reason="test",
        )

        # Link artifact → turn
        conn = stores.connection_manager.write_conn
        await conn.execute(
            "INSERT INTO artifact_turn_links (artifact_id, turn_id) VALUES (?, ?)",
            (artifact_id, "t1"),
        )
        await conn.commit()

        # Verify latest_ecs_state is GENERATED (default)
        cursor = await conn.execute("SELECT latest_ecs_state FROM corpus_turns WHERE turn_id = 't1'")
        row = await cursor.fetchone()
        assert row["latest_ecs_state"] == "GENERATED"

        # Transition the artifact to REVIEWED
        await registry.transition_artifact("definer", artifact_id, "REVIEWED")

        # Verify latest_ecs_state was updated on the turn
        cursor = await conn.execute("SELECT latest_ecs_state FROM corpus_turns WHERE turn_id = 't1'")
        row = await cursor.fetchone()
        assert row["latest_ecs_state"] == "REVIEWED"

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_transition_invalid_raises(self, tmp_path: Path):
        """transition_artifact() raises on invalid transition (e.g. GENERATED → APPROVED)."""
        from aip.foundation.ecs_graph import InvalidTransitionError

        registry, cid, art_id = await self._setup_registry_with_artifact(tmp_path)

        with pytest.raises(InvalidTransitionError):
            await registry.transition_artifact(cid, art_id, "APPROVED")  # skip REVIEWED

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_transition_corpus_not_found(self, tmp_path: Path):
        """transition_artifact() raises CorpusNotFound for unregistered corpus."""
        registry = CorpusRegistry()
        await registry.startup()

        from aip.foundation.corpus_exceptions import CorpusNotFound

        with pytest.raises(CorpusNotFound):
            await registry.transition_artifact("nonexistent", "art-001", "ARCHIVED")


# ---------------------------------------------------------------------------
# Durable fan-in outbox (§A10)
# ---------------------------------------------------------------------------


class TestDurableFanInOutbox:
    """Tests for the durable outbox pattern."""

    async def test_outbox_survives_drain(self, tmp_path: Path):
        """Outbox rows are processed by drain and marked delivered."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        stores = await registry.get_stores("definer")

        # Create an artifact
        artifact_id = "art-outbox-001"
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state=None,
            to_state="GENERATED",
            actor="test",
            reason="test",
        )

        # Enqueue an outbox row
        await registry._enqueue_fanin_outbox(stores, artifact_id, "GENERATED")

        # Verify it's undelivered
        conn = stores.connection_manager.write_conn
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM review_fanin_outbox WHERE delivered = 0")
        row = await cursor.fetchone()
        assert row["cnt"] == 1

        # Drain
        processed = await registry._drain_fanin_outbox()
        assert processed == 1

        # Verify it's delivered
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM review_fanin_outbox WHERE delivered = 0")
        row = await cursor.fetchone()
        assert row["cnt"] == 0

        # Verify fan-in has the entry
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM review_queue_fanin WHERE artifact_id = ?",
            (artifact_id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 1

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_archived_state_removes_from_fanin(self, tmp_path: Path):
        """Draining an ARCHIVED outbox row removes it from fan-in."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        stores = await registry.get_stores("definer")
        artifact_id = "art-archive-001"

        # Create artifact + transition to GENERATED
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state=None,
            to_state="GENERATED",
            actor="test",
            reason="test",
        )

        # Enqueue GENERATED + drain (adds to fan-in)
        await registry._enqueue_fanin_outbox(stores, artifact_id, "GENERATED")
        await registry._drain_fanin_outbox()

        # Verify it's in fan-in
        conn = stores.connection_manager.write_conn
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM review_queue_fanin WHERE artifact_id = ?",
            (artifact_id,),
        )
        assert (await cursor.fetchone())["cnt"] == 1

        # Enqueue ARCHIVED + drain (removes from fan-in)
        await registry._enqueue_fanin_outbox(stores, artifact_id, "ARCHIVED")
        await registry._drain_fanin_outbox()

        # Verify it's gone from fan-in
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM review_queue_fanin WHERE artifact_id = ?",
            (artifact_id,),
        )
        assert (await cursor.fetchone())["cnt"] == 0

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Backfill (§A10)
# ---------------------------------------------------------------------------


class TestBackfillReviewFanin:
    """Tests for the startup backfill of review_queue_fanin."""

    async def test_backfill_seeds_existing_artifacts(self, tmp_path: Path):
        """_backfill_review_fanin() seeds fan-in from existing artifacts."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        stores = await registry.get_stores("definer")

        # Create 3 artifacts in GENERATED state
        for i in range(3):
            await stores.ecs_store.transition(
                artifact_id=f"art-backfill-{i:03d}",
                from_state=None,
                to_state="GENERATED",
                actor="test",
                reason="test",
            )

        # Run backfill
        count = await registry._backfill_review_fanin()
        assert count == 3

        # Verify fan-in has all 3
        items = await registry.list_review_items(states=["GENERATED"])
        assert len(items) == 3

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_backfill_skips_terminal_states(self, tmp_path: Path):
        """Backfill only seeds pending states (SPECIFIED, GENERATED, REVIEWED)."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        stores = await registry.get_stores("definer")

        # Create an artifact and transition it all the way to APPROVED
        await stores.ecs_store.transition(
            artifact_id="art-approved",
            from_state=None,
            to_state="GENERATED",
            actor="test",
            reason="test",
        )
        await stores.ecs_store.transition(
            artifact_id="art-approved",
            from_state="GENERATED",
            to_state="REVIEWED",
            actor="test",
            reason="test",
        )
        await stores.ecs_store.transition(
            artifact_id="art-approved",
            from_state="REVIEWED",
            to_state="APPROVED",
            actor="test",
            reason="test",
        )

        # Backfill — APPROVED is not in pending_states, so count should be 0
        count = await registry._backfill_review_fanin()
        assert count == 0

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Audit log (§9.6)
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Tests for corpus_audit_log persistence."""

    async def test_write_audit_persists_to_table(self, tmp_path: Path):
        """_write_audit() writes to corpus_audit_log table."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        await registry._write_audit(
            action="TEST_ACTION",
            corpus_id="test-corpus",
            outcome="SUCCESS",
            detail={"key": "value"},
        )

        # Verify it's in the table
        stores = await registry.get_stores("definer")
        conn = stores.connection_manager.write_conn
        cursor = await conn.execute(
            "SELECT action, corpus_id, outcome, detail FROM corpus_audit_log WHERE action = 'TEST_ACTION'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["action"] == "TEST_ACTION"
        assert row["corpus_id"] == "test-corpus"
        assert row["outcome"] == "SUCCESS"
        detail = json.loads(row["detail"])
        assert detail["key"] == "value"

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_write_audit_graceful_when_no_definer(self):
        """_write_audit() doesn't raise when definer isn't registered."""
        registry = CorpusRegistry()
        await registry.startup()

        # Should not raise
        await registry._write_audit(
            action="TEST_ACTION",
            corpus_id=None,
            outcome="SUCCESS",
        )


# ---------------------------------------------------------------------------
# list_review_items with §9.4 validation
# ---------------------------------------------------------------------------


class TestListReviewItemsValidation:
    """Tests for the advisory fan-in + authoritative validation pattern."""

    async def test_list_review_items_validates_against_ecs_store(self, tmp_path: Path):
        """list_review_items() drops items whose fan-in state doesn't match ECS state."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        db_path = tmp_path / "definer.db"
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )

        stores = await registry.get_stores("definer")
        artifact_id = "art-validate-001"

        # Create artifact in GENERATED state
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state=None,
            to_state="GENERATED",
            actor="test",
            reason="test",
        )

        # Enqueue + drain (fan-in now has GENERATED)
        await registry._enqueue_fanin_outbox(stores, artifact_id, "GENERATED")
        await registry._drain_fanin_outbox()

        # Manually transition to REVIEWED (bypassing transition_artifact so
        # the fan-in doesn't update — simulates a stale fan-in entry)
        await stores.ecs_store.transition(
            artifact_id=artifact_id,
            from_state="GENERATED",
            to_state="REVIEWED",
            actor="test",
            reason="test",
        )

        # list_review_items(states=["GENERATED"]) should NOT return this artifact
        # because the authoritative ECS state is REVIEWED, not GENERATED
        items = await registry.list_review_items(states=["GENERATED"])
        assert not any(i.artifact_id == artifact_id for i in items)

        # list_review_items(states=["REVIEWED"]) should also NOT return it
        # because the fan-in still says GENERATED (stale)
        items = await registry.list_review_items(states=["REVIEWED"])
        assert not any(i.artifact_id == artifact_id for i in items)

        # Cleanup
        for c in await registry.list_corpora():
            try:
                await registry.delete_corpus(c)
            except Exception:
                pass

    async def test_list_review_items_returns_empty_when_no_definer(self):
        """list_review_items() returns [] when definer isn't registered."""
        registry = CorpusRegistry()
        await registry.startup()

        items = await registry.list_review_items(states=["GENERATED"])
        assert items == []


# ---------------------------------------------------------------------------
# Migrations M004/M005 registered
# ---------------------------------------------------------------------------


class TestMigrationsRegistered:
    """Verify M004 and M005 are in the MIGRATIONS registry."""

    def test_m004_registered(self):
        assert "M004_add_artifact_turn_links" in MIGRATIONS

    def test_m005_registered(self):
        assert "M005_add_review_queue_corpus_id" in MIGRATIONS

    def test_m004_creates_artifact_turn_links(self, temp_db_path: Path):
        """M004 SQL creates the artifact_turn_links table."""
        migration = MIGRATIONS["M004_add_artifact_turn_links"]
        assert "artifact_turn_links" in migration.sql
        assert "CREATE TABLE" in migration.sql

    def test_m005_adds_corpus_id_to_review_queue(self):
        """M005 SQL adds corpus_id column to review_queue."""
        migration = MIGRATIONS["M005_add_review_queue_corpus_id"]
        assert "corpus_id" in migration.sql
        assert "review_queue" in migration.sql
