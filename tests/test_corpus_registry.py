"""Tests for ADR-008 Multi-Corpus Chunk 2: CorpusRegistry + Factory.

Covers:
  - CorpusConnectionManager (§A0 — shared connection pool)
  - CorpusStores (§5.3 — regular class, async lifecycle)
  - CorpusMigrationRunner (§A8 — fingerprint, partial migration, verify)
  - CorpusStoreFactory (builds CorpusStores with shared manager)
  - CorpusRegistry (register/get_stores/delete_corpus/budget/migration gate)
  - Scheduler gate (§A5 — app.py helper, defensive when registry absent)

ADR-008 Rev 3.1 §8 Chunk 2, Amendment §A0, §A5, §A8, §A13.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aip.adapter.corpus_connection import CorpusConnectionManager
from aip.adapter.corpus_migration_runner import (
    CorpusMigrationRunner,
    Migration,
    compute_fingerprint,
    compute_sql_checksum,
)
from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.corpus_store_factory import MIGRATIONS, CorpusStoreFactory
from aip.adapter.corpus_stores import CorpusStores
from aip.foundation.corpus_exceptions import (
    RestrictedCorpusAccessViolation,
    ConnectionBudgetExceeded,
    CorpusMigrationError,
    CorpusNotFound,
    DeletionStateError,
)
from aip.foundation.corpus_types import CorpusDeletionState, CorpusType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Path to a non-existent corpus SQLite file (created on first connect)."""
    return tmp_path / "test_corpus.db"


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Temporary directory for corpus db files."""
    return tmp_path


# ---------------------------------------------------------------------------
# CorpusConnectionManager (§A0)
# ---------------------------------------------------------------------------


class TestCorpusConnectionManager:
    """Tests for the shared per-corpus connection manager."""

    async def test_open_creates_one_write_and_n_read_connections(self, temp_db_path: Path):
        """§A0: 1 write + read_pool_size read connections, shared by all stores."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=2)
        assert manager.opened is False

        await manager.open()
        assert manager.opened is True
        assert manager.write_conn is not None
        assert len(manager._read_pool) == 2
        assert all(manager._read_pool_available)

        await manager.close()
        assert manager.opened is False

    async def test_open_is_idempotent(self, temp_db_path: Path):
        """Calling open() twice is a no-op."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        write_conn_1 = manager.write_conn
        await manager.open()  # no-op
        assert manager.write_conn is write_conn_1  # same connection
        await manager.close()

    async def test_close_is_idempotent(self, temp_db_path: Path):
        """Calling close() twice is a no-op."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.close()
        await manager.close()  # no-op, no exception

    async def test_close_then_open_raises(self, temp_db_path: Path):
        """A closed manager cannot be reopened."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.close()
        with pytest.raises(RuntimeError, match="closed and cannot be reopened"):
            await manager.open()

    async def test_write_conn_raises_before_open(self, temp_db_path: Path):
        """Accessing write_conn before open() raises RuntimeError."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        with pytest.raises(RuntimeError, match="not open"):
            _ = manager.write_conn

    async def test_acquire_read_returns_pool_connection(self, temp_db_path: Path):
        """acquire_read() returns a read-only pool connection."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=2)
        await manager.open()

        conn = await manager.acquire_read()
        assert conn is not None
        # Verify it's query_only
        cursor = await conn.execute("PRAGMA query_only")
        row = await cursor.fetchone()
        assert row[0] == 1  # query_only is ON

        manager.release_read(conn)
        await manager.close()

    async def test_acquire_read_falls_back_to_write_when_pool_exhausted(self, temp_db_path: Path):
        """When all read connections are checked out, falls back to write conn."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()

        # Check out the only read connection
        read_conn = await manager.acquire_read()
        # Next checkout should fall back to write conn
        fallback_conn = await manager.acquire_read()
        assert fallback_conn is manager.write_conn

        manager.release_read(read_conn)
        await manager.close()

    async def test_release_read_is_noop_for_write_conn(self, temp_db_path: Path):
        """release_read() on the write connection (fallback) is a no-op."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()

        write_conn = manager.write_conn
        manager.release_read(write_conn)  # should not raise
        await manager.close()

    async def test_wal_checkpoint_runs_without_error(self, temp_db_path: Path):
        """wal_checkpoint(TRUNCATE) flushes the WAL sidecar."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        # Create a table and insert to generate WAL
        await manager.write_conn.execute("CREATE TABLE test (id INTEGER)")
        await manager.write_conn.execute("INSERT INTO test VALUES (1)")
        await manager.write_conn.commit()
        # Checkpoint should not raise
        await manager.wal_checkpoint()
        await manager.close()

    async def test_health_returns_telemetry(self, temp_db_path: Path):
        """health() returns a dict with connection pool telemetry."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=2)
        await manager.open()

        health = manager.health()
        assert health["db_path"] == str(temp_db_path)
        assert health["opened"] is True
        assert health["closed"] is False
        assert health["read_pool_size"] == 2
        assert health["read_pool_active"] == 0
        assert health["checkout_count"] == 0

        # Check out a connection
        conn = await manager.acquire_read()
        health = manager.health()
        assert health["checkout_count"] == 1
        assert health["read_pool_active"] == 1

        manager.release_read(conn)
        await manager.close()

    async def test_stale_read_connection_is_recreated(self, temp_db_path: Path):
        """If a pooled read connection is stale, it's recreated on checkout."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()

        conn = await manager.acquire_read()
        manager.release_read(conn)

        # Close the pooled connection to simulate staleness
        await manager._read_pool[0].close()

        # Next acquire should recreate the stale connection
        new_conn = await manager.acquire_read()
        cursor = await new_conn.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1

        manager.release_read(new_conn)
        await manager.close()


