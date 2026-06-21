"""AIP Seed Bootstrap — first-run corpus initialization.

Populates a new AIP install with:
  1. Graph seed nodes (28 entities), edges (5 domain bridges), default project
  2. AIP self-knowledge corpus (conversation JSON files)

Safe to run multiple times — all inserts use OR IGNORE.
Only runs when the DB/corpus is effectively empty and no seed sentinel exists.
Respects AIP_AUTO_SEED=false environment variable.

This module is called from scripts/start.sh before the backend launches.
It can also be invoked directly: python -m aip.cli._seed_bootstrap
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("aip.cli.seed_bootstrap")


class SeedStatus(str, enum.Enum):
    """Distinct outcomes for seed bootstrap.

    - SEEDED:  Bootstrap ran and populated data successfully.
    - SKIPPED: Bootstrap was skipped (sentinel exists, DB not empty, or
               AIP_AUTO_SEED=false). This is a *normal* outcome, not an error.
    - FAILED:  Bootstrap attempted but encountered an actual error.
    """

    SEEDED = "seeded"
    SKIPPED = "skipped"
    FAILED = "failed"

    @property
    def exit_code(self) -> int:
        """Exit code for this status: 0 for seeded/skipped, 1 for failed."""
        return 0 if self is not SeedStatus.FAILED else 1


# Columns that MUST exist in corpus_turns for the app to function.
# The seed bootstrap validates these before writing the sentinel.
_REQUIRED_CORPUS_COLUMNS = frozenset(
    {
        "embedded",
        "conversation_id",
        "tagging_version",
    }
)


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


def _get_corpus_columns(conn: sqlite3.Connection) -> set[str]:
    """Return the set of column names in the corpus_turns table."""
    try:
        rows = conn.execute("PRAGMA table_info(corpus_turns)").fetchall()
        return {row[1] for row in rows}
    except sqlite3.OperationalError:
        return set()


def _validate_corpus_schema(conn: sqlite3.Connection) -> bool:
    """Validate that corpus_turns has all required columns.

    Returns True if every column in _REQUIRED_CORPUS_COLUMNS is present.
    """
    existing = _get_corpus_columns(conn)
    missing = _REQUIRED_CORPUS_COLUMNS - existing
    if missing:
        log.error(
            "corpus_turns schema is missing required columns: %s. "
            "Existing columns: %s. The seed bootstrap must use the canonical "
            "schema from CorpusTurnStore, not an ad-hoc CREATE TABLE.",
            sorted(missing),
            sorted(existing),
        )
        return False
    return True


def _write_sentinel(graph_nodes: int, graph_edges: int, corpus_turns: int) -> bool:
    """Write the sentinel file after successful bootstrap.

    Only writes if:
      - graph_nodes > 0
      - graph_edges > 0
      - corpus_turns > 0
      - required corpus schema columns exist

    Returns True if sentinel was written, False if refused.
    """
    if graph_nodes <= 0:
        log.error(
            "Refusing to write sentinel: graph_nodes=%d (must be >0). Seed bootstrap did not populate graph data.",
            graph_nodes,
        )
        return False
    if graph_edges <= 0:
        log.error(
            "Refusing to write sentinel: graph_edges=%d (must be >0). Seed bootstrap did not populate graph edges.",
            graph_edges,
        )
        return False
    if corpus_turns <= 0:
        log.error(
            "Refusing to write sentinel: corpus_turns=%d (must be >0). "
            "Seed bootstrap did not ingest any conversation turns.",
            corpus_turns,
        )
        return False

    # Schema validation
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            if not _validate_corpus_schema(conn):
                log.error("Refusing to write sentinel: corpus_turns schema validation failed")
                return False
        finally:
            conn.close()
    except Exception as exc:
        log.error("Refusing to write sentinel: schema validation error: %s", exc)
        return False

    try:
        _SENTINEL_PATH.write_text(
            f"seed_bootstrapped\ngraph_nodes={graph_nodes}\ngraph_edges={graph_edges}\ncorpus_turns={corpus_turns}\n",
            encoding="utf-8",
        )
        log.info("Seed bootstrap sentinel written: %s", _SENTINEL_PATH)
        return True
    except OSError as exc:
        log.error("Failed to write sentinel file: %s", exc)
        return False


def _ensure_corpus_turns_schema(conn: sqlite3.Connection) -> bool:
    """Create corpus_turns using the CANONICAL DDL from CorpusTurnStore.

    The seed bootstrap runs BEFORE the backend starts, so the table won't
    exist yet. We must create it using the exact same DDL that
    CorpusTurnStore uses, to avoid schema divergence that breaks the app
    at runtime.

    This imports the DDL constants from the canonical store module and
    applies them with the sync sqlite3 connection.
    """
    try:
        from aip.adapter.corpus_turn_store import (
            _DDL_CORPUS_TURNS,
            _DDL_FTS,
            _DDL_INDEXES,
            _DDL_MIGRATIONS,
            _DDL_TRIGGER_DELETE,
            _DDL_TRIGGER_INSERT,
            _DDL_TRIGGER_UPDATE,
        )
    except ImportError as exc:
        log.error(
            "Cannot import canonical corpus_turns DDL from aip.adapter.corpus_turn_store: %s. "
            "The seed bootstrap requires the canonical schema definitions.",
            exc,
        )
        return False

    try:
        conn.execute(_DDL_CORPUS_TURNS)

        for idx_ddl in _DDL_INDEXES:
            conn.execute(idx_ddl)

        for mig_ddl in _DDL_MIGRATIONS:
            try:
                conn.execute(mig_ddl)
            except sqlite3.OperationalError:
                pass  # column already exists

        conn.execute(_DDL_FTS)
        conn.execute(_DDL_TRIGGER_INSERT)
        conn.execute(_DDL_TRIGGER_DELETE)
        conn.execute(_DDL_TRIGGER_UPDATE)

        conn.commit()
        log.info("Ensured corpus_turns table with CANONICAL schema (from CorpusTurnStore)")
        return True
    except Exception as exc:
        log.error("Failed to create corpus_turns table with canonical DDL: %s", exc)
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


def _make_turn_id(conversation_id: str, turn_index: int) -> str:
    """Generate deterministic turn_id matching CorpusTurn.make_turn_id()."""
    key = f"{conversation_id}:{turn_index}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _pair_messages(messages: list[dict]) -> list[tuple[dict, dict]]:
    """Pair sequential human+assistant messages into turns.

    Returns list of (human_msg, assistant_msg) tuples.
    Skips unpaired messages (e.g. two humans in a row).
    """
    pairs: list[tuple[dict, dict]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        sender = msg.get("sender", msg.get("role", "")).lower()
        if sender in ("human", "user") and i + 1 < len(messages):
            next_msg = messages[i + 1]
            next_sender = next_msg.get("sender", next_msg.get("role", "")).lower()
            if next_sender in ("assistant", "ai"):
                pairs.append((msg, next_msg))
                i += 2
                continue
        # Unpaired — skip
        i += 1
    return pairs


def _ingest_conversations(db_path: Path) -> int:
    """Ingest seed conversation JSON files into the canonical corpus_turns schema.

    Handles the conversation JSON format:
      - Top-level: list of conversations, each with uuid, name, chat_messages
      - Each message: uuid, sender, content, created_at

    Pairs human+assistant messages into CorpusTurn-compatible rows with
    user_text/assistant_text (not the old role/content format).

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
                # Ensure canonical schema before inserting
                if not _ensure_corpus_turns_schema(conn):
                    log.error("Cannot ingest conversations: canonical schema creation failed")
                    return 0

                for conv in conversations:
                    conv_uuid = conv.get("uuid", "")
                    conv_name = conv.get("name", conv_file.stem)
                    messages = conv.get("chat_messages", [])
                    if not messages:
                        log.warning("No messages in conversation %s from %s", conv_name, conv_file.name)
                        continue

                    # Pair human+assistant messages into turns
                    pairs = _pair_messages(messages)
                    if not pairs:
                        log.warning(
                            "No human/assistant pairs found in %s from %s",
                            conv_name,
                            conv_file.name,
                        )
                        continue

                    # Derive a stable conversation_id
                    conversation_id = conv_uuid if conv_uuid else f"seed:{conv_name}"
                    export_date = conv.get("created_at", datetime.now(timezone.utc).isoformat())
                    source_path = str(conv_file.relative_to(_REPO_ROOT))

                    for turn_index, (human_msg, assistant_msg) in enumerate(pairs):
                        turn_id = _make_turn_id(conversation_id, turn_index)

                        # Check if turn already exists (idempotence)
                        existing = conn.execute("SELECT 1 FROM corpus_turns WHERE turn_id = ?", (turn_id,)).fetchone()
                        if existing:
                            continue

                        user_text = human_msg.get("content", "")
                        assistant_text = assistant_msg.get("content", "")
                        turn_timestamp = human_msg.get("created_at", human_msg.get("timestamp", ""))

                        # Compute searchable_text and word_count
                        searchable_text = f"{user_text}\n\n{assistant_text}".strip()
                        word_count = len(searchable_text.split())

                        # Compute content_hash
                        content_hash = hashlib.sha256(searchable_text.encode()).hexdigest()[:32]

                        now = datetime.now(timezone.utc).isoformat()

                        metadata = {
                            "seed_source": str(conv_file.name),
                        }
                        if human_msg.get("metadata"):
                            metadata.update(human_msg["metadata"])

                        conn.execute(
                            """INSERT OR IGNORE INTO corpus_turns
                               (turn_id, conversation_id, conversation_name, turn_index,
                                source_model, source_account, export_date,
                                content_hash, source_path, doc_version,
                                user_text, assistant_text, turn_timestamp,
                                thinking_text, domains, primary_domain, tags,
                                importance, bridges, beast_confidence, tagging_version,
                                searchable_text, word_count, embedded,
                                embedding_model, needs_reembed, last_embed_at,
                                metadata_json, embed_fail_count, last_embed_error,
                                created_at, updated_at)
                               VALUES (
                                ?, ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?, ?,
                                ?, ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?,
                                ?, ?)""",
                            (
                                turn_id,
                                conversation_id,
                                conv_name,
                                turn_index,
                                "seed_corpus",
                                "aip_seed",
                                export_date,
                                content_hash,
                                source_path,
                                0,
                                user_text,
                                assistant_text,
                                turn_timestamp,
                                "",  # thinking_text
                                "[]",  # domains
                                "",  # primary_domain
                                "[]",  # tags
                                0.0,  # importance
                                "[]",  # bridges
                                0.0,  # beast_confidence
                                0,  # tagging_version
                                searchable_text,
                                word_count,
                                0,  # embedded
                                "",  # embedding_model
                                0,  # needs_reembed
                                None,  # last_embed_at
                                json.dumps(metadata),
                                0,  # embed_fail_count
                                "",  # last_embed_error
                                now,
                                now,
                            ),
                        )
                        total_turns += 1

                conn.commit()
                log.info("Ingested conversations from %s (%d turns so far)", conv_file.name, total_turns)
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to ingest %s: %s", conv_file.name, exc)

    return total_turns


