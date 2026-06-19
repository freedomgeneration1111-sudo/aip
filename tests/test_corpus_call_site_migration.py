"""Tests for ADR-008 Multi-Corpus Chunk 3: Call-site migration infrastructure.

Covers:
  - AipContainer.corpus_registry field + definer_stores property (§3a)
  - AskStores.from_corpus_stores classmethod (§A1)
  - set_embedding_provider registry-aware path (§A6)

ADR-008 Rev 3.1 §8 Chunk 3, Amendment §A1, §A6.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aip.adapter.api.dependencies import AipContainer
from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.corpus_stores import CorpusStores
from aip.foundation.corpus_types import CorpusType
from aip.orchestration.ask_pipeline import AskStores

# ---------------------------------------------------------------------------
# AipContainer: corpus_registry field + definer_stores property (§3a)
# ---------------------------------------------------------------------------


class TestAipContainerCorpusRegistry:
    """Tests for the corpus_registry field and definer_stores property."""

    def test_corpus_registry_field_defaults_to_none(self):
        """corpus_registry is None on a fresh container."""
        container = AipContainer({})
        assert container.corpus_registry is None

    def test_definer_stores_returns_none_when_no_registry(self):
        """definer_stores property returns None when corpus_registry is None."""
        container = AipContainer({})
        assert container.definer_stores is None

    def test_definer_stores_returns_none_when_registry_has_no_definer(self):
        """definer_stores returns None when registry exists but definer isn't registered."""
        container = AipContainer({})
        registry = CorpusRegistry()
        container.corpus_registry = registry
        # Registry hasn't called startup() yet, so _definer_stores is None
        assert container.definer_stores is None

    async def test_definer_stores_returns_stores_after_startup(self, tmp_path: Path):
        """definer_stores returns the CorpusStores bundle after startup registers definer."""
        container = AipContainer({})
        registry = CorpusRegistry(max_corpora=4)
        container.corpus_registry = registry

        db_path = tmp_path / "definer.db"
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, db_path),
            ],
        )

        ds = container.definer_stores
        assert ds is not None
        assert ds.corpus_id == "definer"
        assert ds.turn_store is not None

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AskStores.from_corpus_stores classmethod (§A1)
# ---------------------------------------------------------------------------


class TestAskStoresFromCorpusStores:
    """Tests for the from_corpus_stores classmethod (§A1)."""

    def test_from_corpus_stores_requires_event_store_and_project_store(self):
        """§A1: event_store and project_store are required keyword args (no defaults)."""
        # Create a minimal CorpusStores shell
        cs = CorpusStores(
            corpus_id="test",
            corpus_type=CorpusType.CONVERSATION,
        )

        # Should raise TypeError if event_store/project_store are missing
        with pytest.raises(TypeError):
            AskStores.from_corpus_stores(cs)  # type: ignore[call-arg]

    def test_from_corpus_stores_builds_ask_stores(self):
        """§A1: from_corpus_stores correctly maps CorpusStores fields to AskStores."""

        # Create mock stores
        class MockStore:
            pass

        cs = CorpusStores(
            corpus_id="test",
            corpus_type=CorpusType.CONVERSATION,
            turn_store=MockStore(),
            lexical_store=MockStore(),
            vector_store=MockStore(),
            graph_store=MockStore(),
            artifact_store=MockStore(),
            ecs_store=MockStore(),
        )

        event_store = MockStore()
        project_store = MockStore()
        model_provider = MockStore()
        embedding_provider = MockStore()

        ask_stores = AskStores.from_corpus_stores(
            cs,
            event_store=event_store,
            project_store=project_store,
            model_provider=model_provider,
            embedding_provider=embedding_provider,
        )

        # Verify all fields are correctly mapped
        assert ask_stores.artifact_store is cs.artifact_store
        assert ask_stores.lexical_store is cs.lexical_store
        assert ask_stores.vector_store is cs.vector_store
        assert ask_stores.event_store is event_store
        assert ask_stores.project_store is project_store
        assert ask_stores.ecs_store is cs.ecs_store
        assert ask_stores.corpus_turn_store is cs.turn_store
        assert ask_stores.graph_store is cs.graph_store
        assert ask_stores.model_provider is model_provider
        assert ask_stores.embedding_provider is embedding_provider

    def test_from_corpus_stores_event_project_are_keyword_only(self):
        """§A1: event_store and project_store must be passed as keyword args."""
        cs = CorpusStores(corpus_id="test", corpus_type=CorpusType.CONVERSATION)

        class _MockStore:
            pass

        # Can't pass event_store/project_store as positional args
        with pytest.raises(TypeError):
            AskStores.from_corpus_stores(cs, _MockStore(), _MockStore())  # type: ignore


# ---------------------------------------------------------------------------
# set_embedding_provider registry-aware path (§A6)
# ---------------------------------------------------------------------------


class TestSetEmbeddingProviderRegistryAware:
    """Tests for the §A6 registry-aware embedding provider update."""

    async def test_registry_path_iterates_corpora(self, tmp_path: Path):
        """§A6: when registry is wired, set_embedding_provider iterates all corpora."""
        container = AipContainer({})
        registry = CorpusRegistry(max_corpora=4)
        container.corpus_registry = registry

        # Register definer
        db_path = tmp_path / "definer.db"
        await registry.startup(
            corpora_to_register=[("definer", CorpusType.CONVERSATION, db_path)],
        )

        # Track if mark_all_for_reembed was called
        stores = await registry.get_stores("definer")
        call_log: list[str] = []

        async def mock_mark(except_model: str = ""):
            call_log.append(f"mark_all_for_reembed({except_model})")
            return 0

        stores.turn_store.mark_all_for_reembed = mock_mark  # type: ignore[method-assign]

        # Create a mock provider
        class MockProvider:
            model = "test-embed-model"
            _embedding_provider = None

        provider = MockProvider()

        # Call set_embedding_provider — should use registry path
        container.set_embedding_provider(provider)

        # Give the async task time to run
        await asyncio.sleep(0.1)

        # Verify mark_all_for_reembed was called on the corpus's turn_store
        assert len(call_log) > 0
        assert "test-embed-model" in call_log[0]

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    def test_legacy_path_falls_back_when_no_registry(self):
        """§A6: when registry is None, falls back to legacy singleton poking."""
        container = AipContainer({})

        # Track if vector_store._embedding_provider was updated
        class MockVectorStore:
            _embedding_provider = None

        class MockCorpusTurnStore:
            async def mark_all_for_reembed(self, except_model: str = ""):
                return 0

        container.vector_store = MockVectorStore()
        container.corpus_turn_store = MockCorpusTurnStore()

        class MockProvider:
            model = "legacy-model"

        provider = MockProvider()
        container.set_embedding_provider(provider)

        # Verify the legacy path poked vector_store._embedding_provider
        assert container.vector_store._embedding_provider is provider

    def test_updates_non_corpus_dependents(self):
        """§A6: beast, knowledge_store, sexton_actor are updated regardless of path."""
        container = AipContainer({})

        class MockBeast:
            _embed = None

        class MockKnowledgeStore:
            _embedding_provider = None

        class MockSexton:
            _embed = None

        container.beast = MockBeast()
        container.knowledge_store = MockKnowledgeStore()
        container.sexton_actor = MockSexton()

        class MockProvider:
            model = "test"

        provider = MockProvider()
        container.set_embedding_provider(provider)

        assert container.beast._embed is provider
        assert container.knowledge_store._embedding_provider is provider
        assert container.sexton_actor._embed is provider

    async def test_registry_path_updates_vector_store_provider(self, tmp_path: Path):
        """§A6: registry path updates each corpus's vector_store._embedding_provider."""
        container = AipContainer({})
        registry = CorpusRegistry(max_corpora=4)
        container.corpus_registry = registry

        db_path = tmp_path / "definer.db"
        await registry.startup(
            corpora_to_register=[("definer", CorpusType.CONVERSATION, db_path)],
        )

        stores = await registry.get_stores("definer")

        # Mock the vector_store's _embedding_provider
        class MockVectorStore:
            _embedding_provider = None

        original_vs = stores.vector_store
        mock_vs = MockVectorStore()
        stores.vector_store = mock_vs  # type: ignore[method-assign]

        class MockProvider:
            model = "vector-test-model"

        provider = MockProvider()
        container.set_embedding_provider(provider)
        await asyncio.sleep(0.1)

        # Verify the corpus's vector_store was updated
        assert mock_vs._embedding_provider is provider

        # Restore for cleanup
        stores.vector_store = original_vs  # type: ignore[method-assign]

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Layer discipline
# ---------------------------------------------------------------------------


class TestChunk3LayerDiscipline:
    """Verify no layer violations introduced."""

    def test_dependencies_no_orchestration_imports(self):
        """AipContainer (adapter) must not import from orchestration."""
        import inspect

        from aip.adapter.api import dependencies

        source = inspect.getsource(dependencies)
        assert "from aip.orchestration" not in source
        assert "import aip.orchestration" not in source
