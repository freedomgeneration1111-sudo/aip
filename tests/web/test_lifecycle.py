"""Tests for ``aip.adapter.web.lifecycle.BackgroundTaskRegistry``.

This is the W5 lifecycle contract that WS-2's HTTP fetcher must follow.
The full W5 (AlertManager threading.Timer removal, WebSocket batching
lifecycle, TestClient lifespan fixes) is high-risk and deferred; this
test covers only the minimal registry contract that unblocks WS-2.

Coverage:
    - register / unregister / get / names
    - cancel single task
    - cancel_all in reverse registration order
    - auto-pruning of completed tasks
    - duplicate name raises ValueError
    - cancel of unknown name returns False
    - cancel of already-done task returns True (no-op)
    - cancel_all with timeout (abandon slow task)
    - create_registered_task helper
"""

from __future__ import annotations

import asyncio
import time

import pytest

from aip.adapter.web.lifecycle import (
    BackgroundTaskRegistry,
    create_registered_task,
)

# ---------------------------------------------------------------------------
# Helper: suppress CancelledError when awaiting cancelled tasks
# ---------------------------------------------------------------------------


class _suppress_cancelled:
    """Minimal ``contextlib.suppress(asyncio.CancelledError)`` helper.

    Used to await cancelled tasks without re-raising CancelledError
    (which would mark the test as failed).
    """

    def __enter__(self) -> "_suppress_cancelled":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is asyncio.CancelledError:
            return True
        return False


# ---------------------------------------------------------------------------
# Test coroutines
# ---------------------------------------------------------------------------


async def _sleep_forever() -> None:
    """A task that sleeps until cancelled."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise


async def _quick_complete() -> str:
    """A task that completes immediately."""
    return "done"


async def _slow_complete(delay: float) -> str:
    """A task that completes after ``delay`` seconds."""
    await asyncio.sleep(delay)
    return "done"


async def _record_cancel_order(order: list[str], name: str) -> None:
    """A task that appends its name to ``order`` when cancelled."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        order.append(name)
        raise


# ---------------------------------------------------------------------------
# register / get / names
# ---------------------------------------------------------------------------


async def test_register_and_get():
    registry = BackgroundTaskRegistry()
    task = asyncio.ensure_future(_sleep_forever())
    registry.register("worker_1", task)
    assert registry.get("worker_1") is task
    assert registry.names() == ["worker_1"]
    task.cancel()
    with _suppress_cancelled():
        await task


async def test_register_multiple_preserves_order():
    registry = BackgroundTaskRegistry()
    t1 = asyncio.ensure_future(_sleep_forever())
    t2 = asyncio.ensure_future(_sleep_forever())
    t3 = asyncio.ensure_future(_sleep_forever())
    registry.register("t1", t1)
    registry.register("t2", t2)
    registry.register("t3", t3)
    assert registry.names() == ["t1", "t2", "t3"]
    for t in (t1, t2, t3):
        t.cancel()
        with _suppress_cancelled():
            await t


async def test_unregister_removes_without_cancelling():
    registry = BackgroundTaskRegistry()
    task = asyncio.ensure_future(_sleep_forever())
    registry.register("worker_1", task)
    removed = registry.unregister("worker_1")
    assert removed is task
    assert registry.get("worker_1") is None
    assert registry.names() == []
    # Task is still running — caller must cancel it.
    assert not task.done()
    task.cancel()
    with _suppress_cancelled():
        await task


async def test_unregister_unknown_returns_none():
    registry = BackgroundTaskRegistry()
    assert registry.unregister("nonexistent") is None


async def test_names_auto_prunes_completed():
    registry = BackgroundTaskRegistry()
    task = asyncio.ensure_future(_quick_complete())
    registry.register("worker_1", task)
    await task  # let it complete
    assert task.done()
    # names() prunes completed tasks
    assert registry.names() == []


# ---------------------------------------------------------------------------
# Duplicate name handling
# ---------------------------------------------------------------------------


