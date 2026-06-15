"""Read connection pool for SQLite stores in WAL mode.

SQLite WAL mode supports concurrent readers alongside a single writer.
This module provides a lightweight pool of read-only connections that
read-heavy stores (lexical, vector, graph, corpus turns) can use
during concurrent ask workloads to avoid serialising all reads
through a single persistent connection.

Pool semantics:
- Fixed size (default 3 connections), created lazily on first checkout.
- Connections are checked out, used, and returned (no timeout-based eviction).
- Read connections use PRAGMA query_only = ON to guarantee no accidental writes.
- If all connections are in use, the caller falls back to the store's
  existing persistent connection (graceful degradation, never blocks).

Telemetry:
- Checkout count: total number of successful pool checkouts.
- Fallback count: times the pool was exhausted and the write conn was used.
- Exhaustion count: subset of fallback where all pool slots were occupied.
- Checkout latency: rolling average of time spent in _checkout_read_conn().

Interpreting exhaustion_rate:
- 0.0-0.1: Healthy. Pool is adequately sized for current load.
  Most checkouts are served from pool connections. No action needed.
- 0.1-0.3: Moderate. Pool handles most traffic but falls back
  occasionally under bursts. Acceptable for most workloads. Consider
  increasing pool_size if latency-sensitive reads are falling back.
- 0.3-0.6: High. A significant fraction of reads hit the write
  connection, which serializes through the single writer. This
  increases read latency and can block writes. **Increase pool_size
  by 1-2 connections** (e.g. from 3 to 5) and re-observe.
- >0.6: Critical. The pool is severely undersized. Most reads
  bypass the pool entirely, defeating its purpose. **Double the
  pool_size** and investigate whether read patterns have changed
  (e.g. new concurrent ask workloads, higher query complexity).

When to increase pool_size:
- If exhaustion_rate is consistently >0.3 on a store.
- If avg_checkout_latency_ms is high (>50ms) due to stale-connection
  recreation (which adds ~10ms per stale conn).
- If concurrent ask workloads are increasing (each ask touches 3-4
  stores, so 5 concurrent asks = ~15-20 simultaneous pool checkouts).

Note: Increasing pool_size increases memory usage (~1MB per SQLite
connection) and file descriptors. For a single-process app, 5-7 pool
connections per store is usually the practical maximum.

Usage::

    class MyStore(StoreHealthMixin, ReadPoolMixin):
        ...

        async def search(self, query, ...):
            conn = await self._checkout_read_conn()
            try:
                cursor = await conn.execute(...)
                return [self._row_to_item(r) for r in await cursor.fetchall()]
            finally:
                self._return_read_conn(conn)
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, TypedDict

import aiosqlite

from aip.logging import get_logger

log = get_logger(__name__)

# Default pool size -- 3 is a good balance for a single-process async app:
# - 2 readers for parallel ask requests
# - 1 spare so a slow read doesn't immediately fall back to the write conn
_DEFAULT_POOL_SIZE = 3

# Rolling average window for checkout latency tracking
_LATENCY_WINDOW = 20


def resolve_pool_size(store_name: str, config: dict | None = None) -> int:
    """Resolve the read pool size for a given store from config.

    Resolution order:
      1. Per-store override: ``[read_pool.stores.<store_name>]`` pool_size
      2. Global default: ``[read_pool]`` pool_size
      3. Module default: 3

    Parameters
    ----------
    store_name:
        Identifier for the store (e.g. "lexical_store", "vector_store",
        "graph_store", "corpus_turn_store").  Used to look up per-store
        overrides in ``[read_pool.stores.<store_name>]``.
    config:
        Full TOML config dict.  If None, the module default is used.

    Returns
    -------
    int
        The resolved pool size, clamped to [1, 20].
    """
    if config is None:
        return _DEFAULT_POOL_SIZE

    read_pool_cfg = config.get("read_pool", {})

    # Per-store override
    stores_cfg = read_pool_cfg.get("stores", {})
    store_cfg = stores_cfg.get(store_name, {})
    if isinstance(store_cfg, dict) and "pool_size" in store_cfg:
        size = int(store_cfg["pool_size"])
        return max(1, min(20, size))

    # Global default
    if "pool_size" in read_pool_cfg:
        size = int(read_pool_cfg["pool_size"])
        return max(1, min(20, size))

    return _DEFAULT_POOL_SIZE


class ReadPoolHealth(TypedDict):
    """Structured type for read pool telemetry metrics.

    Key metrics for production observability:
    - checkout_count: total successful checkouts (pool + fallback)
    - fallback_count: times write conn was used (pool exhausted or stale)
    - exhaustion_count: subset of fallback where all pool slots were occupied
    - exhaustion_rate: exhaustion_count / checkout_count (utilization indicator)
    - avg_checkout_latency_ms: rolling average (window=20)
    - p95_checkout_latency_ms: 95th percentile of recent checkout latencies
    - recommendation: actionable guidance when exhaustion_rate > 0.3
    """

    pool_size: int
    pool_active: int
    checkout_count: int
    fallback_count: int
    exhaustion_count: int
    exhaustion_rate: float
    avg_checkout_latency_ms: float
    p95_checkout_latency_ms: float
    recommendation: str


class ReadPoolMixin:
    """Mixin that adds a small read connection pool to a SQLite store.

    Expects the inheriting class to have:
    - _db_path: str
    - _get_conn(): async method returning the write connection (fallback)

    The pool is created lazily on first checkout. Connections use
    WAL mode and PRAGMA query_only = ON for safety.
    """

    _read_pool: list[aiosqlite.Connection]
    _read_pool_available: list[bool]
    _read_pool_size: int
    _read_pool_initialized: bool

    # Telemetry counters
    _pool_checkout_count: int
    _pool_fallback_count: int
    _pool_exhaustion_count: int
    _pool_checkout_latencies: list[float]

    def _init_read_pool(self, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        """Initialize pool bookkeeping (call from __init__)."""
        self._read_pool = []
        self._read_pool_available = []
        self._read_pool_size = pool_size
        self._read_pool_initialized = False
        # Telemetry
        self._pool_checkout_count = 0
        self._pool_fallback_count = 0
        self._pool_exhaustion_count = 0
        self._pool_checkout_latencies = []

    async def _ensure_read_pool(self) -> None:
        """Create pool connections lazily on first use."""
        if self._read_pool_initialized:
            return
        db_path = getattr(self, "_db_path", None)
        if not db_path:
            return
        try:
            for _ in range(self._read_pool_size):
                conn = await aiosqlite.connect(db_path)
                conn.row_factory = sqlite3.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                # Sprint 6.3: busy_timeout even on read connections
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute("PRAGMA query_only = ON")
                self._read_pool.append(conn)
                self._read_pool_available.append(True)
            self._read_pool_initialized = True
            # Track connection health for the read pool
            if hasattr(self, "_health_track_connect"):
                self._health_track_connect()
            log.debug(
                "read_pool_created store=%s size=%d",
                self.__class__.__name__,
                self._read_pool_size,
            )
        except Exception as exc:
            log.warning(
                "read_pool_init_failed store=%s error=%s -- will fall back to write conn",
                self.__class__.__name__,
                exc,
            )
            # Clean up any partially created connections
            await self._close_read_pool()
            self._read_pool_initialized = False

    async def _checkout_read_conn(self) -> aiosqlite.Connection:
        """Check out a read connection from the pool.

        Returns a read-only connection if available, otherwise falls back
        to the store's existing persistent (write) connection.  Never blocks.
        """
        t0 = time.monotonic()
        await self._ensure_read_pool()

        for i, available in enumerate(self._read_pool_available):
            if available and i < len(self._read_pool):
                self._read_pool_available[i] = False
                conn = self._read_pool[i]
                # Verify connection is still alive
                try:
                    await conn.execute("SELECT 1")
                    self._record_checkout(t0, fallback=False)
                    return conn
                except Exception:
                    # Connection is stale -- recreate it
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    db_path = getattr(self, "_db_path", "")
                    new_conn = await aiosqlite.connect(db_path)
                    new_conn.row_factory = sqlite3.Row
                    await new_conn.execute("PRAGMA journal_mode=WAL")
                    # Sprint 6.3: busy_timeout on recreated read connections
                    await new_conn.execute("PRAGMA busy_timeout=5000")
                    await new_conn.execute("PRAGMA query_only = ON")
                    self._read_pool[i] = new_conn
                    self._record_checkout(t0, fallback=False)
                    return new_conn

        # All pool connections in use -- fall back to write connection
        self._pool_exhaustion_count += 1
        log.debug(
            "read_pool_exhausted store=%s -- falling back to write conn",
            self.__class__.__name__,
        )
        self._record_checkout(t0, fallback=True)
        return await self._get_conn()  # type: ignore[attr-defined]

    def _return_read_conn(self, conn: aiosqlite.Connection) -> None:
        """Return a read connection to the pool.

        If the connection is a pool member, marks it as available.
        If it's the write connection (fallback), this is a no-op.
        """
        for i, pool_conn in enumerate(self._read_pool):
            if pool_conn is conn:
                self._read_pool_available[i] = True
                return
        # Not a pool connection (was a write-conn fallback) -- no action needed

    def _record_checkout(self, t0: float, fallback: bool) -> None:
        """Record checkout telemetry."""
        elapsed = time.monotonic() - t0
        self._pool_checkout_count += 1
        if fallback:
            self._pool_fallback_count += 1
        self._pool_checkout_latencies.append(elapsed)
        if len(self._pool_checkout_latencies) > _LATENCY_WINDOW:
            self._pool_checkout_latencies = self._pool_checkout_latencies[-_LATENCY_WINDOW:]

    def read_pool_health(self) -> ReadPoolHealth:
        """Return read pool telemetry metrics.

        Only meaningful for stores that have ReadPoolMixin. Callers
        should check ``hasattr(store, "read_pool_health")`` before
        invoking.

        Includes utilization indicators:
        - exhaustion_rate: fraction of checkouts that hit pool exhaustion.
          Interpretation:
            0.0-0.1  Healthy -- pool is adequately sized.
            0.1-0.3  Moderate -- occasional fallbacks, usually fine.
            0.3-0.6  High -- increase pool_size by 1-2 connections.
            >0.6     Critical -- double pool_size and investigate.
        - p95_checkout_latency_ms: 95th percentile of recent checkout
          latencies, useful for spotting tail latency from stale connections.
        - recommendation: when exhaustion_rate > 0.3, suggests increasing
          pool_size; otherwise empty string.
        """
        pool_active = sum(1 for avail in getattr(self, "_read_pool_available", []) if not avail)
        latencies = getattr(self, "_pool_checkout_latencies", [])
        avg_latency_ms = 0.0
        p95_latency_ms = 0.0
        if latencies:
            avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
            sorted_lat = sorted(latencies)
            p95_idx = max(0, int(len(sorted_lat) * 0.95) - 1)
            p95_latency_ms = sorted_lat[p95_idx] * 1000

        checkout_count = getattr(self, "_pool_checkout_count", 0)
        exhaustion_count = getattr(self, "_pool_exhaustion_count", 0)
        exhaustion_rate = (exhaustion_count / checkout_count) if checkout_count > 0 else 0.0

        # Generate recommendation when exhaustion rate is high
        pool_size = getattr(self, "_read_pool_size", 0)
        recommendation = ""
        if exhaustion_rate > 0.6:
            recommendation = (
                f"Critical: exhaustion_rate={exhaustion_rate:.2%}. "
                f"Double pool_size from {pool_size} to {pool_size * 2} and investigate read patterns."
            )
        elif exhaustion_rate > 0.3:
            recommendation = (
                f"High: exhaustion_rate={exhaustion_rate:.2%}. "
                f"Consider increasing pool_size from {pool_size} to {pool_size + 2}."
            )

        return {
            "pool_size": pool_size,
            "pool_active": pool_active,
            "checkout_count": checkout_count,
            "fallback_count": getattr(self, "_pool_fallback_count", 0),
            "exhaustion_count": exhaustion_count,
            "exhaustion_rate": round(exhaustion_rate, 4),
            "avg_checkout_latency_ms": round(avg_latency_ms, 3),
            "p95_checkout_latency_ms": round(p95_latency_ms, 3),
            "recommendation": recommendation,
        }

    async def _close_read_pool(self) -> None:
        """Close all read pool connections."""
        for conn in self._read_pool:
            try:
                await conn.close()
            except Exception:
                pass
        self._read_pool = []
        self._read_pool_available = []
        self._read_pool_initialized = False


# ---------------------------------------------------------------------------
# Read Pool Auto-Sizing (Sprint 5.23 -> Sprint 5.24 auto-apply)
# ---------------------------------------------------------------------------

# How many consecutive high-exhaustion observations before suggesting
# a pool size increase.  Prevents transient spikes from triggering
# suggestions.
_AUTO_SIZE_CONSECUTIVE_THRESHOLD = 3

# Sprint 5.24: How many consecutive observations required for auto-apply.
# Higher than suggestion threshold to ensure sustained pressure before
# automatically resizing.
_AUTO_APPLY_CONSECUTIVE_THRESHOLD = 5

# Maximum suggested pool size -- conservative upper bound for a
# single-process app with SQLite WAL mode.
_AUTO_SIZE_MAX_POOL = 10

# Auto-apply safeguards (Sprint 5.24)
_AUTO_APPLY_MAX_INCREASE = 4  # Max additional connections auto-applied above configured
_AUTO_APPLY_MAX_POOL = 12  # Absolute maximum pool size (hard cap)


class PoolSizeAdjustment:
    """A record of an auto-applied pool size change.

    Sprint 5.24 introduces auto-apply mode where the auto-sizer can
    automatically resize pools when exhaustion is sustained, instead of
    only generating suggestions.  All auto-applied changes are recorded
    here for observability and rollback.

    Attributes
    ----------
    store_name:
        The store identifier (e.g. "graph_store").
    configured_pool_size:
        The pool size from config (the "original" value).
    previous_pool_size:
        The pool size before this adjustment.
    new_pool_size:
        The pool size after this adjustment.
    exhaustion_rate:
        The exhaustion rate that triggered the adjustment.
    reason:
        Human-readable explanation.
    applied_at:
        ISO 8601 timestamp of when the adjustment was applied.
    """

    def __init__(
        self,
        store_name: str,
        configured_pool_size: int,
        previous_pool_size: int,
        new_pool_size: int,
        exhaustion_rate: float,
        reason: str,
    ) -> None:
        self.store_name = store_name
        self.configured_pool_size = configured_pool_size
        self.previous_pool_size = previous_pool_size
        self.new_pool_size = new_pool_size
        self.exhaustion_rate = exhaustion_rate
        self.reason = reason
        self.applied_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return {
            "store_name": self.store_name,
            "configured_pool_size": self.configured_pool_size,
            "previous_pool_size": self.previous_pool_size,
            "new_pool_size": self.new_pool_size,
            "exhaustion_rate": round(self.exhaustion_rate, 4),
            "reason": self.reason,
            "applied_at": self.applied_at,
        }


class PoolSizeSuggestion:
    """A single pool-size suggestion for a store.

    Generated by ``ReadPoolAutoSizer`` when a store's exhaustion rate
    has been consistently high over multiple observations.

    Sprint 5.24: When auto_apply_enabled is True and the consecutive
    threshold is met, the suggestion is also auto-applied to the store
    (subject to safeguards: max increase cap, max pool cap).

    Attributes
    ----------
    store_name:
        The store identifier (e.g. "graph_store").
    current_pool_size:
        The pool size at the time of the suggestion.
    suggested_pool_size:
        The recommended new pool size.
    exhaustion_rate:
        The exhaustion rate that triggered the suggestion.
    reason:
        Human-readable explanation.
    created_at:
        ISO 8601 timestamp of when the suggestion was generated.
    auto_applied:
        Whether the suggestion was auto-applied (Sprint 5.24).
    """

    def __init__(
        self,
        store_name: str,
        current_pool_size: int,
        suggested_pool_size: int,
        exhaustion_rate: float,
        reason: str,
        auto_applied: bool = False,
    ) -> None:
        self.store_name = store_name
        self.current_pool_size = current_pool_size
        self.suggested_pool_size = suggested_pool_size
        self.exhaustion_rate = exhaustion_rate
        self.reason = reason
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.auto_applied = auto_applied

    def to_dict(self) -> dict:
        return {
            "store_name": self.store_name,
            "current_pool_size": self.current_pool_size,
            "suggested_pool_size": self.suggested_pool_size,
            "exhaustion_rate": round(self.exhaustion_rate, 4),
            "reason": self.reason,
            "created_at": self.created_at,
            "auto_applied": self.auto_applied,
        }


class ReadPoolAutoSizer:
    """Monitors read pool exhaustion and generates/applies pool-size changes.

    Sprint 5.23: Suggestion-only mode. Tracks exhaustion_rate per store
    and emits suggestions when sustained high exhaustion is detected.

    Sprint 5.24: Auto-apply mode (default on). When ``auto_apply_enabled``
    is True and exhaustion stays high for ``auto_apply_consecutive_threshold``
    (default 5) observations, the pool size is automatically increased on
    the store. Safeguards:
    - ``auto_apply_max_increase``: Max additional connections above the
      configured pool_size (default 4).
    - ``auto_apply_max_pool``: Absolute maximum pool size (default 12).
    - All changes are logged and recorded in ``adjustment_history``.
    - ``rollback()`` can restore the configured pool size.

    Sprint 5.25: Automatic rollback. When ``auto_rollback_enabled`` is True
    and exhaustion_rate drops below the healthy threshold (0.15) for
    ``auto_rollback_consecutive_threshold`` (default 5) consecutive
    observations AFTER an auto-increase, the pool size is automatically
    restored to its configured value. This prevents over-provisioning
    when load patterns change. All rollback events are logged and
    recorded in adjustment_history.

    Usage::

        auto_sizer = ReadPoolAutoSizer(auto_apply_enabled=True)
        # Call after each health check or cycle
        auto_sizer.observe("graph_store", pool_health, store=store_instance)
        # Get current suggestions
        suggestions = auto_sizer.get_suggestions()
        # Get auto-apply status
        status = auto_sizer.get_status()
        # Rollback if needed
        auto_sizer.rollback("graph_store", store=store_instance)
    """

    # Sprint 5.25: Auto-rollback configuration
    # When exhaustion_rate drops below this threshold for sustained period,
    # auto-rollback kicks in to reduce over-provisioning.
    _AUTO_ROLLBACK_HEALTHY_THRESHOLD = 0.15
    _AUTO_ROLLBACK_CONSECUTIVE_THRESHOLD = 5

    def __init__(
        self,
        consecutive_threshold: int = _AUTO_SIZE_CONSECUTIVE_THRESHOLD,
        auto_apply_enabled: bool = True,
        auto_apply_consecutive_threshold: int = _AUTO_APPLY_CONSECUTIVE_THRESHOLD,
        auto_apply_max_increase: int = _AUTO_APPLY_MAX_INCREASE,
        auto_apply_max_pool: int = _AUTO_APPLY_MAX_POOL,
        auto_rollback_enabled: bool = True,
        auto_rollback_consecutive_threshold: int = _AUTO_ROLLBACK_CONSECUTIVE_THRESHOLD,
        auto_rollback_healthy_threshold: float = _AUTO_ROLLBACK_HEALTHY_THRESHOLD,
        exhaustion_threshold: float = 0.3,
    ) -> None:
        self._consecutive_threshold = consecutive_threshold
        self.auto_apply_enabled = auto_apply_enabled
        self._auto_apply_consecutive_threshold = auto_apply_consecutive_threshold
        self._auto_apply_max_increase = auto_apply_max_increase
        self._auto_apply_max_pool = auto_apply_max_pool

        # Sprint 5.25: Auto-rollback settings
        self.auto_rollback_enabled = auto_rollback_enabled
        self._auto_rollback_consecutive_threshold = auto_rollback_consecutive_threshold
        self._auto_rollback_healthy_threshold = auto_rollback_healthy_threshold

        # Sprint 5.27: Configurable exhaustion threshold (driven by AutoTuningPolicy)
        self._exhaustion_threshold: float = exhaustion_threshold

        # store_name -> list of recent exhaustion_rate observations
        self._observations: dict[str, list[float]] = {}
        # store_name -> current pool size (from last observation)
        self._current_pool_sizes: dict[str, int] = {}
        # store_name -> configured pool size (from first observation)
        self._configured_pool_sizes: dict[str, int] = {}
        # store_name -> total auto-applied increase above configured
        self._auto_applied_increase: dict[str, int] = {}
        # Active suggestions
        self._suggestions: list[PoolSizeSuggestion] = []
        # Auto-apply adjustment history (last 20 per store)
        self._adjustment_history: list[PoolSizeAdjustment] = []
        # Sprint 5.25: store_name -> consecutive low-exhaustion observations
        # after an auto-increase (for auto-rollback detection)
        self._post_increase_low_obs: dict[str, int] = {}
        # Sprint 5.25: Optional alert manager for pool adjustment notifications
        self._alert_manager: Any = None

    def observe(
        self,
        store_name: str,
        pool_health: ReadPoolHealth,
        store: ReadPoolMixin | None = None,
    ) -> PoolSizeSuggestion | None:
        """Record a pool health observation and check for sustained high exhaustion.

        Call this periodically (e.g., on each health check or Sexton cycle)
        with the current pool health for each store.  When auto_apply_enabled
        is True and a store is provided, the pool will be resized if
        exhaustion is sustained beyond the auto-apply threshold.

        Parameters
        ----------
        store_name:
            Identifier for the store being observed.
        pool_health:
            The current read pool health metrics.
        store:
            The actual ReadPoolMixin store instance (needed for auto-apply).
            If None, only suggestions are generated (no auto-apply).

        Returns
        -------
        PoolSizeSuggestion or None
            A suggestion if one was generated or auto-applied, else None.
        """
        exhaustion_rate = pool_health.get("exhaustion_rate", 0.0)
        pool_size = pool_health.get("pool_size", 3)

        # Record the configured pool size on first observation
        if store_name not in self._configured_pool_sizes:
            self._configured_pool_sizes[store_name] = pool_size
            self._auto_applied_increase[store_name] = 0

        self._current_pool_sizes[store_name] = pool_size

        # Track observations (keep last N for sliding window)
        if store_name not in self._observations:
            self._observations[store_name] = []
        self._observations[store_name].append(exhaustion_rate)
        # Keep only the last 15 observations
        if len(self._observations[store_name]) > 15:
            self._observations[store_name] = self._observations[store_name][-15:]

        # Check for sustained high exhaustion
        obs = self._observations[store_name]
        suggestion = None

        if len(obs) >= self._consecutive_threshold:
            # Sprint 5.27: Use policy-driven exhaustion threshold instead of hardcoded 0.3
            recent = obs[-self._consecutive_threshold :]
            if all(rate > self._exhaustion_threshold for rate in recent):
                # Sustained high exhaustion -- generate a suggestion
                avg_rate = sum(recent) / len(recent)
                suggested_size = self._compute_suggested_size(pool_size, avg_rate)

                if suggested_size > pool_size:
                    suggestion = PoolSizeSuggestion(
                        store_name=store_name,
                        current_pool_size=pool_size,
                        suggested_pool_size=suggested_size,
                        exhaustion_rate=avg_rate,
                        reason=(
                            f"Exhaustion rate has been > {self._exhaustion_threshold:.0%} for "
                            f"{self._consecutive_threshold} consecutive observations "
                            f"(avg={avg_rate:.2%}). "
                            f"Consider increasing pool_size from {pool_size} to "
                            f"{suggested_size} in [read_pool.stores.{store_name}] "
                            f"or [read_pool] pool_size."
                        ),
                    )

                    # Replace any existing suggestion for this store
                    self._suggestions = [s for s in self._suggestions if s.store_name != store_name]
                    self._suggestions.append(suggestion)

                    log.info(
                        "read_pool_auto_size_suggestion",
                        store=store_name,
                        current_size=pool_size,
                        suggested_size=suggested_size,
                        exhaustion_rate=round(avg_rate, 4),
                    )

        # Auto-apply check (Sprint 5.24)
        if self.auto_apply_enabled and store is not None and len(obs) >= self._auto_apply_consecutive_threshold:
            apply_recent = obs[-self._auto_apply_consecutive_threshold :]
            if all(rate > self._exhaustion_threshold for rate in apply_recent):
                avg_rate = sum(apply_recent) / len(apply_recent)
                applied = self._auto_apply_pool_size(
                    store_name=store_name,
                    store=store,
                    exhaustion_rate=avg_rate,
                )
                if applied is not None and suggestion is not None:
                    # Mark the suggestion as auto-applied
                    suggestion.auto_applied = True

        # If exhaustion has recovered, clear the suggestion for this store
        # Sprint 5.27: Use policy-driven threshold instead of hardcoded 0.3
        if exhaustion_rate <= self._exhaustion_threshold:
            prev_suggestions = len(self._suggestions)
            self._suggestions = [s for s in self._suggestions if s.store_name != store_name]
            if len(self._suggestions) < prev_suggestions:
                log.info(
                    "read_pool_auto_size_cleared",
                    store=store_name,
                    note=f"exhaustion_rate recovered to <= {self._exhaustion_threshold:.0%}",
                )

        # Sprint 5.25: Auto-rollback check
        # When auto-increased and exhaustion drops below the healthy threshold
        # for sustained observations, automatically roll back to configured size.
        if self.auto_rollback_enabled and store is not None and self._auto_applied_increase.get(store_name, 0) > 0:
            if exhaustion_rate < self._auto_rollback_healthy_threshold:
                # Track consecutive low-exhaustion observations after increase
                current_low = self._post_increase_low_obs.get(store_name, 0)
                self._post_increase_low_obs[store_name] = current_low + 1

                if self._post_increase_low_obs[store_name] >= self._auto_rollback_consecutive_threshold:
                    log.info(
                        "read_pool_auto_rollback_triggering",
                        store=store_name,
                        consecutive_low=self._post_increase_low_obs[store_name],
                        exhaustion_rate=round(exhaustion_rate, 4),
                        threshold=self._auto_rollback_healthy_threshold,
                    )
                    self.rollback(store_name, store)
                    # Reset counter after rollback
                    self._post_increase_low_obs[store_name] = 0
            else:
                # Reset counter if exhaustion goes back above threshold
                self._post_increase_low_obs[store_name] = 0

        return suggestion

    def _auto_apply_pool_size(
        self,
        store_name: str,
        store: ReadPoolMixin,
        exhaustion_rate: float,
    ) -> PoolSizeAdjustment | None:
        """Auto-apply a pool size increase when exhaustion is sustained.

        Safeguards:
        - Never exceed auto_apply_max_pool (absolute cap).
        - Never increase by more than auto_apply_max_increase above
          the configured pool_size.
        - All changes are logged and recorded.

        Returns a PoolSizeAdjustment if applied, or None.
        """
        current_size = store._read_pool_size
        configured_size = self._configured_pool_sizes.get(store_name, current_size)
        self._auto_applied_increase.get(store_name, 0)

        # Compute target size based on exhaustion severity
        if exhaustion_rate > 0.6:
            # Critical: more aggressive increase
            target = current_size + 2
        else:
            # High but not critical: conservative +1
            target = current_size + 1

        # Enforce safeguards
        # 1. Absolute cap
        target = min(target, self._auto_apply_max_pool)

        # 2. Max increase above configured
        max_allowed = configured_size + self._auto_apply_max_increase
        target = min(target, max_allowed)

        # 3. Don't apply if no actual change
        if target <= current_size:
            return None

        # Apply the change
        old_size = current_size
        store._read_pool_size = target
        self._auto_applied_increase[store_name] = target - configured_size
        self._current_pool_sizes[store_name] = target

        adjustment = PoolSizeAdjustment(
            store_name=store_name,
            configured_pool_size=configured_size,
            previous_pool_size=old_size,
            new_pool_size=target,
            exhaustion_rate=exhaustion_rate,
            reason=(
                f"Auto-applied pool_size increase from {old_size} to {target} "
                f"(configured={configured_size}, exhaustion_rate={exhaustion_rate:.2%}). "
                f"Sustained high exhaustion for {self._auto_apply_consecutive_threshold}+ observations."
            ),
        )
        self._adjustment_history.append(adjustment)
        # Keep only last 20 adjustments
        if len(self._adjustment_history) > 20:
            self._adjustment_history = self._adjustment_history[-20:]

        log.info(
            "read_pool_auto_apply",
            store=store_name,
            from_size=old_size,
            to_size=target,
            configured_size=configured_size,
            exhaustion_rate=round(exhaustion_rate, 4),
        )

        # Sprint 5.25: Alert on significant pool size adjustment
        if self._alert_manager is not None:
            try:
                from aip.adapter.alerting import Alert

                severity = "warning" if exhaustion_rate > 0.6 else "info"
                self._alert_manager.send_alert(
                    Alert(
                        alert_type="pool_adjustment",
                        severity=severity,
                        subject=f"read_pool.{store_name}",
                        message=(
                            f"Read pool auto-sized {store_name} from {old_size} to {target} connections "
                            f"(configured={configured_size}, exhaustion_rate={exhaustion_rate:.1%}). "
                            f"Sustained high exhaustion for {self._auto_apply_consecutive_threshold}+ observations."
                        ),
                        data={
                            "store_name": store_name,
                            "previous_pool_size": old_size,
                            "new_pool_size": target,
                            "configured_pool_size": configured_size,
                            "exhaustion_rate": round(exhaustion_rate, 4),
                        },
                    )
                )
            except Exception:
                pass  # Alerting is fire-and-forget

        return adjustment

    def rollback(self, store_name: str, store: ReadPoolMixin) -> bool:
        """Rollback auto-applied pool size changes for a store.

        Restores the pool size to its configured value. The pool will
        be re-created with the correct number of connections on the
        next checkout cycle.

        Returns True if a rollback was performed, False if the store
        was already at its configured size.
        """
        configured_size = self._configured_pool_sizes.get(store_name)
        if configured_size is None:
            return False

        current_size = store._read_pool_size
        if current_size <= configured_size:
            return False

        old_size = current_size
        store._read_pool_size = configured_size
        self._auto_applied_increase[store_name] = 0
        self._current_pool_sizes[store_name] = configured_size

        adjustment = PoolSizeAdjustment(
            store_name=store_name,
            configured_pool_size=configured_size,
            previous_pool_size=old_size,
            new_pool_size=configured_size,
            exhaustion_rate=0.0,
            reason=f"Rollback: restored pool_size from {old_size} to configured value {configured_size}.",
        )
        self._adjustment_history.append(adjustment)
        if len(self._adjustment_history) > 20:
            self._adjustment_history = self._adjustment_history[-20:]

        log.info(
            "read_pool_auto_rollback",
            store=store_name,
            from_size=old_size,
            to_size=configured_size,
        )

        # Sprint 5.25: Alert on rollback
        if self._alert_manager is not None:
            try:
                from aip.adapter.alerting import Alert

                self._alert_manager.send_alert(
                    Alert(
                        alert_type="pool_adjustment",
                        severity="info",
                        subject=f"read_pool.{store_name}.rollback",
                        message=(
                            f"Read pool for {store_name} rolled back from {old_size} to "
                            f"configured value {configured_size}. Exhaustion has recovered."
                        ),
                        data={
                            "store_name": store_name,
                            "previous_pool_size": old_size,
                            "new_pool_size": configured_size,
                            "configured_pool_size": configured_size,
                            "rollback": True,
                        },
                    )
                )
            except Exception:
                pass  # Alerting is fire-and-forget
        return True

    @staticmethod
    def _compute_suggested_size(current_size: int, exhaustion_rate: float) -> int:
        """Compute a suggested pool size based on current size and exhaustion rate.

        Conservative strategy:
        - If exhaustion > 0.6 (critical): double the pool size
        - If exhaustion > 0.3 (high): add 2 connections
        - Never exceed _AUTO_SIZE_MAX_POOL (10)
        - Never decrease (only suggest increases)
        """
        if exhaustion_rate > 0.6:
            suggested = current_size * 2
        else:
            suggested = current_size + 2

        return min(suggested, _AUTO_SIZE_MAX_POOL)

    def get_suggestions(self) -> list[dict]:
        """Return all active suggestions as dicts.

        Suitable for inclusion in the /health endpoint response.
        """
        return [s.to_dict() for s in self._suggestions]

    def get_adjustment_history(self, store_name: str | None = None) -> list[dict]:
        """Return adjustment history, optionally filtered by store.

        Parameters
        ----------
        store_name:
            If provided, return only adjustments for this store.
            Otherwise, return all adjustments.
        """
        if store_name is not None:
            return [a.to_dict() for a in self._adjustment_history if a.store_name == store_name]
        return [a.to_dict() for a in self._adjustment_history]

    def get_status(self) -> dict:
        """Return auto-sizing status for all observed stores.

        Suitable for inclusion in the /health endpoint response.
        Shows current vs. configured pool sizes, auto-applied increases,
        auto-rollback status, and recent adjustment history.
        """
        stores_status = {}
        for store_name in self._current_pool_sizes:
            configured = self._configured_pool_sizes.get(store_name, self._current_pool_sizes[store_name])
            current = self._current_pool_sizes[store_name]
            increase = self._auto_applied_increase.get(store_name, 0)
            obs = self._observations.get(store_name, [])
            recent_rate = obs[-1] if obs else 0.0
            low_obs_count = self._post_increase_low_obs.get(store_name, 0)

            stores_status[store_name] = {
                "configured_pool_size": configured,
                "current_pool_size": current,
                "auto_applied_increase": increase,
                "recent_exhaustion_rate": round(recent_rate, 4),
                "observations_count": len(obs),
                "adjustments": len([a for a in self._adjustment_history if a.store_name == store_name]),
                # Sprint 5.25: Auto-rollback status
                "consecutive_low_exhaustion_obs": low_obs_count,
                "pending_auto_rollback": (increase > 0 and low_obs_count >= self._auto_rollback_consecutive_threshold),
            }

        return {
            "auto_apply_enabled": self.auto_apply_enabled,
            "auto_apply_consecutive_threshold": self._auto_apply_consecutive_threshold,
            "auto_apply_max_increase": self._auto_apply_max_increase,
            "auto_apply_max_pool": self._auto_apply_max_pool,
            "auto_rollback_enabled": self.auto_rollback_enabled,
            "auto_rollback_consecutive_threshold": self._auto_rollback_consecutive_threshold,
            "auto_rollback_healthy_threshold": self._auto_rollback_healthy_threshold,
            # Sprint 5.27: Policy-driven exhaustion threshold
            "exhaustion_threshold": self._exhaustion_threshold,
            "stores": stores_status,
            "recent_adjustments": [a.to_dict() for a in self._adjustment_history[-5:]],
        }

    def clear_suggestion(self, store_name: str) -> None:
        """Manually clear a suggestion for a store (e.g., after operator acts on it)."""
        self._suggestions = [s for s in self._suggestions if s.store_name != store_name]
