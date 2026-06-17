"""ECS state graph — declarative valid transitions.

Single source of truth for artifact lifecycle state machine.
No storage, no I/O — pure validation logic in foundation layer.

ADR-008 Rev 3.1 §5.1: ARCHIVED added as a second terminal state alongside
SUPERSEDED. Semantic distinction:
  - SUPERSEDED = canonical artifact made obsolete by a conceptual replacement
  - ARCHIVED   = content intentionally withdrawn from retrieval (e.g., an
                 old manuscript chapter draft replaced by a new revision),
                 while remaining on disk for revision-history traversal.

Both are terminal — no exits from either state. ARCHIVED is reachable from
GENERATED, REVIEWED, and APPROVED (but NOT from SPECIFIED — you cannot
archive something that has not been generated yet).
"""

from __future__ import annotations

# Declarative ECS state graph
VALID_TRANSITIONS: dict[str, set[str]] = {
    "SPECIFIED": {"GENERATED"},
    "GENERATED": {"REVIEWED", "REJECTED", "FAILED", "ARCHIVED"},
    "REVIEWED": {"APPROVED", "REJECTED", "ARCHIVED"},
    "REJECTED": {"GENERATED"},  # re-synthesis loop
    "APPROVED": {"SUPERSEDED", "ARCHIVED"},
    "FAILED": {"SPECIFIED"},  # re-specify after failure
    "SUPERSEDED": set(),  # terminal state
    "ARCHIVED": set(),  # terminal — content withdrawn from retrieval (ADR-008 Rev 3.1)
}

# All known states
ALL_STATES: set[str] = set(VALID_TRANSITIONS.keys())

# Terminal states (no outgoing transitions) — ADR-008 Rev 3.1 §5.1
TERMINAL_STATES: frozenset[str] = frozenset({state for state, targets in VALID_TRANSITIONS.items() if not targets})


class InvalidTransitionError(Exception):
    """Raised when an ECS transition violates the state graph.

    This is a controlled rejection, not a crash.
    No action may bypass DEFINER gates.
    The graph makes it structurally impossible to skip states.
    """

    def __init__(self, from_state: str, to_state: str, message: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(message or f"Invalid ECS transition: {from_state} → {to_state}")


def validate_transition(from_state: str, to_state: str) -> None:
    """Validate that a transition is allowed by the state graph.

    Raises InvalidTransitionError if the transition is not valid.
    """
    if from_state not in VALID_TRANSITIONS:
        raise InvalidTransitionError(
            from_state,
            to_state,
            f"Unknown from_state: {from_state!r}. Known states: {sorted(ALL_STATES)}",
        )
    allowed = VALID_TRANSITIONS[from_state]
    if to_state not in allowed:
        raise InvalidTransitionError(
            from_state,
            to_state,
            f"Transition {from_state} → {to_state} not allowed. Allowed from {from_state}: {sorted(allowed)}",
        )


def is_terminal(state: str) -> bool:
    """Return True if the state has no outgoing transitions."""
    return len(VALID_TRANSITIONS.get(state, set())) == 0
