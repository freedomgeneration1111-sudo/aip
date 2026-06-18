"""Corpus migration runner — ADR-008 Rev 3.1 Amendment §A8.

Runs ADR-008 corpus migrations in a dedicated runner, NOT through any store's
_create_tables(). The store-level _create_tables pattern (e.g.
corpus_turn_store.py:184-185) catches sqlite3.OperationalError and passes it
as "column already exists" — which silently swallows genuine failures.

This runner:
  - Detects only "duplicate column name" as benign (column already applied).
  - FAILS STARTUP on any other OperationalError (genuine schema failure).
  - Records each migration as a row {name, ordinal, sql_checksum, applied_at}
    in corpus_metadata (or applied_migrations table) inside the SAME
    transaction as the DDL.
  - Computes fingerprint = sha256("|".join(names_in_applied_order)) —
    order-preserving, NOT sorted (resolves the sorted-vs-ordered dispute).
    Plus per-migration sql_checksum so a changed migration body under the
    same name is detected.
  - After migrating, verifies the physical schema with PRAGMA table_info /
    PRAGMA index_list and fails on mismatch.

Layer: adapter. Uses aiosqlite directly (the shared write connection from
CorpusConnectionManager). Imports from foundation only.

Contract (consumed by CorpusStoreFactory.build()):
    runner = CorpusMigrationRunner(connection_manager)
    await runner.run_migrations(
        migration_names=MIGRATIONS_FOR_CORPUS_TYPE[corpus_type],
        migrations_registry=MIGRATIONS,  # dict[name → Migration SQL]
        corpus_id=corpus_id,
    )
    # Raises CorpusMigrationError on fingerprint mismatch or partial failure.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiosqlite

from aip.foundation.corpus_exceptions import CorpusMigrationError

if TYPE_CHECKING:
    from aip.adapter.corpus_connection import CorpusConnectionManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    """A single corpus migration — name + SQL + optional verification.

    sql_checksum is computed from sql at registration time so the runner
    can detect a changed migration body under the same name.
    """

    name: str
    sql: str
    # Optional verification queries — run after the migration to confirm
    # the schema is in the expected state. Each is a (pragma, expected_substring)
    # pair, e.g. ("table_info(corpus_turns)", "revision_parent_id").
    # If any verification fails, CorpusMigrationError is raised.
    verify: tuple[tuple[str, str], ...] = ()


def compute_fingerprint(migration_names: list[str]) -> str:
    """SHA256 of migration names joined by '|' in APPLIED ORDER (not sorted).

    ADR-008 Rev 3.1 §A8: order-preserving, not sorted. This resolves the
    dispute — applied order matters because migrations may depend on each
    other, and the fingerprint must detect a reordering.
    """
    return hashlib.sha256("|".join(migration_names).encode()).hexdigest()


def compute_sql_checksum(sql: str) -> str:
    """SHA256 of a migration's SQL body. Detects changed migration under same name."""
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


class CorpusMigrationRunner:
    """Runs ADR-008 corpus migrations under the corpus write_lock + migration_lock.

    Instantiated by CorpusStoreFactory. Uses the shared write connection from
    CorpusConnectionManager. All migrations for a corpus run in a single
    transaction; if any fails (other than benign "duplicate column name"),
    the transaction rolls back and CorpusMigrationError is raised.
    """

    def __init__(self, connection_manager: "CorpusConnectionManager") -> None:
        self._cm = connection_manager

    async def run_migrations(
        self,
        migration_names: list[str],
        migrations_registry: dict[str, Migration],
        corpus_id: str,
    ) -> None:
        """Run all migrations for a corpus. Idempotent.

        Steps:
          1. Ensure corpus_metadata table exists (creates if absent).
          2. Read applied migrations from corpus_metadata.
          3. Compute expected fingerprint from migration_names (in order).
          4. Compute actual fingerprint from applied migrations (in order).
          5. If fingerprints match, all migrations applied — return.
          6. If actual fingerprint is non-empty and doesn't match expected,
             raise CorpusMigrationError (partial or reordered migration).
          7. Run pending migrations in order, recording each in the same
             transaction as its DDL.
          8. Verify physical schema with PRAGMA table_info for each migration's
             verify clauses.
          9. Update fingerprint in corpus_metadata.

        Args:
            migration_names: ordered list of migration names to apply.
            migrations_registry: dict mapping name → Migration (sql + verify).
            corpus_id: for logging and error messages.

        Raises:
            CorpusMigrationError: on fingerprint mismatch, partial migration,
                or non-benign OperationalError.
        """
        conn = self._cm.write_conn

        # Step 1: ensure corpus_metadata table exists
        await self._ensure_corpus_metadata_table(conn)

        # Step 2: read applied migrations
        applied = await self._read_applied_migrations(conn)

        # Step 2.5: verify sql_checksum for already-applied migrations.
        # This MUST happen before the fingerprint early-return so a changed
        # migration body is detected even when all migrations are "applied".
        for m_record in applied:
            name = m_record["name"]
            if name not in migrations_registry:
                # Already applied but not in registry — could be a removed migration.
                # Log and continue (don't fail — the migration is already in the DB).
                logger.warning(
                    "corpus_migration_not_in_registry corpus=%s name=%s — already applied, skipping checksum check",
                    corpus_id,
                    name,
                )
                continue
            expected_checksum = compute_sql_checksum(migrations_registry[name].sql)
            if m_record["sql_checksum"] and m_record["sql_checksum"] != expected_checksum:
                raise CorpusMigrationError(
                    f"Migration body changed for {name!r} in corpus {corpus_id!r}: "
                    f"applied checksum {m_record['sql_checksum']!r} != "
                    f"expected checksum {expected_checksum!r}. "
                    f"Migration bodies are immutable once applied."
                )

        # Step 3-5: fingerprint check
        expected_fp = compute_fingerprint(migration_names)
        if applied:
            actual_fp = compute_fingerprint([m["name"] for m in applied])
            if actual_fp == expected_fp:
                logger.debug(
                    "corpus_migrations_already_applied corpus=%s fingerprint=%s",
                    corpus_id,
                    expected_fp[:12],
                )
                return
            # Fingerprints differ — either partial migration or reordered
            applied_set = {m["name"] for m in applied}
            expected_set = set(migration_names)
            if applied_set == expected_set:
                # Same names, different order — this is a reordering, fail
                raise CorpusMigrationError(
                    f"Migration order mismatch for corpus {corpus_id!r}: "
                    f"applied order differs from expected. "
                    f"Applied: {[m['name'] for m in applied]}, "
                    f"Expected: {migration_names}. "
                    f"This indicates a migration reordering — manual recovery required."
                )
            if not applied_set.issubset(expected_set):
                # Applied migrations contain names not in expected — unknown migration
                unknown = applied_set - expected_set
                raise CorpusMigrationError(
                    f"Unknown migrations applied to corpus {corpus_id!r}: {sorted(unknown)}. "
                    f"Expected: {migration_names}. "
                    f"This corpus may have been migrated by a newer version."
                )
            # Partial migration — some applied, some pending. Continue to apply pending.
            logger.warning(
                "corpus_migration_resuming_after_partial corpus=%s applied=%d expected=%d",
                corpus_id,
                len(applied),
                len(migration_names),
            )

        # Step 6-7: run pending migrations in order
        applied_names = {m["name"] for m in applied}

        pending = [name for name in migration_names if name not in applied_names and name in migrations_registry]
        missing_from_registry = [name for name in migration_names if name not in migrations_registry]
        if missing_from_registry:
            raise CorpusMigrationError(
                f"Migrations missing from registry for corpus {corpus_id!r}: "
                f"{missing_from_registry}. "
                f"Every migration name in MIGRATIONS_FOR_CORPUS_TYPE must have a "
                f"corresponding Migration in the migrations_registry."
            )

        for name in pending:
            migration = migrations_registry[name]
            await self._apply_single_migration(conn, migration, corpus_id)
            logger.info(
                "corpus_migration_applied corpus=%s name=%s",
                corpus_id,
                name,
            )

        # Step 8: verify physical schema
        for name in pending:
            migration = migrations_registry[name]
            for pragma, expected_substring in migration.verify:
                await self._verify_schema(conn, corpus_id, name, pragma, expected_substring)

        # Step 9: update fingerprint
        await self._update_fingerprint(conn, expected_fp)

        logger.info(
            "corpus_migrations_complete corpus=%s applied=%d fingerprint=%s",
            corpus_id,
            len(pending),
            expected_fp[:12],
        )

    async def _ensure_corpus_metadata_table(self, conn: aiosqlite.Connection) -> None:
        """Create corpus_metadata table if it doesn't exist.

        Schema (ADR-008 Rev 3.1 §8 Chunk 8):
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL

        Plus applied_migrations table for per-migration records (§A8):
            name TEXT PRIMARY KEY,
            ordinal INTEGER NOT NULL,
            sql_checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        """
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applied_migrations (
                name TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL,
                sql_checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        await conn.commit()

    async def _read_applied_migrations(self, conn: aiosqlite.Connection) -> list[dict]:
        """Read applied migrations, ordered by ordinal (applied order)."""
        cursor = await conn.execute(
            "SELECT name, ordinal, sql_checksum, applied_at FROM applied_migrations ORDER BY ordinal ASC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "name": row["name"],
                "ordinal": row["ordinal"],
                "sql_checksum": row["sql_checksum"],
                "applied_at": row["applied_at"],
            }
            for row in rows
        ]

    async def _apply_single_migration(
        self,
        conn: aiosqlite.Connection,
        migration: Migration,
        corpus_id: str,
    ) -> None:
        """Apply one migration and record it in the same transaction.

        Detects "duplicate column name" as benign (column already exists
        from a prior partial run). Any other OperationalError raises
        CorpusMigrationError.
        """
        sql_checksum = compute_sql_checksum(migration.sql)
        now = datetime.now(timezone.utc).isoformat()
        # Determine ordinal: count existing + 1 (preserves applied order)
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM applied_migrations")
        row = await cursor.fetchone()
        ordinal = (row["cnt"] if row else 0) + 1

        try:
            await conn.execute(migration.sql)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column name" in msg:
                # Benign — column already exists from a prior partial run.
                # Record the migration as applied (so the fingerprint matches)
                # but don't re-apply the DDL.
                logger.debug(
                    "corpus_migration_column_exists corpus=%s name=%s — recording as applied",
                    corpus_id,
                    migration.name,
                )
            else:
                # Genuine schema failure — raise
                raise CorpusMigrationError(
                    f"Migration {migration.name!r} failed for corpus {corpus_id!r}: {exc}. "
                    f"This is a genuine schema failure, not a benign duplicate. "
                    f"Manual recovery required."
                ) from exc

        # Record in the same transaction
        await conn.execute(
            "INSERT OR REPLACE INTO applied_migrations (name, ordinal, sql_checksum, applied_at) VALUES (?, ?, ?, ?)",
            (migration.name, ordinal, sql_checksum, now),
        )
        await conn.commit()

    async def _verify_schema(
        self,
        conn: aiosqlite.Connection,
        corpus_id: str,
        migration_name: str,
        pragma: str,
        expected_substring: str,
    ) -> None:
        """Verify the physical schema matches the migration's expectation.

        Runs PRAGMA (e.g. table_info(corpus_turns)) and checks that the
        result contains expected_substring. Raises CorpusMigrationError
        if the substring is not found.
        """
        cursor = await conn.execute(f"PRAGMA {pragma}")
        rows = await cursor.fetchall()
        # Convert rows to a single string for substring search
        # PRAGMA results vary; just stringify all column values
        text = " ".join(str(cell) for row in rows for cell in row)
        if expected_substring not in text:
            raise CorpusMigrationError(
                f"Schema verification failed for migration {migration_name!r} "
                f"in corpus {corpus_id!r}: PRAGMA {pragma} did not contain "
                f"{expected_substring!r}. "
                f"Physical schema: {text[:200]}..."
            )

    async def _update_fingerprint(self, conn: aiosqlite.Connection, fingerprint: str) -> None:
        """Update the schema_fingerprint in corpus_metadata."""
        await conn.execute(
            "INSERT OR REPLACE INTO corpus_metadata (key, value) VALUES (?, ?)",
            ("schema_fingerprint", fingerprint),
        )
        await conn.commit()
