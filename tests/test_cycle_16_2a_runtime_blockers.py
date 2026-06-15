"""Cycle 16.2A — Regression tests for dogfood runtime blockers.

F01: build_aip_demo_db.py lexical indexing crash (SQL tuple-wrapping bug)
F02: Vigil coroutine subscript crash (unawaited async get_cycles in __init__)
F03: Entity store metadata column mismatch (metadata vs metadata_json)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# F01: Demo DB builder lexical indexing — no tuple-wrapping of SQL
# ---------------------------------------------------------------------------


class TestF01DemoDBLexicalIndexing:
    """Regression: _index_lexical must pass SQL as a string, not a tuple.

    The bug was conn.execute(("SQL",), params) instead of conn.execute("SQL", params).
    This caused: TypeError: execute() argument 1 must be str, not tuple
    """

    def test_lexical_insert_uses_string_not_tuple(self, tmp_path: Path) -> None:
        """Simulate the _index_lexical path: insert into FTS5 must not wrap SQL in tuple."""
        lexical_db = tmp_path / "lexical.db"

        # Create the FTS5 schema matching the real build script
        conn = sqlite3.connect(str(lexical_db))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS fts_documents (
                doc_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                domain TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_index
                USING fts5(content, domain, metadata, tokenize=unicode61);
        """
        )
        conn.close()

        # Now execute the CORRECTED insert pattern (no tuple wrapping)
        conn = sqlite3.connect(str(lexical_db))
        # This was the broken pattern: conn.execute(("INSERT ...",), (params,))
        # The fix: conn.execute("INSERT ...", (params,))
        conn.execute(
            "INSERT OR REPLACE INTO fts_documents "
            "(doc_id, content, domain, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            ("turn:t1", "hello world", "test", json.dumps({"type": "corpus_turn"}), "2026-01-01"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO fts_documents "
            "(doc_id, content, domain, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            ("wiki:w1", "wiki content", "test", json.dumps({"type": "wiki"}), "2026-01-01"),
        )
        conn.commit()

        # Verify data was inserted
        rows = conn.execute("SELECT doc_id, content FROM fts_documents ORDER BY doc_id").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "turn:t1"
        assert rows[1][0] == "wiki:w1"
        conn.close()

    def test_tuple_wrapped_sql_would_fail(self, tmp_path: Path) -> None:
        """Verify that the OLD broken pattern (tuple-wrapped SQL) raises TypeError."""
        lexical_db = tmp_path / "lexical.db"
        conn = sqlite3.connect(str(lexical_db))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS fts_documents (
                doc_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                domain TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """
        )

        # The OLD broken pattern — SQL string wrapped in a single-element tuple
        with pytest.raises(TypeError, match="str"):
            conn.execute(
                (
                    "INSERT OR REPLACE INTO fts_documents "
                    "(doc_id, content, domain, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                ),
                ("turn:t1", "hello", "test", "{}", "2026-01-01"),
            )
        conn.close()


# ---------------------------------------------------------------------------
# F02: Vigil coroutine subscript — get_cycles must be awaited
# ---------------------------------------------------------------------------


class TestF02VigilCoroutineCrash:
    """Regression: Vigil.__init__ called async get_cycles without await.

    The bug caused: TypeError: 'coroutine' object is not subscriptable
    when _compute_trend_indicators tried to access _cycle_report_history[-1].
    """

    def test_unawaited_get_cycles_produces_coroutine(self) -> None:
        """Calling an async method without await returns a coroutine, not a list."""
        mock_store = AsyncMock()
        mock_store.get_cycles.return_value = [{"avg_citation_rate": 0.5}]

        # Without await, get_cycles returns a coroutine object
        result = mock_store.get_cycles(last_n_cycles=10)
        # It's a coroutine, not a list
        assert asyncio.iscoroutine(result)
        # Subscripting it would fail with TypeError
        with pytest.raises(TypeError, match="not subscriptable"):
            result[-1]

        # Clean up the coroutine to avoid RuntimeWarning
        try:
            asyncio.run(result)
        except RuntimeError:
            # If event loop is already running or closed, just close the coroutine
            result.close()

    def test_awaited_get_cycles_returns_list(self) -> None:
        """Properly awaiting get_cycles returns the list as expected."""
        mock_store = AsyncMock()
        mock_store.get_cycles.return_value = [{"avg_citation_rate": 0.5}]

        result = asyncio.run(mock_store.get_cycles(last_n_cycles=10))
        assert isinstance(result, list)
        assert result[0]["avg_citation_rate"] == 0.5

    def test_vigil_load_quality_history_awaits_get_cycles(self) -> None:
        """Vigil._load_quality_history() must await get_cycles, not call it sync."""
        from aip.orchestration.actors.vigil import Vigil, VigilConfig

        # Build minimal Vigil with a quality store mock
        config = VigilConfig()
        mock_vigil_store = AsyncMock()
        mock_canonical_store = AsyncMock()
        mock_entity_store = AsyncMock()
        mock_model_provider = AsyncMock()
        mock_trace_store = AsyncMock()

        mock_quality_store = AsyncMock()
        mock_quality_store.get_cycles.return_value = [
            {"avg_citation_rate": 0.8, "avg_grounding_rate": 0.7, "avg_llm_faithfulness": 0.9}
        ]

        vigil = Vigil(
            config=config,
            vigil_store=mock_vigil_store,
            canonical_store=mock_canonical_store,
            entity_store=mock_entity_store,
            model_provider=mock_model_provider,
            trace_store=mock_trace_store,
            quality_store=mock_quality_store,
        )

        # Before loading, history should be empty
        assert vigil._cycle_report_history == []
        assert vigil._history_loaded is False

        # Load history (async)
        asyncio.run(vigil._load_quality_history())

        # After loading, history should be populated (not a coroutine)
        assert isinstance(vigil._cycle_report_history, list)
        assert len(vigil._cycle_report_history) == 1
        assert vigil._cycle_report_history[0]["avg_citation_rate"] == 0.8
        assert vigil._history_loaded is True

        # get_cycles was awaited exactly once
        mock_quality_store.get_cycles.assert_called_once_with(last_n_cycles=10)

    def test_vigil_compute_trend_indicators_no_crash_after_load(self) -> None:
        """After loading history, _compute_trend_indicators must not crash."""
        from aip.orchestration.actors.vigil import Vigil, VigilConfig

        config = VigilConfig()
        mock_vigil_store = AsyncMock()
        mock_canonical_store = AsyncMock()
        mock_entity_store = AsyncMock()
        mock_model_provider = AsyncMock()
        mock_trace_store = AsyncMock()

        mock_quality_store = AsyncMock()
        mock_quality_store.get_cycles.return_value = [
            {"avg_citation_rate": 0.8, "avg_grounding_rate": 0.7, "avg_llm_faithfulness": 0.9}
        ]

        vigil = Vigil(
            config=config,
            vigil_store=mock_vigil_store,
            canonical_store=mock_canonical_store,
            entity_store=mock_entity_store,
            model_provider=mock_model_provider,
            trace_store=mock_trace_store,
            quality_store=mock_quality_store,
        )

        # Load history first
        asyncio.run(vigil._load_quality_history())

        # Now compute trend indicators — must NOT raise TypeError
        trends = vigil._compute_trend_indicators(
            avg_citation_rate=0.85,
            avg_grounding_rate=0.75,
            avg_llm_faithfulness=0.92,
        )

        assert "citation_rate_trend" in trends
        assert "grounding_rate_trend" in trends
        assert "llm_faithfulness_trend" in trends
        # With higher values than previous, should show improvement
        assert trends["citation_rate_trend"] == "improving"

    def test_vigil_no_quality_store_still_works(self) -> None:
        """Vigil must work even without a quality store."""
        from aip.orchestration.actors.vigil import Vigil, VigilConfig

        config = VigilConfig()
        mock_vigil_store = AsyncMock()
        mock_canonical_store = AsyncMock()
        mock_entity_store = AsyncMock()
        mock_model_provider = AsyncMock()
        mock_trace_store = AsyncMock()

        vigil = Vigil(
            config=config,
            vigil_store=mock_vigil_store,
            canonical_store=mock_canonical_store,
            entity_store=mock_entity_store,
            model_provider=mock_model_provider,
            trace_store=mock_trace_store,
            quality_store=None,
        )

        # Load should be a no-op with None quality store
        asyncio.run(vigil._load_quality_history())
        assert vigil._cycle_report_history == []

        # Trend indicators should return baseline
        trends = vigil._compute_trend_indicators(0.5, 0.5, 0.5)
        assert trends["citation_rate_trend"] == "baseline"


# ---------------------------------------------------------------------------
# F03: Entity store metadata column mismatch
# ---------------------------------------------------------------------------


class TestF03EntityMetadataColumnMismatch:
    """Regression: entity store queries used 'metadata' but aip init creates 'metadata_json'.

    The bug caused: OperationalError: no such column: metadata
    Fix: align all queries to use metadata_json matching aip init DDL.
    """

    def test_entity_store_uses_metadata_json_column(self, tmp_path: Path) -> None:
        """Entity store must use metadata_json column name, matching aip init DDL."""
        from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore

        db_path = str(tmp_path / "test_entity.db")
        store = SqliteEntityStore(db_path)

        # Initialize (creates table via _DDL_ENTITIES)
        asyncio.run(store.initialize())

        # Verify the column name is metadata_json in the actual DB
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(entities)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "metadata_json" in columns, f"Expected metadata_json column, got: {columns}"
        assert "metadata" not in columns, "Old 'metadata' column should not exist"

    def test_entity_store_crud_with_metadata_json(self, tmp_path: Path) -> None:
        """Full CRUD cycle must work with metadata_json column."""
        from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore

        db_path = str(tmp_path / "test_entity.db")
        store = SqliteEntityStore(db_path)
        asyncio.run(store.initialize())

        # Create
        asyncio.run(
            store.update_entity(
                "e1",
                {
                    "entity_type": "concept",
                    "name": "TestEntity",
                    "metadata": {"x": 1, "y": "hello"},
                },
            )
        )

        # Read
        entity = asyncio.run(store.get_entity("e1"))
        assert entity is not None
        assert entity["name"] == "TestEntity"
        assert entity["metadata"] == {"x": 1, "y": "hello"}

        # List
        entities = asyncio.run(store.list_entities(entity_type="concept"))
        assert len(entities) == 1
        assert entities[0]["metadata"] == {"x": 1, "y": "hello"}

        # Update
        asyncio.run(store.update_entity("e1", {"metadata": {"x": 2, "z": True}}))
        updated = asyncio.run(store.get_entity("e1"))
        assert updated["metadata"]["x"] == 2
        assert updated["metadata"]["z"] is True

        asyncio.run(store.close())

    def test_entity_store_init_schema_matches_aip_init(self, tmp_path: Path) -> None:
        """Entity store DDL must match the schema created by aip init."""
        from aip.adapter.entity.sqlite_entity_store import _DDL_ENTITIES

        # The DDL must use metadata_json, not metadata
        assert "metadata_json" in _DDL_ENTITIES, "DDL must use metadata_json column"
        # Ensure no bare 'metadata' column (without _json suffix) in DDL
        ddl_columns = re.findall(r"(\w+)\s+TEXT", _DDL_ENTITIES)
        assert "metadata" not in ddl_columns, f"DDL should not have bare 'metadata' column, got: {ddl_columns}"
        assert "metadata_json" in ddl_columns, f"DDL should have 'metadata_json' column, got: {ddl_columns}"

    def test_entity_store_queries_reference_metadata_json(self) -> None:
        """All SQL queries in sqlite_entity_store.py must reference metadata_json, not metadata."""
        from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore

        source = inspect.getsource(SqliteEntityStore)

        # Find all SELECT ... FROM entities patterns
        # They must use metadata_json, not bare metadata
        select_queries = re.findall(r"SELECT\s+(.*?)\s+FROM\s+entities", source, re.IGNORECASE)
        for query_cols in select_queries:
            # Should not have bare "metadata" without "_json"
            # But "metadata_json" is fine
            if "metadata" in query_cols.lower():
                assert "metadata_json" in query_cols, (
                    f"SELECT query uses 'metadata' instead of 'metadata_json': {query_cols}"
                )

        # Find INSERT INTO entities patterns
        insert_queries = re.findall(r"INSERT INTO entities\s*\((.*?)\)", source, re.IGNORECASE)
        for insert_cols in insert_queries:
            if "metadata" in insert_cols.lower():
                assert "metadata_json" in insert_cols, (
                    f"INSERT query uses 'metadata' instead of 'metadata_json': {insert_cols}"
                )

        # Find SET clauses with bare "metadata = ?" (not metadata_json)
        set_clauses = re.findall(r"metadata\s*=\s*\?", source)
        for clause in set_clauses:
            # This pattern should be "metadata_json = ?" not "metadata = ?"
            assert False, "Found bare 'metadata = ?' in SET clause, should be 'metadata_json = ?'"

        # Positive: check metadata_json = ? exists
        json_set_clauses = re.findall(r"metadata_json\s*=\s*\?", source)
        assert len(json_set_clauses) > 0, "Expected at least one 'metadata_json = ?' SET clause"

    def test_entity_store_health_check_no_column_error(self, tmp_path: Path) -> None:
        """Entity store health/list/search path must not fail with 'no such column: metadata'."""
        from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore

        db_path = str(tmp_path / "test_entity.db")
        store = SqliteEntityStore(db_path)
        asyncio.run(store.initialize())

        # Insert some test data
        asyncio.run(
            store.update_entity(
                "health-test",
                {
                    "entity_type": "test",
                    "name": "HealthTest",
                    "metadata": {"status": "ok"},
                },
            )
        )

        # These operations must not raise OperationalError about "no such column: metadata"
        entity = asyncio.run(store.get_entity("health-test"))
        assert entity is not None
        assert entity["metadata"] == {"status": "ok"}

        entities = asyncio.run(store.list_entities())
        assert len(entities) >= 1

        entities_filtered = asyncio.run(store.list_entities(entity_type="test"))
        assert len(entities_filtered) >= 1

        asyncio.run(store.close())
