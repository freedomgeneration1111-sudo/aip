"""QW14 (2026-07-23) — Codeforge corpus end-to-end acceptance test.

Validates the full Phase 1.6 Codebase-as-Corpus flow:
  1. Register a codeforge corpus (CorpusType.CODE)
  2. Ingest real Python source into it via code_ingest_pipeline
  3. Query the corpus via CorpusTurnStore search
  4. Verify hits are returned with the ingested content

This test closes ND10 from the 2026-07-23 tech-debt assessment: "No
end-to-end test for extension→corpus→retrieval flow." While the existing
test_corpus_code_ingest.py tests the pipeline in isolation, this test
exercises the full registry → ingest → search path that an operator
would use in production.

ADR-008 §8 Chunk 7 / Phase 1.6 Codebase-as-Corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aip.adapter.code_ingest_pipeline import ingest_python_directory
from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.foundation.corpus_types import CorpusType


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_python_source(tmp_path: Path) -> Path:
    """A small Python source tree for ingest testing."""
    src_dir = tmp_path / "sample_src"
    src_dir.mkdir()

    # A module with functions, a class, and a registration call
    (src_dir / "module_a.py").write_text(
        '''"""Module A — sample source for codeforge ingest testing."""


def greet(name: str) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name}!"


def calculate_sum(a: int, b: int) -> int:
    """Add two numbers and return the result."""
    return a + b


class DataProcessor:
    """Process data records."""

    def __init__(self, config: dict):
        self.config = config

    def run(self, records: list) -> list:
        """Run the processor on a list of records."""
        return [r for r in records if r]


# Module-level registration call (captured by the parser)
register_plugin("data_processor", DataProcessor)
''',
        encoding="utf-8",
    )

    # A second module
    (src_dir / "module_b.py").write_text(
        '''"""Module B — more sample functions."""


def filter_active(items: list) -> list:
    """Filter items to only active ones."""
    return [item for item in items if item.get("active")]


async def async_fetch(url: str) -> str:
    """Fetch data from a URL asynchronously."""
    return f"data from {url}"
''',
        encoding="utf-8",
    )

    # A test file (should be skipped by the parser)
    (src_dir / "test_module.py").write_text(
        'def test_greet(): assert True\n', encoding="utf-8"
    )

    return src_dir


# ---------------------------------------------------------------------------
# AC-10: Codeforge end-to-end (QW14)
# ---------------------------------------------------------------------------


class TestAC10CodeforgeEndToEnd:
    """QW14 — Codeforge corpus end-to-end: register → ingest → search → verify.

    This acceptance test exercises the full Phase 1.6 Codebase-as-Corpus
    flow that an operator would use:
      1. CorpusRegistry.startup() registers codeforge (CorpusType.CODE)
      2. code_ingest_pipeline.ingest_python_directory() populates it
      3. CorpusTurnStore search retrieves the ingested turns
    """

    async def test_register_ingest_search_codeforge(self, tmp_path: Path, sample_python_source: Path):
        """Full flow: register codeforge, ingest Python source, search, verify hits."""
        # Step 1: Register the codeforge corpus via the registry
        registry = CorpusRegistry(max_corpora=8)
        codeforge_db = tmp_path / "codeforge.db"

        await registry.startup(
            corpora_to_register=[
                ("codeforge", CorpusType.CODE, codeforge_db),
            ],
        )

        registered = await registry.list_corpora()
        assert "codeforge" in registered, "codeforge must be registered"

        # Step 2: Ingest the sample Python source
        stores = await registry.get_stores("codeforge")
        turn_store = stores.turn_store
        assert turn_store is not None, "turn_store must be attached after registration"

        counts = await ingest_python_directory(
            source_dir=sample_python_source,
            turn_store=turn_store,
            corpus_id="codeforge",
            skip_existing=True,
        )

        # 3 .py files scanned, 1 skipped (test_module.py), 2 parsed
        assert counts["files_scanned"] == 3, f"expected 3 files scanned, got {counts['files_scanned']}"
        assert counts["files_skipped"] == 1, f"expected 1 file skipped (test_*), got {counts['files_skipped']}"
        assert counts["files_parsed"] == 2, f"expected 2 files parsed, got {counts['files_parsed']}"
        assert counts["turns_created"] > 0, "expected at least 1 turn created"

        # Step 3: Verify the ingested turns are searchable
        # The CorpusTurnStore has a search method — use it to find "greet"
        total_turns = await turn_store.total_turns()
        assert total_turns == counts["turns_created"], (
            f"total_turns ({total_turns}) != turns_created ({counts['turns_created']})"
        )

        # Step 4: Verify specific content was ingested
        # Search for "greet" — should find the greet function
        search_results = await turn_store.search("greet", limit=10)
        assert len(search_results) > 0, "search for 'greet' must return hits"

        # Verify the hit contains the expected content
        hit_texts = [r.searchable_text.lower() if hasattr(r, "searchable_text") else "" for r in search_results]
        found_greet = any("greet" in t for t in hit_texts)
        assert found_greet, f"search results must include the greet function: {hit_texts}"

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_stale_detection_skips_unchanged(self, tmp_path: Path, sample_python_source: Path):
        """Re-ingesting the same source skips unchanged turns (content_hash)."""
        registry = CorpusRegistry(max_corpora=8)
        codeforge_db = tmp_path / "codeforge.db"
        await registry.startup(
            corpora_to_register=[("codeforge", CorpusType.CODE, codeforge_db)],
        )
        stores = await registry.get_stores("codeforge")
        turn_store = stores.turn_store

        # First ingest
        counts1 = await ingest_python_directory(
            source_dir=sample_python_source,
            turn_store=turn_store,
            corpus_id="codeforge",
            skip_existing=True,
        )
        assert counts1["turns_created"] > 0
        assert counts1["turns_skipped_stale"] == 0

        # Second ingest — all turns should be skipped (content_hash matches)
        counts2 = await ingest_python_directory(
            source_dir=sample_python_source,
            turn_store=turn_store,
            corpus_id="codeforge",
            skip_existing=True,
        )
        assert counts2["turns_created"] == 0, "no new turns on re-ingest"
        assert counts2["turns_skipped_stale"] == counts1["turns_created"], (
            f"all first-ingest turns should be skipped as stale: "
            f"first={counts1['turns_created']}, skipped={counts2['turns_skipped_stale']}"
        )

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_codeforge_registered_alongside_definer(self, tmp_path: Path, sample_python_source: Path):
        """Both definer + codeforge registered, ingest into codeforge only."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )

        registered = await registry.list_corpora()
        assert "definer" in registered
        assert "codeforge" in registered

        # Ingest into codeforge
        codeforge_stores = await registry.get_stores("codeforge")
        counts = await ingest_python_directory(
            source_dir=sample_python_source,
            turn_store=codeforge_stores.turn_store,
            corpus_id="codeforge",
        )
        assert counts["turns_created"] > 0

        # Verify definer is unaffected (0 turns — we didn't ingest into it)
        definer_stores = await registry.get_stores("definer")
        definer_count = await definer_stores.turn_store.total_turns()
        assert definer_count == 0, "definer must have 0 turns (we only ingested into codeforge)"

        # codeforge has the turns
        codeforge_count = await codeforge_stores.turn_store.total_turns()
        assert codeforge_count == counts["turns_created"]

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_ingest_real_aip_source_subset(self, tmp_path: Path):
        """Smoke test: ingest a real subset of the AIP codebase.

        Uses the foundation/ directory (small, stable, no external deps).
        This verifies the pipeline works on real AIP code, not just synthetic
        test fixtures.
        """
        registry = CorpusRegistry(max_corpora=8)
        codeforge_db = tmp_path / "codeforge.db"
        await registry.startup(
            corpora_to_register=[("codeforge", CorpusType.CODE, codeforge_db)],
        )
        stores = await registry.get_stores("codeforge")

        # Ingest the foundation directory (pure Python, no I/O deps)
        aip_foundation = Path(__file__).resolve().parent.parent.parent / "src" / "aip" / "foundation"
        if not aip_foundation.exists():
            pytest.skip(f"AIP foundation source not found at {aip_foundation}")

        counts = await ingest_python_directory(
            source_dir=aip_foundation,
            turn_store=stores.turn_store,
            corpus_id="codeforge",
        )

        # Foundation has multiple .py files — verify we parsed at least some
        assert counts["files_scanned"] > 0, "no .py files found in foundation/"
        assert counts["files_parsed"] > 0, "no files successfully parsed"
        assert counts["turns_created"] > 0, "no turns created from real AIP source"

        # Verify the CorpusType enum is searchable (it's a real AIP type)
        search_results = await stores.turn_store.search("CorpusType", limit=5)
        assert len(search_results) > 0, "search for 'CorpusType' must return hits from foundation/"

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass
