"""Tests for ADR-008 Multi-Corpus Chunk 4: Retrieval scoping.

Covers:
  - Hit ID namespacing: namespace_hit_id / parse_hit_id (§4)
  - Cache key: corpus_aware_cache_key with sorted corpus_ids (§4)
  - Fusion-layer ECS filter: filter_excluded_states (§A2)
  - Multi-corpus fan-out: gather_corpus_results with Branham graceful degrade (§A12)

ADR-008 Rev 3.1 §4, §A2, Amendment §A12.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.corpus_retrieval import (
    corpus_aware_cache_key,
    filter_excluded_states,
    gather_corpus_results,
    namespace_hit_id,
    parse_hit_id,
)
from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.foundation.corpus_types import CorpusType
from aip.foundation.schemas.corpus_turn import CorpusTurn

# ---------------------------------------------------------------------------
# Hit ID namespacing (§4)
# ---------------------------------------------------------------------------


class TestHitIdNamespacing:
    """Tests for namespace_hit_id / parse_hit_id."""

    def test_namespace_hit_id_format(self):
        """namespace_hit_id produces {corpus_id}:{hit_id}."""
        assert namespace_hit_id("definer", "t1") == "definer:t1"
        assert namespace_hit_id("codeforge", "func_abc") == "codeforge:func_abc"

    def test_parse_hit_id_round_trip(self):
        """parse_hit_id reverses namespace_hit_id."""
        namespaced = namespace_hit_id("definer", "t1")
        corpus_id, hit_id = parse_hit_id(namespaced)
        assert corpus_id == "definer"
        assert hit_id == "t1"

    def test_parse_hit_id_preserves_colons_in_hit_id(self):
        """parse_hit_id splits on FIRST colon only."""
        corpus_id, hit_id = parse_hit_id("definer:t1:with:colons")
        assert corpus_id == "definer"
        assert hit_id == "t1:with:colons"

    def test_parse_hit_id_no_colon(self):
        """parse_hit_id returns ('', original) when no colon present."""
        corpus_id, hit_id = parse_hit_id("plain_id")
        assert corpus_id == ""
        assert hit_id == "plain_id"

    def test_namespaced_ids_prevent_rrf_collision(self):
        """Same hit_id in different corpora produces different namespaced IDs."""
        id1 = namespace_hit_id("definer", "t1")
        id2 = namespace_hit_id("codeforge", "t1")
        assert id1 != id2  # RRF won't collapse them


# ---------------------------------------------------------------------------
# Cache key (§4)
# ---------------------------------------------------------------------------


class TestCorpusAwareCacheKey:
    """Tests for corpus_aware_cache_key."""

    def test_different_corpus_sets_produce_different_keys(self):
        """Different active_corpus_ids → different cache keys."""
        k1 = corpus_aware_cache_key("query", ["definer"], "model")
        k2 = corpus_aware_cache_key("query", ["definer", "codeforge"], "model")
        assert k1 != k2

    def test_key_is_order_independent(self):
        """Sorted corpus_ids means order doesn't matter."""
        k1 = corpus_aware_cache_key("query", ["definer", "codeforge"], "model")
        k2 = corpus_aware_cache_key("query", ["codeforge", "definer"], "model")
        assert k1 == k2

    def test_different_queries_produce_different_keys(self):
        """Different queries → different keys."""
        k1 = corpus_aware_cache_key("query1", ["definer"], "model")
        k2 = corpus_aware_cache_key("query2", ["definer"], "model")
        assert k1 != k2

    def test_different_models_produce_different_keys(self):
        """Different model_ids → different keys."""
        k1 = corpus_aware_cache_key("query", ["definer"], "model1")
        k2 = corpus_aware_cache_key("query", ["definer"], "model2")
        assert k1 != k2

    def test_empty_corpus_list_defaults_to_definer(self):
        """Empty corpus_ids list defaults to ['definer']."""
        k1 = corpus_aware_cache_key("query", [], "model")
        k2 = corpus_aware_cache_key("query", ["definer"], "model")
        assert k1 == k2

    def test_key_is_sha256_hex(self):
        """Cache key is a 64-char hex string (SHA256)."""
        key = corpus_aware_cache_key("query", ["definer"], "model")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
# Fusion-layer ECS filter (§A2)
# ---------------------------------------------------------------------------


