"""ND3 (2026-07-23) — CorpusStoreFactory builds lexical + graph store slots.

Verifies that CorpusStoreFactory now builds lexical_store and graph_store
slots on CorpusStores (previously only turn_store, ecs_store, artifact_store
were built; lexical/vector/graph were None).

vector_store is intentionally deferred — it needs an embedding_provider
(container-level, not per-corpus); Phase β will wire it.

ADR-008 §8 Chunk 8, ND3 from the 2026-07-23 tech-debt assessment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aip.adapter.corpus_registry import CorpusRegistry
from aip.foundation.corpus_types import CorpusType


class TestCorpusStoreSlots:
    """ND3 — verify lexical_store + graph_store are built per corpus."""

    async def test_lexical_store_built(self, tmp_path: Path):
        """Each corpus must have a non-None lexical_store after registration."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        for cid in ["definer", "codeforge"]:
            stores = await registry.get_stores(cid)
            assert stores.lexical_store is not None, (
                f"lexical_store must not be None for corpus {cid} (ND3)"
            )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_graph_store_built(self, tmp_path: Path):
        """Each corpus must have a non-None graph_store after registration."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        for cid in ["definer", "codeforge"]:
            stores = await registry.get_stores(cid)
            assert stores.graph_store is not None, (
                f"graph_store must not be None for corpus {cid} (ND3)"
            )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_vector_store_still_none(self, tmp_path: Path):
        """vector_store must be None (deferred — needs embedding provider injection).

        Phase β will wire vector_store. For now, it's intentionally None.
        This test documents the deferral and will fail when Phase β adds it
        (which is the intended signal to update the test).
        """
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        stores = await registry.get_stores("codeforge")
        assert stores.vector_store is None, (
            "vector_store must be None — deferred to Phase β (needs embedding "
            "provider injection). If this test fails, Phase β has wired it; "
            "update the test to assert non-None."
        )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_lexical_store_is_per_corpus(self, tmp_path: Path):
        """Each corpus gets its own lexical_store instance (not shared)."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        definer_stores = await registry.get_stores("definer")
        codeforge_stores = await registry.get_stores("codeforge")

        assert definer_stores.lexical_store is not codeforge_stores.lexical_store, (
            "Each corpus must have its own lexical_store instance"
        )
        assert definer_stores.graph_store is not codeforge_stores.graph_store, (
            "Each corpus must have its own graph_store instance"
        )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_graph_store_searchable_after_build(self, tmp_path: Path):
        """The per-corpus graph_store is initialized and can be queried."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        stores = await registry.get_stores("codeforge")
        # GraphStore.node_count() should work (returns 0 for empty graph)
        node_count = await stores.graph_store.node_count()
        assert node_count == 0, (
            f"New codeforge graph should have 0 nodes, got {node_count}"
        )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass
