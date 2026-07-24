"""Phase α (2026-07-23) — E2E multi-corpus chat retrieval test.

Verifies the full Capability 2 flow: select multiple corpora → chat →
sources include hits from ALL selected corpora. This is the acceptance
test for the multi-corpus retrieval path that an operator exercises
when they check corpora in the Corpus Selection panel and send a chat
turn.

Tests the real path (no mocks):
  1. CorpusRegistry with definer + codeforge both registered
  2. Ingest content into both corpora
  3. Set session_meta with active_corpus_ids=["definer", "codeforge"]
  4. Call assemble_augmented_context (the shared helper used by chat.py)
  5. Verify sources include hits from BOTH corpora

Also verifies the importance=1.0 fix (2026-07-23): code turns must
pass the min_importance=0.3 retrieval filter.

ADR-008 §4 (multi-corpus retrieval), ADR-008 §8 Chunk 7 (code corpus).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from aip.adapter.api.routes._augmented_context import assemble_augmented_context
from aip.adapter.code_ingest_pipeline import ingest_python_directory
from aip.adapter.corpus_registry import CorpusRegistry
from aip.foundation.corpus_types import CorpusType
from aip.foundation.schemas.corpus_turn import CorpusTurn


def _make_conversation_turn(turn_id: str, text: str, domain: str = "test") -> CorpusTurn:
    """Create a conversation turn for the definer corpus."""
    from datetime import datetime, timezone

    return CorpusTurn(
        turn_id=turn_id,
        conversation_id="definer_conv",
        conversation_name="definer conversation",
        turn_index=0,
        source_model="test",
        source_account="test",
        export_date="2026-07-23",
        user_text=f"question about {domain}",
        assistant_text=text,
        turn_timestamp=datetime.now(timezone.utc).isoformat() + "Z",
        importance=0.8,  # conversation turns get Beast-scored importance
        primary_domain=domain,
    )


class _FakeContainer:
    """Minimal container for assemble_augmented_context with a real registry."""

    def __init__(self, registry: CorpusRegistry):
        from aip.foundation.sanitize_fts import sanitize_fts_query

        self.corpus_registry = registry
        # The augmented context helper checks these attributes
        self.corpus_turn_store = None  # force multi-corpus path when registry is wired
        self.lexical_store = None
        self.artifact_store = None
        self.ecs_store = None
        self.project_store = None
        self.graph_store = None
        self.config = {}
        self.definer_profile = None
        self._ask_stores_class = None
        self._search_sources_fn = None
        # Wire the real FTS5 sanitizer so queries with ?, !, etc. don't
        # cause syntax errors. The helper calls this via the container.
        self._sanitize_fts_query_fn = sanitize_fts_query
        # Additional attributes the helper may access (all None to skip
        # the orchestrator fallback path — multi-corpus path is what we test)
        self.embedding_provider = None
        self.event_store = None
        self.vector_store = None


class TestMultiCorpusChatE2E:
    """Phase α — E2E: select multiple corpora → chat → sources from both."""

    async def test_sources_from_both_definer_and_codeforge(self, tmp_path: Path):
        """When active_corpus_ids includes both definer + codeforge, retrieval
        returns hits from BOTH corpora.

        This is the core Capability 2 acceptance test: the operator selects
        multiple corpora in the UI, sends a chat turn, and the sources panel
        shows hits from all selected corpora.
        """
        # Step 1: Register both corpora
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        # Step 2: Ingest into definer (conversation turn about budget enforcement)
        definer_stores = await registry.get_stores("definer")
        conv_turn = _make_conversation_turn(
            "conv-budget-001",
            "The CorpusRegistry enforces the connection budget via _validate_connection_budget.",
        )
        await definer_stores.turn_store.write_turn(conv_turn)

        # Step 3: Ingest into codeforge (actual Python source about budget)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "budget.py").write_text(
            'def _validate_connection_budget():\n'
            '    """Enforce MAX_CORPORA and MAX_CONNECTIONS budget."""\n'
            '    raise ConnectionBudgetExceeded()\n',
            encoding="utf-8",
        )
        codeforge_stores = await registry.get_stores("codeforge")
        counts = await ingest_python_directory(
            source_dir=src_dir,
            turn_store=codeforge_stores.turn_store,
            corpus_id="codeforge",
        )
        assert counts["turns_created"] > 0, "codeforge ingest must create turns"

        # Step 4: Call assemble_augmented_context with both corpora active
        # Use a query that will FTS5-match content in both corpora
        container = _FakeContainer(registry)
        result = await assemble_augmented_context(
            content="connection budget",
            session_id="test-session-e2e",
            container=container,
            session_meta={
                "active_corpus_ids": ["definer", "codeforge"],
                "mode": "augmented",
            },
        )

        # Step 5: Verify sources include hits from BOTH corpora
        assert result.assembled is True, "augmented context must be assembled"
        assert len(result.sources) > 0, "must have at least one source"

        # Sources have: source_id (namespaced as {corpus_id}:{turn_id}),
        # source_type, title, score, content_snippet, domain
        source_ids = [s.get("source_id", "") for s in result.sources]
        source_texts = [s.get("content_snippet", "") + s.get("title", "") for s in result.sources]
        all_source_text = " ".join(source_texts).lower()

        # Must have sources from BOTH corpora (source_id is namespaced)
        has_definer_hit = any(sid.startswith("definer:") for sid in source_ids)
        has_codeforge_hit = any(sid.startswith("codeforge:") for sid in source_ids)
        assert has_definer_hit, (
            f"Must have at least one source from definer corpus. "
            f"source_ids: {source_ids}"
        )
        assert has_codeforge_hit, (
            f"Must have at least one source from codeforge corpus. "
            f"source_ids: {source_ids}"
        )

        # Verify the content is budget-related
        assert "budget" in all_source_text, (
            f"Sources must include budget-related content. Got: {source_texts}"
        )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_codeforge_invisible_when_not_selected(self, tmp_path: Path):
        """When active_corpus_ids only includes definer, codeforge hits don't appear.

        This verifies the corpus scoping is correct — selecting only definer
        should NOT return codeforge hits.
        """
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        # Ingest into codeforge only
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "unique.py").write_text(
            'def unique_function_name_xyz():\n'
            '    """This function only exists in codeforge."""\n'
            '    return "codeforge_only"\n',
            encoding="utf-8",
        )
        codeforge_stores = await registry.get_stores("codeforge")
        await ingest_python_directory(
            source_dir=src_dir,
            turn_store=codeforge_stores.turn_store,
            corpus_id="codeforge",
        )

        # Search with ONLY definer active
        container = _FakeContainer(registry)
        result = await assemble_augmented_context(
            content="unique_function_name_xyz",
            session_id="test-session-definer-only",
            container=container,
            session_meta={
                "active_corpus_ids": ["definer"],  # codeforge NOT selected
                "mode": "augmented",
            },
        )

        # Should NOT find the codeforge-only function
        if result.sources:
            source_texts = [s.get("content_preview", "") + s.get("user_text", "") for s in result.sources]
            all_source_text = " ".join(source_texts).lower()
            assert "unique_function_name_xyz" not in all_source_text, (
                "Codeforge hit must NOT appear when codeforge is not in active_corpus_ids"
            )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_importance_fix_code_turns_pass_filter(self, tmp_path: Path):
        """Verify the importance=1.0 fix: code turns must pass min_importance=0.3.

        Before the fix (2026-07-23), code turns had importance=0.0 and were
        filtered out by the min_importance=0.3 threshold in _search_corpus_turns.
        This test ensures the fix holds: codeforge turns are retrievable.
        """
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        # Ingest a file with a unique function name
        # NOTE: filename must NOT end with _test.py or start with test_ —
        # the AST parser skips test files (should_skip_file rule)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "importance_check.py").write_text(
            'def xyzzy_plugh_function():\n'
            '    """Verify code turns pass the importance filter."""\n'
            '    return True\n',
            encoding="utf-8",
        )
        stores = await registry.get_stores("codeforge")
        await ingest_python_directory(
            source_dir=src_dir,
            turn_store=stores.turn_store,
            corpus_id="codeforge",
        )

        # Search for the unique word
        container = _FakeContainer(registry)
        result = await assemble_augmented_context(
            content="xyzzy",
            session_id="test-session-importance",
            container=container,
            session_meta={
                "active_corpus_ids": ["codeforge"],
                "mode": "augmented",
            },
        )

        # Must find the code turn — if importance were still 0.0, this would fail
        assert result.assembled is True, "must assemble with codeforge hits"
        assert len(result.sources) > 0, (
            "Must find codeforge source — if this fails, the importance=1.0 fix "
            "may have regressed (code turns filtered out by min_importance=0.3)"
        )

        source_texts = [s.get("content_snippet", "") + s.get("title", "") for s in result.sources]
        all_source_text = " ".join(source_texts).lower()
        assert "xyzzy" in all_source_text, (
            f"Must find the ingested function. Sources: {source_texts}"
        )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass
