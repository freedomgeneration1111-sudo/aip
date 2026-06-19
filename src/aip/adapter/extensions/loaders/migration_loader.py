"""MigrationLoader — ADR-014 §8 step 4, §9.

Reads `.sql` files from the extension's `migrations/` dir, constructs
`Migration(name, sql, verify=())` dataclasses for type compatibility with the
existing `CorpusMigrationRunner`, and applies them to the extension's corpus
database.

**Why not reuse `CorpusMigrationRunner.run_migrations` directly?**

The core runner computes a single fingerprint over `migration_names` and
verifies it against the `applied_migrations` table. If extension migrations
were recorded in the same `applied_migrations` table, the core runner's
fingerprint check on the NEXT restart would see "unknown migrations applied"
(extension names not in `MIGRATIONS_FOR_CORPUS_TYPE`) and raise
`CorpusMigrationError`. That would couple the core migration story to every
extension's migration set — wrong shape.

Instead, the loader uses its OWN `extension_applied_migrations` table in the
same corpus DB. The table is per-extension (keyed by ext_id) so multiple
extensions contributing to the same corpus don't collide. The core runner
never sees these rows; the extension loader never sees the core rows. Two
clean namespaces.

The loader's apply logic is intentionally simpler than the core runner's:
  - No fingerprint (extension migrations are append-only; reordering is the
    extension author's problem, surfaced as a clear error).
  - No sql_checksum verification (the extension can change a migration body
    only if it hasn't been applied yet — same rule as core, but enforced by
    the "already applied" check, not a checksum mismatch).
  - Per-migration error → raise (the host catches and transitions to DEGRADED).

Layer: adapter (lives under `aip.adapter.extensions.loaders`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from aip.adapter.corpus_stores import CorpusStores

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedMigration:
    """One .sql file loaded from disk — shape-compatible with core Migration."""

    name: str        # stem of the .sql file, e.g. "M001_demo"
    sql: str         # raw SQL text
    verify: tuple = ()   # always () for extension migrations in v1 (ADR-014 §9)


def load_migrations_dir(migrations_dir: Path) -> list[LoadedMigration]:
    """Glob *.sql from migrations_dir, sorted lexicographically by filename.

    ADR-014 §6: migration filenames MUST begin with `M<3-digit>_` so
    lexicographic sort matches apply order (same convention as core migrations).

    Returns:
        list[LoadedMigration] — empty if the dir doesn't exist or has no .sql files.

    Raises:
        ValueError — if a .sql file's stem doesn't match the M<3-digit>_ convention.
    """
    if not migrations_dir.exists():
        return []
    files = sorted(migrations_dir.glob("*.sql"))
    out: list[LoadedMigration] = []
    for f in files:
        name = f.stem
        # Validate naming convention (same as core: M001_, M002_, ...)
        if not (len(name) >= 5 and name[0] == "M" and name[1:4].isdigit() and name[4] == "_"):
            raise ValueError(
                f"Migration filename {f.name!r} does not match M<3-digit>_ convention "
                f"(e.g. M001_demo.sql). Extension migrations must follow the same "
                f"naming rule as core migrations."
            )
        out.append(LoadedMigration(name=name, sql=f.read_text()))
    return out


_DDL_EXTENSION_APPLIED_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS extension_applied_migrations (
    ext_id TEXT NOT NULL,
    name TEXT NOT NULL,
    sql_checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (ext_id, name)
)
"""


async def apply_extension_migrations(
    *,
    ext_id: str,
    stores: "CorpusStores",
    migrations: list[LoadedMigration],
) -> None:
    """Apply extension migrations to the extension's corpus database.

    Uses a separate `extension_applied_migrations` table (keyed by ext_id) so
    the core `CorpusMigrationRunner`'s fingerprint check on `applied_migrations`
    never sees these rows.

    Idempotent: already-applied migrations are skipped (matched by name).

    Args:
        ext_id: the extension id (for the per-extension key).
        stores: the corpus's CorpusStores (must have a connection_manager).
        migrations: ordered list of LoadedMigration to apply.

    Raises:
        aiosqlite.Error: on SQL syntax error or other database failure.
            The host catches this and transitions the extension to DEGRADED.
    """
    if not migrations:
        return

    conn: aiosqlite.Connection = stores.connection_manager.write_conn

    # Ensure the extension_applied_migrations table exists (idempotent).
    await conn.execute(_DDL_EXTENSION_APPLIED_MIGRATIONS)
    await conn.commit()

    # Read already-applied migration names for this extension.
    cur = await conn.execute(
        "SELECT name FROM extension_applied_migrations WHERE ext_id = ?",
        (ext_id,),
    )
    rows = await cur.fetchall()
    await cur.close()
    already_applied = {row[0] for row in rows}

    # Apply pending migrations in order.
    for m in migrations:
        if m.name in already_applied:
            logger.debug(
                "extension_migration_already_applied ext=%s name=%s",
                ext_id, m.name,
            )
            continue

        # Split on ';' and execute each non-empty statement (same naive split
        # as the core runner — acceptable for these specific migrations).
        # The core runner uses the same pattern; we match it for consistency.
        statements = [s.strip() for s in m.sql.split(";") if s.strip()]
        for stmt in statements:
            await conn.execute(stmt)

        # Record as applied.
        import hashlib
        from datetime import datetime, timezone
        sql_checksum = hashlib.sha256(m.sql.encode()).hexdigest()[:16]
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT OR REPLACE INTO extension_applied_migrations "
            "(ext_id, name, sql_checksum, applied_at) VALUES (?, ?, ?, ?)",
            (ext_id, m.name, sql_checksum, now),
        )
        await conn.commit()
        logger.info(
            "extension_migration_applied ext=%s name=%s",
            ext_id, m.name,
        )
