"""ExtensionState — ADR-014 §3.

Pure foundation-level types for the extension lifecycle. No I/O, no imports
from orchestration or adapter internals (only from `aip.foundation` if needed).

Layer: adapter (lives under `aip.adapter.extensions` because the host wires the
container, FastAPI, and GUI — but this module itself is pure).

Contracts (consumed by ExtensionHost, ExtensionRegistry, health surface, GUI):
  - ExtensionState: 8 states with terminal-ish semantics.
  - Failure: structured per-contribution failure record for the health surface.

Pinned by tests/test_extension_lifecycle.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExtensionState(str, Enum):
    """Per-extension lifecycle state — ADR-014 §3.

    Terminal-ish semantics:
      - FAILED / DISABLED do not serve.
      - DEGRADED serves what it can.
      - DISCOVERED / VALIDATED / MIGRATING are transient (only mid-start).
      - REGISTERED is the v1.0 terminal state (backend live, no GUI mount).
      - MOUNTED is the v1.1 terminal state (GUI mounted, fully live).
    """

    DISCOVERED = "DISCOVERED"   # manifest found, not yet parsed
    VALIDATED = "VALIDATED"     # manifest + config schema valid
    MIGRATING = "MIGRATING"     # running contributed migrations
    REGISTERED = "REGISTERED"   # corpora/channels/actors/workflows registered
    MOUNTED = "MOUNTED"         # GUI mounted (v1.1); fully live
    DEGRADED = "DEGRADED"       # partially up; one contribution failed, host intact
    DISABLED = "DISABLED"       # operator-disabled OR host.stop() called; not serving
    FAILED = "FAILED"           # could not reach REGISTERED; isolated, host intact


@dataclass(frozen=True)
class Failure:
    """Structured per-contribution failure record — ADR-014 §7.

    Carries enough to debug without reading logs. Surfaced verbatim in the
    health output (`container.extensions.health()`).
    """

    stage: str             # "discover" | "validate" | "migrate" | "register" | "mount" | "ready"
    contribution: str      # "manifest" | "config" | "migrations" | "corpora" | "channels" | "actors" | "workflows_dir" | "gui" | "hook"
    reason: str            # human-readable error message (typically str(exc))

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "contribution": self.contribution,
            "reason": self.reason,
        }
