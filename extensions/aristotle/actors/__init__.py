"""Aristotle actors package — ADR-ARISTOTLE §2.

Phase A ships SOCRATES only. EXAMINER, MENTOR are Phase A follow-ups;
HERALD is Phase C (depends on the Phase 0 web/feed layer).

All actors conform to the foundation Actor Protocol (ADR-014 §5.2):
  - name: str (unique across all extensions)
  - cadence: float (seconds between cycles; 0 = manual only)
  - run_cycle(ctx) -> ActorResult
  - health() -> dict
"""
from __future__ import annotations

from aristotle.actors.socrates import SocratesActor

__all__ = ["SocratesActor"]