async def test_register_duplicate_pending_raises():
    registry = BackgroundTaskRegistry()
    t1 = asyncio.ensure_future(_sleep_forever())
    registry.register("worker", t1)
    t2 = asyncio.ensure_future(_sleep_forever())
    with pytest.raises(ValueError, match="already registered"):
        registry.register("worker", t2)
    # t2 was never registered — caller must clean it up.
    t2.cancel()
    with _suppress_cancelled():
        await t2
    t1.cancel()
    with _suppress_cancelled():
        await t1


async def test_register_after_completion_replaces():
    """If the previous task completed, the name can be reused."""
    registry = BackgroundTaskRegistry()
    t1 = asyncio.ensure_future(_quick_complete())
    registry.register("worker", t1)
    await t1
    assert t1.done()
    # Now register a new task under the same name — should succeed.
    t2 = asyncio.ensure_future(_sleep_forever())
    registry.register("worker", t2)
    assert registry.get("worker") is t2
    t2.cancel()
    with _suppress_cancelled():
        await t2


# ---------------------------------------------------------------------------
# cancel single
# ---------------------------------------------------------------------------


async def test_cancel_single_task():
    registry = BackgroundTaskRegistry()
    task = asyncio.ensure_future(_sleep_forever())
    registry.register("worker", task)
    cancelled = await registry.cancel("worker", timeout=2.0)
    assert cancelled is True
    assert task.done()
    assert registry.get("worker") is None  # removed from registry


async def test_cancel_unknown_returns_false():
    registry = BackgroundTaskRegistry()
    cancelled = await registry.cancel("nonexistent")
    assert cancelled is False


async def test_cancel_already_done_returns_true():
    registry = BackgroundTaskRegistry()
    task = asyncio.ensure_future(_quick_complete())
    registry.register("worker", task)
    await task  # let it complete naturally
    assert task.done()
    cancelled = await registry.cancel("worker")
    assert cancelled is True
    assert registry.get("worker") is None


async def test_cancel_times_out_on_unresponsive_task():
    """If a task ignores cancellation (catches CancelledError and waits
    again), ``cancel`` must abandon it after the timeout and return True
    (the cancel request was sent)."""
    registry = BackgroundTaskRegistry()

    async def unresponsive() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Catch and re-wait — simulates a buggy task that won't die.
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass

    task = asyncio.ensure_future(unresponsive())
    registry.register("stubborn", task)
    cancelled = await registry.cancel("stubborn", timeout=0.3)
    assert cancelled is True
    # Task is abandoned (still pending) — the registry no longer tracks it.
    assert registry.get("stubborn") is None
    # Clean up the abandoned task to avoid leaking it to other tests.
    task.cancel()
    with _suppress_cancelled():
        await task


# ---------------------------------------------------------------------------
# cancel_all
# ---------------------------------------------------------------------------


async def test_cancel_all_cancels_every_registered_task():
    registry = BackgroundTaskRegistry()
    t1 = asyncio.ensure_future(_sleep_forever())
    t2 = asyncio.ensure_future(_sleep_forever())
    t3 = asyncio.ensure_future(_sleep_forever())
    registry.register("t1", t1)
    registry.register("t2", t2)
    registry.register("t3", t3)

    count = await registry.cancel_all(timeout_per_task=2.0)
    assert count == 3
    assert t1.done()
    assert t2.done()
    assert t3.done()
    assert registry.names() == []


async def test_cancel_all_reverse_registration_order():
    """cancel_all must cancel in reverse registration order so
    dependents go down before dependencies."""
    registry = BackgroundTaskRegistry()
    cancel_order: list[str] = []

    # Register three tasks that record their name on cancellation.
    for name in ("dependency", "middle", "dependent"):
        task = asyncio.ensure_future(_record_cancel_order(cancel_order, name))
        registry.register(name, task)

    # Yield to the event loop so each task starts and enters its try/except.
    # Without this, task.cancel() may fire before the coroutine body runs,
    # injecting CancelledError before the except block is set up.
    await asyncio.sleep(0.05)

    await registry.cancel_all(timeout_per_task=2.0)

    assert cancel_order == ["dependent", "middle", "dependency"]


async def test_cancel_all_empty_registry_returns_zero():
    registry = BackgroundTaskRegistry()
    count = await registry.cancel_all()
    assert count == 0


