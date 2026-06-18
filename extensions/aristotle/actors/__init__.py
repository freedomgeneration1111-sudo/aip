"""Aristotle actors package — ADR-ARISTOTLE §2.

Phase A ships three actors: SOCRATES (teach), EXAMINER (probe/quiz/evaluate),
and MENTOR (long-arc tracking + struggle_pattern). HERALD is Phase C
(depends on the Phase 0 web/feed layer).

All actors conform to the foundation Actor Protocol (ADR-014 §5.2):
  - name: str (unique across all extensions)
  - cadence: float (seconds between cycles; 0 = manual only)
  - run_cycle(ctx) -> ActorResult
  - health() -> dict

All are manual-only (cadence=0.0) — the tutoring state machine is driven
by user turns, not by a timer (ADR-ARISTOTLE §3: "the learner only feels
rhythm"). The host runs one cycle on start, then waits for cancellation.
"""
from __future__ import annotations

from aristotle.actors.examiner import ExaminerActor
from aristotle.actors.mentor import MentorActor
from aristotle.actors.socrates import SocratesActor

__all__ = ["SocratesActor", "ExaminerActor", "MentorActor"]
