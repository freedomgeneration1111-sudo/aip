"""ExtensionHost — ADR-014 §2, §4, §5.

The lifecycle driver for the extension platform. One instance per AipContainer,
instantiated in lifespan. Drives every extension through:

    discover -> validate -> migrate -> register -> [mount v1.1] -> ready

Each stage is per-extension and sandbox-wrapped: a raise transitions that
extension to DEGRADED/FAILED and never propagates to the host. The host itself
stays up so a broken extension doesn't take down the shell.

The lifespan learns about extensions ONLY through this host:

    host = ExtensionHost(extensions_dir=..., container=container)
    container.extensions = host
    await host.start()
    # ... existing actor schedulers start after host.start()
    # On shutdown:
    await host.stop()

Layer: adapter (wires the container, FastAPI, and eventually GUI).

Pinned by tests/test_extension_lifecycle.py (11 contract tests).
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from aip.adapter.extensions.loaders.migration_loader import (
    LoadedMigration,
    apply_extension_migrations,
    load_migrations_dir,
)
from aip.adapter.extensions.manifest import Manifest
from aip.adapter.extensions.registry import (
    ActorRegistration,
    ExtensionRegistry,
    ExtensionRecord,
    NavItem,
)
from aip.adapter.extensions.state import ExtensionState
from aip.adapter.extensions.supervision import supervised_task
from aip.foundation.corpus_types import CorpusType

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _parse_python_path(path: str) -> tuple[str, str]:
    """Split 'pkg.module:attr' → ('pkg.module', 'attr'). Raises ValueError if no ':'."""
    if ":" not in path:
        raise ValueError(
            f"Python path {path!r} must be in 'module:attr' form (e.g. 'aristotle.config:AristotleSettings')."
        )
    module, _, attr = path.partition(":")
    if not module or not attr:
        raise ValueError(f"Python path {path!r} has empty module or attr.")
    return module, attr


def _import_class(path: str) -> type:
    """Import 'pkg.module:ClassName' and return the class.

    Raises ImportError if the module can't be imported; AttributeError if the
    class doesn't exist. The host catches both as config-schema failures.
    """
    module_name, attr = _parse_python_path(path)
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _validate_config_schema_class(cls: type) -> None:
    """Verify the imported class is a BaseSettings or dataclass subclass.

    ADR-014 §6.4: arbitrary classes are rejected. Accepts pydantic_settings
    BaseSettings subclasses or dataclass-decorated types.
    """
    import dataclasses

    if dataclasses.is_dataclass(cls):
        return
    # Try pydantic_settings.BaseSettings without hard-importing it (it's an
    # optional dep — some configs may use a plain dataclass instead).
    try:
        from pydantic_settings import BaseSettings  # type: ignore
        if isinstance(cls, type) and issubclass(cls, BaseSettings):
            return
    except ImportError:
        pass
    # Fallback: accept pydantic BaseModel (also a valid config container).
    try:
        from pydantic import BaseModel  # type: ignore
        if isinstance(cls, type) and issubclass(cls, BaseModel):
            return
    except ImportError:
        pass
    raise TypeError(
        f"config.schema class {cls.__name__!r} must be a dataclass, "
        f"pydantic.BaseModel, or pydantic_settings.BaseSettings subclass."
    )


# --------------------------------------------------------------------------
# Actor scheduler
# --------------------------------------------------------------------------


async def _actor_scheduler_loop(
    *,
    registration: ActorRegistration,
    container: Any,
    config: Any,
    manifest: Manifest,
    cancel_event: asyncio.Event,
) -> None:
    """Run an actor's run_cycle() on its configured cadence until cancelled.

    cadence=0 means "manual only" — the loop starts, runs one cycle, then
    waits on the cancel event forever (no automatic re-runs). This is the
    ARISTOTLE shape (synchronous tutoring state machine; cycles are driven
    by user turns, not by the scheduler).

    cadence>0 means run every `cadence` seconds, gated on cancel_event.
    """
    # Build the actor instance once.
    try:
        actor = registration.factory()
    except Exception as exc:
        logger.warning(
            "actor_factory_failed ext=%s name=%s error=%s:%s",
            registration.ext_id, registration.name,
            type(exc).__name__, exc,
        )
        return

    # Minimal ActorContext per ADR-014 §5.2.
    ctx = _ActorContext(
        container=container,
        config=config,
        cancel_event=cancel_event,
    )

    # Run one cycle immediately (so manual-only actors do something on start).
    try:
        await actor.run_cycle(ctx)
    except Exception as exc:
        logger.warning(
            "actor_cycle_failed ext=%s name=%s error=%s:%s",
            registration.ext_id, registration.name,
            type(exc).__name__, exc,
        )

    # If cadence is 0, wait forever for cancellation (manual-only actor).
    if registration.cadence <= 0:
        await cancel_event.wait()
        return

    # Cadence > 0: loop until cancelled.
    while not cancel_event.is_set():
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=registration.cadence)
        except asyncio.TimeoutError:
            # Cadence interval elapsed — run a cycle.
            try:
                await actor.run_cycle(ctx)
            except Exception as exc:
                logger.warning(
                    "actor_cycle_failed ext=%s name=%s error=%s:%s",
                    registration.ext_id, registration.name,
                    type(exc).__name__, exc,
                )


@dataclass
class _ActorContext:
    """Minimal ActorContext — ADR-014 §5.2.

    Carries the container, the extension's validated config, and the cancel
    event. Passed to every actor.run_cycle() call.
    """
    container: Any
    config: Any
    cancel_event: asyncio.Event


# --------------------------------------------------------------------------
# ExtensionHost
# --------------------------------------------------------------------------


class ExtensionHost:
    """Lifecycle driver for the extension platform — ADR-014 §2, §4, §5.

    One instance per AipContainer. Instantiated in lifespan; drives every
    extension through discover → validate → migrate → register → (mount v1.1)
    → ready. Each stage is sandbox-wrapped per extension.
    """

    def __init__(
        self,
        *,
        extensions_dir: Path,
        container: Any,
        manifest_version_range: tuple[int, int] = (1, 1),
        workflow_registry: Any = None,
    ) -> None:
        self._extensions_dir = Path(extensions_dir)
        self._container = container
        self._manifest_version_range = manifest_version_range
        self._workflow_registry = workflow_registry
        self._registry = ExtensionRegistry()
        # Per-actor cancel events (set by stop() to unblock scheduler loops).
        self._cancel_events: dict[str, asyncio.Event] = {}
        # The currently-executing on_load extension id (for host.config/manifest).
        self._current_ext_id: str | None = None

    # ------------------------------------------------------------------
    # Public properties — ADR-014 §5.1
    # ------------------------------------------------------------------

    @property
    def container(self) -> Any:
        return self._container

    @property
    def registry(self) -> ExtensionRegistry:
        """Direct registry access (for advanced consumers / tests)."""
        return self._registry

    @property
    def manifest(self) -> Manifest:
        """The currently-executing extension's manifest (inside on_load only)."""
        if self._current_ext_id is None:
            raise RuntimeError(
                "host.manifest is only available inside an extension's on_load hook."
            )
        rec = self._registry.get_record(self._current_ext_id)
        if rec is None or rec.manifest is None:
            raise RuntimeError(
                f"No manifest loaded for current extension {self._current_ext_id!r}."
            )
        return rec.manifest

    @property
    def config(self) -> Any:
        """The currently-executing extension's validated config (inside on_load only)."""
        if self._current_ext_id is None:
            raise RuntimeError(
                "host.config is only available inside an extension's on_load hook."
            )
        rec = self._registry.get_record(self._current_ext_id)
        if rec is None:
            raise RuntimeError(
                f"No record for current extension {self._current_ext_id!r}."
            )
        return rec.config

    # ------------------------------------------------------------------
    # Lifecycle — ADR-014 §4
    # ------------------------------------------------------------------

    async def discover(self) -> list[ExtensionRecord]:
        """Stage 0: find extension.yaml files under the operator-owned dir.

        Records are keyed by **directory name** (the unique physical key). The
        manifest's `id` field is checked for collisions at stage 1 validate.

        Returns the list of discovered records (state=DISCOVERED). Idempotent —
        re-running after start() returns the same records without re-parsing.
        """
        if not self._extensions_dir.exists():
            logger.info(
                "extension_discover_dir_missing path=%s — no extensions will load",
                self._extensions_dir,
            )
            return []

        # Sort subdirs by name for stable discovery order.
        subdirs = sorted(
            p for p in self._extensions_dir.iterdir()
            if p.is_dir() and (p / "extension.yaml").exists()
        )
        out: list[ExtensionRecord] = []
        for d in subdirs:
            # Key by directory name — the directory is the unique physical key.
            # The manifest's `id` is checked for collisions at validate().
            rec = self._registry.upsert_record(d.name)
            rec.ext_dir = d
            rec.state = ExtensionState.DISCOVERED
            out.append(rec)
            logger.debug("extension_discovered dir=%s", d.name)
        return out

    async def validate(self) -> None:
        """Stage 1: parse + validate manifests, load config schemas, check id uniqueness.

        Transitions each extension to VALIDATED, FAILED, or DISABLED. Idempotent.

        Records are keyed by directory name (the unique physical key from
        discover()). The manifest's `id` field is checked for collisions
        against other records' manifest ids — a collision transitions the
        second-and-later record to FAILED.
        """
        seen_manifest_ids: set[str] = set()
        for rec in self._registry.records():
            if rec.state != ExtensionState.DISCOVERED:
                continue
            await self._validate_one(rec, seen_manifest_ids)

    async def _validate_one(self, rec: ExtensionRecord, seen_manifest_ids: set[str]) -> None:
        """Validate a single extension's manifest + config. Transitions state.

        The record stays keyed by its directory name (rec.id is the dir name).
        The manifest's `id` is stored on rec.manifest and checked for collisions.
        """
        assert rec.ext_dir is not None
        manifest_path = rec.ext_dir / "extension.yaml"

        # Parse the manifest.
        try:
            raw = yaml.safe_load(manifest_path.read_text())
        except Exception as exc:
            rec.add_failure(
                stage="validate", contribution="manifest",
                reason=f"YAML parse error: {exc}",
            )
            rec.state = ExtensionState.FAILED
            return

        # Validate via pydantic.
        try:
            manifest = Manifest.model_validate(raw)
        except Exception as exc:
            # If pydantic rejected it, check whether it's a manifest_version
            # out-of-range issue (the test pins this specific reason).
            reason = str(exc)
            if isinstance(raw, dict) and "manifest_version" in raw:
                mv = raw.get("manifest_version")
                if isinstance(mv, int) and not (
                    self._manifest_version_range[0] <= mv <= self._manifest_version_range[1]
                ):
                    rec.add_failure(
                        stage="validate", contribution="manifest",
                        reason=(
                            f"manifest_version {mv} outside host range "
                            f"{self._manifest_version_range}"
                        ),
                    )
                    rec.state = ExtensionState.FAILED
                    return
            rec.add_failure(
                stage="validate", contribution="manifest",
                reason=f"schema validation error: {reason}",
            )
            rec.state = ExtensionState.FAILED
            return

        # Manifest_version range check (catches out-of-range even if pydantic
        # accepted it as an int).
        mv = manifest.manifest_version
        if not (self._manifest_version_range[0] <= mv <= self._manifest_version_range[1]):
            rec.add_failure(
                stage="validate", contribution="manifest",
                reason=(
                    f"manifest_version {mv} outside host range "
                    f"{self._manifest_version_range}"
                ),
            )
            rec.state = ExtensionState.FAILED
            return

        # Manifest id collision check (ADR-014 §4.1, §6.1).
        # Records are keyed by directory name; the manifest id is the logical
        # identity that must be unique across all extensions.
        if manifest.id in seen_manifest_ids:
            rec.add_failure(
                stage="validate", contribution="manifest",
                reason=(
                    f"extension id {manifest.id!r} collides with another extension — "
                    f"manifest ids must be unique"
                ),
            )
            rec.state = ExtensionState.FAILED
            return
        seen_manifest_ids.add(manifest.id)

        # Update the record with the parsed manifest. The record stays keyed
        # by directory name; rec.manifest.id is the logical identity.
        rec.manifest = manifest

        # Handle `enabled: false` → DISABLED (skip further validation).
        if not manifest.enabled:
            rec.state = ExtensionState.DISABLED
            logger.info("extension_disabled id=%s dir=%s (manifest enabled=false)",
                        manifest.id, rec.id)
            return

        # Load + validate config.schema if declared.
        if manifest.config.schema_ is not None:
            try:
                cls = _import_class(manifest.config.schema_)
                _validate_config_schema_class(cls)
                # Instantiate (BaseSettings reads env vars; dataclass needs args
                # — for v1 we only support zero-arg construction).
                try:
                    rec.config = cls()
                except Exception as exc:
                    rec.add_failure(
                        stage="validate", contribution="config",
                        reason=f"config instantiation failed: {exc}",
                    )
                    rec.state = ExtensionState.FAILED
                    return
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                rec.add_failure(
                    stage="validate", contribution="config",
                    reason=f"config.schema load failed: {exc}",
                )
                rec.state = ExtensionState.FAILED
                return

        rec.state = ExtensionState.VALIDATED
        logger.info("extension_validated id=%s version=%s", manifest.id, manifest.version)

    async def start(self) -> None:
        """Run stages 0–3 + 5 (v1.0). Stage 4 (mount) is v1.1.

        Idempotent: re-calling after start() is a no-op if already running.
        """
        if self._registry.running:
            return

        # Stage 0: discover
        await self.discover()

        # Stage 1: validate
        await self.validate()

        # Stages 2 + 3 + 5 per extension.
        for rec in self._registry.records():
            if rec.state == ExtensionState.DISABLED:
                continue  # operator-disabled; skip
            if rec.state == ExtensionState.FAILED:
                continue  # stage 1 already failed; skip
            await self._migrate_register_ready_one(rec)

        # Start all registered actor scheduler tasks.
        await self._start_actor_tasks()

        self._registry.mark_running()
        logger.info(
            "extension_host_started extensions=%d",
            len(self._registry.records()),
        )

    async def _migrate_register_ready_one(self, rec: ExtensionRecord) -> None:
        """Stages 2 (migrate) + 3 (register) + 5 (ready) for one extension.

        Each stage is sandbox-wrapped: a raise transitions the extension to
        DEGRADED and records a structured failure. The host stays up.
        """
        assert rec.manifest is not None
        manifest = rec.manifest

        # ---- Stage 2: migrate ----
        rec.state = ExtensionState.MIGRATING
        try:
            await self._migrate_one(rec, manifest)
        except Exception as exc:
            rec.add_failure(
                stage="migrate", contribution="migrations",
                reason=f"{type(exc).__name__}: {exc}",
            )
            rec.state = ExtensionState.DEGRADED
            logger.warning(
                "extension_migrate_failed id=%s error=%s",
                manifest.id, exc,
            )
            return  # Don't proceed to register — the corpus schema is suspect.

        # ---- Stage 3: register ----
        try:
            await self._register_one(rec, manifest)
        except Exception as exc:
            rec.add_failure(
                stage="register", contribution="corpora",
                reason=f"{type(exc).__name__}: {exc}",
            )
            rec.state = ExtensionState.DEGRADED
            logger.warning(
                "extension_register_failed id=%s error=%s",
                manifest.id, exc,
            )
            return

        # ---- Stage 4: mount (v1.1) — skipped in v1.0 ----
        # If the manifest declares a gui: block, we DON'T mount it in v1.0.
        # The test_mounts_extension_gui_pages test is expected to fail until
        # v1.1 lands. We don't transition to DEGRADED — the extension is
        # backend-live, just not GUI-mounted.

        # ---- Stage 5: ready (run on_load hook) ----
        try:
            await self._run_on_load(rec, manifest)
        except Exception as exc:
            rec.add_failure(
                stage="ready", contribution="hook",
                reason=f"on_load raised: {type(exc).__name__}: {exc}",
            )
            rec.state = ExtensionState.DEGRADED
            logger.warning(
                "extension_on_load_failed id=%s error=%s",
                manifest.id, exc,
            )
            return

        # Success — REGISTERED (v1.0 terminal state). v1.1 would transition
        # to MOUNTED after stage 4 mount succeeds.
        rec.state = ExtensionState.REGISTERED
        logger.info("extension_registered id=%s", manifest.id)

    # ------------------------------------------------------------------
    # Stage 2: migrate
    # ------------------------------------------------------------------

    async def _migrate_one(self, rec: ExtensionRecord, manifest: Manifest) -> None:
        """Register the extension's corpora + apply contributed .sql migrations.

        Per ADR-014 §6.2, contributed corpora are registered as
        `{ext_id}:{role}`. The core CorpusRegistry.register() runs the
        corpus-type's core migrations (M001/M003/M004 for DOCUMENT) via the
        existing CorpusMigrationRunner. THEN the host applies the extension's
        contributed .sql migrations via the MigrationLoader (separate
        `extension_applied_migrations` table — does NOT contaminate the core
        runner's fingerprint).
        """
        assert rec.ext_dir is not None
        registry = getattr(self._container, "corpus_registry", None)
        if registry is None:
            raise RuntimeError("container.corpus_registry is None — cannot register corpora")

        for corpus_decl in manifest.contributes.corpora:
            corpus_id = f"{manifest.id}:{corpus_decl.role}"
            corpus_type = CorpusType(corpus_decl.type)
            db_path = rec.ext_dir / f"{corpus_decl.role}.db"
            await registry.register(
                corpus_id=corpus_id,
                corpus_type=corpus_type,
                db_path=db_path,
                sensitive=corpus_decl.sensitive,
            )

        # Apply contributed .sql migrations to the first corpus (if any).
        # For v1, all extension migrations apply to the first declared corpus.
        # (ARISTOTLE has one corpus per extension in practice; multi-corpus
        # migration targeting is a v1.1+ concern.)
        #
        # Path resolution (ADR-014 §6.3): manifest.migrations_path() returns
        # extensions_dir / id / contributes.migrations, which equals
        # rec.ext_dir / contributes.migrations. Same physical path.
        if manifest.contributes.corpora:
            first_corpus_id = f"{manifest.id}:{manifest.contributes.corpora[0].role}"
            stores = await registry.get_stores(first_corpus_id)
            migrations_dir = manifest.migrations_path(self._extensions_dir)
            loaded = load_migrations_dir(migrations_dir)
            if loaded:
                await apply_extension_migrations(
                    ext_id=manifest.id,
                    stores=stores,
                    migrations=loaded,
                )

    # ------------------------------------------------------------------
    # Stage 3: register (channels + workflows; actors come from on_load)
    # ------------------------------------------------------------------

    async def _register_one(self, rec: ExtensionRecord, manifest: Manifest) -> None:
        """Register contributed channels + workflows.

        Actors are registered from on_load (ADR-014 §5.3) — not here.
        Workflows are re-globbed onto the WorkflowRegistry via add_path()
        (ADR-014 §5.4) when the host was constructed with one.
        """
        # Channels: the manifest's `channels` list is advisory. Actual
        # registration happens from on_load via host.register_channel().
        # Here we just record the advisory list on the extension record.
        for ch_name in manifest.contributes.channels:
            self._registry.register_channel(ext_id=manifest.id, name=ch_name)

        # Workflows: re-glob the extension's workflows_dir onto the
        # WorkflowRegistry via add_path() (ADR-014 §5.4). Also record each
        # workflow path on the extension record for the health surface.
        workflows_path = manifest.workflows_path(self._extensions_dir)
        if workflows_path.exists():
            # Call add_path on the host-owned WorkflowRegistry (if wired).
            if self._workflow_registry is not None:
                try:
                    self._workflow_registry.add_path(workflows_path)
                except Exception as exc:
                    # add_path is sandboxed internally (parse failures are
                    # logged as warnings, not raised). If it raises anyway,
                    # record a workflow-tagged failure but don't fail the
                    # whole register stage — workflows are best-effort.
                    rec.add_failure(
                        stage="register", contribution="workflows_dir",
                        reason=f"workflow_registry.add_path raised: {type(exc).__name__}: {exc}",
                    )
            # Record each workflow on the extension record (for health surface).
            for wf in sorted(workflows_path.glob("*.yaml")):
                self._registry.register_workflow(ext_id=manifest.id, path=str(wf))
            logger.info(
                "extension_workflows_registered id=%s dir=%s count=%d",
                manifest.id,
                workflows_path,
                len(list(workflows_path.glob("*.yaml"))),
            )
        else:
            logger.debug(
                "extension_no_workflows_dir id=%s path=%s (skipping)",
                manifest.id,
                workflows_path,
            )

    # ------------------------------------------------------------------
    # Stage 5: ready (run on_load hook)
    # ------------------------------------------------------------------

    async def _run_on_load(self, rec: ExtensionRecord, manifest: Manifest) -> None:
        """Load hooks.py from the extension dir and call on_load(host).

        The host sets `_current_ext_id` so `host.config` / `host.manifest`
        resolve to the right extension inside the hook.
        """
        assert rec.ext_dir is not None
        hooks_path = rec.ext_dir / "hooks.py"
        if not hooks_path.exists():
            # No hooks.py — nothing to do. The extension is still REGISTERED.
            logger.debug("extension_no_hooks id=%s", manifest.id)
            return

        # Load hooks.py as a module. Use a unique module name to avoid collisions.
        module_name = f"aip_extension_{manifest.id}_hooks"
        spec = importlib.util.spec_from_file_location(module_name, hooks_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load hooks.py from {hooks_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        on_load = getattr(module, "on_load", None)
        if on_load is None:
            logger.debug("extension_no_on_load id=%s", manifest.id)
            return

        # Set the current extension id so host.config/manifest resolve correctly.
        prev = self._current_ext_id
        self._current_ext_id = manifest.id
        try:
            # on_load is sync per ADR-014 §5.3 (register_actor etc. are sync).
            on_load(self)
        finally:
            self._current_ext_id = prev

        # Warn about declared-but-not-registered actors/channels (ADR-014 §5.3).
        rec_after = self._registry.get_record(manifest.id)
        if rec_after is not None:
            declared_actors = set(manifest.contributes.actors)
            registered_actors = set(rec_after.actors)
            missing_actors = declared_actors - registered_actors
            if missing_actors:
                logger.warning(
                    "extension_declared_actors_not_registered id=%s missing=%s "
                    "(declared in manifest but not registered by on_load)",
                    manifest.id, sorted(missing_actors),
                )

    # ------------------------------------------------------------------
    # Actor scheduler task startup
    # ------------------------------------------------------------------

    async def _start_actor_tasks(self) -> None:
        """Start one supervised_task per registered actor.

        Called once at the end of start(). Each task runs _actor_scheduler_loop
        until cancel_event is set by stop().
        """
        for reg in self._registry.actor_registrations():
            cancel_event = asyncio.Event()
            self._cancel_events[reg.name] = cancel_event
            task = supervised_task(
                name=f"actor:{reg.name}",
                coro=_actor_scheduler_loop(
                    registration=reg,
                    container=self._container,
                    config=self._registry.get_record(reg.ext_id).config
                    if self._registry.get_record(reg.ext_id) else None,
                    manifest=self._registry.get_record(reg.ext_id).manifest
                    if self._registry.get_record(reg.ext_id) else None,
                    cancel_event=cancel_event,
                ),
            )
            self._registry.attach_actor_task(reg.name, task)

    # ------------------------------------------------------------------
    # Shutdown — ADR-014 §4.2
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Shutdown stages (reverse order): cancel actors, call on_unload, mark DISABLED."""
        if not self._registry.running:
            return

        # 1. Signal every actor scheduler to cancel.
        for name, event in self._cancel_events.items():
            event.set()
        # 2. Cancel the tasks themselves (in case they're blocked on something
        #    other than cancel_event.wait()).
        for rec in self._registry.records():
            for task in rec.actor_tasks:
                if not task.done():
                    task.cancel()
        # 3. Await them briefly so cleanup runs.
        all_tasks: list[asyncio.Task] = []
        for rec in self._registry.records():
            all_tasks.extend(rec.actor_tasks)
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

        # 4. Call on_unload hooks (sandboxed).
        for rec in self._registry.records():
            if rec.manifest is None or rec.ext_dir is None:
                continue
            await self._run_on_unload(rec)

        # 5. Clear actor registrations + cancel events.
        for name in list(self._registry.registered_actor_names()):
            self._registry.unregister_actor(name)
        self._cancel_events.clear()

        # 6. Mark every extension DISABLED.
        for rec in self._registry.records():
            rec.state = ExtensionState.DISABLED
            rec.actor_tasks.clear()

        self._registry.mark_stopped()
        logger.info("extension_host_stopped")

    async def _run_on_unload(self, rec: ExtensionRecord) -> None:
        """Call hooks.py::on_unload(host) if present. Sandboxed."""
        assert rec.manifest is not None and rec.ext_dir is not None
        hooks_path = rec.ext_dir / "hooks.py"
        if not hooks_path.exists():
            return
        # The module was already loaded during on_load under the name
        # `aip_extension_{id}_hooks` — try to reuse it.
        module_name = f"aip_extension_{rec.manifest.id}_hooks"
        module = sys.modules.get(module_name)
        if module is None:
            # on_load never ran (extension failed before ready). Skip on_unload.
            return
        on_unload = getattr(module, "on_unload", None)
        if on_unload is None:
            return
        prev = self._current_ext_id
        self._current_ext_id = rec.manifest.id
        try:
            on_unload(self)
        except Exception as exc:
            logger.warning(
                "extension_on_unload_failed id=%s error=%s:%s",
                rec.manifest.id, type(exc).__name__, exc,
            )
        finally:
            self._current_ext_id = prev

    # ------------------------------------------------------------------
    # Registration functions (called from on_load) — ADR-014 §5.1
    # ------------------------------------------------------------------

    def register_actor(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        cadence: float = 0.0,
    ) -> None:
        """Register an actor factory. The host starts the scheduler task at end of start().

        Args:
            name: unique actor name across all extensions.
            factory: zero-arg callable returning an Actor instance.
            cadence: seconds between cycles; 0 = manual only.
        """
        if self._current_ext_id is None:
            raise RuntimeError(
                "host.register_actor() can only be called inside an extension's on_load hook."
            )
        self._registry.register_actor(
            ext_id=self._current_ext_id,
            name=name,
            factory=factory,
            cadence=cadence,
        )

    def register_channel(self, name: str, register_fn: Callable) -> None:
        """Register a custom retrieval channel (host-owned).

        For v1.0: records the channel on the extension's record. The actual
        RetrievalOrchestrator wiring is step 2 of the build order.
        """
        if self._current_ext_id is None:
            raise RuntimeError(
                "host.register_channel() can only be called inside an extension's on_load hook."
            )
        self._registry.register_channel(ext_id=self._current_ext_id, name=name)
        logger.info(
            "extension_channel_registered id=%s name=%s (orchestrator wiring deferred to step 2)",
            self._current_ext_id, name,
        )

    def register_workflow(self, path: str) -> None:
        """Register a single workflow YAML file at runtime (ADR-014 §5.4).

        For v1.0: records the path on the extension's record. The actual
        WorkflowRegistry.add_path is step 2 of the build order.
        """
        if self._current_ext_id is None:
            raise RuntimeError(
                "host.register_workflow() can only be called inside an extension's on_load hook."
            )
        self._registry.register_workflow(ext_id=self._current_ext_id, path=path)

    def register_page(
        self,
        route: str,
        title: str,
        icon: str,
        builder_fn: Callable,
        *,
        order: int = 50,
    ) -> None:
        """Register a GUI page (v1.1). Records on the extension's record."""
        if self._current_ext_id is None:
            raise RuntimeError(
                "host.register_page() can only be called inside an extension's on_load hook."
            )
        item = NavItem(
            ext_id=self._current_ext_id,
            label=title,
            icon=icon,
            route=route,
            order=order,
            builder_fn=builder_fn,
        )
        self._registry.register_nav_item(ext_id=self._current_ext_id, item=item)

    # ------------------------------------------------------------------
    # State / health accessors — ADR-014 §5.1, §7
    # ------------------------------------------------------------------

    def state(self, ext_id: str) -> ExtensionState:
        rec = self._registry.get_record(ext_id)
        if rec is None:
            return ExtensionState.DISABLED  # unknown extension → treated as not-serving
        return rec.state

    def failures(self, ext_id: str) -> list:
        """Return the list of Failure records for an extension (empty if none)."""
        rec = self._registry.get_record(ext_id)
        if rec is None:
            return []
        return list(rec.failures)

    def registered_actors(self) -> list[str]:
        """Return the list of registered actor names across all extensions."""
        return self._registry.registered_actor_names()

    def nav_items(self) -> list[NavItem]:
        """Return all GUI nav items across all extensions (v1.1)."""
        return self._registry.nav_items()

    def health(self) -> list[dict]:
        """Return the health snapshot — ADR-014 §7."""
        return self._registry.health_snapshot()

    def is_running(self) -> bool:
        """True if host.start() has completed and host.stop() has not."""
        return self._registry.running
