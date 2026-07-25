"""Tests for Context Reset Protocol."""

import pytest

from aip.foundation.schemas import SessionContext, TrajectorySignal
from aip.orchestration.l4_regulation.context_reset import (
    execute_context_reset,
    inject_deterministic_recovery,
)


class FakeArtifactStore:
    def __init__(self):
        self._data = {}

    async def write(self, id, content, metadata):
        self._data[id] = {"content": content, "metadata": metadata}

    async def read(self, id, version=None):
        return self._data.get(id, {}).get("content", "")


class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None, **kwargs):
        ev = {"node_type": node_type, "intervention_type": kwargs.get("intervention_type", "context_reset")}
        ev.update(kwargs)
        self.events.append(ev)


class FakeEventStore:
    def __init__(self):
        self.events = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append({"event_type": event_type, "actor": actor, **kwargs})


class FakeEcsStore:
    def __init__(self):
        self.transitions = []

    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        self.transitions.append({"to_state": to_state})


@pytest.mark.asyncio
async def test_execute_context_reset_full_protocol():
    session_ctx = SessionContext(
        session_id="s1",
        project_id="p1",
        turn_count=12,
        artifacts_produced=["a1", "a2"],
    )
    signals = [
        TrajectorySignal(signal_type="anxiety", session_id="s1", failure_type="F", detail="length collapse"),
    ]

    artifact = FakeArtifactStore()
    trace = FakeTraceStore()
    events = FakeEventStore()
    ecs = FakeEcsStore()

    new_ctx = await execute_context_reset(session_ctx, signals, artifact, trace, events, ecs)

    # Step 3: progress summary written
    assert any("progress_summary" in k for k in artifact._data.keys())

    # Step 4: trace event with intervention_type
    assert any(e.get("intervention_type") == "context_reset" for e in trace.events)

    # Step 5: surfaced to DEFINER
    assert any(e.get("event_type") == "context_reset" for e in events.events)

    # Step 6: fresh context
    assert new_ctx.turn_count == 0
    assert new_ctx.last_reset_at is not None

    # (f) ECS transition recorded (per §10.2 prose + gate verification list)
    assert len(ecs.transitions) >= 1

    # intervention fields on trace (prose step 4 + historical compatibility)
    assert any(
        e.get("intervention_type") == "context_reset" and e.get("intervention_applied") == 1 for e in trace.events
    )


@pytest.mark.asyncio
async def test_inject_deterministic_recovery():
    signals = [
        TrajectorySignal(signal_type="loop", session_id="s1", failure_type="D"),
        TrajectorySignal(signal_type="anxiety", session_id="s1", failure_type="F"),
    ]
    instruction = await inject_deterministic_recovery(signals)
    assert "TRAJECTORY REGULATION" in instruction
    assert "D" in instruction and "F" in instruction
