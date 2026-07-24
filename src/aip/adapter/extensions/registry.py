"""ExtensionRegistry — ADR-014 §2, §5, §7.

Per-extension state, failures, registered actors, and nav items. Owned by
`ExtensionHost`; the host drives state transitions through this registry.

This is NOT the module-level `_custom_channels` pattern — it's a host-owned
instance. The host creates one registry at construction; all registration
functions (`register_actor`, `register_channel`, `register_workflow`,
`register_page`) mutate THIS instance, not module globals.

Layer: adapter (lives under `aip.adapter.extensions`).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from aip.adapter.extensions.manifest import Manifest
from aip.adapter.extensions.state import ExtensionState, Failure


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class PendingCorpusProvider:
    """A dynamic corpus registration requested during on_load (ND9, 2026-07-23).

    Extensions call host.register_corpus_provider(role, type, ...) inside
    on_load. The host records a PendingCorpusProvider on the extension's
    record, then executes the async registration after on_load returns.
    This allows extensions to register corpora based on runtime conditions
    (e.g. config-driven corpus count) rather than only manifest-static ones.
    """

    role: str               # corpus role (e.g. "textbook"); corpus_id = {ext_id}:{role}
    corpus_type: str        # "conversation" | "code" | "document" | "book"
    db_path: str | None = None   # optional; defaults to {ext_dir}/{role}.db
    sensitive: bool = False
    access_note: str = ""


@dataclass
class ExtensionRecord:
    """Per-extension runtime record — one per discovered extension."""

    id: str
    manifest: Manifest | None = None       # None until stage 1 validate
    ext_dir: Path | None = None            # the on-disk extension directory
    state: ExtensionState = ExtensionState.DISCOVERED
    failures: list[Failure] = field(default_factory=list)

    # Registered contributions (populated at stage 3 register / stage 4 mount).
    actors: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    nav_items: list["NavItem"] = field(default_factory=list)

    # Actor scheduler tasks created by register_actor (for stop() to cancel).
    actor_tasks: list[asyncio.Task] = field(default_factory=list)

    # Validated config (the extension's own BaseSettings/dataclass instance).
    config: Any = None

    # Pending dynamic corpus providers (ND9, 2026-07-23).
    # Populated by host.register_corpus_provider() during on_load.
    # Executed by _migrate_register_ready_one after on_load returns.
    pending_corpus_providers: list["PendingCorpusProvider"] = field(default_factory=list)

    def add_failure(self, stage: str, contribution: str, reason: str) -> None:
        self.failures.append(Failure(stage=stage, contribution=contribution, reason=reason))


@dataclass
class NavItem:
    """One GUI nav entry — ADR-014 §5.1 `host.nav_items()` element (v1.1)."""

    ext_id: str
    label: str
    icon: str
    route: str
    order: int = 50
    builder_fn: Callable | None = None   # the page builder; None if not mounted


# --------------------------------------------------------------------------
# Actor registration record
# --------------------------------------------------------------------------


@dataclass
class ActorRegistration:
    """One actor registered via host.register_actor — ADR-014 §5.1, §5.2.

    start_policy (DEBT-020 fix, 2026-07-23):
        "scheduled"  — run one cycle immediately on start (default; safe for
                       read-only / health-check actors)
        "manual_only" — do NOT run a cycle on start; wait for explicit trigger
                       (safe for write-capable actors that must not execute
                       before gates are wired)
    """

    ext_id: str           # owning extension
    name: str             # actor name (unique within the host)
    factory: Callable[[], Any]   # zero-arg callable returning an Actor instance
    cadence: float        # seconds between cycles; 0 = manual only
    start_policy: str = "scheduled"  # "scheduled" | "manual_only" (DEBT-020)
    task: asyncio.Task | None = None   # the scheduler task once started


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class ExtensionRegistry:
    """Host-owned registry of discovered extensions and their contributions.

    One instance per ExtensionHost. All registration calls mutate THIS instance.
    """

    def __init__(self) -> None:
        self._records: dict[str, ExtensionRecord] = {}    # ext_id → record
        self._actors: dict[str, ActorRegistration] = {}   # actor_name → registration
        self._running: bool = False

    # ------------------------------------------------------------------
    # Record lifecycle
    # ------------------------------------------------------------------

    def upsert_record(self, ext_id: str) -> ExtensionRecord:
        """Get or create a record for ext_id. Returns the record."""
        if ext_id not in self._records:
            self._records[ext_id] = ExtensionRecord(id=ext_id)
        return self._records[ext_id]

    def get_record(self, ext_id: str) -> ExtensionRecord | None:
        return self._records.get(ext_id)

    def records(self) -> list[ExtensionRecord]:
        return list(self._records.values())

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def set_state(self, ext_id: str, state: ExtensionState) -> None:
        rec = self.upsert_record(ext_id)
        rec.state = state

    def add_failure(self, ext_id: str, *, stage: str, contribution: str, reason: str) -> None:
        rec = self.upsert_record(ext_id)
        rec.add_failure(stage=stage, contribution=contribution, reason=reason)

    # ------------------------------------------------------------------
    # Running flag (host.start() / host.stop())
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def mark_running(self) -> None:
        self._running = True

    def mark_stopped(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Actor registration (host.register_actor delegates here)
    # ------------------------------------------------------------------

    def register_actor(
        self,
        *,
        ext_id: str,
        name: str,
        factory: Callable[[], Any],
        cadence: float = 0.0,
        start_policy: str = "scheduled",
    ) -> None:
        """Register an actor factory. The host starts the scheduler task.

        Args:
            start_policy: "scheduled" (default) runs one cycle on start;
                "manual_only" skips the startup cycle (DEBT-020 fix — safe
                for write-capable actors).

        Raises ValueError if the actor name is already registered.
        """
        if name in self._actors:
            raise ValueError(
                f"Actor name {name!r} already registered (by extension "
                f"{self._actors[name].ext_id!r}). Actor names must be unique "
                f"across all extensions."
            )
        self._actors[name] = ActorRegistration(
            ext_id=ext_id, name=name, factory=factory, cadence=cadence,
            start_policy=start_policy,
        )
        # Record the actor name on the owning extension's record.
        rec = self.upsert_record(ext_id)
        if name not in rec.actors:
            rec.actors.append(name)

    def unregister_actor(self, name: str) -> ActorRegistration | None:
        """Remove an actor registration. Returns the removed registration or None."""
        reg = self._actors.pop(name, None)
        if reg is not None:
            rec = self.get_record(reg.ext_id)
            if rec is not None and reg.name in rec.actors:
                rec.actors.remove(reg.name)
        return reg

    def actor_registrations(self) -> list[ActorRegistration]:
        return list(self._actors.values())

    def registered_actor_names(self) -> list[str]:
        return list(self._actors.keys())

    def attach_actor_task(self, name: str, task: asyncio.Task) -> None:
        """Attach a started scheduler task to both the actor reg and ext record."""
        if name in self._actors:
            self._actors[name].task = task
        # Also push to every ext record's actor_tasks for stop() iteration.
        # (We push to the owning ext's record; stop() iterates all records.)
        reg = self._actors.get(name)
        if reg is not None:
            rec = self.get_record(reg.ext_id)
            if rec is not None:
                rec.actor_tasks.append(task)

    # ------------------------------------------------------------------
    # Channel / workflow / nav registration (host-owned, not module global)
    # ------------------------------------------------------------------

    def register_channel(self, *, ext_id: str, name: str) -> None:
        rec = self.upsert_record(ext_id)
        if name not in rec.channels:
            rec.channels.append(name)

    def register_workflow(self, *, ext_id: str, path: str) -> None:
        rec = self.upsert_record(ext_id)
        if path not in rec.workflows:
            rec.workflows.append(path)

    def register_nav_item(self, *, ext_id: str, item: NavItem) -> None:
        rec = self.upsert_record(ext_id)
        rec.nav_items.append(item)

    def nav_items(self) -> list[NavItem]:
        """All nav items across all extensions, sorted by order then label."""
        out: list[NavItem] = []
        for rec in self._records.values():
            out.extend(rec.nav_items)
        out.sort(key=lambda n: (n.order, n.label))
        return out

    # ------------------------------------------------------------------
    # Health snapshot — ADR-014 §7
    # ------------------------------------------------------------------

    def health_snapshot(self) -> list[dict]:
        """Return a list of per-extension health dicts.

        Each dict includes: id, version, state, failures, and nav_items
        (the GUI nav entries the extension registered via host.register_page).
        The nav_items field lets the GUI render extension nav entries
        dynamically — no hardcoded extension names in layout.py.
        """
        out: list[dict] = []
        for rec in self._records.values():
            out.append({
                "id": rec.id,
                "version": rec.manifest.version if rec.manifest else "unknown",
                "state": rec.state.value,
                "failures": [f.to_dict() for f in rec.failures],
                "nav_items": [
                    {
                        "label": item.label,
                        "route": item.route,
                        "icon": item.icon,
                        "order": item.order,
                    }
                    for item in rec.nav_items
                ],
            })
        # Sort by id for stable output.
        out.sort(key=lambda h: h["id"])
        return out