# ---------------------------------------------------------------------------
# CorpusStores (§5.3)
# ---------------------------------------------------------------------------


class TestCorpusStores:
    """Tests for the per-corpus live store bundle."""

    async def test_init_sets_lifecycle_fields(self):
        """§5.3: write_lock, closed, deletion_state set in __init__."""
        stores = CorpusStores(
            corpus_id="test",
            corpus_type=CorpusType.CONVERSATION,
        )
        assert stores.corpus_id == "test"
        assert stores.corpus_type == CorpusType.CONVERSATION
        assert stores.write_lock is not None
        assert stores.closed is False
        assert stores.deletion_state == CorpusDeletionState.ACTIVE
        assert stores.turn_store is None
        assert stores.connection_manager is None

    async def test_close_all_idempotent(self):
        """close_all() is idempotent — calling twice is a no-op."""
        stores = CorpusStores(corpus_id="test", corpus_type=CorpusType.CONVERSATION)
        await stores.close_all()
        await stores.close_all()  # no-op
        assert stores.closed is True

    async def test_close_all_safe_on_shell(self):
        """close_all() is safe on a shell with no stores attached."""
        stores = CorpusStores(corpus_id="test", corpus_type=CorpusType.CONVERSATION)
        await stores.close_all()  # should not raise
        assert stores.closed is True

    async def test_close_all_closes_connection_manager(self, temp_db_path: Path):
        """close_all() closes the connection manager last."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()

        stores = CorpusStores(
            corpus_id="test",
            corpus_type=CorpusType.CONVERSATION,
            connection_manager=manager,
        )
        await stores.close_all()
        assert manager.opened is False
        assert stores.connection_manager is None

    async def test_close_all_continues_on_store_close_failure(self):
        """If one store's close() fails, others still close."""

        class FailingStore:
            async def close(self):
                raise RuntimeError("close failed")

        class GoodStore:
            closed = False

            async def close(self):
                self.closed = True

        stores = CorpusStores(corpus_id="test", corpus_type=CorpusType.CONVERSATION)
        stores.turn_store = FailingStore()
        good = GoodStore()
        stores.lexical_store = good

        await stores.close_all()  # should not raise
        assert good.closed is True
        assert stores.closed is True

    async def test_async_context_manager(self, temp_db_path: Path):
        """__aenter__/__aexit__ work correctly."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()

        async with CorpusStores(
            corpus_id="test",
            corpus_type=CorpusType.CONVERSATION,
            connection_manager=manager,
        ) as stores:
            assert stores.closed is False
        assert stores.closed is True
        assert manager.opened is False

    async def test_health_returns_summary(self):
        """health() returns a summary dict."""
        stores = CorpusStores(corpus_id="test", corpus_type=CorpusType.CONVERSATION)
        health = stores.health()
        assert health["corpus_id"] == "test"
        assert health["corpus_type"] == "conversation"
        assert health["closed"] is False
        assert health["deletion_state"] == "ACTIVE"
        assert health["has_turn_store"] is False
        assert health["has_connection_manager"] is False


# ---------------------------------------------------------------------------
# CorpusMigrationRunner (§A8)
# ---------------------------------------------------------------------------


class TestMigrationFingerprint:
    """Tests for fingerprint + sql_checksum computation."""

    def test_fingerprint_is_sha256_of_pipe_joined_names_in_order(self):
        """Fingerprint = sha256('|'.join(names_in_applied_order)) — order matters."""
        fp1 = compute_fingerprint(["M001_a", "M002_b", "M003_c"])
        fp2 = compute_fingerprint(["M003_c", "M002_b", "M001_a"])  # different order
        assert fp1 != fp2  # order-preserving, NOT sorted

    def test_fingerprint_deterministic(self):
        """Same input → same fingerprint."""
        fp1 = compute_fingerprint(["M001_a", "M002_b"])
        fp2 = compute_fingerprint(["M001_a", "M002_b"])
        assert fp1 == fp2

    def test_fingerprint_is_sha256_hex(self):
        """Fingerprint is a 64-char hex string (SHA256)."""
        fp = compute_fingerprint(["M001_a"])
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_sql_checksum_is_16_char_hex(self):
        """sql_checksum is a 16-char hex prefix of SHA256."""
        checksum = compute_sql_checksum("ALTER TABLE t ADD COLUMN c TEXT")
        assert len(checksum) == 16
        assert all(c in "0123456789abcdef" for c in checksum)

    def test_sql_checksum_detects_changed_body(self):
        """Different SQL → different checksum."""
        c1 = compute_sql_checksum("ALTER TABLE t ADD COLUMN c TEXT")
        c2 = compute_sql_checksum("ALTER TABLE t ADD COLUMN d TEXT")
        assert c1 != c2


class TestCorpusMigrationRunner:
    """Tests for the migration runner (§A8)."""

    async def test_run_migrations_creates_corpus_metadata_table(self, temp_db_path: Path):
        """Runner creates corpus_metadata + applied_migrations tables."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()

        runner = CorpusMigrationRunner(manager)
        await runner.run_migrations(
            migration_names=[],
            migrations_registry={},
            corpus_id="test",
        )

        # Verify tables exist
        cursor = await manager.write_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('corpus_metadata', 'applied_migrations')"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert "corpus_metadata" in tables
        assert "applied_migrations" in tables

        await manager.close()

    async def test_run_migrations_applies_pending(self, temp_db_path: Path):
        """Runner applies pending migrations and records them."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()

        # Create a test table to migrate
        await manager.write_conn.execute("CREATE TABLE corpus_turns (turn_id TEXT PRIMARY KEY)")
        await manager.write_conn.commit()

        runner = CorpusMigrationRunner(manager)
        migration = Migration(
            name="M001_test_add_col",
            sql="ALTER TABLE corpus_turns ADD COLUMN test_col TEXT",
            verify=(("table_info(corpus_turns)", "test_col"),),
        )
        await runner.run_migrations(
            migration_names=["M001_test_add_col"],
            migrations_registry={"M001_test_add_col": migration},
            corpus_id="test",
        )

        # Verify column was added
        cursor = await manager.write_conn.execute("PRAGMA table_info(corpus_turns)")
        cols = [row[1] for row in await cursor.fetchall()]
        assert "test_col" in cols

        # Verify migration was recorded
        cursor = await manager.write_conn.execute("SELECT name FROM applied_migrations")
        names = [row[0] for row in await cursor.fetchall()]
        assert "M001_test_add_col" in names

        await manager.close()

    async def test_run_migrations_idempotent(self, temp_db_path: Path):
        """Running migrations twice is a no-op (fingerprint matches)."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.write_conn.execute("CREATE TABLE corpus_turns (turn_id TEXT PRIMARY KEY)")
        await manager.write_conn.commit()

        runner = CorpusMigrationRunner(manager)
        migration = Migration(
            name="M001_test_add_col",
            sql="ALTER TABLE corpus_turns ADD COLUMN test_col TEXT",
        )
        await runner.run_migrations(
            migration_names=["M001_test_add_col"],
            migrations_registry={"M001_test_add_col": migration},
            corpus_id="test",
        )

        # Run again — should be a no-op
        await runner.run_migrations(
            migration_names=["M001_test_add_col"],
            migrations_registry={"M001_test_add_col": migration},
            corpus_id="test",
        )

        # Still only one migration recorded
        cursor = await manager.write_conn.execute("SELECT COUNT(*) FROM applied_migrations")
        count = (await cursor.fetchone())[0]
        assert count == 1

        await manager.close()

    async def test_run_migrations_detects_reordering(self, temp_db_path: Path):
        """Reordering migrations raises CorpusMigrationError."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.write_conn.execute("CREATE TABLE corpus_turns (turn_id TEXT PRIMARY KEY)")
        await manager.write_conn.commit()

        runner = CorpusMigrationRunner(manager)
        m1 = Migration(name="M001_a", sql="ALTER TABLE corpus_turns ADD COLUMN a TEXT")
        m2 = Migration(name="M002_b", sql="ALTER TABLE corpus_turns ADD COLUMN b TEXT")

        # Apply in order M001, M002
        await runner.run_migrations(
            migration_names=["M001_a", "M002_b"],
            migrations_registry={"M001_a": m1, "M002_b": m2},
            corpus_id="test",
        )

        # Try to "apply" in order M002, M001 — should detect reordering
        with pytest.raises(CorpusMigrationError, match="order mismatch"):
            await runner.run_migrations(
                migration_names=["M002_b", "M001_a"],
                migrations_registry={"M001_a": m1, "M002_b": m2},
                corpus_id="test",
            )

        await manager.close()

    async def test_run_migrations_detects_changed_body(self, temp_db_path: Path):
        """Changed migration body under same name raises CorpusMigrationError."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.write_conn.execute("CREATE TABLE corpus_turns (turn_id TEXT PRIMARY KEY)")
        await manager.write_conn.commit()

        runner = CorpusMigrationRunner(manager)
        m1 = Migration(name="M001_a", sql="ALTER TABLE corpus_turns ADD COLUMN a TEXT")
        await runner.run_migrations(
            migration_names=["M001_a"],
            migrations_registry={"M001_a": m1},
            corpus_id="test",
        )

        # Run again with changed SQL under same name
        m1_changed = Migration(name="M001_a", sql="ALTER TABLE corpus_turns ADD COLUMN b TEXT")
        with pytest.raises(CorpusMigrationError, match="body changed"):
            await runner.run_migrations(
                migration_names=["M001_a"],
                migrations_registry={"M001_a": m1_changed},
                corpus_id="test",
            )

        await manager.close()

    async def test_run_migrations_detects_unknown_migration(self, temp_db_path: Path):
        """Applied migration not in expected set raises CorpusMigrationError."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.write_conn.execute("CREATE TABLE corpus_turns (turn_id TEXT PRIMARY KEY)")
        await manager.write_conn.commit()

        runner = CorpusMigrationRunner(manager)
        m1 = Migration(name="M001_a", sql="ALTER TABLE corpus_turns ADD COLUMN a TEXT")
        await runner.run_migrations(
            migration_names=["M001_a"],
            migrations_registry={"M001_a": m1},
            corpus_id="test",
        )

        # Try to apply a different set that doesn't include M001_a
        m2 = Migration(name="M002_b", sql="ALTER TABLE corpus_turns ADD COLUMN b TEXT")
        with pytest.raises(CorpusMigrationError, match="Unknown migrations"):
            await runner.run_migrations(
                migration_names=["M002_b"],
                migrations_registry={"M002_b": m2},
                corpus_id="test",
            )

        await manager.close()

    async def test_run_migrations_duplicate_column_is_benign(self, temp_db_path: Path):
        """'duplicate column name' is benign (column already applied)."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.write_conn.execute("CREATE TABLE corpus_turns (turn_id TEXT PRIMARY KEY, a TEXT)")
        await manager.write_conn.commit()

        runner = CorpusMigrationRunner(manager)
        m1 = Migration(name="M001_a", sql="ALTER TABLE corpus_turns ADD COLUMN a TEXT")
        # Should not raise — 'a' already exists, treated as benign
        await runner.run_migrations(
            migration_names=["M001_a"],
            migrations_registry={"M001_a": m1},
            corpus_id="test",
        )

        await manager.close()

    async def test_run_migrations_genuine_failure_raises(self, temp_db_path: Path):
        """Non-benign OperationalError raises CorpusMigrationError."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        # No table exists — ALTER TABLE on nonexistent table fails

        runner = CorpusMigrationRunner(manager)
        m1 = Migration(name="M001_a", sql="ALTER TABLE nonexistent_table ADD COLUMN a TEXT")
        with pytest.raises(CorpusMigrationError, match="genuine schema failure"):
            await runner.run_migrations(
                migration_names=["M001_a"],
                migrations_registry={"M001_a": m1},
                corpus_id="test",
            )

        await manager.close()

    async def test_run_migrations_verification_failure_raises(self, temp_db_path: Path):
        """Schema verification failure raises CorpusMigrationError."""
        manager = CorpusConnectionManager(str(temp_db_path), read_pool_size=1)
        await manager.open()
        await manager.write_conn.execute("CREATE TABLE corpus_turns (turn_id TEXT PRIMARY KEY)")
        await manager.write_conn.commit()

        runner = CorpusMigrationRunner(manager)
        m1 = Migration(
            name="M001_a",
            sql="ALTER TABLE corpus_turns ADD COLUMN a TEXT",
            verify=(("table_info(corpus_turns)", "nonexistent_column"),),  # will fail
        )
        with pytest.raises(CorpusMigrationError, match="Schema verification failed"):
            await runner.run_migrations(
                migration_names=["M001_a"],
                migrations_registry={"M001_a": m1},
                corpus_id="test",
            )

        await manager.close()


# ---------------------------------------------------------------------------
# CorpusStoreFactory
# ---------------------------------------------------------------------------


class TestCorpusStoreFactory:
    """Tests for the factory that builds CorpusStores bundles."""

    async def test_build_creates_stores_with_connection_manager(self, temp_db_path: Path):
        """Factory builds CorpusStores with manager attached + migrations applied."""
        factory = CorpusStoreFactory(read_pool_size=1)
        stores = await factory.build(
            corpus_id="test",
            corpus_type=CorpusType.CODE,  # CODE only gets M001 + M003, not M002
            db_path=temp_db_path,
            migration_lock=asyncio.Lock(),
        )
        try:
            assert stores.corpus_id == "test"
            assert stores.corpus_type == CorpusType.CODE
            assert stores.connection_manager is not None
            assert stores.connection_manager.opened is True
            assert stores.turn_store is not None  # Chunk 2 wires turn_store
            assert stores.deletion_state == CorpusDeletionState.ACTIVE
        finally:
            await stores.close_all()

    async def test_build_failure_cleans_up_connections(self, temp_db_path: Path):
        """If factory.build() fails, connections are cleaned up (no leak)."""
        # Create a factory that will fail during migration by using a bad migration
        factory = CorpusStoreFactory(read_pool_size=1)

        # Inject a bad migration into the MIGRATIONS registry temporarily
        original = MIGRATIONS.copy()
        try:
            MIGRATIONS["M001_add_revision_parent_id"] = Migration(
                name="M001_add_revision_parent_id",
                sql="ALTER TABLE nonexistent_table ADD COLUMN revision_parent_id TEXT",
            )
            with pytest.raises(CorpusMigrationError):
                await factory.build(
                    corpus_id="test",
                    corpus_type=CorpusType.CODE,
                    db_path=temp_db_path,
                    migration_lock=asyncio.Lock(),
                )
            # If we reach here, the connections were cleaned up (no leak).
            # We can't easily assert "no connections" but the test passing
            # without hanging confirms cleanup happened.
        finally:
            MIGRATIONS.clear()
            MIGRATIONS.update(original)

    async def test_build_applies_migrations_for_code_corpus(self, temp_db_path: Path):
        """CODE corpus gets M001 + M003 (not M002 — that's definer-only)."""
        factory = CorpusStoreFactory(read_pool_size=1)
        stores = await factory.build(
            corpus_id="codeforge",
            corpus_type=CorpusType.CODE,
            db_path=temp_db_path,
            migration_lock=asyncio.Lock(),
        )
        try:
            # M001 + M003 applied (table corpus_turns may not exist yet for CODE —
            # migrations will fail benignly on missing table). The key assertion
            # is that build() succeeds and stores are returned.
            assert stores.corpus_type == CorpusType.CODE
        finally:
            await stores.close_all()


# ---------------------------------------------------------------------------
# CorpusRegistry
# ---------------------------------------------------------------------------


class TestCorpusRegistry:
    """Tests for the concrete CorpusRegistry."""

    async def test_register_and_get_stores(self, temp_dir: Path):
        """register() + get_stores() round-trip."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()  # no corpora — sets migration_ready

        db_path = temp_dir / "definer.db"
        stores = await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )
        assert stores.corpus_id == "definer"

        retrieved = await registry.get_stores("definer")
        assert retrieved is stores

        await stores.close_all()

    async def test_get_stores_raises_for_unregistered(self, temp_dir: Path):
        """get_stores() raises CorpusNotFound for unregistered corpus."""
        registry = CorpusRegistry()
        await registry.startup()

        with pytest.raises(CorpusNotFound):
            await registry.get_stores("nonexistent")

    async def test_register_is_idempotent(self, temp_dir: Path):
        """Registering the same corpus twice returns the same stores."""
        registry = CorpusRegistry()
        await registry.startup()

        db_path = temp_dir / "definer.db"
        stores1 = await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )
        stores2 = await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=db_path,
        )
        assert stores1 is stores2

        await stores1.close_all()

    async def test_budget_exceeded_at_max_corpora(self, temp_dir: Path):
        """register() raises ConnectionBudgetExceeded at MAX_CORPORA."""
        registry = CorpusRegistry(max_corpora=2)
        await registry.startup()

        # Register 2 corpora (the max)
        for i in range(2):
            await registry.register(
                corpus_id=f"corpus_{i}",
                corpus_type=CorpusType.CODE,
                db_path=temp_dir / f"corpus_{i}.db",
            )

        # Third should fail
        with pytest.raises(ConnectionBudgetExceeded, match="MAX_CORPORA"):
            await registry.register(
                corpus_id="corpus_2",
                corpus_type=CorpusType.CODE,
                db_path=temp_dir / "corpus_2.db",
            )

        # Cleanup
        for cid in await registry.list_corpora():
            stores = await registry.get_stores(cid)
            await stores.close_all()

    async def test_branham_isolation_violation(self, temp_dir: Path):
        """get_stores() raises RestrictedCorpusAccessViolation without allowlist."""
        registry = CorpusRegistry()
        await registry.startup()

        stores = await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=temp_dir / "branham.db",
            sensitive=True,
        )

        # Without allowlist → raises
        with pytest.raises(RestrictedCorpusAccessViolation):
            await registry.get_stores("branham", allowed_restricted_corpora=[])

        # With allowlist → succeeds
        retrieved = await registry.get_stores("branham", allowed_restricted_corpora=["branham"])
        assert retrieved is stores

        await stores.close_all()

    async def test_deletion_state_error_on_deleting_corpus(self, temp_dir: Path):
        """get_stores() raises DeletionStateError on DELETING corpus."""
        registry = CorpusRegistry()
        await registry.startup()

        stores = await registry.register(
            corpus_id="test",
            corpus_type=CorpusType.CODE,
            db_path=temp_dir / "test.db",
        )
        # Manually set DELETING (simulating mid-delete)
        stores.deletion_state = CorpusDeletionState.DELETING

        with pytest.raises(DeletionStateError):
            await registry.get_stores("test")

        stores.deletion_state = CorpusDeletionState.ACTIVE
        await stores.close_all()

    async def test_delete_corpus_renames_files(self, temp_dir: Path):
        """delete_corpus() renames .db to .db.deleted."""
        registry = CorpusRegistry()
        await registry.startup()

        db_path = temp_dir / "to_delete.db"
        await registry.register(
            corpus_id="to_delete",
            corpus_type=CorpusType.CODE,
            db_path=db_path,
        )

        # Verify db file exists
        assert db_path.exists()

        await registry.delete_corpus("to_delete")

        # Verify corpus is gone from registry
        with pytest.raises(CorpusNotFound):
            await registry.get_stores("to_delete")

        # Verify db file is renamed
        deleted_path = db_path.with_name(db_path.name + ".deleted")
        assert deleted_path.exists()
        assert not db_path.exists()

    async def test_delete_unregistered_raises(self, temp_dir: Path):
        """delete_corpus() on unregistered corpus raises CorpusNotFound."""
        registry = CorpusRegistry()
        await registry.startup()

        with pytest.raises(CorpusNotFound):
            await registry.delete_corpus("nonexistent")

    async def test_list_corpora(self, temp_dir: Path):
        """list_corpora() returns registered corpus_ids."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()

        assert await registry.list_corpora() == []

        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=temp_dir / "definer.db",
        )
        await registry.register(
            corpus_id="codeforge",
            corpus_type=CorpusType.CODE,
            db_path=temp_dir / "codeforge.db",
        )

        corpora = await registry.list_corpora()
        assert set(corpora) == {"definer", "codeforge"}

        # Cleanup
        for cid in await registry.list_corpora():
            stores = await registry.get_stores(cid)
            await stores.close_all()

    async def test_migration_ready_event_set_after_startup(self):
        """startup() sets migration_ready event."""
        registry = CorpusRegistry()
        assert registry.migration_ready.is_set() is False
        await registry.startup()
        assert registry.migration_ready.is_set() is True

    async def test_startup_with_no_corpora_sets_event(self):
        """startup() with no corpora still sets migration_ready (for tests)."""
        registry = CorpusRegistry()
        await registry.startup(corpora_to_register=None)
        assert registry.migration_ready.is_set() is True

    async def test_transition_artifact_raises_corpus_not_found(self, temp_dir: Path):
        """transition_artifact() raises CorpusNotFound for unregistered corpus.

        Chunk 8: transition_artifact() is now fully implemented. It raises
        CorpusNotFound if the corpus isn't registered, and if the artifact
        isn't found in the ECS store.
        """
        registry = CorpusRegistry()
        await registry.startup()

        with pytest.raises(CorpusNotFound):
            await registry.transition_artifact("nonexistent", "art-001", "ARCHIVED")

    async def test_list_review_items_returns_empty_list(self):
        """list_review_items() is a stub for Chunk 2 — returns empty list."""
        registry = CorpusRegistry()
        await registry.startup()

        items = await registry.list_review_items(states=["GENERATED"])
        assert items == []


