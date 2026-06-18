"""SOCRATES actor — ADR-ARISTOTLE §2.

Role: Teach / explain / re-explain. Pulls the passage + alternate framings
from the textbook corpus when the first explanation misses.

Phase A dogfood scope: this is a MINIMAL SOCRATES that proves the platform
contract. It conforms to the foundation Actor Protocol (ADR-014 §5.2),
runs one cycle on start (cadence=0 = manual-only — the ARISTOTLE shape),
and does one real platform interaction: verifies its corpus
(`aristotle:textbook`) is registered and logs its presence.

A full SOCRATES would: query the concept graph for the current concept,
pull the passage + alternate framings, call a model to generate the
explanation, and persist the result. That's beyond the dogfood drop —
the goal here is to prove the actor can reach the container + corpus
registry + its own config. Each gap is a Phase 0 protocol gap to log.

Layer: this module is imported by hooks.py::on_load via the host's
sys.path addition of extensions/. It imports from aip.foundation only
(ActorResult) — no adapter or orchestration imports. The container is
accessed via ctx.container (duck-typed as Any in the foundation Protocol).
"""
from __future__ import annotations

from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


class SocratesActor:
    """SOCRATES — the teaching mode of Aristotle (ADR-ARISTOTLE §2).

    Conforms to the foundation Actor Protocol. cadence=0 means manual-only:
    the host's scheduler runs one cycle on start, then waits for
    cancellation. The tutoring state machine (TEACH→PROBE→QUIZ→EVALUATE→
    REMEDIATE) will be driven by user turns, not by a timer.

    The actor is a SINGLE internal orchestration mode — the learner never
    meets "SOCRATES" as a persona. Aristotle is the only voice (ADR-ARISTOTLE §1).
    """

    name: str = "socrates"
    cadence: float = 0.0  # manual-only — driven by user turns, not a timer

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Run one SOCRATES cycle.

        Phase A dogfood: verify the aristotle:textbook corpus is registered
        and log its presence. This proves the actor can:
          1. Access the container via ctx.container
          2. Reach the CorpusRegistry
          3. Confirm its own contributed corpus exists

        A full SOCRATES cycle would: query the concept graph for the current
        concept, pull the passage from the textbook corpus, generate an
        explanation via a model call, and persist the result. That's the
        tutoring loop — Phase A follow-up work.
        """
        logger = ctx.logger
        config = ctx.config
        container: Any = ctx.container

        # Read the bilingual config (ADR-ARISTOTLE §7)
        primary_lang = getattr(config, "primary_language", "en") if config else "en"
        alt_lang = getattr(config, "alt_language", "ur") if config else "ur"

        # Verify the aristotle:textbook corpus is registered (ADR-014 §6.2 namespacing)
        corpus_id = "aristotle:textbook"
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            logger.warning(
                "socrates_corpus_registry_missing — container.corpus_registry is None; "
                "cannot verify textbook corpus"
            )
            return ActorResult(ok=False, error="corpus_registry not available on container")

        try:
            # get_stores is async — but we're already in an async context
            stores = await registry.get_stores(corpus_id)
            if stores is None:
                logger.warning(
                    "socrates_corpus_not_found corpus=%s — extension may not have registered it",
                    corpus_id,
                )
                return ActorResult(ok=False, error=f"corpus {corpus_id!r} not found")
            logger.info(
                "socrates_cycle_ok corpus=%s primary_lang=%s alt_lang=%s — "
                "textbook corpus reachable; ready for tutoring loop",
                corpus_id, primary_lang, alt_lang,
            )
            return ActorResult(ok=True)
        except Exception as exc:
            logger.warning(
                "socrates_corpus_access_failed corpus=%s error=%s:%s",
                corpus_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")

    def health(self) -> dict:
        """Health snapshot for the health surface (ADR-014 §7)."""
        return {
            "state": "active",
            "name": self.name,
            "cadence": self.cadence,
            "mode": "manual-only",
            "last_run": None,  # populated by a real implementation
            "error_count": 0,
        }
