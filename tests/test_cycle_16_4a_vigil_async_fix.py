"""Cycle 16.4A — Vigil Async Runtime Fix regression tests.

Verifies that R01-R03 unawaited coroutine bugs are resolved:

  R01: app.py wiring code assigned a coroutine (not a list) to
       container.vigil._cycle_report_history via unawaited get_cycles().
  R02: vigil._run_retrieval_quality_sample() called record_cycle()
       without await.
  R03: vigil._write_cycle_quality_report() called record_cycle()
       without await.

All three caused ``TypeError: 'coroutine' object is not subscriptable``
when _compute_trend_indicators() or sample code subscripted the history.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import CoroutineType
from typing import Any

import pytest

from aip.foundation.schemas import VigilConfig
from aip.orchestration.actors.vigil import Vigil

# ---------------------------------------------------------------------------
# Fake stores — record whether async methods were properly awaited
# ---------------------------------------------------------------------------


class AwaitTracker:
    """Mixin that tracks whether an async method's coroutine was awaited."""

    def __init__(self) -> None:
        self.get_cycles_awaited: bool = False
        self.record_cycle_awaited: bool = False
        self.record_cycle_calls: list[dict] = []
        self.get_cycles_calls: list[dict] = []
        # If True, record_cycle / get_cycles will raise to test error handling
        self.fail_record_cycle: bool = False
        self.fail_get_cycles: bool = False


class FakeVigilQualityStore(AwaitTracker):
    """Fake quality store that tracks await discipline."""

    async def get_cycles(self, last_n_cycles: int | None = None, **kw: Any) -> list[dict]:
        """Async get_cycles — must be awaited."""
        self.get_cycles_calls.append({"last_n_cycles": last_n_cycles})
        if self.fail_get_cycles:
            raise RuntimeError("fake get_cycles failure")
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "avg_citation_rate": 0.85,
                "avg_grounding_rate": 0.90,
                "avg_llm_faithfulness": 0.80,
                "evaluated_count": 5,
                "flagged_count": 0,
                "timestamp": now,
            }
        ]

    async def record_cycle(self, cycle_report: dict) -> bool:
        """Async record_cycle — must be awaited."""
        self.record_cycle_calls.append(cycle_report)
        if self.fail_record_cycle:
            raise RuntimeError("fake record_cycle failure")
        return True


class FakeVigilStore:
    async def record_vigil_check(self, **kw: Any) -> None:
        pass

    async def list_stale_canonicals(self, threshold_days: int = 30) -> list:
        return []


class FakeCanonicalStore:
    async def list_canonical(self, domain: Any = None) -> list:
        return []

    async def read_canonical(self, artifact_id: str) -> None:
        return None


class FakeEntityStore:
    async def list_entities(self, entity_type: Any = None) -> list:
        return []

    async def get_entity(self, entity_id: str) -> None:
        return None


class FakeModelProvider:
    async def call(self, slot: Any, messages: Any, **kw: Any) -> dict:
        return {"content": "mock"}


class FakeTraceStore:
    async def write_event(self, **kwargs: Any) -> None:
        pass

    async def get_recent_events(self, session_id: str, limit: int = 100) -> list:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vigil(quality_store: FakeVigilQualityStore | None = None) -> Vigil:
    config = VigilConfig(
        stale_threshold_days=30,
        re_evaluate_on_slot_change=True,
    )
    return Vigil(
        config=config,
        vigil_store=FakeVigilStore(),
        canonical_store=FakeCanonicalStore(),
        entity_store=FakeEntityStore(),
        model_provider=FakeModelProvider(),
        trace_store=FakeTraceStore(),
        quality_store=quality_store,
    )


# ===========================================================================
# Test R01 — app.py wiring must not assign coroutine to _cycle_report_history
# ===========================================================================


