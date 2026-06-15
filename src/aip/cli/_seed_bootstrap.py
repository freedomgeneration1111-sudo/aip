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


def _write_sentinel(graph_nodes: int, corpus_turns: int) -> None:
    """Write the sentinel file after successful bootstrap.

    Only writes if both graph_nodes > 0 and corpus_turns > 0,
    ensuring the bootstrap actually populated data.
    """
    if graph_nodes <= 0:
        log.error(
            "Refusing to write sentinel: graph_nodes=%d (must be >0). "
            "Seed bootstrap did not populate graph data.",
            graph_nodes,
        )
        return
    if corpus_turns <= 0:
        log.error(
            "Refusing to write sentinel: corpus_turns=%d (must be >0). "
            "Seed bootstrap did not ingest any conversation turns.",
            corpus_turns,
        )
        return
    try:
        _SENTINEL_PATH.write_text(
            f"seed_bootstrapped\ngraph_nodes={graph_nodes}\ncorpus_turns={corpus_turns}\n",
            encoding="utf-8",
        )
        log.info("Seed bootstrap sentinel written: %s", _SENTINEL_PATH)
    except OSError as exc:
        log.error("Failed to write sentinel file: %s", exc)


def _ensure_corpus_turns_schema(conn: sqlite3.Connection) -> bool:
    """Ensure the corpus_turns table exists with the correct schema.

    The backend creates this table during its own init, but the seed
    bootstrap runs BEFORE the backend starts. We must create the table
    ourselves to avoid INSERT failures.
    """
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS corpus_turns (
                turn_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'unknown',
                content TEXT NOT NULL DEFAULT '',
                source_model TEXT NOT NULL DEFAULT '',
                source_account TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                embedding_status TEXT DEFAULT 'pending',
                embedding_failure_count INTEGER DEFAULT 0,
                domain_tag TEXT,
                importance_score REAL DEFAULT 0.0,
                bridge_tag TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
        log.info("Ensured corpus_turns table schema exists")
        return True
    except Exception as exc:
        log.error("Failed to create corpus_turns table: %s", exc)
        return False


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

    Handles the conversation JSON format:
      - Top-level: list of conversations, each with uuid, name, chat_messages
      - Each message: uuid, sender, content, created_at

    Returns the number of conversation turns ingested.
    """
    if not _CONVERSATIONS_DIR.exists():
        log.error("Conversations directory not found: %s", _CONVERSATIONS_DIR)
        return 0

    total_turns = 0
    for conv_file in sorted(_CONVERSATIONS_DIR.glob("*.json")):
        try:
            data = json.loads(conv_file.read_text(encoding="utf-8"))

            # Handle both formats:
            # 1. List of conversations with chat_messages (standard format)
            # 2. Flat list of turns with turn_id/role/content
            conversations: list[dict] = []
            if isinstance(data, list):
                if data and isinstance(data[0], dict) and "chat_messages" in data[0]:
                    # Standard conversation format
                    conversations = data
                elif data and isinstance(data[0], dict) and "turn_id" in data[0]:
                    # Flat turn format
                    conversations = [{"uuid": "", "name": conv_file.name, "chat_messages": data}]
                else:
                    log.warning("Unrecognized format in %s", conv_file.name)
                    continue
            elif isinstance(data, dict):
                if "chat_messages" in data:
                    conversations = [data]
                elif "turns" in data:
                    conversations = [{"uuid": "", "name": conv_file.name, "chat_messages": data["turns"]}]
                else:
                    conversations = [data]

            conn = sqlite3.connect(str(db_path))
            try:
                # Ensure schema before inserting
                _ensure_corpus_turns_schema(conn)

                for conv in conversations:
                    conv_uuid = conv.get("uuid", "")
                    conv_name = conv.get("name", conv_file.stem)
                    messages = conv.get("chat_messages", [])
                    if not messages:
                        log.warning("No messages in conversation %s from %s", conv_name, conv_file.name)
                        continue

                    for msg in messages:
                        # Use message uuid as turn_id, or generate one
                        turn_id = msg.get("uuid", msg.get("turn_id", ""))
                        if not turn_id:
                            import uuid as uuid_mod
                            turn_id = str(uuid_mod.uuid4())

                        # Map sender → role
                        sender = msg.get("sender", msg.get("role", "unknown"))
                        role = sender  # Keep original: "human", "assistant", etc.

                        content = msg.get("content", "")
                        msg_timestamp = msg.get("created_at", msg.get("timestamp", ""))

                        # Check if turn already exists (idempotence)
                        existing = conn.execute(
                            "SELECT 1 FROM corpus_turns WHERE turn_id = ?", (turn_id,)
                        ).fetchone()
                        if existing:
                            continue

                        metadata = {
                            "conversation_uuid": conv_uuid,
                            "conversation_name": conv_name,
                            "seed_source": str(conv_file.name),
                        }
                        if msg.get("metadata"):
                            metadata.update(msg["metadata"])

                        conn.execute(
                            """INSERT OR IGNORE INTO corpus_turns
                               (turn_id, source_path, role, content, source_model,
                                source_account, timestamp, metadata_json)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                turn_id,
                                str(conv_file.relative_to(_REPO_ROOT)),
                                role,
                                content,
                                msg.get("source_model", ""),
                                "aip_seed",
                                msg_timestamp,
                                json.dumps(metadata),
                            ),
                        )
                        total_turns += 1

                conn.commit()
                log.info("Ingested conversations from %s (%d turns)", conv_file.name, total_turns)
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to ingest %s: %s", conv_file.name, exc)

    return total_turns


def run_seed_bootstrap() -> bool:
    """Run the seed bootstrap if conditions are met.

    Conditions:
      - AIP_AUTO_SEED is not "false"
      - Sentinel file does not exist
      - DB is effectively empty (no graph nodes, no corpus turns)

    Returns True if bootstrap ran successfully AND populated both
    graph nodes and corpus turns. Returns False otherwise.
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

    # Verify graph nodes were actually created
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        graph_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        conn.close()
    except Exception as exc:
        log.error("Could not verify graph nodes after bootstrap: %s", exc)
        graph_nodes = 0

    if graph_nodes == 0:
        log.error("Graph bootstrap produced 0 nodes — seed data may be corrupt")
        return False

    # Step 2: Ingest conversations
    log.info("--- Step 2: Ingest seed conversations ---")
    total_turns = _ingest_conversations(_DB_PATH)
    log.info("Ingested %d conversation turns total", total_turns)

    if total_turns == 0:
        log.error(
            "Seed bootstrap ingested 0 turns. Conversation files may be missing "
            "or corrupt. Sentinel will NOT be written — bootstrap will retry on next start."
        )
        return False

    # Write sentinel only if both graph and corpus data exist
    _write_sentinel(graph_nodes=graph_nodes, corpus_turns=total_turns)

    log.info("=== Seed bootstrap complete: %d graph nodes, %d corpus turns ===", graph_nodes, total_turns)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    success = run_seed_bootstrap()
    sys.exit(0 if success else 1)
