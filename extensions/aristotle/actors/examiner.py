"""EXAMINER actor — ADR-ARISTOTLE §2.

Role: Probe / quiz / evaluate. Generates questions, scores answers, decides
mastery. Drives the PROBE → QUIZ → EVALUATE transitions in the tutoring
state machine (ADR-ARISTOTLE §3).

Phase A dogfood scope: a minimal EXAMINER that proves the multi-actor
pattern (SOCRATES + EXAMINER + MENTOR all register from one extension).
It conforms to the foundation Actor Protocol, runs one cycle on start
(cadence=0 = manual-only), and does one real platform interaction:
verifies the aristotle:textbook corpus is reachable and checks whether
a model provider is configured on the container.

A full EXAMINER would: generate probe questions (low-stakes "tell me in
your own words"), generate quiz questions (real questions), score answers,
and decide mastery (update the concept's mastery level). Without a model
configured, it returns NEEDS_CONFIGURATION per the governance invariants
(AGENTS.md §1.7: "No silent model calls").

Layer: imports from aip.foundation only (ActorResult, ActorContext). The
container is accessed via ctx.container (duck-typed as Any).
"""
from __future__ import annotations

from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


class ExaminerActor:
    """EXAMINER — the probing/quizzing/evaluating mode of Aristotle (ADR-ARISTOTLE §2).

    Conforms to the foundation Actor Protocol. cadence=0 means manual-only:
    the tutoring state machine is driven by user turns, not by a timer.

    The actor is a SINGLE internal orchestration mode — the learner never
    meets "EXAMINER" as a persona. Aristotle is the only voice (ADR-ARISTOTLE §1).
    """

    name: str = "examiner"
    cadence: float = 0.0  # manual-only — driven by user turns

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Run one EXAMINER cycle.

        Phase A dogfood: verify the corpus is reachable + check model
        availability. This proves the actor can:
          1. Access the container via ctx.container
          2. Reach the CorpusRegistry
          3. Check whether a model provider is configured (for graceful degrade)

        A full EXAMINER cycle would: generate a probe/quiz question, call a
        model to score the student's answer, and update mastery. Without a
        model, it returns NEEDS_CONFIGURATION (never a placeholder answer).
        """
        logger = ctx.logger
        container: Any = ctx.container

        # Verify the aristotle:textbook corpus is registered
        corpus_id = "aristotle:textbook"
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            logger.warning(
                "examiner_corpus_registry_missing — container.corpus_registry is None"
            )
            return ActorResult(ok=False, error="corpus_registry not available on container")

        try:
            stores = await registry.get_stores(corpus_id)
            if stores is None:
                logger.warning(
                    "examiner_corpus_not_found corpus=%s", corpus_id,
                )
                return ActorResult(ok=False, error=f"corpus {corpus_id!r} not found")
        except Exception as exc:
            logger.warning(
                "examiner_corpus_access_failed corpus=%s error=%s:%s",
                corpus_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")

        # Check model availability (governance invariant: no silent model calls)
        model_provider = getattr(container, "model_provider", None)
        if model_provider is None:
            logger.info(
                "examiner_model_not_configured — quiz/question generation "
                "will return NEEDS_CONFIGURATION when invoked"
            )
            # This is NOT a failure — the actor is ready, just can't generate
            # questions without a model. The tutoring loop checks this before
            # attempting a quiz.
            return ActorResult(
                ok=True,
                error=None,  # ok=True because the actor itself is healthy
            )

        logger.info(
            "examiner_cycle_ok corpus=%s model_configured=True — "
            "ready for probe/quiz/evaluate",
            corpus_id,
        )
        return ActorResult(ok=True)

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