class TestR01GetCyclesAwaitedInLoadHistory:
    """R01: _load_quality_history() awaits get_cycles() and assigns a list."""

    @pytest.mark.asyncio
    async def test_load_quality_history_assigns_list_not_coroutine(self) -> None:
        """After _load_quality_history, _cycle_report_history is a list, not a coroutine."""
        store = FakeVigilQualityStore()
        vigil = _make_vigil(quality_store=store)

        # Before loading, history is an empty list
        assert isinstance(vigil._cycle_report_history, list)
        assert vigil._cycle_report_history == []

        # Load history — must await
        await vigil._load_quality_history()

        # After loading, history must still be a list, never a coroutine
        assert isinstance(vigil._cycle_report_history, list), (
            f"_cycle_report_history is {type(vigil._cycle_report_history)}, expected list"
        )
        assert not isinstance(vigil._cycle_report_history, CoroutineType)

    @pytest.mark.asyncio
    async def test_load_quality_history_actually_awaited(self) -> None:
        """Verify that get_cycles was actually called (proving it was awaited)."""
        store = FakeVigilQualityStore()
        vigil = _make_vigil(quality_store=store)

        await vigil._load_quality_history()

        assert len(store.get_cycles_calls) == 1
        assert store.get_cycles_calls[0]["last_n_cycles"] == 10

    @pytest.mark.asyncio
    async def test_load_quality_history_failure_does_not_assign_coroutine(self) -> None:
        """If get_cycles raises, _cycle_report_history must remain a list."""
        store = FakeVigilQualityStore()
        store.fail_get_cycles = True
        vigil = _make_vigil(quality_store=store)

        # Should not raise — the error is caught and logged
        await vigil._load_quality_history()

        # History must still be a list, never a coroutine
        assert isinstance(vigil._cycle_report_history, list)
        assert not isinstance(vigil._cycle_report_history, CoroutineType)

    @pytest.mark.asyncio
    async def test_load_quality_history_idempotent(self) -> None:
        """Calling _load_quality_history twice only loads once."""
        store = FakeVigilQualityStore()
        vigil = _make_vigil(quality_store=store)

        await vigil._load_quality_history()
        await vigil._load_quality_history()

        # get_cycles should only have been called once (idempotent)
        assert len(store.get_cycles_calls) == 1

    @pytest.mark.asyncio
    async def test_no_coroutine_subscript_error_in_trend_indicators(self) -> None:
        """_compute_trend_indicators must not raise 'coroutine not subscriptable'."""
        store = FakeVigilQualityStore()
        vigil = _make_vigil(quality_store=store)

        # Load history (populates _cycle_report_history)
        await vigil._load_quality_history()

        # Compute trend indicators — should NOT raise TypeError
        # If _cycle_report_history were a coroutine, [-1] would raise
        result = vigil._compute_trend_indicators(
            avg_citation_rate=0.90,
            avg_grounding_rate=0.95,
            avg_llm_faithfulness=0.85,
        )
        assert isinstance(result, dict)
        assert "citation_rate_trend" in result

    @pytest.mark.asyncio
    async def test_app_wiring_code_uses_load_quality_history(self) -> None:
        """Verify the app.py wiring code path calls _load_quality_history.

        This is a static code check: the app.py lifespan wiring should call
        _load_quality_history() instead of directly calling get_cycles().
        """
        import aip.adapter.api.app as app_module

        source = inspect.getsource(app_module.lifespan)
        # The old broken pattern should NOT exist
        assert "container._vigil_quality_store.get_cycles(" not in source, (
            "app.py still has unawaited get_cycles() call — R01 not fixed"
        )
        # The new correct pattern should exist
        assert "_load_quality_history" in source, "app.py should use _load_quality_history() for quality store wiring"


# ===========================================================================
# Test R02 — record_cycle awaited in _run_retrieval_quality_sample
# ===========================================================================


class TestR02RecordCycleAwaitedInSample:
    """R02: _run_retrieval_quality_sample() awaits record_cycle()."""

    @pytest.mark.asyncio
    async def test_record_cycle_in_sample_path_is_awaited(self) -> None:
        """record_cycle must be awaited in _run_retrieval_quality_sample."""
        store = FakeVigilQualityStore()
        config = VigilConfig(
            stale_threshold_days=30,
            re_evaluate_on_slot_change=True,
            retrieval_quality_sampling_enabled=True,
            retrieval_quality_sample_interval_cycles=1,
        )
        vigil = Vigil(
            config=config,
            vigil_store=FakeVigilStore(),
            canonical_store=FakeCanonicalStore(),
            entity_store=FakeEntityStore(),
            model_provider=FakeModelProvider(),
            trace_store=FakeTraceStore(),
            quality_store=store,
        )
        # Mark history as loaded so _load_quality_history is a no-op
        vigil._history_loaded = True
        # Increment cycle count so the sample gate passes
        vigil._cycle_count = 1

        # The sample method requires retrieval infrastructure (vector store, etc.).
        # It will likely skip/return early because those aren't wired,
        # but the key test is that if it *does* call record_cycle, it awaits it.
        # Let's check the source code statically to confirm await is present.
        source = inspect.getsource(vigil._run_retrieval_quality_sample)
        assert "await self._quality_store.record_cycle(" in source, (
            "_run_retrieval_quality_sample should await record_cycle — R02 not fixed"
        )

    @pytest.mark.asyncio
    async def test_record_cycle_in_sample_not_fire_and_forget(self) -> None:
        """Verify record_cycle is not called with asyncio.create_task or similar."""
        source = inspect.getsource(Vigil._run_retrieval_quality_sample)
        # Should not use fire-and-forget patterns
        assert "create_task" not in source or "record_cycle" not in source.split("create_task")[0], (
            "record_cycle should not be wrapped in create_task (fire-and-forget)"
        )