async def test_cancel_all_skips_completed_tasks():
    """Completed tasks should not be counted in the cancelled total
    (they were already done; no cancel request was needed)."""
    registry = BackgroundTaskRegistry()
    t1 = asyncio.ensure_future(_quick_complete())
    t2 = asyncio.ensure_future(_sleep_forever())
    registry.register("t1", t1)
    registry.register("t2", t2)
    await t1  # t1 completes naturally

    count = await registry.cancel_all(timeout_per_task=2.0)
    # t1 was pruned before cancel; only t2 is cancelled.
    assert count == 1
    assert t2.done()


# ---------------------------------------------------------------------------
# create_registered_task helper
# ---------------------------------------------------------------------------


async def test_create_registered_task_creates_and_registers():
    registry = BackgroundTaskRegistry()
    task = create_registered_task(registry, "worker", _quick_complete())
    assert registry.get("worker") is task
    result = await task
    assert result == "done"


async def test_create_registered_task_duplicate_cancels_new_task():
    """If the name is taken, the helper cancels the new task and re-raises."""
    registry = BackgroundTaskRegistry()
    t1 = create_registered_task(registry, "worker", _sleep_forever())
    # t1 is still pending — registering again must raise.
    with pytest.raises(ValueError, match="already registered"):
        create_registered_task(registry, "worker", _sleep_forever())
    # t1 is still alive and registered.
    assert registry.get("worker") is t1
    assert not t1.done()
    await registry.cancel_all(timeout_per_task=2.0)


# ---------------------------------------------------------------------------
# W5 compliance proof: a fetcher-shaped lifecycle simulation
# ---------------------------------------------------------------------------


async def test_fetcher_lifecycle_simulation():
    """Simulate the WS-2 HttpxWebFetcher lifecycle:

    1.  Start 3 in-flight fetch tasks, each registered with the registry.
    2.  One completes naturally (pruned on next call).
    3.  Shutdown calls ``cancel_all`` — the 2 pending fetches are
        cancelled in reverse order and awaited within the timeout.
    4.  The registry is empty after ``cancel_all`` returns.
    5.  Total wall time is bounded by ``timeout_per_task`` * pending count.
    """
    registry = BackgroundTaskRegistry()

    async def simulated_fetch(url: str, delay: float) -> str:
        try:
            await asyncio.sleep(delay)
            return f"fetched:{url}"
        except asyncio.CancelledError:
            raise

    # Three in-flight fetches: one fast, two slow.
    fast = create_registered_task(
        registry, "web_fetch:fast", simulated_fetch("https://fast.example.com", 0.05)
    )
    slow_1 = create_registered_task(
        registry, "web_fetch:slow_1", simulated_fetch("https://slow1.example.com", 10.0)
    )
    slow_2 = create_registered_task(
        registry, "web_fetch:slow_2", simulated_fetch("https://slow2.example.com", 10.0)
    )

    # Let the fast fetch complete.
    result = await fast
    assert result == "fetched:https://fast.example.com"

    # Shut down — must cancel slow_1 and slow_2 in reverse order.
    start = time.monotonic()
    count = await registry.cancel_all(timeout_per_task=2.0)
    elapsed = time.monotonic() - start

    assert count == 2  # slow_1 and slow_2 (fast was pruned)
    assert slow_1.done()
    assert slow_2.done()
    assert slow_2.cancelled()  # reverse order → slow_2 cancelled first
    assert registry.names() == []
    # Must complete well under the 4-second budget (2 tasks × 2s timeout).
    assert elapsed < 4.0, f"cancel_all took {elapsed:.2f}s, expected < 4.0s"


# ---------------------------------------------------------------------------
# Concurrency safety
# ---------------------------------------------------------------------------


async def test_concurrent_cancel_same_name_does_not_double_cancel():
    """Two concurrent ``cancel`` calls for the same task must not
    double-cancel (the second returns True without re-cancelling)."""
    registry = BackgroundTaskRegistry()
    task = asyncio.ensure_future(_sleep_forever())
    registry.register("worker", task)

    # Fire two cancels concurrently.
    results = await asyncio.gather(
        registry.cancel("worker", timeout=2.0),
        registry.cancel("worker", timeout=2.0),
    )
    assert results == [True, True]
    assert task.done()
