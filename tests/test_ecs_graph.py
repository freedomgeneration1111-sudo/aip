"""Tests for ECS state graph and guardrailed store."""

import pytest

from aip.foundation.ecs_graph import (
    ALL_STATES,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    is_terminal,
    validate_transition,
)
from aip.foundation.protocols import EcsStore, EventStore

# --- Pure graph validation tests ---


def test_all_valid_transitions_pass():
    """Every transition in VALID_TRANSITIONS must pass validation."""
    for from_state, to_states in VALID_TRANSITIONS.items():
        for to_state in to_states:
            validate_transition(from_state, to_state)  # should not raise


def test_invalid_transitions_raise():
    """Known invalid transitions must raise InvalidTransitionError."""
    invalid = [
        ("SPECIFIED", "APPROVED"),  # skip GENERATED and REVIEWED
        ("GENERATED", "APPROVED"),  # skip REVIEWED
        ("REVIEWED", "GENERATED"),  # cannot go back to GENERATED
        ("APPROVED", "GENERATED"),  # cannot go back
        ("SUPERSEDED", "APPROVED"),  # terminal state
        ("SUPERSEDED", "SPECIFIED"),  # terminal state
        # ARCHIVED transitions — ADR-008 Rev 3.1 §5.1
        ("SPECIFIED", "ARCHIVED"),  # cannot archive before generation
        ("ARCHIVED", "GENERATED"),  # terminal state
        ("ARCHIVED", "APPROVED"),  # terminal state
        ("ARCHIVED", "SPECIFIED"),  # terminal state
    ]
    for from_state, to_state in invalid:
        with pytest.raises(InvalidTransitionError):
            validate_transition(from_state, to_state)


def test_unknown_from_state_raises():
    with pytest.raises(InvalidTransitionError):
        validate_transition("NONEXISTENT", "GENERATED")


def test_superseded_is_terminal():
    assert is_terminal("SUPERSEDED")


def test_archived_is_terminal():
    """ARCHIVED is a terminal state — ADR-008 Rev 3.1 §5.1."""
    assert is_terminal("ARCHIVED")


def test_archived_valid_transitions():
    """GENERATED, REVIEWED, APPROVED can all transition to ARCHIVED — ADR-008 Rev 3.1 §5.1."""
    # These should NOT raise
    validate_transition("GENERATED", "ARCHIVED")
    validate_transition("REVIEWED", "ARCHIVED")
    validate_transition("APPROVED", "ARCHIVED")


def test_all_states_accounted_for():
    expected = {"SPECIFIED", "GENERATED", "REVIEWED", "APPROVED", "SUPERSEDED", "FAILED", "REJECTED", "ARCHIVED"}
    assert ALL_STATES == expected


# --- Minimal fake stores for guardrail testing ---


class FakeEcsStore(EcsStore):
    def __init__(self):
        self._states: dict[str, str] = {}

    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        self._states[artifact_id] = to_state

    async def current_state(self, artifact_id):
        return self._states.get(artifact_id)


class FakeEventStore(EventStore):
    def __init__(self):
        self.events = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "artifact_id": artifact_id,
                "from_state": from_state,
                "to_state": to_state,
            },
        )

    async def query(self, *a, **k):
        return []


import asyncio  # noqa: E402 -- import after class definitions for guardrailed tests

# --- GuardrailedEcsStore tests ---


def test_guardrailed_store_valid_transition_writes_event():
    from aip.adapter.ecs_store_guardrailed import GuardrailedEcsStore

    underlying = FakeEcsStore()
    events = FakeEventStore()
    guard = GuardrailedEcsStore(underlying, events)

    # First transition: SPECIFIED → GENERATED (no current state yet)
    import asyncio

    asyncio.run(guard.transition("art1", None, "GENERATED", "definer", "initial generation"))

    assert guard._state["art1"] == "GENERATED"
    assert len(events.events) == 1
    assert events.events[0]["to_state"] == "GENERATED"


def test_guardrailed_store_invalid_transition_raises():
    from aip.adapter.ecs_store_guardrailed import GuardrailedEcsStore

    underlying = FakeEcsStore()
    events = FakeEventStore()
    guard = GuardrailedEcsStore(underlying, events)

    asyncio.run(guard.transition("art1", None, "GENERATED", "definer", "gen"))

    with pytest.raises(InvalidTransitionError):
        asyncio.run(guard.transition("art1", "GENERATED", "APPROVED", "someone", "invalid"))


def test_guardrailed_store_from_state_precondition():
    from aip.adapter.ecs_store_guardrailed import GuardrailedEcsStore

    underlying = FakeEcsStore()
    events = FakeEventStore()
    guard = GuardrailedEcsStore(underlying, events)

    asyncio.run(guard.transition("art1", None, "GENERATED", "definer", "gen"))

    with pytest.raises(InvalidTransitionError):
        asyncio.run(guard.transition("art1", "REVIEWED", "APPROVED", "definer", "bad precondition"))
