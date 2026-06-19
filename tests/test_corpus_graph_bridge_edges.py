"""Tests for ADR-008 Multi-Corpus Chunk 6: Graph bridge edges.

Covers:
  - GraphEdge.target_corpus_id field (§A7)
  - M002 migration: target_corpus_id column added to graph_edges (§A7)
  - Named column SELECTs (no SELECT * on graph_edges) (§A7)
  - _row_to_edge reads target_corpus_id (§A7)
  - upsert_bridge_edge / delete_bridge_edges / get_bridge_neighbors (§6)
  - get_orphan_bridge_targets (§A13)
  - _reconcile_bridge_edges on startup (§A13)
  - delete_corpus cleans up bridge edges (§A13)

ADR-008 Rev 3.1 §6, §A7, §A9, §A13.
"""

from __future__ import annotations

from pathlib import Path

from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.graph_store import GraphEdge, GraphStore
from aip.foundation.corpus_types import CorpusType

# ---------------------------------------------------------------------------
# GraphEdge.target_corpus_id field (§A7)
# ---------------------------------------------------------------------------


class TestGraphEdgeTargetCorpusId:
    """Tests for the target_corpus_id field on GraphEdge."""

    def test_default_is_none(self):
        """target_corpus_id defaults to None (intra-corpus edge)."""
        e = GraphEdge(id="e1", source_id="s", target_id="t", relationship_type="REFS")
        assert e.target_corpus_id is None

    def test_can_set_to_corpus_id(self):
        """target_corpus_id can be set to mark a bridge edge."""
        e = GraphEdge(
            id="e1",
            source_id="s",
            target_id="t",
            relationship_type="REFS",
            target_corpus_id="codeforge",
        )
        assert e.target_corpus_id == "codeforge"


# ---------------------------------------------------------------------------
# M002 migration: target_corpus_id column (§A7)
# ---------------------------------------------------------------------------


class TestM002Migration:
    """Tests for the M002 migration (target_corpus_id column)."""

    async def test_column_exists_after_initialize(self, tmp_path: Path):
        """graph_edges has target_corpus_id column after GraphStore.initialize()."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        conn = await store._get_conn()
        cursor = await conn.execute("PRAGMA table_info(graph_edges)")
        cols = [row[1] for row in await cursor.fetchall()]
        assert "target_corpus_id" in cols

        await store.close()

    async def test_migration_is_idempotent(self, tmp_path: Path):
        """Running initialize() twice doesn't error on duplicate column."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()
        await store.close()

        store2 = GraphStore(str(tmp_path / "test.db"))
        await store2.initialize()  # should not raise

        conn = await store2._get_conn()
        cursor = await conn.execute("PRAGMA table_info(graph_edges)")
        cols = [row[1] for row in await cursor.fetchall()]
        assert "target_corpus_id" in cols

        await store2.close()


# ---------------------------------------------------------------------------
# Named column SELECTs + _row_to_edge (§A7)
# ---------------------------------------------------------------------------