class TestFilterExcludedStates:
    """Tests for the fusion-layer ECS filter."""

    async def test_filter_removes_archived(self, tmp_path: Path):
        """filter_excluded_states removes ARCHIVED turns."""
        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        # Write two turns
        await store.write_turn(_make_turn("t1"))
        await store.write_turn(_make_turn("t2"))

        # Set t1 to ARCHIVED
        conn = await store._get_conn()
        await conn.execute("UPDATE corpus_turns SET latest_ecs_state = 'ARCHIVED' WHERE turn_id = 't1'")
        await conn.commit()

        hits = [
            {"turn_id": "t1", "content": "archived"},
            {"turn_id": "t2", "content": "active"},
        ]

        filtered = await filter_excluded_states(hits, store)
        assert len(filtered) == 1
        assert filtered[0]["turn_id"] == "t2"

        await store.close()

    async def test_filter_removes_superseded(self, tmp_path: Path):
        """filter_excluded_states removes SUPERSEDED turns."""
        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        await store.write_turn(_make_turn("t1"))
        await store.write_turn(_make_turn("t2"))

        conn = await store._get_conn()
        await conn.execute("UPDATE corpus_turns SET latest_ecs_state = 'SUPERSEDED' WHERE turn_id = 't1'")
        await conn.commit()

        hits = [{"turn_id": "t1"}, {"turn_id": "t2"}]
        filtered = await filter_excluded_states(hits, store)
        assert len(filtered) == 1
        assert filtered[0]["turn_id"] == "t2"

        await store.close()

    async def test_filter_include_archived_passes_all(self, tmp_path: Path):
        """include_archived=True passes all hits through."""
        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        await store.write_turn(_make_turn("t1"))
        conn = await store._get_conn()
        await conn.execute("UPDATE corpus_turns SET latest_ecs_state = 'ARCHIVED' WHERE turn_id = 't1'")
        await conn.commit()

        hits = [{"turn_id": "t1"}]
        filtered = await filter_excluded_states(hits, store, include_archived=True)
        assert len(filtered) == 1

        await store.close()

    async def test_filter_passes_through_non_turn_hits(self, tmp_path: Path):
        """Hits without turn_id are passed through unchanged."""
        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        hits = [
            {"source_id": "wiki:abc", "content": "wiki article"},  # no turn_id
            {"turn_id": "t1", "content": "turn"},
        ]
        await store.write_turn(_make_turn("t1"))

        filtered = await filter_excluded_states(hits, store)
        # Both should pass (t1 is GENERATED, wiki has no turn_id)
        assert len(filtered) == 2

        await store.close()

    async def test_filter_empty_hits_returns_empty(self):
        """Empty hits list returns empty."""
        filtered = await filter_excluded_states([], turn_store=None)
        assert filtered == []

    async def test_filter_fails_open_on_states_for_error(self):
        """If states_for() raises, filter fails open (returns all hits)."""

        class BrokenStore:
            async def states_for(self, turn_ids):
                raise RuntimeError("states_for broken")

        hits = [{"turn_id": "t1"}]
        filtered = await filter_excluded_states(hits, BrokenStore())
        assert len(filtered) == 1  # fail open


# ---------------------------------------------------------------------------
# Multi-corpus fan-out (§A12)
# ---------------------------------------------------------------------------


class TestGatherCorpusResults:
    """Tests for the multi-corpus fan-out with Branham graceful degrade."""

    async def test_gather_returns_empty_for_no_active_corpora(self):
        """gather_corpus_results returns ([], []) when active_corpus_ids is empty."""
        container = _make_container_with_registry(CorpusRegistry())
        hits, excs = await gather_corpus_results(
            query="test",
            active_corpus_ids=[],
            container=container,
        )
        assert hits == []
        assert excs == []

    async def test_gather_returns_empty_when_no_registry(self):
        """gather_corpus_results returns ([], []) when registry is None."""
        container = _make_container_no_registry()
        hits, excs = await gather_corpus_results(
            query="test",
            active_corpus_ids=["definer"],
            container=container,
        )
        assert hits == []
        assert excs == []

    async def test_gather_suppresses_branham_violation(self, tmp_path: Path):
        """RestrictedCorpusAccessViolation is suppressed, not re-raised (§A12)."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        # Register definer + branham (with policy enabled)
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=tmp_path / "branham.db",
            sensitive=True,
        )

        container = _make_container_with_registry(registry)

        # Search both, but don't pass allowlist — Branham should be suppressed
        hits, suppressed = await gather_corpus_results(
            query="test",
            active_corpus_ids=["definer", "branham"],
            container=container,
            allowed_restricted_corpora=[],
        )

        # Branham was suppressed (not re-raised)
        assert len(suppressed) == 1
        # Definer may or may not have hits, but no exception was raised
        assert isinstance(hits, list)

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_gather_with_allowlist_includes_branham(self, tmp_path: Path):
        """With allowlist=True, Branham results are included (not suppressed)."""
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
            sensitive=True,
        )

        container = _make_container_with_registry(registry)

        # With allowlist, Branham should not be suppressed
        hits, suppressed = await gather_corpus_results(
            query="test",
            active_corpus_ids=["definer", "branham"],
            container=container,
            allowed_restricted_corpora=["branham"],
        )

        # No suppressions
        assert len(suppressed) == 0

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(turn_id: str = "t1") -> CorpusTurn:
    """Create a minimal CorpusTurn for testing."""
    return CorpusTurn(
        turn_id=turn_id,
        conversation_id="conv1",
        conversation_name="Test",
        turn_index=0,
        source_model="test",
        source_account="test",
        export_date="2026-01-01",
        user_text="What is AIP?",
        assistant_text="A knowledge engine.",
        turn_timestamp="2026-01-01T00:00:00Z",
    )


def _make_container_with_registry(registry: CorpusRegistry) -> Any:
    """Create a mock container with corpus_registry wired."""

    class FakeContainer:
        def __init__(self):
            self.corpus_registry = registry

    return FakeContainer()


def _make_container_no_registry() -> Any:
    """Create a mock container with corpus_registry = None."""

    class FakeContainer:
        corpus_registry = None

    return FakeContainer()
