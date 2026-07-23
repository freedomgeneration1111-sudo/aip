"""Corpus-layer constants — ADR-008 Multi-Corpus Architecture.

Pure foundation layer: constants only. No I/O, no imports from
orchestration or adapter.

These constants are consumed by:
  - adapter/corpus_registry.py (budget validation, migration gating)
  - adapter/corpus_connection.py (shared connection pool sizing)
  - adapter/corpus_store_factory.py (batch sizing)
  - adapter/api/app.py (actor scheduler gating)

ADR-008 Rev 3.1 §8 Chunk 1, §9.3, §A0 (corrected budget).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Connection budget — ADR-008 Rev 3.1 §9.3 (corrected by §A0)
# ---------------------------------------------------------------------------

# Hard cap on total SQLite connections across the entire process.
MAX_CONNECTIONS: int = 64

# Conservative cap on registered corpora. Theoretical max is 12
# (floor(36 / 3) at CORPUS_READ_POOL_SIZE=2). Raised from 4 to 8 on
# 2026-07-23 (QW10) to accommodate the ADR-015 fleet vision (definer +
# ARISTOTLE + 6 future domain extensions). 8 × 3 = 24 connections,
# leaving 12 of headroom under the 36-connection corpus budget.
# DEFINER may raise further after confirming runtime fd headroom.
MAX_CORPORA: int = 8

# Read pool size for corpus stores. Smaller than the non-corpus default
# (3) because each corpus shares ONE write connection + ONE read pool
# across all 6 stores (ADR-008 Rev 3.1 §A0 — shared connection manager).
CORPUS_READ_POOL_SIZE: int = 2

# Read pool size for the 7 pre-existing non-corpus DB files (state.db,
# lexical.db, vectors.db, vigil_quality.db, alert_history.db, trace.db,
# ace_playbook.db). Matches read_pool._DEFAULT_POOL_SIZE — keep in sync.
NON_CORPUS_READ_POOL_SIZE: int = 3

# Number of pre-existing non-corpus SQLite files — used in budget formula.
KNOWN_NON_CORPUS_DB_FILES: int = 7


# ---------------------------------------------------------------------------
# Write serialization — ADR-008 Rev 3.1 §3.6, §9.5
# ---------------------------------------------------------------------------

# Sexton yields the corpus write_lock after every N turns to allow chat
# routes to interleave. ADR-008 Rev 3.1 §9.5: between batches, Sexton calls
# asyncio.sleep(0.001) (not sleep(0)) so a chat-route coroutine that
# arrives between release and re-acquire can enter the FIFO lock queue
# before Sexton's next batch.
SEXTON_WRITE_BATCH_SIZE: int = 100
SEXTON_BATCH_YIELD_DELAY: float = 0.001  # seconds — deliberate scheduling hint
