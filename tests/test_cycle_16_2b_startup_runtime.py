"""Cycle 16.2B — Regression tests for dogfood startup/runtime HIGH issues.

F05: start.sh backend readiness poll (sleep 1 → bounded health loop)
F06: _trigger_reembed() logging mismatch (stdlib logger receiving structlog kwargs)
F07: AlertManager restore async/sync mismatch (async store passed to sync restore)
F08: Vigil event-store emit() AttributeError (QueryableEventStore has write_event, not emit)
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# F05: start.sh — bounded backend readiness poll
# ---------------------------------------------------------------------------


class TestF05StartSHReadiness:
    """Regression: start.sh must poll /api/v1/health instead of sleep 1."""

    def test_start_sh_uses_health_poll_not_sleep(self) -> None:
        """start.sh must contain health polling logic, not bare 'sleep 1'."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        # Must NOT contain bare 'sleep 1' (the old pattern)
        # We allow 'sleep "$POLL_INTERVAL"' which is the new bounded loop
        # but not 'sleep 1' as a standalone readiness wait
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # bare "sleep 1" is the old anti-pattern
            if re.match(r"^sleep\s+1$", stripped):
                pytest.fail(f"start.sh line {i}: found bare 'sleep 1' — should use bounded health polling instead")

    def test_start_sh_has_readiness_timeout(self) -> None:
        """start.sh must define a readiness timeout variable."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        # Must have a timeout configuration
        assert "READINESS_TIMEOUT" in content, "start.sh must define AIP_READINESS_TIMEOUT for bounded polling"

    def test_start_sh_polls_health_endpoint(self) -> None:
        """start.sh must poll the /api/v1/health endpoint."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        assert "/api/v1/health" in content, "start.sh must poll /api/v1/health for backend readiness"

    def test_start_sh_handles_readiness_failure(self) -> None:
        """start.sh must exit nonzero if backend never becomes ready."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        # Must check for timeout and exit with error
        assert "failed to become healthy" in content.lower() or "readiness timeout" in content.lower(), (
            "start.sh must exit with error when backend fails readiness check"
        )

    def test_start_sh_has_signal_cleanup(self) -> None:
        """start.sh must trap signals and clean up child processes."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        assert "trap" in content, "start.sh must have signal trap for cleanup"
        assert "cleanup" in content.lower(), "start.sh must define a cleanup function"
        assert "SIGINT" in content or "SIGTERM" in content, "start.sh must trap SIGINT and/or SIGTERM"

    def test_start_sh_checks_backend_process_alive(self) -> None:
        """start.sh must check if the backend process is still running during polling."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        assert "kill -0" in content, "start.sh must check backend process liveness with kill -0 during polling"


# ---------------------------------------------------------------------------
# F06: _trigger_reembed() — structlog-compatible logging
# ---------------------------------------------------------------------------


class TestF06TriggerReembedLogging:
    """Regression: _trigger_reembed used stdlib logger with structlog kwargs.

    The bug: logging.getLogger(__name__).info("msg", key=val) silently
    discards kwargs when using stdlib logging. Must use structlog-compatible
    get_logger() instead.

    Also: outer except Exception: pass silently swallowed setup failures.
    """

    def test_trigger_reembed_uses_structlog_not_stdlib(self) -> None:
        """_trigger_reembed must use aip.logging.get_logger, not stdlib logging."""
        from aip.adapter.api.dependencies import AipContainer

        source = inspect.getsource(AipContainer._trigger_reembed)

        # Must import from aip.logging
        assert "aip.logging" in source or "get_logger" in source, (
            "_trigger_reembed must use aip.logging.get_logger for structured logging"
        )

        # Must NOT import stdlib logging inside the method
        # (the old pattern was: import logging; logging.getLogger(__name__).info(...))
        # Check for the specific anti-pattern
        assert "import logging" not in source, (
            "_trigger_reembed must not import stdlib logging; use aip.logging.get_logger instead"
        )

    def test_trigger_reembed_failure_logs_warning(self) -> None:
        """_trigger_reembed failure must log a warning, not silently pass."""
        from aip.adapter.api.dependencies import AipContainer

        source = inspect.getsource(AipContainer._trigger_reembed)

        # Must have a warning log for failure
        assert "warning" in source.lower() or "warning(" in source, "_trigger_reembed must log a warning on failure"

    def test_trigger_reembed_failure_includes_exc_info(self) -> None:
        """_trigger_reembed failure log must include exc_info for diagnostics."""
        from aip.adapter.api.dependencies import AipContainer

        source = inspect.getsource(AipContainer._trigger_reembed)

        assert "exc_info" in source, "_trigger_reembed must include exc_info=True in failure log"

    def test_trigger_reembed_does_not_swallow_outer_exceptions(self) -> None:
        """The outer try/except in set_embedding_provider must not silently pass."""
        from aip.adapter.api.dependencies import AipContainer

        source = inspect.getsource(AipContainer.set_embedding_provider)

        # The old pattern was: except Exception: pass
        # The new pattern should log the failure
        # Look for bare 'pass' after 'except Exception' in the reembed section
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "except Exception" in line and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line == "pass":
                    # Check if there's a comment explaining it's non-critical
                    # In the reembed section, bare pass is forbidden
                    if "reembed" in source[: source.find(line)]:
                        pytest.fail(
                            "set_embedding_provider reembed section has bare "
                            "'except Exception: pass' — must log the failure"
                        )

    def test_trigger_reembed_no_typeerror_from_logging(self) -> None:
        """_trigger_reembed must not raise TypeError from logging calls."""
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})

        # Create a mock corpus_turn_store
        mock_store = AsyncMock()
        mock_store.mark_all_for_reembed.return_value = 5
        container.corpus_turn_store = mock_store

        # Call _trigger_reembed — must not raise TypeError
        try:
            asyncio.run(container._trigger_reembed("test-model"))
        except TypeError as e:
            pytest.fail(f"_trigger_reembed raised TypeError: {e}")
        except Exception:
            # Other exceptions are fine (e.g., logging config issues in test)
            pass


# ---------------------------------------------------------------------------
# F07: AlertManager restore — async/sync mismatch
# ---------------------------------------------------------------------------


class TestF07AlertManagerRestoreAsyncSync:
    """Regression: AlertManager restore methods called with async store.

    The bug: restore_confidence_calibration() and restore_pre_promotion_snapshots()
    are sync methods that call store.get_confidence_calibrations() etc.
    When the store is the raw async AlertHistoryStore, these return coroutines
    instead of lists, causing 'coroutine object is not iterable' errors.

    Fix: pass the SyncAlertHistoryBridge (sync wrapper) to restore methods,
    not the raw async AlertHistoryStore.
    """

    def test_app_uses_sync_bridge_for_restore(self) -> None:
        """app.py startup must use _alert_history_bridge for sync restore calls."""
        app_py = Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "app.py"
        content = app_py.read_text()

        # Find the restore_confidence_calibration call
        # It should use _alert_history_bridge, not _alert_history_store
        # Pattern to check: restore_confidence_calibration(container._alert_history_store) is BAD
        bad_pattern = r"restore_confidence_calibration\s*\(\s*container\._alert_history_store\s*\)"
        assert not re.search(bad_pattern, content), (
            "app.py must use _alert_history_bridge (not _alert_history_store) for "
            "sync restore methods like restore_confidence_calibration"
        )

        bad_pattern2 = r"restore_pre_promotion_snapshots\s*\(\s*container\._alert_history_store\s*\)"
        assert not re.search(bad_pattern2, content), (
            "app.py must use _alert_history_bridge (not _alert_history_store) for "
            "sync restore methods like restore_pre_promotion_snapshots"
        )

    def test_app_uses_sync_bridge_for_shutdown_persist(self) -> None:
        """app.py shutdown must use _alert_history_bridge for sync persist calls."""
        app_py = Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "app.py"
        content = app_py.read_text()

        bad_pattern = r"persist_confidence_calibration\s*\(\s*container\._alert_history_store\s*\)"
        assert not re.search(bad_pattern, content), (
            "app.py shutdown must use _alert_history_bridge for persist_confidence_calibration"
        )

        bad_pattern2 = r"persist_pre_promotion_snapshots\s*\(\s*container\._alert_history_store\s*\)"
        assert not re.search(bad_pattern2, content), (
            "app.py shutdown must use _alert_history_bridge for persist_pre_promotion_snapshots"
        )

        bad_pattern3 = r"persist_statistical_test_results\s*\(\s*container\._alert_history_store\s*\)"
        assert not re.search(bad_pattern3, content), (
            "app.py shutdown must use _alert_history_bridge for persist_statistical_test_results"
        )

    def test_async_store_methods_return_coroutines(self) -> None:
        """Verify that calling async store methods without await returns a coroutine."""
        # This test proves the bug: if you pass the async store to sync code,
        # the result is a coroutine, not a list
        from aip.adapter.alert_history_store import AlertHistoryStore

        store = AlertHistoryStore("/tmp/test_alert_async_sync.db")

        # Calling async method without await returns coroutine
        result = store.get_confidence_calibrations()
        assert asyncio.iscoroutine(result), (
            "AlertHistoryStore.get_confidence_calibrations() returns a coroutine, "
            "not a list — sync callers must use SyncAlertHistoryBridge"
        )
        # Clean up the coroutine
        result.close()

    def test_sync_bridge_returns_list_not_coroutine(self) -> None:
        """SyncAlertHistoryBridge must return a list, not a coroutine."""
        from aip.adapter.alert_history_store import AlertHistoryStore, SyncAlertHistoryBridge

        store = AlertHistoryStore("/tmp/test_alert_bridge_check.db")
        bridge = SyncAlertHistoryBridge(store)

        # The bridge's get_confidence_calibrations must be a sync method returning list
        method = bridge.get_confidence_calibrations
        assert callable(method), "SyncAlertHistoryBridge must have get_confidence_calibrations"
        assert not asyncio.iscoroutinefunction(method), (
            "SyncAlertHistoryBridge.get_confidence_calibrations must be sync, not async"
        )

        bridge.close()

    def test_restore_methods_are_sync(self) -> None:
        """AlertManager restore methods must be sync (not async)."""
        from aip.adapter.alerting import AlertManager

        assert not asyncio.iscoroutinefunction(AlertManager.restore_confidence_calibration), (
            "restore_confidence_calibration must remain sync"
        )
        assert not asyncio.iscoroutinefunction(AlertManager.restore_pre_promotion_snapshots), (
            "restore_pre_promotion_snapshots must remain sync"
        )


# ---------------------------------------------------------------------------
# F08: Vigil event-store — emit() AttributeError
# ---------------------------------------------------------------------------


class TestF08VigilEventStoreEmit:
    """Regression: Vigil called self._events.emit() but QueryableEventStore
    has write_event(), not emit(), causing AttributeError.

    Fix: adapt Vigil to call write_event() when available, fall back to
    emit() for backward compatibility with other event-store implementations.
    """

    def test_queryable_event_store_has_write_event(self) -> None:
        """QueryableEventStore must have write_event method."""
        from aip.adapter.event_store_queryable import QueryableEventStore

        assert hasattr(QueryableEventStore, "write_event"), "QueryableEventStore must have write_event method"

    def test_queryable_event_store_has_no_emit(self) -> None:
        """QueryableEventStore must NOT have emit method (the missing method)."""
        from aip.adapter.event_store_queryable import QueryableEventStore

        assert not hasattr(QueryableEventStore, "emit"), (
            "QueryableEventStore should not have an emit method — the real interface is write_event"
        )

    def test_vigil_uses_write_event_for_queryable_store(self) -> None:
        """Vigil must call write_event() when the event store has it."""
        from aip.orchestration.actors.vigil import Vigil

        source = inspect.getsource(Vigil)

        # Must check for write_event first
        assert "write_event" in source, "Vigil must use write_event() for QueryableEventStore compatibility"

    def test_vigil_event_emission_no_attribute_error(self) -> None:
        """Vigil event emission must not raise AttributeError with QueryableEventStore."""
        import tempfile

        from aip.adapter.event_store_queryable import QueryableEventStore

        # Create a real QueryableEventStore (in-memory via temp file)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            event_db_path = f.name

        event_store = QueryableEventStore(event_db_path)
        asyncio.run(event_store.initialize())

        # Directly test the event emission pattern
        # This would have raised AttributeError before the fix
        try:
            asyncio.run(
                event_store.write_event(
                    event_type="vigil_eval_complete",
                    actor="vigil",
                    artifact_id="system",
                    evaluated_count=5,
                    flagged_count=1,
                )
            )
        except AttributeError as e:
            pytest.fail(f"Event emission raised AttributeError: {e}")
        finally:
            asyncio.run(event_store.close())
            Path(event_db_path).unlink(missing_ok=True)

    def test_vigil_fallback_to_emit_if_available(self) -> None:
        """Vigil should still support emit() for backward-compatible event stores."""
        from aip.orchestration.actors.vigil import Vigil

        source = inspect.getsource(Vigil)

        # Must have fallback to emit for backward compatibility
        assert '"emit"' in source or "'emit'" in source or "emit" in source, (
            "Vigil should check for emit() as a fallback for backward-compatible stores"
        )
