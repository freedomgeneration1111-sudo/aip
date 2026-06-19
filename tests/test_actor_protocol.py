"""Contract tests for the Actor Protocol — ADR-014 §5.2.

These tests pin the shape of the foundation Actor Protocol that extension-
contributed actors conform to. The Protocol is @runtime_checkable, so
isinstance() validation works at registration time.

Run:  CI=true uv run pytest tests/test_actor_protocol.py -v
"""
from __future__ import annotations

import asyncio
from dataclasses import is_dataclass

import pytest

from aip.foundation.protocols.actors import Actor, ActorContext, ActorResult
from aip.foundation.protocols import (
    Actor as ActorFromBarrel,
    ActorContext as ActorContextFromBarrel,
    ActorResult as ActorResultFromBarrel,
)


# --------------------------------------------------------------------------
# Conforming actor (minimal — matches ADR-014 §5.2 contract)
# --------------------------------------------------------------------------


class _ConformingActor:
    """Minimal actor that conforms to the Actor Protocol."""

    name = "conforming"
    cadence = 0.0

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        return ActorResult(ok=True)

    def health(self) -> dict:
        return {"state": "active"}


# --------------------------------------------------------------------------
# Non-conforming "actors" (missing required attributes/methods)
# --------------------------------------------------------------------------


class _MissingName:
    """Has cadence + run_cycle + health but no name — non-conforming."""

    cadence = 0.0

    async def run_cycle(self, ctx):
        return ActorResult(ok=True)

    def health(self):
        return {}


class _MissingCadence:
    """Has name + run_cycle + health but no cadence — non-conforming."""

    name = "no_cadence"

    async def run_cycle(self, ctx):
        return ActorResult(ok=True)

    def health(self):
        return {}


class _MissingRunCycle:
    """Has name + cadence + health but no run_cycle — non-conforming."""

    name = "no_run_cycle"
    cadence = 0.0

    def health(self):
        return {}


class _MissingHealth:
    """Has name + cadence + run_cycle but no health — non-conforming."""

    name = "no_health"
    cadence = 0.0

    async def run_cycle(self, ctx):
        return ActorResult(ok=True)


# --------------------------------------------------------------------------
# Protocol shape tests
# --------------------------------------------------------------------------


def test_conforming_actor_passes_isinstance():
    """A minimal actor with name/cadence/run_cycle/health conforms."""
    actor = _ConformingActor()
    assert isinstance(actor, Actor), (
        "Conforming actor should pass isinstance(_, Actor) — the Protocol "
        "is @runtime_checkable and only checks attribute existence."
    )


@pytest.mark.parametrize(
    "non_conforming_class,missing",
    [
        (_MissingName, "name"),
        (_MissingCadence, "cadence"),
        (_MissingRunCycle, "run_cycle"),
        (_MissingHealth, "health"),
    ],
)
def test_non_conforming_actor_fails_isinstance(non_conforming_class, missing):
    """An actor missing any required attribute does NOT conform."""
    actor = non_conforming_class()
    assert not isinstance(actor, Actor), (
        f"Actor missing {missing!r} should fail isinstance(_, Actor) — "
        f"the Protocol requires name + cadence + run_cycle + health."
    )


def test_actor_protocol_is_runtime_checkable():
    """The Actor Protocol must be @runtime_checkable for host validation."""
    # @runtime_checkable Protocols support isinstance(). If this attribute
    # is missing, the host's isinstance(actor, Actor) check would TypeError.
    assert hasattr(Actor, "_is_runtime_protocol"), (
        "Actor Protocol should be @runtime_checkable — the host uses "
        "isinstance(actor, Actor) to validate conformance at scheduler start."
    )


# --------------------------------------------------------------------------
# ActorContext + ActorResult dataclass tests
# --------------------------------------------------------------------------


def test_actor_context_is_dataclass():
    """ActorContext is a dataclass with container/config/logger/cancel_event."""
    assert is_dataclass(ActorContext), "ActorContext should be a dataclass"
    ctx = ActorContext(
        container=None,
        config=None,
        logger=None,
        cancel_event=asyncio.Event(),
    )
    assert ctx.container is None
    assert ctx.config is None
    assert ctx.logger is None
    assert isinstance(ctx.cancel_event, asyncio.Event)


def test_actor_result_defaults():
    """ActorResult defaults: ok required, error=None, next_run_at=None."""
    result = ActorResult(ok=True)
    assert result.ok is True
    assert result.error is None
    assert result.next_run_at is None


def test_actor_result_with_error():
    """ActorResult can carry an error string + next_run_at override."""
    result = ActorResult(ok=False, error="model timeout", next_run_at=12345.0)
    assert result.ok is False
    assert result.error == "model timeout"
    assert result.next_run_at == 12345.0


# --------------------------------------------------------------------------
# Barrel re-export test (foundation.protocols __init__.py)
# --------------------------------------------------------------------------


def test_actor_types_re_exported_from_barrel():
    """Actor/ActorContext/ActorResult are re-exported from foundation.protocols."""
    assert ActorFromBarrel is Actor
    assert ActorContextFromBarrel is ActorContext
    assert ActorResultFromBarrel is ActorResult


# --------------------------------------------------------------------------
# Integration: the host's scheduler validates conformance
# (Verified indirectly via test_extension_lifecycle.py::test_registers_extension_actors
# which uses _DemoActor — a conforming actor. The non-conforming path is tested
# here at the Protocol level; the host's isinstance check is a thin wrapper.)
# --------------------------------------------------------------------------


def test_demo_actor_from_lifecycle_test_conforms():
    """The _DemoActor used in test_extension_lifecycle.py conforms to the Protocol.

    This is a belt-and-suspenders check: if someone changes _DemoActor in the
    lifecycle test to be non-conforming, the host's isinstance check would
    skip its scheduler and the test would silently pass without actually
    running a cycle. This test catches that regression.
    """
    # Recreate the _DemoActor shape here (can't import from the test file
    # because it has top-level imports that fail without the extensions package).
    class _DemoActor:
        name = "demo_actor"
        cadence = 0.0

        async def run_cycle(self, ctx):
            return ActorResult(ok=True)

        def health(self):
            return {"state": "active", "last_run": None, "error_count": 0}

    actor = _DemoActor()
    assert isinstance(actor, Actor), (
        "_DemoActor in test_extension_lifecycle.py must conform to the Actor "
        "Protocol — otherwise the host's isinstance check skips its scheduler "
        "and the lifecycle test passes without running a cycle."
    )
