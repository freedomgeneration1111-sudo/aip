"""AIP Seed Bootstrap — first-run corpus initialization.

Populates a new AIP install with:
  1. Graph seed nodes (28 entities), edges (5 domain bridges), default project
  2. AIP self-knowledge corpus (conversation JSON files)

Safe to run multiple times — all inserts use OR IGNORE.
Only runs when the DB/corpus is effectively empty and no seed sentinel exists.
Respects AIP_AUTO_SEED=false environment variable.

This module is called from scripts/start.sh before the backend launches.
It can also be invoked directly: python -m aip.cli seed_bootstrap
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger("aip.cli.seed_bootstrap")

# Paths relative to project root.
# When running as `python -m aip.cli._seed_bootstrap` from the project root,
# __file__ is src/aip/cli/_seed_bootstrap.py. We need 4 parents to reach the
# project root. We also check for examples/seed_corpus to validate.
def _find_repo_root() -> Path:
    """Find the project root by looking for examples/seed_corpus."""
    # Try relative to this file first
    candidate = Path(__file__).resolve().parent.parent.parent.parent
    if (candidate / "examples" / "seed_corpus").exists():
        return candidate
    # Try cwd
    cwd = Path.cwd()
    if (cwd / "examples" / "seed_corpus").exists():
        return cwd
    # Fall back to file-based calculation anyway
    return Path(__file__).resolve().parent.parent.parent.parent

_REPO_ROOT = _find_repo_root()
_SEED_DIR = _REPO_ROOT / "examples" / "seed_corpus"
_SQL_PATH = _SEED_DIR / "seed_bootstrap.sql"
_CONVERSATIONS_DIR = _SEED_DIR / "conversations"
_DB_DIR = _REPO_ROOT / "db"
_DB_PATH = _DB_DIR / "state.db"
_SENTINEL_PATH = _DB_DIR / ".seed_bootstrapped"


def _is_empty_db(db_path: Path) -> bool:
    """Check if the database is effectively empty (no corpus turns, no graph nodes)."""
    if not db_path.exists():
        return True
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            # Check graph_nodes count
            try:
                nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
                if nodes > 0:
                    return False
            except sqlite3.OperationalError:
                pass  # Table doesn't exist — DB is empty

            # Check corpus turns count
            try:
                turns = conn.execute("SELECT COUNT(*) FROM corpus_turns").fetchone()[0]
                if turns > 0:
                    return False
            except sqlite3.OperationalError:
                pass  # Table doesn't exist — DB is empty

            return True
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Error checking DB emptiness: %s", exc)
        return False


def _sentinel_exists() -> bool:
    """Check if the seed bootstrap sentinel file exists."""
    return _SENTINEL_PATH.exists()


def _write_sentinel() -> None:
    """Write the sentinel file after successful bootstrap."""
    try:
        _SENTINEL_PATH.write_text("seed_bootstrapped\n", encoding="utf-8")
        log.info("Seed bootstrap sentinel written: %s", _SENTINEL_PATH)
    except OSError as exc:
        log.error("Failed to write sentinel file: %s", exc)


def _run_sql_bootstrap(db_path: Path) -> bool:
    """Run the SQL bootstrap script (graph nodes, edges, default project)."""
    if not _SQL_PATH.exists():
        log.error("SQL bootstrap file not found: %s", _SQL_PATH)
        return False

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            sql_text = _SQL_PATH.read_text(encoding="utf-8")
            conn.executescript(sql_text)
            conn.commit()

            # Verify
            try:
                nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
                edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
                log.info("Graph bootstrap: %d nodes, %d edges", nodes, edges)
            except sqlite3.OperationalError:
                log.warning("Could not verify graph tables after bootstrap")

            return True
        finally:
            conn.close()
    except Exception as exc:
        log.error("SQL bootstrap failed: %s", exc)
        return False


def _ingest_conversations(db_path: Path) -> int:
    """Ingest seed conversation JSON files directly into the database.

    Returns the number of conversations ingested.
    """
    if not _CONVERSATIONS_DIR.exists():
        log.warning("Conversations directory not found: %s", _CONVERSATIONS_DIR)
        return 0

    ingested = 0
    for conv_file in sorted(_CONVERSATIONS_DIR.glob("*.json")):
        try:
            data = json.loads(conv_file.read_text(encoding="utf-8"))
            turns = data if isinstance(data, list) else data.get("turns", [data])
            if not turns:
                log.warning("No turns found in %s", conv_file.name)
                continue

            conn = sqlite3.connect(str(db_path))
            try:
                for turn in turns:
                    turn_id = turn.get("turn_id", "")
                    if not turn_id:
                        import uuid
                        turn_id = str(uuid.uuid4())

                    # Check if turn already exists (idempotence)
                    existing = conn.execute(
                        "SELECT 1 FROM corpus_turns WHERE turn_id = ?", (turn_id,)
                    ).fetchone()
                    if existing:
                        continue

                    conn.execute(
                        """INSERT OR IGNORE INTO corpus_turns
                           (turn_id, source_path, role, content, source_model,
                            source_account, timestamp, metadata_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            turn_id,
                            str(conv_file.relative_to(_REPO_ROOT)),
                            turn.get("role", "unknown"),
                            turn.get("content", ""),
                            turn.get("source_model", "claude"),
                            turn.get("source_account", "aip_seed"),
                            turn.get("timestamp", ""),
                            json.dumps(turn.get("metadata", {})),
                        ),
                    )
                conn.commit()
                ingested += 1
                log.info("Ingested %d turns from %s", len(turns), conv_file.name)
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to ingest %s: %s", conv_file.name, exc)

    return ingested


def run_seed_bootstrap() -> bool:
    """Run the seed bootstrap if conditions are met.

    Conditions:
      - AIP_AUTO_SEED is not "false"
      - Sentinel file does not exist
      - DB is effectively empty (no graph nodes, no corpus turns)

    Returns True if bootstrap ran successfully, False otherwise.
    """
    # Check opt-out env var
    auto_seed = os.environ.get("AIP_AUTO_SEED", "true").lower()
    if auto_seed in ("false", "0", "no"):
        log.info("Seed bootstrap skipped: AIP_AUTO_SEED=%s", auto_seed)
        return False

    # Check sentinel
    if _sentinel_exists():
        log.info("Seed bootstrap skipped: sentinel exists (%s)", _SENTINEL_PATH)
        return False

    # Ensure DB directory exists
    _DB_DIR.mkdir(parents=True, exist_ok=True)

    # Check if DB is empty
    if not _is_empty_db(_DB_PATH):
        log.info("Seed bootstrap skipped: DB is not empty")
        return False

    log.info("=== AIP Seed Bootstrap: first-run initialization ===")

    # Ensure DB exists (aip init or equivalent)
    if not _DB_PATH.exists():
        log.info("Creating database: %s", _DB_PATH)
        conn = sqlite3.connect(str(_DB_PATH))
        conn.close()

    # Step 1: SQL bootstrap (graph nodes, edges, default project)
    log.info("--- Step 1: Bootstrap graph and default project ---")
    if not _run_sql_bootstrap(_DB_PATH):
        log.error("SQL bootstrap failed — aborting seed bootstrap")
        return False

    # Step 2: Ingest conversations
    log.info("--- Step 2: Ingest seed conversations ---")
    ingested = _ingest_conversations(_DB_PATH)
    log.info("Ingested %d conversation files", ingested)

    # Write sentinel
    _write_sentinel()

    log.info("=== Seed bootstrap complete ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    success = run_seed_bootstrap()
    sys.exit(0 if success else 1)
