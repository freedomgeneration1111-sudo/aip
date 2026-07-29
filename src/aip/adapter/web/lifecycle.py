"""Background task registry for deterministic shutdown (ADR-017 WS-2 prerequisite).

Minimal lifecycle contract extracted from W5 (issue #3 — process hang on
shutdown).  The full W5 work (AlertManager threading.Timer removal,
WebSocket batching lifecycle, TestClient lifespan fixes) is high-risk and
deferred; this module provides only the piece WS-2's HTTP fetcher needs:
a central registry where background tasks register on creation and are
cancelled in reverse-registration order on shutdown.

Contract:

    registry = BackgroundTaskRegistry()
    task = asyncio.create_task(fetch_worker(), name="web_fetch:abc")
    registry.register("web_fetch:abc", task)
    ...
    await registry.cancel_all()  # cancels in reverse order, awaits each

Design:

    - Registration order is preserved; ``cancel_all`` cancels in reverse
      order so dependencies shut down before their dependents.
    - ``cancel`` and ``cancel_all`` swallow ``asyncio.CancelledError`` —
      callers do not need a try/except.
    - Tasks that complete naturally are auto-pruned from the registry on
      the next ``register`` / ``cancel_all`` call (cheap scan).
    - The registry is NOT thread-safe; it lives on the async event loop.
      If a background thread ever needs to register a task, it must
      ``loop.call_soon_threadsafe(registry.register, name, task)``.
    - The registry does NOT own task creation — callers create tasks
      with ``asyncio.create_task`` and register them.  This keeps the
      registry a pure lifecycle coordinator, not a scheduler.

WS-2 integration:

    The ``HttpxWebFetcher`` will register every in-flight fetch task so
    that ``app.py`` shutdown can cancel them cleanly.  The integrator
    wires ``registry.cancel_all()`` into the lifespan shutdown sequence
    BEFORE store closes (so in-flight fetches release their HTTP
    connections before the connection pool is torn down).

Future W5 work:

    The full W5 will migrate the existing ``_beast_scheduler``,
    ``_vigil_scheduler``, ``_sexton_actor_scheduler``, etc. tasks from
    container-attribute lifecycle to this registry.  That migration is
    intentionally NOT done here — it touches high-risk AlertManager
    internals and is tracked separately in TECH_DEBT.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Awaitable

logger = logging.getLogger(__name__)


class BackgroundTaskRegistry:
    """Central registry for asyncio background tasks with deterministic shutdown.

    Tasks are stored in registration order in an ``OrderedDict`` keyed by
    name.  ``cancel_all`` iterates in reverse registration order so that
    tasks registered later (typically dependents) are cancelled before
    tasks registered earlier (typically dependencies).

    The registry auto-prunes completed tasks on each ``register`` and
    ``cancel_all`` call to prevent unbounded growth.
    """

    def __init__(self) -> None:
        # name -> asyncio.Task (preserves insertion order)
        self._tasks: OrderedDict[str, asyncio.Task[object]] = OrderedDict()
        # Track tasks currently being cancelled to avoid double-cancel
        self._cancelling: set[str] = set()

    def register(self, name: str, task: asyncio.Task[object]) -> None:
        """Register a background task under ``name``.

        If a task with the same name is already registered and still
        pending, it is NOT replaced — the caller gets a ``ValueError``.
        This prevents accidental shadowing of a long-running task.

        If the existing task has completed, it is pruned and the new
        task is registered under the name.
        """
        self._prune_completed()

        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            raise ValueError(
                f"a task named {name!r} is already registered and still pending; "
                "choose a unique name or cancel the existing task first"
            )

        self._tasks[name] = task
        logger.debug("background_task_registered", extra={"name": name})

    def unregister(self, name: str) -> asyncio.Task[object] | None:
        """Remove a task from the registry by name (does not cancel it).

        Returns the removed task, or ``None`` if no task was registered
        under ``name``.  Useful when a task completes naturally and the
        owner wants to clean up the registry entry explicitly.
        """
        return self._tasks.pop(name, None)

    def get(self, name: str) -> asyncio.Task[object] | None:
        """Return the task registered under ``name``, or ``None``."""
        return self._tasks.get(name)

    def names(self) -> list[str]:
        """Return registered task names in registration order."""
        self._prune_completed()
        return list(self._tasks.keys())

    async def cancel(self, name: str, *, timeout: float = 5.0) -> bool:
        """Cancel a single registered task by name and await its termination.

        Args:
            name: Task name to cancel.
            timeout: Maximum seconds to wait for the task to terminate
                after cancellation.  If the task does not terminate
                within this window, it is abandoned (not awaited) and
                a warning is logged.  The task is removed from the
                registry regardless.

        Returns:
            ``True`` if the task was found and cancelled (or was
            already done, or is currently being cancelled by a
            concurrent call); ``False`` if no task was ever registered
            under ``name``.
        """
        # Check the cancelling-set FIRST so concurrent calls for the
        # same name return True without re-popping / re-cancelling.
        if name in self._cancelling:
            return True

        task = self._tasks.get(name)
        if task is None:
            return False

        if task.done():
            # Prune and return — no cancellation needed.
            self._tasks.pop(name, None)
            return True

        self._cancelling.add(name)
        try:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "background_task_cancel_timeout",
                    extra={"name": name, "timeout": timeout},
                )
            except asyncio.CancelledError:
                pass  # expected — the task was cancelled
            except Exception as exc:
                logger.warning(
                    "background_task_cancel_error",
                    extra={"name": name, "error": str(exc)},
                )
            # Remove from registry only AFTER the cancel+await completes,
            # so concurrent calls see the _cancelling flag and return True.
            self._tasks.pop(name, None)
            return True
        finally:
            self._cancelling.discard(name)

    async def cancel_all(self, *, timeout_per_task: float = 5.0) -> int:
        """Cancel all registered tasks in reverse registration order.

        Args:
            timeout_per_task: Maximum seconds to wait for each task to
                terminate after its cancellation.  Tasks that do not
                terminate within this window are abandoned (not awaited)
                and a warning is logged per task.

        Returns:
            The number of tasks that were cancelled (or were already
            done).  Tasks that timed out are still counted as cancelled
            (the cancellation request was sent).
        """
        self._prune_completed()

        # Snapshot in reverse registration order; iterate a copy so
        # ``cancel`` can mutate ``self._tasks`` safely.
        names_reversed = list(reversed(self._tasks.keys()))
        cancelled_count = 0
        for name in names_reversed:
            try:
                await self.cancel(name, timeout=timeout_per_task)
                cancelled_count += 1
            except Exception as exc:
                logger.warning(
                    "background_task_cancel_all_error",
                    extra={"name": name, "error": str(exc)},
                )
        return cancelled_count

    def _prune_completed(self) -> None:
        """Remove completed tasks from the registry (cheap scan)."""
        # Iterate over a list copy so we can mutate the dict during iteration.
        for name in list(self._tasks.keys()):
            task = self._tasks[name]
            if task.done():
                self._tasks.pop(name, None)


# ---------------------------------------------------------------------------
# Helper: run a coroutine as a registered background task
# ---------------------------------------------------------------------------


def create_registered_task(
    registry: BackgroundTaskRegistry,
    name: str,
    coro: Awaitable[object],
) -> asyncio.Task[object]:
    """Create an asyncio task from ``coro`` and register it under ``name``.

    Convenience wrapper for the common pattern::

        task = asyncio.create_task(worker(), name="web_fetch:abc")
        registry.register("web_fetch:abc", task)

    The task is created with ``asyncio.create_task`` (so it inherits the
    current event loop) and immediately registered.  If the registration
    raises (duplicate name), the task is cancelled before the exception
    propagates so we don't leak an unregistered task.
    """
    task_name = name
    task = asyncio.ensure_future(coro)
    # Set the task's name for debug visibility (asyncio.Task.set_name is 3.11+).
    try:
        task.set_name(task_name)  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass  # older Python or unsupported; not critical
    try:
        registry.register(name, task)
    except ValueError:
        # Duplicate name — cancel the task we just created and re-raise.
        task.cancel()
        raise
    return task


__all__ = [
    "BackgroundTaskRegistry",
    "create_registered_task",
]
