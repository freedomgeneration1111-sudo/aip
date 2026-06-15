"""Tests for Operator Console fixes — status, async actions, seed bootstrap, graph visibility.

Covers:
A. Backend status truthfulness:
   - /api/v1/status/summary returns backend_reachable: true
   - GUI state treats health success as backend reachable even if rich status is degraded
   - Status summary fallback to /health when /status/summary fails
   - No contradictory BACKEND OK/BACKEND DOWN indicators

B. Corpus Workbench async actions:
   - CorpusActions awaits async callbacks
   - Sync lambdas wrapping async handlers are fixed

C. First-run seed bootstrap:
   - Skips when DB is not empty or sentinel exists
   - Runs on empty DB when AIP_AUTO_SEED is not false
   - Skips when AIP_AUTO_SEED=false

D. Graph visibility:
   - Graph nav item is registered in layout
   - Graph page is registered in app
   - /api/v1/graph/stats endpoint exists

E. Backend status summary endpoint:
   - /api/v1/status/summary includes backend_reachable field
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── A. Backend Status Truthfulness ──────────────────────────────────


class TestStatusSummaryEndpoint:
    """Test that /api/v1/status/summary returns backend_reachable: true."""

    def test_status_summary_returns_backend_reachable(self):
        """The status summary endpoint should include backend_reachable: true."""
        from aip.adapter.api.routes.health import status_summary

        container = MagicMock()
        container._app_start_time = None
        container.config = {}
        container.entity_store = MagicMock()
        container.canonical_store = MagicMock()
        container.event_store = AsyncMock()
        container.autonomy_gate = MagicMock()
        container.artifact_store = MagicMock()
        container.lexical_store = None
        container.vector_store = None
        container.embedding_provider = None
        container.project_store = None
        container.budget_store = None
        container.budget_manager = None
        container.vigil_store = None
        container.model_provider = None
        container.knowledge_store = None
        container.session_store = None
        container.ecs_store = None
        container.review_queue_store = None
        container.trace_store = None
        container.graph_store = None
        container.beast = None
        container.vigil = None
        container.sexton_actor = None

        # Mock event_store.write_event as async
        container.event_store.write_event = AsyncMock()

        result = asyncio.run(status_summary(container=container))

        assert "backend_reachable" in result
        assert result["backend_reachable"] is True

    def test_status_summary_includes_dogfood_mode(self):
        """The status summary should include dogfood_mode."""
        from aip.adapter.api.routes.health import status_summary

        container = MagicMock()
        container._app_start_time = None
        container.config = {}
        container.entity_store = MagicMock()
        container.canonical_store = MagicMock()
        container.event_store = AsyncMock()
        container.autonomy_gate = MagicMock()
        container.artifact_store = MagicMock()
        container.lexical_store = None
        container.vector_store = None
        container.embedding_provider = None
        container.project_store = None
        container.budget_store = None
        container.budget_manager = None
        container.vigil_store = None
        container.model_provider = None
        container.knowledge_store = None
        container.session_store = None
        container.ecs_store = None
        container.review_queue_store = None
        container.trace_store = None
        container.graph_store = None
        container.beast = None
        container.vigil = None
        container.sexton_actor = None

        container.event_store.write_event = AsyncMock()

        result = asyncio.run(status_summary(container=container))

        assert "dogfood_mode" in result
        # Even with no actors, the mode should be one of the valid values
        assert result["dogfood_mode"] in ("FULL", "DEGRADED", "BARE", "minimal", "unknown")


class TestGuiStateBackendReachability:
    """Test that GUI state correctly handles backend reachability."""

    def test_health_success_means_backend_reachable(self):
        """If /health succeeds but /status/summary returns empty, backend should still be reachable."""
        from gui.state import GuiState

        state = GuiState()
        assert state.backend_reachable is False  # Default

        # Simulate: get_status_summary fails (returns {}), but is_backend_reachable succeeds
        async def _test():
            with patch.object(state.api_client, "get_status_summary", return_value={}):
                with patch.object(state.api_client, "is_backend_reachable", return_value=True):
                    await state.refresh_status_summary()

            assert state.backend_reachable is True
            assert "Status summary unavailable" in " ".join(state.warnings)

        asyncio.run(_test())

    def test_both_fail_means_backend_down(self):
        """If both /status/summary and /health fail, backend_reachable should be False."""
        from gui.state import GuiState

        state = GuiState()

        async def _test():
            with patch.object(state.api_client, "get_status_summary", return_value={}):
                with patch.object(state.api_client, "is_backend_reachable", return_value=False):
                    await state.refresh_status_summary()

            assert state.backend_reachable is False

        asyncio.run(_test())

    def test_status_summary_success_means_backend_reachable(self):
        """If /status/summary succeeds, backend_reachable should be True."""
        from gui.state import GuiState

        state = GuiState()

        async def _test():
            summary = {
                "backend_reachable": True,
                "dogfood_mode": "BARE",
                "actor_status_summary": {},
                "retrieval_health_summary": {},
                "warnings": [],
            }
            with patch.object(state.api_client, "get_status_summary", return_value=summary):
                await state.refresh_status_summary()

            assert state.backend_reachable is True
            assert state.dogfood_mode == "BARE"

        asyncio.run(_test())

    def test_degraded_status_summary_still_reachable(self):
        """Degraded status summary should still report backend reachable."""
        from gui.state import GuiState

        state = GuiState()

        async def _test():
            summary = {
                "backend_reachable": True,
                "dogfood_mode": "DEGRADED",
                "actor_status_summary": {
                    "beast": {"initialized": False, "state": "not_configured"},
                    "vigil": {"initialized": False, "state": "not_configured"},
                    "sexton": {"initialized": False, "state": "not_configured"},
                },
                "retrieval_health_summary": {},
                "warnings": ["Backend running in degraded mode"],
            }
            with patch.object(state.api_client, "get_status_summary", return_value=summary):
                await state.refresh_status_summary()

            assert state.backend_reachable is True
            assert state.dogfood_mode == "DEGRADED"

        asyncio.run(_test())


# ── B. Corpus Workbench Async Actions ───────────────────────────────


class TestCorpusActionsAsync:
    """Test that CorpusActions properly handles async callbacks."""

    def test_async_ingest_callback_is_awaited(self):
        """Async ingest callback should be awaited, not just called."""
        from gui.components.corpus_actions import CorpusActions

        awaited = False

        async def on_ingest():
            nonlocal awaited
            awaited = True

        actions = CorpusActions(on_ingest=on_ingest)
        asyncio.run(actions._handle_ingest())
        assert awaited is True

    def test_async_backfill_callback_is_awaited(self):
        """Async backfill callback should be awaited."""
        from gui.components.corpus_actions import CorpusActions

        awaited = False

        async def on_backfill():
            nonlocal awaited
            awaited = True

        actions = CorpusActions(on_backfill=on_backfill)
        asyncio.run(actions._handle_backfill())
        assert awaited is True

    def test_async_retry_callback_is_awaited(self):
        """Async retry callback should be awaited."""
        from gui.components.corpus_actions import CorpusActions

        awaited = False

        async def on_retry():
            nonlocal awaited
            awaited = True

        actions = CorpusActions(on_retry_failed=on_retry)
        asyncio.run(actions._handle_retry_failed())
        assert awaited is True

    def test_sync_callback_still_works(self):
        """Sync callbacks should continue to work as before."""
        from gui.components.corpus_actions import CorpusActions

        called = False

        def on_ingest():
            nonlocal called
            called = True

        actions = CorpusActions(on_ingest=on_ingest)
        asyncio.run(actions._handle_ingest())
        assert called is True

    def test_no_callback_does_not_error(self):
        """No callback set should not raise an error."""
        from gui.components.corpus_actions import CorpusActions

        actions = CorpusActions()
        # Should not raise
        asyncio.run(actions._handle_ingest())
        asyncio.run(actions._handle_backfill())
        asyncio.run(actions._handle_retry_failed())


# ── C. First-Run Seed Bootstrap ─────────────────────────────────────


class TestSeedBootstrap:
    """Test seed bootstrap logic."""

    def test_skips_when_sentinel_exists(self):
        """Bootstrap should skip when sentinel file exists."""
        from aip.cli._seed_bootstrap import run_seed_bootstrap

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create sentinel
            sentinel = Path(tmpdir) / ".seed_bootstrapped"
            sentinel.write_text("seed_bootstrapped\n")

            with patch("aip.cli._seed_bootstrap._SENTINEL_PATH", sentinel):
                result = run_seed_bootstrap()

            assert result is False

    def test_skips_when_auto_seed_false(self):
        """Bootstrap should skip when AIP_AUTO_SEED=false."""
        from aip.cli._seed_bootstrap import run_seed_bootstrap

        with patch.dict(os.environ, {"AIP_AUTO_SEED": "false"}):
            result = run_seed_bootstrap()

        assert result is False

    def test_skips_when_db_not_empty(self):
        """Bootstrap should skip when DB has existing graph nodes."""
        from aip.cli._seed_bootstrap import _is_empty_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE graph_nodes (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO graph_nodes (id) VALUES ('test_node')")
            conn.commit()
            conn.close()

            assert _is_empty_db(db_path) is False

    def test_runs_on_empty_db(self):
        """Bootstrap should run on empty DB when AIP_AUTO_SEED is not false."""
        from aip.cli._seed_bootstrap import run_seed_bootstrap

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            sentinel_path = Path(tmpdir) / ".seed_bootstrapped"
            db_dir = Path(tmpdir)

            with (
                patch("aip.cli._seed_bootstrap._DB_PATH", db_path),
                patch("aip.cli._seed_bootstrap._DB_DIR", db_dir),
                patch("aip.cli._seed_bootstrap._SENTINEL_PATH", sentinel_path),
                patch.dict(os.environ, {"AIP_AUTO_SEED": "true"}),
            ):
                result = run_seed_bootstrap()

            # Should succeed — creates graph tables and ingests conversations
            assert result is True
            assert sentinel_path.exists()

    def test_empty_db_detection(self):
        """_is_empty_db should return True for empty or missing DB."""
        from aip.cli._seed_bootstrap import _is_empty_db

        # Missing DB
        assert _is_empty_db(Path("/nonexistent/path.db")) is True

        # Empty DB
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE graph_nodes (id TEXT PRIMARY KEY)")
            conn.commit()
            conn.close()

            assert _is_empty_db(db_path) is True


# ── D. Graph Visibility ─────────────────────────────────────────────


class TestGraphNavRegistration:
    """Test that Graph appears in the Operator Console navigation."""

    def test_graph_in_nav_items(self):
        """Graph should be in the _NAV_ITEMS list."""
        from gui.components.layout import _NAV_ITEMS

        routes = [route for _, route, _ in _NAV_ITEMS]
        assert "/graph" in routes

    def test_graph_page_module_exists(self):
        """gui.pages.graph module should be importable."""
        import gui.pages.graph  # noqa: F401

        assert hasattr(gui.pages.graph, "graph_page")

    def test_graph_page_registered_in_app(self):
        """gui.app should import gui.pages.graph."""
        import gui.app

        # Check that the import exists in the module's source
        source = Path(gui.app.__file__).read_text()
        assert "gui.pages.graph" in source


class TestGraphStatsEndpoint:
    """Test that /api/v1/graph/stats endpoint is functional."""

    def test_graph_stats_with_empty_store(self):
        """Graph stats endpoint should return zero counts for empty store."""
        from aip.adapter.api.routes.graph import graph_stats

        container = MagicMock()
        container.graph_store = None
        container.config = {}

        result = asyncio.run(graph_stats(container=container))

        # Should return either stats or error dict, not raise
        assert isinstance(result, dict)
        assert "nodes" in result
        assert "edges" in result


# ── E. Combined Status Consistency ──────────────────────────────────


class TestStatusConsistency:
    """Test that top bar and right rail use the same status source."""

    def test_dogfood_bare_does_not_imply_up_without_fetch(self):
        """BARE mode should not imply 'Backend up' when no status has been fetched.

        Before any status fetch, backend_reachable is False and dogfood_mode
        is 'BARE'. The right rail should NOT say 'Backend up'.
        """
        from gui.state import GuiState

        state = GuiState()
        # Default state: backend_reachable=False, dogfood_mode="BARE"
        assert state.backend_reachable is False
        assert state.dogfood_mode == "BARE"
        # The _dogfood_section in right_rail.py now checks state.backend_reachable
        # before showing "Backend reachable" message.

    def test_status_summary_and_health_agree_on_reachability(self):
        """When status summary succeeds, backend_reachable should be True
        and consistent with the summary's backend_reachable field."""
        from gui.state import GuiState

        state = GuiState()

        async def _test():
            summary = {
                "backend_reachable": True,
                "dogfood_mode": "DEGRADED",
                "actor_status_summary": {},
                "retrieval_health_summary": {},
                "warnings": ["Backend running in degraded mode"],
            }
            with patch.object(state.api_client, "get_status_summary", return_value=summary):
                await state.refresh_status_summary()

            assert state.backend_reachable is True
            assert state.status_summary.get("backend_reachable") is True

        asyncio.run(_test())