class TestNamedColumnSelects:
    """Tests that _row_to_edge correctly reads target_corpus_id."""

    async def test_row_to_edge_reads_target_corpus_id(self, tmp_path: Path):
        """_row_to_edge returns GraphEdge with target_corpus_id populated."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        # Insert a bridge edge directly
        conn = await store._get_conn()
        await conn.execute(
            "INSERT INTO graph_edges (id, source_id, target_id, relationship_type, "
            "bridge_tag, confidence, evidence_turn_ids_json, weight, created_at, target_corpus_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("e1", "s1", "t1", "REFS", None, 1.0, "[]", 1.0, "2026-01-01", "codeforge"),
        )
        await conn.commit()

        # Read it back via get_edges_for_node (uses named columns)
        edges = await store.get_edges_for_node("s1")
        assert len(edges) == 1
        assert edges[0].target_corpus_id == "codeforge"

        await store.close()

    async def test_row_to_edge_defaults_none_for_intra_corpus(self, tmp_path: Path):
        """_row_to_edge returns None for intra-corpus edges."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        conn = await store._get_conn()
        await conn.execute(
            "INSERT INTO graph_edges (id, source_id, target_id, relationship_type, "
            "bridge_tag, confidence, evidence_turn_ids_json, weight, created_at, target_corpus_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("e1", "s1", "t1", "REFS", None, 1.0, "[]", 1.0, "2026-01-01", None),
        )
        await conn.commit()

        edges = await store.get_edges_for_node("s1")
        assert len(edges) == 1
        assert edges[0].target_corpus_id is None

        await store.close()

    async def test_get_all_edges_reads_target_corpus_id(self, tmp_path: Path):
        """get_all_edges (which used SELECT *) now reads target_corpus_id."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        conn = await store._get_conn()
        await conn.execute(
            "INSERT INTO graph_edges (id, source_id, target_id, relationship_type, "
            "bridge_tag, confidence, evidence_turn_ids_json, weight, created_at, target_corpus_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("e1", "s1", "t1", "REFS", None, 1.0, "[]", 1.0, "2026-01-01", "branham"),
        )
        await conn.commit()

        edges = await store.get_all_edges()
        assert len(edges) == 1
        assert edges[0].target_corpus_id == "branham"

        await store.close()


# ---------------------------------------------------------------------------
# Bridge edge methods (§6)
# ---------------------------------------------------------------------------


class TestBridgeEdgeMethods:
    """Tests for upsert_bridge_edge, delete_bridge_edges, get_bridge_neighbors."""

    async def test_upsert_bridge_edge(self, tmp_path: Path):
        """upsert_bridge_edge inserts a bridge edge with target_corpus_id."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        edge_id = await store.upsert_bridge_edge(
            source_id="turn_abc",
            source_corpus_id="definer",
            target_id="func_xyz",
            target_corpus_id="codeforge",
            edge_type="REFERENCES",
        )
        assert "codeforge" in edge_id

        # Verify it was inserted with target_corpus_id
        edges = await store.get_bridge_neighbors("turn_abc")
        assert len(edges) == 1
        assert edges[0].target_corpus_id == "codeforge"
        assert edges[0].relationship_type == "REFERENCES"

        await store.close()

    async def test_upsert_bridge_edge_is_idempotent(self, tmp_path: Path):
        """Upserting the same bridge edge twice doesn't duplicate."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        await store.upsert_bridge_edge(
            source_id="s1",
            source_corpus_id="definer",
            target_id="t1",
            target_corpus_id="codeforge",
            edge_type="REFS",
        )
        await store.upsert_bridge_edge(
            source_id="s1",
            source_corpus_id="definer",
            target_id="t1",
            target_corpus_id="codeforge",
            edge_type="REFS",
        )

        edges = await store.get_bridge_neighbors("s1")
        assert len(edges) == 1  # not duplicated

        await store.close()

    async def test_delete_bridge_edges(self, tmp_path: Path):
        """delete_bridge_edges removes all bridge edges to a target corpus."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        # Insert 3 bridge edges to codeforge + 1 to branham
        for i in range(3):
            await store.upsert_bridge_edge(
                source_id=f"s{i}",
                source_corpus_id="definer",
                target_id=f"t{i}",
                target_corpus_id="codeforge",
                edge_type="REFS",
            )
        await store.upsert_bridge_edge(
            source_id="s3",
            source_corpus_id="definer",
            target_id="t3",
            target_corpus_id="branham",
            edge_type="REFS",
        )

        # Delete codeforge bridge edges
        deleted = await store.delete_bridge_edges("codeforge")
        assert deleted == 3

        # Verify codeforge edges are gone, branham remains
        edges = await store.get_all_edges()
        assert len(edges) == 1
        assert edges[0].target_corpus_id == "branham"

        await store.close()

    async def test_delete_bridge_edges_idempotent(self, tmp_path: Path):
        """delete_bridge_edges returns 0 when no edges exist."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        deleted = await store.delete_bridge_edges("nonexistent")
        assert deleted == 0

        await store.close()

    async def test_get_bridge_neighbors_returns_empty(self, tmp_path: Path):
        """get_bridge_neighbors returns [] when no bridge edges exist."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        edges = await store.get_bridge_neighbors("nonexistent_turn")
        assert edges == []

        await store.close()

    async def test_get_bridge_neighbors_with_corpus_filter(self, tmp_path: Path):
        """get_bridge_neighbors filters by target corpus_id."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        await store.upsert_bridge_edge(
            source_id="s1",
            source_corpus_id="definer",
            target_id="t1",
            target_corpus_id="codeforge",
            edge_type="REFS",
        )
        await store.upsert_bridge_edge(
            source_id="s1",
            source_corpus_id="definer",
            target_id="t2",
            target_corpus_id="branham",
            edge_type="REFS",
        )

        # Filter to codeforge only
        edges = await store.get_bridge_neighbors("s1", corpus_id="codeforge")
        assert len(edges) == 1
        assert edges[0].target_corpus_id == "codeforge"

        # No filter — both
        edges = await store.get_bridge_neighbors("s1")
        assert len(edges) == 2

        await store.close()


# ---------------------------------------------------------------------------
# get_orphan_bridge_targets (§A13)
# ---------------------------------------------------------------------------


class TestGetOrphanBridgeTargets:
    """Tests for get_orphan_bridge_targets."""

    async def test_returns_distinct_targets(self, tmp_path: Path):
        """get_orphan_bridge_targets returns distinct target_corpus_id values."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        await store.upsert_bridge_edge("s1", "definer", "t1", "codeforge", "REFS")
        await store.upsert_bridge_edge("s2", "definer", "t2", "codeforge", "REFS")
        await store.upsert_bridge_edge("s3", "definer", "t3", "branham", "REFS")

        targets = await store.get_orphan_bridge_targets()
        assert set(targets) == {"codeforge", "branham"}

        await store.close()

    async def test_returns_empty_when_no_bridge_edges(self, tmp_path: Path):
        """get_orphan_bridge_targets returns [] when no bridge edges exist."""
        store = GraphStore(str(tmp_path / "test.db"))
        await store.initialize()

        targets = await store.get_orphan_bridge_targets()
        assert targets == []

        await store.close()


# ---------------------------------------------------------------------------
# _reconcile_bridge_edges (§A13)
# ---------------------------------------------------------------------------


class TestReconcileBridgeEdges:
    """Tests for the startup orphan bridge edge reconciliation."""

    async def test_reconcile_cleans_orphans(self, tmp_path: Path):
        """_reconcile_bridge_edges cleans bridge edges to unregistered corpora."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        # Register definer
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )

        # Manually insert a bridge edge to a non-existent corpus
        from aip.adapter.graph_store import GraphStore

        gs = GraphStore(str(tmp_path / "definer.db"))
        await gs.initialize()
        await gs.upsert_bridge_edge(
            source_id="s1",
            source_corpus_id="definer",
            target_id="t1",
            target_corpus_id="deleted_corpus",
            edge_type="REFS",
        )
        await gs.close()

        # Verify the orphan exists
        gs2 = GraphStore(str(tmp_path / "definer.db"))
        await gs2.initialize()
        targets = await gs2.get_orphan_bridge_targets()
        assert "deleted_corpus" in targets
        await gs2.close()

        # Run reconciliation
        await registry._reconcile_bridge_edges()

        # Verify orphan is cleaned
        gs3 = GraphStore(str(tmp_path / "definer.db"))
        await gs3.initialize()
        targets = await gs3.get_orphan_bridge_targets()
        assert "deleted_corpus" not in targets
        await gs3.close()

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_reconcime_preserves_registered_targets(self, tmp_path: Path):
        """_reconcile_bridge_edges does NOT clean bridge edges to registered corpora."""
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

        # Insert a bridge edge to codeforge (which IS registered)
        from aip.adapter.graph_store import GraphStore

        gs = GraphStore(str(tmp_path / "definer.db"))
        await gs.initialize()
        await gs.upsert_bridge_edge(
            source_id="s1",
            source_corpus_id="definer",
            target_id="t1",
            target_corpus_id="codeforge",
            edge_type="REFS",
        )
        await gs.close()

        # Run reconciliation
        await registry._reconcile_bridge_edges()

        # Verify codeforge bridge edge is preserved
        gs2 = GraphStore(str(tmp_path / "definer.db"))
        await gs2.initialize()
        targets = await gs2.get_orphan_bridge_targets()
        assert "codeforge" in targets  # preserved — codeforge is registered
        await gs2.close()

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass
