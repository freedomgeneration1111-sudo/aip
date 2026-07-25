"""Context reset protocol — six-step reset.

Six-step reset:
1. Detect context anxiety or degeneration
2. Instruct model to produce progress summary
3. Commit progress summary to artifact store
4. Log reset event to trace_events
5. Surface to DEFINER
6. Start fresh session with progress summary as seed
"""

from __future__ import annotations

from datetime import datetime, timezone

from aip.foundation.protocols import ArtifactStore, EcsStore, EventStore, TraceStore
from aip.foundation.schemas import SessionContext, TrajectorySignal

# Recovery instruction templates by failure type
_RECOVERY_TEMPLATES = {
    "D": "Session drift detected. Avoid repeating prior outputs. "
    "Introduce new perspectives and expand the analysis scope.",
    "E": "False completion detected. Do NOT report completion until "
    "all required deliverables are verified present. Include a "
    "self-verification step before finalizing any output.",
    "F": "Context anxiety detected. Do not rush to conclude. "
    "Maintain output depth and completeness regardless of "
    "perceived context pressure.",
}


async def execute_context_reset(
    session_context: SessionContext,
    signals: list[TrajectorySignal],
    artifact_store: ArtifactStore,
    trace_store: TraceStore,
    event_store: EventStore,
    ecs_store: EcsStore,
    config: "AipConfig | dict | None" = None,  # noqa: F821
) -> SessionContext:
    """Execute the full six-step context reset protocol.

    Args:
        session_context: Current session state.
        signals: TrajectorySignals that triggered the intervention.
        artifact_store: For writing progress summary.
        trace_store: For logging reset trace event.
        event_store: For surfacing reset to DEFINER.
        ecs_store: For ECS state transitions.
        config: AipConfig or dict.

    Returns:
        New SessionContext for the fresh session.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Step 1: Validated — signals already received
    signal_details = "; ".join(f"{s.signal_type}({s.failure_type}): {s.detail}" for s in signals)

    # Step 2: Produce progress summary (deterministic in CI)
    summary_id = f"{session_context.session_id}_progress_summary"
    summary_content = (
        f"Session {session_context.session_id} progress summary:\n"
        f"- Turns completed: {session_context.turn_count}\n"
        f"- Artifacts produced: {len(session_context.artifacts_produced)}\n"
        f"- Reset triggered by: {signal_details}\n"
        f"- Context utilization at reset: "
        f"{session_context.context_tokens_estimate}/{session_context.context_window_limit}"
    )

    # Step 3: Commit progress summary
    await artifact_store.write(
        summary_id,
        summary_content,
        metadata={
            "type": "progress_summary",
            "session_id": session_context.session_id,
            "reset_reason": signal_details,
        },
    )

    # ECS transition recorded (summary as artifact)
    await ecs_store.transition(
        artifact_id=summary_id,
        from_state="GENERATED",
        to_state="REVIEWED",
        actor="trajectory_regulator",
        reason=f"Progress summary committed during context reset. {signal_details}",
    )

    # Step 4: Log reset event to trace_events
    await trace_store.write_event(
        session_id=session_context.session_id,
        node_type="L4",
        failure_type=signals[0].failure_type if signals else "",
        outcome="success",
        detail=f"Context reset executed. Reason: {signal_details}",
        intervention_applied=1,
        intervention_type="context_reset",
    )

    # Step 5: Surface to DEFINER
    await event_store.write_event(
        event_type="context_reset",
        actor="trajectory_regulator",
        artifact_id=session_context.session_id,
        from_state=None,
        to_state=None,
        reason=signal_details,
        progress_summary_id=summary_id,
    )

    # Step 6: Return fresh session context with progress summary as seed
    new_artifacts = list(session_context.artifacts_produced) + [summary_id]
    return SessionContext(
        session_id=session_context.session_id,
        project_id=session_context.project_id,
        turn_count=0,
        context_tokens_estimate=0,
        context_window_limit=session_context.context_window_limit,
        artifacts_produced=new_artifacts,
        last_reset_at=now,
    )


async def inject_deterministic_recovery(
    signals: list[TrajectorySignal],
    config: "AipConfig | dict | None" = None,  # noqa: F821
) -> str:
    """Generate deterministic recovery instruction from signals.

    Lighter-weight than full context reset. Appends instruction
    to the next synthesis call's context.

    Args:
        signals: TrajectorySignals that triggered the intervention.
        config: AipConfig or dict.

    Returns:
        Recovery instruction string.
    """
    detected_types = sorted(set(s.failure_type for s in signals))
    instructions = []
    for ft in detected_types:
        template = _RECOVERY_TEMPLATES.get(ft, f"Address failure type {ft}.")
        instructions.append(template)

    return (
        "TRAJECTORY REGULATION — CORRECTIVE INSTRUCTION:\n"
        f"Detected issues: {', '.join(detected_types)}\n" + "\n".join(instructions)
    )