def run_seed_bootstrap() -> SeedStatus:
    """Run the seed bootstrap if conditions are met.

    Conditions:
      - AIP_AUTO_SEED is not "false"
      - Sentinel file does not exist
      - DB is effectively empty (no graph nodes, no corpus turns)

    Returns:
      SeedStatus.SEEDED  — bootstrap ran and populated data successfully
      SeedStatus.SKIPPED — bootstrap was skipped (sentinel, non-empty DB, opt-out)
      SeedStatus.FAILED  — bootstrap attempted but encountered an actual error
    """
    # Check opt-out env var
    auto_seed = os.environ.get("AIP_AUTO_SEED", "true").lower()
    if auto_seed in ("false", "0", "no"):
        log.info("Seed bootstrap skipped: AIP_AUTO_SEED=%s", auto_seed)
        return SeedStatus.SKIPPED

    # Check sentinel
    if _sentinel_exists():
        log.info("Seed bootstrap skipped: sentinel exists (%s)", _SENTINEL_PATH)
        return SeedStatus.SKIPPED

    # Ensure DB directory exists
    _DB_DIR.mkdir(parents=True, exist_ok=True)

    # Check if DB is empty
    if not _is_empty_db(_DB_PATH):
        log.info("Seed bootstrap skipped: DB is not empty")
        return SeedStatus.SKIPPED

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
        return SeedStatus.FAILED

    # Verify graph nodes and edges were actually created
    graph_nodes = 0
    graph_edges = 0
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        graph_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        graph_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        conn.close()
    except Exception as exc:
        log.error("Could not verify graph tables after bootstrap: %s", exc)

    if graph_nodes == 0:
        log.error("Graph bootstrap produced 0 nodes — seed data may be corrupt")
        return SeedStatus.FAILED

    if graph_edges == 0:
        log.error("Graph bootstrap produced 0 edges — seed data may be corrupt")
        return SeedStatus.FAILED

    # Step 2: Ingest conversations (uses canonical CorpusTurnStore schema)
    log.info("--- Step 2: Ingest seed conversations ---")
    total_turns = _ingest_conversations(_DB_PATH)
    log.info("Ingested %d conversation turns total", total_turns)

    if total_turns == 0:
        log.error(
            "Seed bootstrap ingested 0 turns. Conversation files may be missing "
            "or corrupt. Sentinel will NOT be written — bootstrap will retry on next start."
        )
        return SeedStatus.FAILED

    # Validate corpus schema before writing sentinel
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            if not _validate_corpus_schema(conn):
                log.error(
                    "corpus_turns schema is missing required columns. "
                    "Sentinel will NOT be written — bootstrap will retry on next start."
                )
                return SeedStatus.FAILED
        finally:
            conn.close()
    except Exception as exc:
        log.error("Schema validation error: %s", exc)
        return SeedStatus.FAILED

    # Write sentinel only if all data and schema checks pass
    if not _write_sentinel(graph_nodes=graph_nodes, graph_edges=graph_edges, corpus_turns=total_turns):
        return SeedStatus.FAILED

    log.info(
        "=== Seed bootstrap complete: %d graph nodes, %d graph edges, %d corpus turns ===",
        graph_nodes,
        graph_edges,
        total_turns,
    )
    return SeedStatus.SEEDED


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    status = run_seed_bootstrap()
    if status == SeedStatus.SEEDED:
        log.info("Seed bootstrap result: %s", status.value)
    elif status == SeedStatus.SKIPPED:
        log.info("Seed bootstrap result: %s (normal — no action needed)", status.value)
    else:
        log.error("Seed bootstrap result: %s", status.value)
    sys.exit(status.exit_code)