# ===========================================================================
# Test R03 — record_cycle awaited in _write_cycle_quality_report
# ===========================================================================


class TestR03RecordCycleAwaitedInCycleReport:
    """R03: _write_cycle_quality_report() awaits record_cycle()."""

    @pytest.mark.asyncio
    async def test_record_cycle_in_report_path_is_awaited(self) -> None:
        """record_cycle must be awaited in _write_cycle_quality_report."""
        source = inspect.getsource(Vigil._write_cycle_quality_report)
        assert "await self._quality_store.record_cycle(" in source, (
            "_write_cycle_quality_report should await record_cycle — R03 not fixed"
        )

    @pytest.mark.asyncio
    async def test_record_cycle_in_report_not_fire_and_forget(self) -> None:
        """Verify record_cycle is not called with asyncio.create_task or similar."""
        source = inspect.getsource(Vigil._write_cycle_quality_report)
        assert "create_task" not in source, "record_cycle should not be wrapped in create_task (fire-and-forget)"


# ===========================================================================
# Test coroutine-subscript error no longer occurs
# ===========================================================================


class TestNoCoroutineSubscriptError:
    """Verify the 'coroutine object is not subscriptable' error is gone."""

    @pytest.mark.asyncio
    async def test_trend_indicators_no_subscript_error(self) -> None:
        """_compute_trend_indicators should work when history is properly loaded."""
        store = FakeVigilQualityStore()
        vigil = _make_vigil(quality_store=store)

        await vigil._load_quality_history()

        # This would raise TypeError if _cycle_report_history were a coroutine
        result = vigil._compute_trend_indicators(
            avg_citation_rate=0.90,
            avg_grounding_rate=0.95,
            avg_llm_faithfulness=0.85,
        )
        # With loaded history, trend should NOT be "baseline"
        assert result["citation_rate_trend"] != "baseline"

    @pytest.mark.asyncio
    async def test_cycle_report_history_subscript_safe(self) -> None:
        """Directly subscripting _cycle_report_history[-1] should not raise."""
        store = FakeVigilQualityStore()
        vigil = _make_vigil(quality_store=store)

        await vigil._load_quality_history()

        # This is the exact pattern that caused the crash
        last = vigil._cycle_report_history[-1]
        assert isinstance(last, dict)
        assert "avg_citation_rate" in last

    @pytest.mark.asyncio
    async def test_sample_result_update_history_no_subscript_error(self) -> None:
        """_cycle_report_history[-1].update() should not raise 'coroutine not subscriptable'."""
        store = FakeVigilQualityStore()
        vigil = _make_vigil(quality_store=store)

        await vigil._load_quality_history()

        # This is what the sample code does at line 797
        if vigil._cycle_report_history:
            vigil._cycle_report_history[-1].update({"test_key": "test_value"})
            assert vigil._cycle_report_history[-1]["test_key"] == "test_value"


# ===========================================================================
# Broader static check — no unawaited async store calls remain
# ===========================================================================


class TestNoUnawaitedAsyncCalls:
    """Static checks that no unawaited async quality_store calls remain."""

    def test_no_unawaited_record_cycle_in_vigil(self) -> None:
        """All record_cycle calls in vigil.py must be awaited."""
        source = inspect.getsource(Vigil)
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "self._quality_store.record_cycle(" in stripped:
                # Must be preceded by 'await' on the same logical line
                assert "await" in stripped, f"Line {i + 1} in Vigil has unawaited record_cycle: {stripped}"

    def test_no_unawaited_get_cycles_in_vigil(self) -> None:
        """All get_cycles calls in vigil.py must be awaited."""
        source = inspect.getsource(Vigil)
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "self._quality_store.get_cycles(" in stripped:
                assert "await" in stripped, f"Line {i + 1} in Vigil has unawaited get_cycles: {stripped}"

    def test_no_unawaited_get_cycles_in_app_py(self) -> None:
        """app.py must not have a direct unawaited get_cycles call."""
        import aip.adapter.api.app as app_module

        source = inspect.getsource(app_module.lifespan)
        assert "container._vigil_quality_store.get_cycles(" not in source, (
            "app.py lifespan still has direct unawaited get_cycles call"
        )
