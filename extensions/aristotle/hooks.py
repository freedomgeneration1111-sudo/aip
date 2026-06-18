"""Aristotle on_load / on_unload hooks — ADR-014 §4 stage 5.

The host calls on_load(host) after discover → validate → migrate → register.
This is where ARISTOTLE registers its actors (ADR-014 §5.3: manifest's
`actors` list is advisory; actual registration happens here).

The host sets `_current_ext_id` before calling on_load, so `host.config`
and `host.manifest` resolve to ARISTOTLE's validated config + manifest.
"""
from __future__ import annotations

from aristotle.actors import ExaminerActor, MentorActor, SocratesActor


def on_load(host) -> None:
    """Register ARISTOTLE's actors (ADR-ARISTOTLE §2).

    Phase A ships three actors:
      - SOCRATES — teach / explain / re-explain (ADR-ARISTOTLE §2)
      - EXAMINER — probe / quiz / evaluate (ADR-ARISTOTLE §2)
      - MENTOR — track the long arc + struggle_pattern (ADR-ARISTOTLE §2)

    HERALD (field awareness) is Phase C — depends on the Phase 0 web/feed
    layer (ADR-014 §3.4), which is not yet built.

    All three are manual-only (cadence=0.0) — the tutoring state machine
    is driven by user turns, not by a timer (ADR-ARISTOTLE §3: "the learner
    only feels rhythm"). The host runs one cycle on start, then waits for
    cancellation.
    """
    host.register_actor("socrates", SocratesActor, cadence=0.0)
    host.register_actor("examiner", ExaminerActor, cadence=0.0)
    host.register_actor("mentor", MentorActor, cadence=0.0)


def on_unload(host) -> None:
    """Cleanup hook — called by host.stop() (ADR-014 §4.2).

    ARISTOTLE has no background resources to release in Phase A. The
    aristotle:textbook corpus is owned by CorpusRegistry and closed by
    its own shutdown. Actor scheduler tasks are cancelled by the host.
    """
    pass
