"""MENTOR actor — ADR-ARISTOTLE §2.

Role: Track the long arc. Mastery per concept + the `struggle_pattern`
field (one persistent AI-written diagnostic sentence per student — the
tutor's memory of *who this learner is*; feeds every REMEDIATE prompt).

Phase A dogfood scope: a minimal MENTOR that proves per-student state.
It conforms to the foundation Actor Protocol, runs one cycle on start
(cadence=0 = manual-only), and does one real platform interaction:
reads the current struggle_pattern from the aristotle_struggle_pattern
table (or initializes it if absent). This proves the actor can:
  1. Access the corpus stores via ctx.container.corpus_registry
  2. Execute SQL against the extension's own corpus
  3. Read per-student state

A full MENTOR would: update struggle_pattern after each EVALUATE
transition (the AI writes a new diagnostic sentence based on the
student's recent performance), and feed it to every REMEDIATE prompt.

Layer: imports from aip.foundation only (ActorResult, ActorContext).
The container is accessed via ctx.container (duck-typed as Any). SQL
is executed via the corpus's write connection (stores.connection_manager.write_conn).
"""
from __future__ import annotations

from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


class MentorActor:
    """MENTOR — the long-arc tracking mode of Aristotle (ADR-ARISTOTLE §2).

    Conforms to the foundation Actor Protocol. cadence=0 means manual-only.
    Reads/writes the aristotle_struggle_pattern table in the
    aristotle:textbook corpus.

    The actor is a SINGLE internal orchestration mode — the learner never
    meets "MENTOR" as a persona. Aristotle is the only voice (ADR-ARISTOTLE §1).
    """

    name: str = "mentor"
    cadence: float = 0.0  # manual-only — driven by user turns

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Run one MENTOR cycle.

        Phase A dogfood: read the current struggle_pattern for the default
        student ('definer' — pre-alpha single-tenant). If absent, initialize
        it with a placeholder. This proves the actor can execute SQL against
        the extension's own corpus.

        A full MENTOR cycle would: analyze recent EVALUATE results, call a
        model to write a new diagnostic sentence, and UPDATE the table.
        Without a model, it reads the existing pattern (or initializes a
        placeholder) and logs it.
        """
        logger = ctx.logger
        container: Any = ctx.container

        # Verify the aristotle:textbook corpus is registered
        corpus_id = "aristotle:textbook"
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            logger.warning(
                "mentor_corpus_registry_missing — container.corpus_registry is None"
            )
            return ActorResult(ok=False, error="corpus_registry not available on container")

        try:
            stores = await registry.get_stores(corpus_id)
            if stores is None:
                logger.warning(
                    "mentor_corpus_not_found corpus=%s", corpus_id,
                )
                return ActorResult(ok=False, error=f"corpus {corpus_id!r} not found")
        except Exception as exc:
            logger.warning(
                "mentor_corpus_access_failed corpus=%s error=%s:%s",
                corpus_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")

        # Read the current struggle_pattern for the default student.
        # Pre-alpha single-tenant: student_id = 'definer'.
        student_id = "definer"
        try:
            conn = stores.connection_manager.write_conn
            cur = await conn.execute(
                "SELECT pattern_text FROM aristotle_struggle_pattern WHERE student_id = ?",
                (student_id,),
            )
            row = await cur.fetchone()
            await cur.close()

            if row is None:
                # Initialize with a placeholder. A full MENTOR would call a
                # model to write the first diagnostic sentence after the first
                # EVALUATE transition. For now, a neutral placeholder.
                placeholder = "No struggles recorded yet — the tutor is still learning who this learner is."
                await conn.execute(
                    "INSERT OR REPLACE INTO aristotle_struggle_pattern "
                    "(student_id, pattern_text) VALUES (?, ?)",
                    (student_id, placeholder),
                )
                await conn.commit()
                logger.info(
                    "mentor_struggle_pattern_initialized student=%s — placeholder set",
                    student_id,
                )
                return ActorResult(ok=True)
            else:
                logger.info(
                    "mentor_struggle_pattern_read student=%s pattern=%s",
                    student_id, row[0][:80] + "..." if len(row[0]) > 80 else row[0],
                )
                return ActorResult(ok=True)

        except Exception as exc:
            logger.warning(
                "mentor_struggle_pattern_failed student=%s error=%s:%s",
                student_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"struggle_pattern read/write failed: {exc}")

    def health(self) -> dict:
        """Health snapshot for the health surface (ADR-014 §7)."""
        return {
            "state": "active",
            "name": self.name,
            "cadence": self.cadence,
            "mode": "manual-only",
            "last_run": None,
            "error_count": 0,
        }
