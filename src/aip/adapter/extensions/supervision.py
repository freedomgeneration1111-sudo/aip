"""Supervised task helper — ADR-014 §3.4 / §9.

Every actor scheduler task created by `ExtensionHost.register_actor` needs:
  - A name (for logs and health).
  - Exception logging (not silent swallowing).
  - A reference held by the host so `stop()` can cancel them.

This module provides `_supervised_task(name, coro)` — a thin wrapper around
`asyncio.create_task` that:
  1. Logs (at WARNING) any exception that escapes the coroutine.
  2. Returns the `asyncio.Task` so the caller can cancel/await it on stop.

The wrapper does NOT swallow exceptions into a degraded state — that's the
caller's responsibility (the actor scheduler loop catches per-cycle exceptions
inline and records them on the actor). This helper only ensures that a task
which raises all the way out of its loop is visible in the logs rather than
vanishing into the asyncio void.

Layer: adapter (internal to the extensions package).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _supervised_inner(name: str, coro: Awaitable[T]) -> T:
    """Await coro; log and re-raise on exception (caller decides what to do)."""
    try:
        return await coro
    except asyncio.CancelledError:
        # Cancellation is expected during stop() — don't log as an error.
        logger.debug("supervised_task_cancelled name=%s", name)
        raise
    except Exception as exc:
        logger.warning(
            "supervised_task_failed name=%s error=%s:%s",
            name,
            type(exc).__name__,
            exc,
        )
        raise


def supervised_task(name: str, coro: Awaitable[T]) -> "asyncio.Task[T]":
    """Create a named, supervised asyncio task.

    Wraps `asyncio.create_task` so exceptions are logged with the task name
    before propagating. The returned Task is tracked by the caller (the host's
    ExtensionRegistry holds a list) so `stop()` can cancel them.

    Args:
        name: stable identifier for log correlation (e.g. "actor:demo_actor").
        coro: the coroutine to run.

    Returns:
        asyncio.Task — caller is responsible for cancellation on shutdown.
    """
    task = asyncio.create_task(_supervised_inner(name, coro), name=name)
    return task
