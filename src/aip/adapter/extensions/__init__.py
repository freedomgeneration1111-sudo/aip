"""ExtensionHost package — ADR-014 Phase 0 Extension Platform.

Public API:
  - ExtensionHost: lifecycle driver (discover → validate → migrate → register → ready → stop).
  - ExtensionState: 8 per-extension lifecycle states.
  - ExtensionRegistry: host-owned registry of extensions + contributions.
  - Manifest: pydantic v2 manifest model (v1 schema).
  - Failure: structured per-contribution failure record.
  - NavItem: GUI nav entry (v1.1).
  - supervised_task: named, supervised asyncio.create_task wrapper.

Layer: adapter (wires the container, FastAPI, and eventually GUI). Imports
from foundation only (via the existing CorpusRegistry / CorpusType / etc.).

Pinned by tests/test_extension_lifecycle.py.
"""
from __future__ import annotations

from aip.adapter.extensions.host import ExtensionHost
from aip.adapter.extensions.manifest import (
    ConfigBlock,
    Contributes,
    CorpusContribution,
    GuiContribution,
    Manifest,
)
from aip.adapter.extensions.registry import (
    ActorRegistration,
    ExtensionRecord,
    ExtensionRegistry,
    NavItem,
)
from aip.adapter.extensions.state import ExtensionState, Failure
from aip.adapter.extensions.supervision import supervised_task

__all__ = [
    "ExtensionHost",
    "ExtensionState",
    "ExtensionRegistry",
    "ExtensionRecord",
    "Manifest",
    "Contributes",
    "CorpusContribution",
    "GuiContribution",
    "ConfigBlock",
    "Failure",
    "NavItem",
    "ActorRegistration",
    "supervised_task",
]
