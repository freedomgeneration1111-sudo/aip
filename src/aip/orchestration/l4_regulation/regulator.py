"""Trajectory regulation free functions (support).

Provides the exact interface expected by the ANNEX and SessionManager:
- regulate_trajectory
- should_intervene

This is an extension layer. It re-uses the
TrajectoryRegulator class (l4/regulator.py) for the 2-of-3 decision logic while
providing the free-function shape the session manager and later engine integration
expect.

Issue 14: Wire all three L4 detectors (loop, anxiety, failure_streak).
Issue 15: should_intervene is sync and filters by intervention_min_confidence.

Type E detection fix: The default substance_score for trace events that lack
a substance_score field is now configurable (default 0.3) and below the
detection threshold (0.4). Previously hardcoded at 0.5, which was always >= 0.4
and made Type E detection completely non-functional.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aip.foundation.schemas import TrajectorySignal
from aip.logging import get_logger
from aip.orchestration.l4.anxiety_detector import ContextAnxietyDetector
from aip.orchestration.l4.failure_streak import FailureStreakDetector
from aip.orchestration.l4.loop_detector import LoopDetector
from aip.orchestration.l4.regulator import TrajectoryRegulator

if TYPE_CHECKING:
    from aip.foundation.protocols import TraceStore
    from aip.foundation.schemas import SessionContext

logger = get_logger(__name__)

# Default substance_score for trace events that lack the field.
# MUST be below the FailureStreakDetector substance_threshold (0.4 by default)
# so that Type E detection can fire. Previously hardcoded at 0.5 which was
# always >= 0.4, making Type E detection completely non-functional.
_DEFAULT_SUBSTANCE_SCORE = 0.3


# Reusable instances (the L4 classes are stateless)
_regulator = TrajectoryRegulator()


async def regulate_trajectory(
    session_context: SessionContext,
    trace_store: TraceStore,
    config: Any = None,
) -> list[TrajectorySignal]:
    """Return trajectory signals for the session.

    Issue 14: Calls all three L4 detectors (loop, anxiety, failure_streak).
    Each detector produces a TrajectorySignal (or None). The non-None results
    are collected and returned.
    """
    session_id = getattr(session_context, "session_id", None) if session_context else None
    if not session_id:
        return []

    # Extract intervention_min_confidence from config
    intervention_min_confidence = 0.50
    if config is not None:
        if hasattr(config, "model_dump"):
            cfg = config.model_dump()
        elif isinstance(config, dict):
            cfg = config
        else:
            cfg = {}
        trajectory_cfg = cfg.get("trajectory", {}) if isinstance(cfg, dict) else {}
        intervention_min_confidence = trajectory_cfg.get("intervention_min_confidence", 0.50)

    signals: list[TrajectorySignal] = []

    # 1. Loop detector (Type D)
    try:
        loop_detector = LoopDetector(trace_store)
        loop_signal = await loop_detector.detect(session_id=session_id)
        if loop_signal is not None and loop_signal.confidence >= intervention_min_confidence:
            signals.append(loop_signal)
    except Exception:
        logger.warning("Loop detector failed for session %s", session_id, exc_info=True)

    # 2. Anxiety detector (Type F)
    try:
        anxiety_detector = ContextAnxietyDetector()
        # Get recent events from trace store to extract outputs
        recent_events = []
        if hasattr(trace_store, "query_events"):
            recent_events = await trace_store.query_events(session_id=session_id, limit=10)
        recent_outputs = [e.get("content", "") or e.get("detail", "") for e in recent_events if isinstance(e, dict)]
        anxiety_signal = await anxiety_detector.detect(
            session_id=session_id,
            recent_outputs=recent_outputs if recent_outputs else None,
        )
        if anxiety_signal is not None and anxiety_signal.confidence >= intervention_min_confidence:
            signals.append(anxiety_signal)
    except Exception:
        logger.warning("Anxiety detector failed for session %s", session_id, exc_info=True)

    # 3. Failure streak detector (Type E)
    try:
        failure_streak_detector = FailureStreakDetector()
        # Get recent outcomes from trace store
        recent_outcomes = []
        if hasattr(trace_store, "query_events"):
            recent_events = await trace_store.query_events(session_id=session_id, limit=10)
            recent_outcomes = [
                {
                    "claimed_done": "done" in str(e.get("outcome", "")).lower() or e.get("outcome") == "success",
                    "substance_score": e.get(
                        "substance_score",
                        _DEFAULT_SUBSTANCE_SCORE,
                    ),  # default below 0.4 threshold so Type E can fire
                }
                for e in recent_events
                if isinstance(e, dict)
            ]
        streak_signal = await failure_streak_detector.detect(
            session_id=session_id,
            recent_outcomes=recent_outcomes if recent_outcomes else None,
        )
        if streak_signal is not None and streak_signal.confidence >= intervention_min_confidence:
            signals.append(streak_signal)
    except Exception:
        logger.warning("Failure streak detector failed for session %s", session_id, exc_info=True)

    return signals


def should_intervene(
    signals: list[TrajectorySignal],
    config: Any = None,
) -> bool:
    """Apply the "2 of 3" rule.

    Issue 15: Sync (not async). Filters by intervention_min_confidence
    (default 0.50) from config before checking distinct types.
    """
    if not signals:
        return False

    # Extract intervention_min_confidence from config
    intervention_min_confidence = 0.50
    if config is not None:
        if hasattr(config, "model_dump"):
            cfg = config.model_dump()
        elif isinstance(config, dict):
            cfg = config
        else:
            cfg = {}
        trajectory_cfg = cfg.get("trajectory", {}) if isinstance(cfg, dict) else {}
        intervention_min_confidence = trajectory_cfg.get("intervention_min_confidence", 0.50)

    # Filter signals by minimum confidence
    qualifying = [s for s in signals if s.confidence >= intervention_min_confidence]
    if not qualifying:
        return False

    signal_types = {s.signal_type for s in qualifying}
    # A single signal type firing multiple times does not count (per 5.5 prose)
    return len(signal_types) >= 2
