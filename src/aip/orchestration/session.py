"""Multi-turn session context manager.

Context is assembled from explicit stores, not chat history.
Trajectory regulation at each turn boundary.
Context reset when session degrades.
Composes L5 workflow engine with L4 regulation layer.
"""

from __future__ import annotations

from aip.foundation.protocols import ArtifactStore, EcsStore, EventStore, TraceStore
from aip.foundation.schemas import SessionContext, TrajectorySignal
from aip.orchestration.l4_regulation.context_reset import (
    execute_context_reset,
    inject_deterministic_recovery,
)
from aip.orchestration.l4_regulation.regulator import regulate_trajectory, should_intervene


class SessionManager:
    """Manages multi-turn session state with trajectory regulation.

    Creates sessions, advances turns, checks trajectory,
    and handles interventions (recovery or reset).
    """

    def __init__(self, config: "AipConfig | dict | None" = None) -> None:  # noqa: F821
        cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
        self._config = cfg
        self._models_cfg = cfg.get("models", {})
        self._context_window_limit = self._models_cfg.get("context_window_limit", 128000)

    def create_session(self, session_id: str, project_id: str) -> SessionContext:
        """Initialize a fresh session context."""
        return SessionContext(
            session_id=session_id,
            project_id=project_id,
            turn_count=0,
            context_tokens_estimate=0,
            context_window_limit=self._context_window_limit,
            artifacts_produced=[],
            last_reset_at=None,
        )

    async def advance_turn(self, session_context: SessionContext, output_tokens: int) -> SessionContext:
        """Advance the session by one turn.

        Increments turn count and updates context token estimate.
        """
        # Estimate: prior context + new output (rough approximation)
        new_estimate = session_context.context_tokens_estimate + output_tokens
        return SessionContext(
            session_id=session_context.session_id,
            project_id=session_context.project_id,
            turn_count=session_context.turn_count + 1,
            context_tokens_estimate=new_estimate,
            context_window_limit=session_context.context_window_limit,
            artifacts_produced=session_context.artifacts_produced,
            last_reset_at=session_context.last_reset_at,
        )

    async def check_trajectory(
        self,
        session_context: SessionContext,
        trace_store: TraceStore,
    ) -> tuple[list[TrajectorySignal], bool]:
        """Run trajectory regulation and check if intervention is needed.

        Returns:
            Tuple of (signals, should_intervene_flag).
        """
        signals = await regulate_trajectory(session_context, trace_store, self._config)
        intervene = should_intervene(signals, self._config)  # sync call
        return signals, intervene

    async def handle_intervention(
        self,
        session_context: SessionContext,
        signals: list[TrajectorySignal],
        artifact_store: ArtifactStore,
        trace_store: TraceStore,
        event_store: EventStore,
        ecs_store: EcsStore,
    ) -> SessionContext:
        """Handle trajectory intervention.

        If any signal is Type D or Type F: execute full context reset.
        If only Type E: inject deterministic recovery instruction.

        Returns:
            Updated SessionContext (fresh after reset, or same after recovery).
        """
        failure_types = {s.failure_type for s in signals}

        # Full reset for drift (D) or anxiety (F)
        if "D" in failure_types or "F" in failure_types:
            return await execute_context_reset(
                session_context,
                signals,
                artifact_store,
                trace_store,
                event_store,
                ecs_store,
                self._config,
            )

        # Lighter recovery for failure streak (E)
        _recovery = await inject_deterministic_recovery(signals, self._config)
        # The recovery instruction is returned to the caller
        # (workflow engine) to inject into the next synthesis call.
        # For now, we store it in session context metadata via a simple approach:
        # the caller checks this by calling inject_deterministic_recovery directly.
        return session_context

    def context_utilization(self, session_context: SessionContext) -> float:
        """Return current context window utilization ratio."""
        if session_context.context_window_limit <= 0:
            return 0.0
        return session_context.context_tokens_estimate / session_context.context_window_limit
