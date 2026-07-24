"""DEBT-020 fix (2026-07-23) — start_policy field prevents startup hazard.

Before this fix, the _actor_scheduler_loop ran one cycle immediately for
ALL actors at startup (host.py:179-180), including cadence=0 manual-only
actors. This meant a write-capable extension actor (e.g. one that writes
to corpus) would execute at boot before any gate could intervene.

The fix adds a `start_policy` field:
  "scheduled"  — run one cycle on start (default; backward compatible)
  "manual_only" — skip the startup cycle (safe for write-capable actors)

ADR-014 §5.2, DEBT-020.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from aip.adapter.extensions.host import ExtensionHost, _actor_scheduler_loop
from aip.adapter.extensions.registry import ActorRegistration
from aip.foundation.protocols.actors import Actor, ActorContext, ActorResult


class _CycleCountingActor:
    """Test actor that counts how many times run_cycle was called."""

    def __init__(self):
        self.cycle_count = 0

    @property
    def name(self) -> str:
        return "test-actor"

    @property
    def cadence(self) -> float:
        return 0.0  # manual only

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        self.cycle_count += 1
        return ActorResult(ok=True)

    def health(self) -> dict:
        return {"state": "active", "cycles": self.cycle_count}


class TestStartPolicyFix:
    """DEBT-020 — verify start_policy prevents the startup hazard."""

    def test_actor_registration_has_start_policy_field(self):
        """ActorRegistration must have a start_policy field defaulting to 'scheduled'."""
        reg = ActorRegistration(
            ext_id="test",
            name="test-actor",
            factory=lambda: None,
            cadence=0.0,
        )
        assert reg.start_policy == "scheduled", (
            "default start_policy must be 'scheduled' (backward compat)"
        )

    def test_start_policy_can_be_set_to_manual_only(self):
        """ActorRegistration accepts start_policy='manual_only'."""
        reg = ActorRegistration(
            ext_id="test",
            name="test-actor",
            factory=lambda: None,
            cadence=0.0,
            start_policy="manual_only",
        )
        assert reg.start_policy == "manual_only"

    async def test_scheduled_actor_runs_startup_cycle(self):
        """start_policy='scheduled' runs one cycle on startup (default behavior)."""
        actor = _CycleCountingActor()
        reg = ActorRegistration(
            ext_id="test",
            name="test-actor",
            factory=lambda: actor,
            cadence=0.0,
            start_policy="scheduled",
        )
        cancel_event = asyncio.Event()

        # Run the scheduler loop — it will run one cycle then wait on cancel_event
        # We cancel immediately after the first cycle to avoid hanging
        async def _cancel_after_delay():
            await asyncio.sleep(0.1)
            cancel_event.set()

        asyncio.create_task(_cancel_after_delay())

        ctx = ActorContext(container=None, config=None, logger=None, cancel_event=cancel_event)
        await _actor_scheduler_loop(
            registration=reg,
            container=None,
            config=None,
            manifest=None,
            cancel_event=cancel_event,
        )

        assert actor.cycle_count == 1, (
            f"scheduled actor must run exactly 1 cycle on startup, got {actor.cycle_count}"
        )

    async def test_manual_only_actor_skips_startup_cycle(self):
        """start_policy='manual_only' does NOT run a cycle on startup.

        This is the DEBT-020 fix: write-capable actors must not execute
        before gates are wired.
        """
        actor = _CycleCountingActor()
        reg = ActorRegistration(
            ext_id="test",
            name="test-actor",
            factory=lambda: actor,
            cadence=0.0,
            start_policy="manual_only",  # THE FIX
        )
        cancel_event = asyncio.Event()

        # Cancel immediately — the scheduler should skip the startup cycle
        # and go straight to waiting on cancel_event
        async def _cancel_after_delay():
            await asyncio.sleep(0.1)
            cancel_event.set()

        asyncio.create_task(_cancel_after_delay())

        await _actor_scheduler_loop(
            registration=reg,
            container=None,
            config=None,
            manifest=None,
            cancel_event=cancel_event,
        )

        assert actor.cycle_count == 0, (
            f"manual_only actor must NOT run any cycle on startup, got {actor.cycle_count}. "
            f"This is the DEBT-020 fix — write-capable actors must not execute before gates are wired."
        )

    def test_host_register_actor_accepts_start_policy(self):
        """host.register_actor must accept start_policy as a keyword arg."""
        import inspect
        from aip.adapter.extensions.host import ExtensionHost

        sig = inspect.signature(ExtensionHost.register_actor)
        assert "start_policy" in sig.parameters, (
            "register_actor must have a start_policy parameter"
        )
        assert sig.parameters["start_policy"].default == "scheduled", (
            "start_policy default must be 'scheduled'"
        )