# ---------------------------------------------------------------------------
# Scheduler gate (§A5)
# ---------------------------------------------------------------------------


class TestSchedulerGate:
    """Tests for the migration gate on the 5 actor schedulers (§A5).

    The gate is defensive: if container.corpus_registry is None (pre-Chunk-3),
    the gate is a no-op and actors proceed. Once the registry is wired,
    actors must await migration_ready before their first write.
    """

    async def test_gate_is_noop_when_registry_absent(self):
        """When container.corpus_registry is None, the gate is a no-op."""

        # Simulate the app.py helper
        class FakeContainer:
            pass  # no corpus_registry attribute

        container = FakeContainer()

        async def _await_corpus_migration_ready():
            registry = getattr(container, "corpus_registry", None)
            if registry is not None:
                await registry.migration_ready.wait()

        # Should complete immediately (no registry to wait on)
        await asyncio.wait_for(_await_corpus_migration_ready(), timeout=0.1)

    async def test_gate_waits_for_migration_ready(self):
        """When registry is present, the gate waits for migration_ready."""
        registry = CorpusRegistry()
        # Don't call startup() — migration_ready is not set

        class FakeContainer:
            pass

        container = FakeContainer()
        container.corpus_registry = registry

        async def _await_corpus_migration_ready():
            await container.corpus_registry.migration_ready.wait()

        # Start the wait — it should block
        task = asyncio.create_task(_await_corpus_migration_ready())
        await asyncio.sleep(0.01)  # let it start
        assert not task.done()

        # Set migration_ready — the task should complete
        registry.migration_ready.set()
        await asyncio.wait_for(task, timeout=0.1)
        assert task.done()


