"""Actor Protocols — ADR-011 (role boundaries) + ADR-014 §5.2 (extension actors).

Two concerns live here:

1. **VigilStore** — storage Protocol for the Vigil actor (canonical health
   monitoring, stale detection, health check recording). Pre-existing.

2. **Actor / ActorContext / ActorResult** — the actor-framework Protocol
   that extension-contributed actors conform to (ADR-014 §5.2). Beast,
   Vigil, and Sexton are NOT migrated to this Protocol — they keep their
   existing 12-param constructors and hand-wired schedulers in lifespan.
   New actors (ARISTOTLE's SOCRATES/EXAMINER/MENTOR, future LOOM/CodeForge
   actors) conform to this Protocol and are driven by ExtensionHost's
   ActorScheduler.

Layer: foundation. Pure types only — no I/O, no imports from orchestration
or adapter. The `container`, `config`, `logger`, and `cancel_event` fields
on ActorContext are typed as `Any` because foundation cannot import
AipContainer (adapter), BaseSettings (pydantic_settings, optional dep),
structlog.BoundLogger, or asyncio at this layer. The Protocol promises
shape, not concrete types; the host constructs ActorContext with the real
types and consumers access them via duck typing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aip.foundation.schemas import VigilHealthStatus


@runtime_checkable
class VigilStore(Protocol):
    """Protocol for Vigil actor storage needs (canonical health, entity consistency).

    Vigil is read-only; it detects and reports, never modifies autonomously.
    """

    async def get_canonical_health(self, artifact_id: str) -> dict | None:
        """Get health metadata for a canonical artifact.

        Returns dict with: artifact_id, last_evaluated_at, model_slot_used,
        faithfulness_score, domain_coherence_score, status (VigilHealthStatus).
        Returns None if artifact not found.
        """
        ...

    async def list_stale_canonicals(self, threshold_days: int) -> list[dict]:
        """Return canonical artifacts that have not been re-evaluated within threshold_days."""
        ...

    async def record_vigil_check(
        self,
        canonical_count: int,
        stale_count: int,
        status: VigilHealthStatus,
    ) -> None:
        """Record the result of a Vigil health check pass."""
        ...

    async def get_last_vigil_check(self) -> dict | None:
        """Return the most recent Vigil check result, or None if never run."""
        ...


# ---------------------------------------------------------------------------
# ADR-014 §5.2 — Actor Protocol for extension-contributed actors
# ---------------------------------------------------------------------------


@dataclass
class ActorContext:
    """Context passed to every `Actor.run_cycle()` call — ADR-014 §5.2.

    Carries the host container (for accessing CorpusRegistry, model providers,
    core actors like Vigil), the extension's own validated config, a bound
    logger, and the cancel event the scheduler sets on `host.stop()`.

    Layer note: `container`, `config`, `logger`, and `cancel_event` are typed
    as `Any` because foundation cannot import AipContainer (adapter),
    BaseSettings (pydantic_settings), structlog.BoundLogger, or asyncio.
    The host constructs this with the real types; consumers access them via
    duck typing. This preserves the foundation → adapter/orchestration import
    boundary.
    """

    container: Any
    config: Any
    logger: Any
    cancel_event: Any  # asyncio.Event at runtime


@dataclass
class ActorResult:
    """Return value of `Actor.run_cycle()` — ADR-014 §5.2.

    `next_run_at` overrides the actor's configured cadence for the next cycle
    only (epoch seconds). Use this for actors that need to back off after an
    error or speed up after a burst. None means "use the configured cadence".
    """

    ok: bool
    error: str | None = None
    next_run_at: float | None = None


@runtime_checkable
class Actor(Protocol):
    """Actor Protocol — ADR-014 §5.2.

    Extension-contributed actors conform to this Protocol. The host's
    ActorScheduler runs one asyncio.Task per registered actor, calling
    `run_cycle(ctx)` on the configured cadence (0 = manual only — the actor
    runs one cycle on start, then waits for cancellation).

    Core actors (Beast, Vigil, Sexton) do NOT conform to this Protocol —
    they keep their existing 12-param constructors and hand-wired schedulers
    in lifespan. ADR-014 §1: "Do not migrate Beast/Vigil/Sexton — adapt them
    at the boundary with a thin Actor-conforming wrapper" (future work, not
    required for v1.0).

    Conformance contract:
      - `name: str` — unique actor name across all extensions.
      - `cadence: float` — seconds between cycles; 0 = manual only.
      - `run_cycle(ctx) -> ActorResult` — async; called by the scheduler.
      - `health() -> dict` — sync; called by the health surface.

    The Protocol is `@runtime_checkable` so the host can validate conformance
    at registration time (a factory that returns a non-conforming object is
    logged as a warning and the actor is unregistered).
    """

    name: str
    cadence: float

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Run one actor cycle. Called by the scheduler on the configured cadence.

        Args:
            ctx: ActorContext with container, config, logger, cancel_event.

        Returns:
            ActorResult with ok/error/next_run_at. The scheduler logs
            non-ok results but does NOT transition the extension to DEGRADED
            — a single failed cycle is a transient event, not a lifecycle
            state change. Repeated failures should be surfaced via the
            actor's health() method.
        """
        ...

    def health(self) -> dict:
        """Return a health summary for this actor.

        Called by the health surface (container.extensions.health() can
        aggregate per-actor health if needed). Should include at minimum:
        `{"state": "active"|"degraded"|"failed", "last_run": ..., "error_count": ...}`.
        """
        ...


__all__ = [
    "VigilStore",
    "Actor",
    "ActorContext",
    "ActorResult",
]
