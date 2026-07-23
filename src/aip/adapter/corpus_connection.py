"""CorpusConnectionManager — shared connection pool per corpus SQLite file.

ADR-008 Rev 3.1 Amendment §A0 (APPROVED):

The six stores in a CorpusStores bundle (turn_store, lexical_store,
vector_store, graph_store, artifact_store, ecs_store) MUST share one write
connection and one bounded read pool, both owned by this manager and injected
into each store. Without sharing, each store opens its own connection + pool,
costing 6 × (1 + 3) = 24 connections per corpus — which under a 64-connection
cap with ~28 consumed by the 7 pre-existing files leaves only ~36, giving
MAX_CORPORA = floor(36 / 24) = 1. Defeats the multi-corpus purpose.

With sharing: per_corpus = 1 (shared write) + CORPUS_READ_POOL_SIZE (shared
read pool) = 1 + 2 = 3. MAX_CORPORA = floor(36 / 3) = 12, shipped at 8
(conservative headroom — raised from 4 on 2026-07-23 per QW10).

Layer: adapter. Holds live aiosqlite connections. Imports from foundation only.

Contract (consumed by CorpusStoreFactory, which injects the manager into each
store's constructor):
    manager = CorpusConnectionManager(db_path, read_pool_size=2)
    await manager.open()
    write_conn = manager.write_conn           # for writes (under write_lock)
    read_conn = await manager.acquire_read()  # for reads (auto-returned)
    await manager.release_read(read_conn)     # explicit release
    await manager.close()                     # idempotent

Each store gains a constructor path accepting an injected CorpusConnectionManager
(additive — the existing db_path constructor is kept for the legacy single-corpus
stores until Chunk 3 removes them).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class CorpusConnectionManager:
    """One write connection + one read pool per corpus SQLite file, shared by
    all six stores in the corpus.

    Owned by CorpusStores; opened in CorpusStoreFactory.build(); closed in
    CorpusStores.close_all(). Stores receive this manager instead of a db_path
    and use manager.write_conn / manager.acquire_read() instead of opening
    their own connections.

    The read pool is a fixed-size list of read-only aiosqlite connections
    (PRAGMA query_only = ON). If all read connections are checked out, callers
    fall back to the write connection (graceful degradation, never blocks).
    """

    __slots__ = (
        "_db_path",
        "_read_pool_size",
        "_write_conn",
        "_read_pool",
        "_read_pool_available",
        "_read_pool_lock",
        "_opened",
        "_closed",
        "_checkout_count",
        "_fallback_count",
        "_exhaustion_count",
    )

    def __init__(self, db_path: str, read_pool_size: int = 2) -> None:
        self._db_path: str = db_path
        self._read_pool_size: int = max(1, read_pool_size)
        self._write_conn: aiosqlite.Connection | None = None
        self._read_pool: list[aiosqlite.Connection] = []
        self._read_pool_available: list[bool] = []
        self._read_pool_lock: asyncio.Lock = asyncio.Lock()
        self._opened: bool = False
        self._closed: bool = False
        # Telemetry
        self._checkout_count: int = 0
        self._fallback_count: int = 0
        self._exhaustion_count: int = 0

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def read_pool_size(self) -> int:
        return self._read_pool_size

    @property
    def opened(self) -> bool:
        return self._opened and not self._closed

    @property
    def write_conn(self) -> aiosqlite.Connection:
        """The shared write connection. Raises RuntimeError if not open.

        Callers MUST hold the CorpusStores.write_lock before using this
        connection for writes. The manager itself does not enforce the lock
        — that's the CorpusStores' responsibility.
        """
        if not self._opened or self._write_conn is None:
            raise RuntimeError(
                f"CorpusConnectionManager for {self._db_path!r} is not open. Call await manager.open() first."
            )
        return self._write_conn

    async def open(self) -> None:
        """Open the write connection and the read pool.

        Idempotent: calling open() twice is a no-op. Sets WAL mode and
        busy_timeout on all connections for safe concurrent access.

        Raises:
            aiosqlite.Error: if the database cannot be opened.
        """
        if self._opened:
            return
        if self._closed:
            raise RuntimeError(f"CorpusConnectionManager for {self._db_path!r} is closed and cannot be reopened.")

        # Open write connection
        self._write_conn = await aiosqlite.connect(self._db_path)
        self._write_conn.row_factory = sqlite3.Row
        await self._write_conn.execute("PRAGMA journal_mode=WAL")
        await self._write_conn.execute("PRAGMA busy_timeout=5000")
        # write connection is NOT query_only — it's the writer

        # Open read pool
        for _ in range(self._read_pool_size):
            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA query_only = ON")
            self._read_pool.append(conn)
            self._read_pool_available.append(True)

        self._opened = True
        logger.debug(
            "corpus_connection_manager_opened db_path=%s write_conn=1 read_pool=%d",
            self._db_path,
            self._read_pool_size,
        )

    async def acquire_read(self) -> aiosqlite.Connection:
        """Check out a read connection from the shared pool.

        Returns a read-only connection if available, otherwise falls back to
        the write connection (with a warning log). Never blocks.

        Callers MUST call release_read(conn) when done so the connection
        returns to the pool. If the returned conn is the write connection
        (fallback), release_read() is a no-op.
        """
        if not self._opened:
            raise RuntimeError(f"CorpusConnectionManager for {self._db_path!r} is not open.")

        self._checkout_count += 1

        async with self._read_pool_lock:
            for i, available in enumerate(self._read_pool_available):
                if available and i < len(self._read_pool):
                    self._read_pool_available[i] = False
                    conn = self._read_pool[i]
                    # Verify connection is still alive
                    try:
                        await conn.execute("SELECT 1")
                        return conn
                    except Exception:
                        # Stale connection — recreate it
                        try:
                            await conn.close()
                        except Exception:
                            pass
                        new_conn = await aiosqlite.connect(self._db_path)
                        new_conn.row_factory = sqlite3.Row
                        await new_conn.execute("PRAGMA journal_mode=WAL")
                        await new_conn.execute("PRAGMA busy_timeout=5000")
                        await new_conn.execute("PRAGMA query_only = ON")
                        self._read_pool[i] = new_conn
                        return new_conn

        # All read connections in use — fall back to write connection
        self._exhaustion_count += 1
        self._fallback_count += 1
        logger.debug(
            "corpus_read_pool_exhausted db_path=%s -- falling back to write conn",
            self._db_path,
        )
        assert self._write_conn is not None  # opened check above
        return self._write_conn

    def release_read(self, conn: aiosqlite.Connection) -> None:
        """Return a read connection to the pool.

        If the connection is a pool member, marks it as available.
        If it's the write connection (fallback), this is a no-op.
        """
        for i, pool_conn in enumerate(self._read_pool):
            if pool_conn is conn:
                self._read_pool_available[i] = True
                return
        # Not a pool connection (was a write-conn fallback) — no action needed

    async def wal_checkpoint(self) -> None:
        """Run PRAGMA wal_checkpoint(TRUNCATE) on the write connection.

        Used by delete_corpus (§A13) and backup (§9.7 Option A) to flush
        the WAL sidecar before renaming/copying the db file.

        MUST be called under the CorpusStores.write_lock — this blocks
        until all readers drain.
        """
        if not self._opened or self._write_conn is None:
            raise RuntimeError(f"CorpusConnectionManager for {self._db_path!r} is not open.")
        await self._write_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def health(self) -> dict:
        """Return telemetry for /health endpoints."""
        pool_active = sum(1 for avail in self._read_pool_available if not avail)
        checkout_count = self._checkout_count
        exhaustion_rate = self._exhaustion_count / checkout_count if checkout_count > 0 else 0.0
        return {
            "db_path": self._db_path,
            "opened": self._opened,
            "closed": self._closed,
            "read_pool_size": self._read_pool_size,
            "read_pool_active": pool_active,
            "checkout_count": checkout_count,
            "fallback_count": self._fallback_count,
            "exhaustion_count": self._exhaustion_count,
            "exhaustion_rate": round(exhaustion_rate, 4),
        }

    async def close(self) -> None:
        """Close all connections. Idempotent.

        Closes the read pool first, then the write connection. Logs but
        does not raise on individual close failures (best-effort cleanup).
        """
        if self._closed:
            return
        self._closed = True
        self._opened = False

        # Close read pool
        for conn in self._read_pool:
            try:
                await conn.close()
            except Exception as exc:
                logger.debug(
                    "corpus_read_conn_close_failed db_path=%s error=%s",
                    self._db_path,
                    exc,
                )
        self._read_pool = []
        self._read_pool_available = []

        # Close write connection
        if self._write_conn is not None:
            try:
                await self._write_conn.close()
            except Exception as exc:
                logger.debug(
                    "corpus_write_conn_close_failed db_path=%s error=%s",
                    self._db_path,
                    exc,
                )
            self._write_conn = None

        logger.debug("corpus_connection_manager_closed db_path=%s", self._db_path)