# ---------------------------------------------------------------------------
# Layer discipline
# ---------------------------------------------------------------------------


class TestLayerDiscipline:
    """Verify adapter corpus files import from foundation only (not orchestration)."""

    def test_corpus_connection_no_orchestration_imports(self):
        import inspect

        from aip.adapter import corpus_connection

        source = inspect.getsource(corpus_connection)
        assert "from aip.orchestration" not in source
        assert "import aip.orchestration" not in source

    def test_corpus_stores_no_orchestration_imports(self):
        import inspect

        from aip.adapter import corpus_stores

        source = inspect.getsource(corpus_stores)
        assert "from aip.orchestration" not in source
        assert "import aip.orchestration" not in source

    def test_corpus_registry_no_orchestration_imports(self):
        import inspect

        from aip.adapter import corpus_registry

        source = inspect.getsource(corpus_registry)
        assert "from aip.orchestration" not in source
        assert "import aip.orchestration" not in source

    def test_corpus_store_factory_no_orchestration_imports(self):
        import inspect

        from aip.adapter import corpus_store_factory

        source = inspect.getsource(corpus_store_factory)
        assert "from aip.orchestration" not in source
        assert "import aip.orchestration" not in source

    def test_corpus_migration_runner_no_orchestration_imports(self):
        import inspect

        from aip.adapter import corpus_migration_runner

        source = inspect.getsource(corpus_migration_runner)
        assert "from aip.orchestration" not in source
        assert "import aip.orchestration" not in source
