"""Dependency injection for the AIP FastAPI surfaces.

Single AipContainer as the source of truth. Routes never import concrete adapters.

Removed direct orchestration imports from adapter layer.
Orchestration components (SessionManager, BudgetManager, etc.) are typed
as Any and injected at runtime via lifespan wiring. This preserves the
three-layer discipline: adapter may only import foundation.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from aip.foundation.protocols import (
    AutonomyGate,
    BudgetStore,
    CanonicalStore,
    EmbeddingProvider,
    EntityStore,
    EventStore,
    GraphStore,
    KnowledgeStore,
    LexicalStore,
    ModelProvider,
    ProjectStore,
    TraceStore,
    VectorStore,
)


class AipContainer:
    """Central DI container for all API surfaces.

    Populated in lifespan startup from config + adapter and orchestration
    implementations.

    Orchestration components are typed as Any (injected at runtime) to avoid
    direct adapter→orchestration import dependency.
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        # Will be populated by lifespan / factory
        self.vector_store: VectorStore | None = None
        # ADR-008 Chunk 3: ecs_store, artifact_store, corpus_turn_store are
        # now PROPERTIES that delegate to definer_stores when the registry
        # is wired. The _legacy_* attributes hold the pre-registry values
        # for backward compat (tests, pre-wiring lifespan).
        self._legacy_ecs_store: Any = None
        self._legacy_artifact_store: Any = None
        self.event_store: EventStore | None = None
        self.trace_store: TraceStore | None = None
        self.budget_store: BudgetStore | None = None
        self.project_store: ProjectStore | None = None
        self.entity_store: EntityStore | None = None
        self.lexical_store: LexicalStore | None = None
        self.canonical_store: CanonicalStore | None = None
        self.autonomy_gate: AutonomyGate | None = None
        self.model_provider: ModelProvider | None = None
        self.embedding_provider: EmbeddingProvider | None = None
        self.knowledge_store: KnowledgeStore | None = None
        # DefinerProfile — optional profile for augmented chat injection (degrades to no injection)
        self.definer_profile: Any = None
        # VigilStore not in protocols yet — typed as Any
        self.vigil_store: Any = None
        # artifact_store already declared above as ArtifactStore | None
        # Orchestration components — typed as Any to avoid adapter→orchestration import
        self.session_manager: Any = None
        self.budget_manager: Any = None
        self.adaptive_router: Any = None
        self.sexton_actor: Any = None  # ADR-011 full maintenance worker (actors.sexton.Sexton)
        self.beast: Any = None
        self.vigil: Any = None
        self.ace_playbook: Any = None
        # PerformanceProfiler — None when not configured (API returns BACKEND_UNAVAILABLE)
        self.performance_profiler: Any = None
        # CollaboratorManager — None when auth not fully wired
        self.collaborator_manager: Any = None
        # ReviewQueueStore — None when not initialized
        self.review_queue_store: Any = None
        # SessionStore — None when not initialized (degrades to in-memory)
        self.session_store: Any = None
        # CorpusTurnStore — now a property delegating to definer_stores.
        self._legacy_corpus_turn_store: Any = None
        # GraphStore — knowledge graph nodes and edges (degrades to no graph retrieval)
        self.graph_store: GraphStore | None = None
        # Sprint 5.27: Operational components wired into the running application
        self._vigil_quality_store: Any = None  # VigilQualityStore for persistent quality history
        self._alert_manager: Any = None  # AlertManager for operator notifications
        self._config_watcher: Any = None  # ConfigWatcher for hot-reload
        self._read_pool_auto_sizer: Any = None  # ReadPoolAutoSizer for auto pool sizing
        self._auto_tuning_policy: Any = None  # AutoTuningPolicy for configurable thresholds
        # Sprint 5.29: Persistent alert history store
        self._alert_history_store: Any = None  # AlertHistoryStore for SQLite-backed alert history
        # SyncAlertHistoryBridge for AlertManager compatibility (wraps async store)
        self._alert_history_bridge: Any = None
        # Backfill status for async backfill tracking (simple in-memory for now)
        self.backfill_status: dict = {"running": False, "last_result": None, "progress": {}}
        # Startup background tasks — stored on container so shutdown can cancel them
        self._sexton_startup_task: Any = None
        self._vigil_startup_task: Any = None
        # Orchestration function references — populated in lifespan.
        # Routes access these through the container instead of importing
        # orchestration directly, preserving layer discipline (adapter → foundation only).
        self._ask_fn: Any = None  # ask_pipeline.ask
        self._ask_stores_class: Any = None  # ask_pipeline.AskStores
        self._search_sources_fn: Any = None  # ask_pipeline._search_sources_with_trace
        self._sanitize_fts_query_fn: Any = None  # ask_pipeline._sanitize_fts_query
        self._ingest_conversation_fn: Any = None  # ingestion.pipeline.ingest_conversation
        self._ingest_file_fn: Any = None  # ingestion.pipeline.ingest_file
        # Chunk 6: Container-mediated corpus ingest functions (avoids route→orchestration import)
        self._corpus_ingest_config_class: Any = None  # CorpusIngestConfig
        self._ingest_directory_to_corpus_fn: Any = None  # ingest_directory_to_corpus
        self._ingest_file_to_corpus_fn: Any = None  # ingest_file_to_corpus
        # Chunk 6: Container-mediated retrieval orchestrator access (avoids route→orchestration import)
        self._get_orchestrator_cache_fn: Any = None  # get_orchestrator_cache
        self._builtin_channels: Any = None  # BUILTIN_CHANNELS list
        # Chunk 6: Container-mediated retrieval dashboard classes (avoids route→orchestration import)
        self._orchestrator_config_class: Any = None  # OrchestratorConfig
        self._adaptive_budget_tuner_class: Any = None  # AdaptiveBudgetTuner
        # Store registry — maps store_name → db_path for datastore truth.
        # Populated during lifespan startup as each store is initialized.
        # Used by startup validation, backup, and the /health/datastore endpoint.
        self._store_registry: dict[str, str] = {}
        # ADR-008 Multi-Corpus: the primary store-access interface.
        # None until lifespan calls corpus_registry.startup(). Once set,
        # routes/actors access per-corpus stores via get_stores(corpus_id)
        # or the definer_stores convenience property.
        self.corpus_registry: Any = None

    # ADR-008 Chunk 3: per-corpus store properties that delegate to the
    # registry's definer_stores when wired, falling back to legacy
    # singletons otherwise. This makes all 264 call sites automatically
    # use the registry without mechanical rewriting.
    @property
    def corpus_turn_store(self) -> Any:
        """CorpusTurnStore — delegates to definer_stores when registry is wired."""
        ds = self.definer_stores
        return ds.turn_store if ds is not None else self._legacy_corpus_turn_store

    @corpus_turn_store.setter
    def corpus_turn_store(self, value: Any) -> None:
        self._legacy_corpus_turn_store = value

    @property
    def artifact_store(self) -> Any:
        """ArtifactStore — delegates to definer_stores when registry is wired."""
        ds = self.definer_stores
        return ds.artifact_store if ds is not None else self._legacy_artifact_store

    @artifact_store.setter
    def artifact_store(self, value: Any) -> None:
        self._legacy_artifact_store = value

    @property
    def ecs_store(self) -> Any:
        """EcsStore — delegates to definer_stores when registry is wired."""
        ds = self.definer_stores
        return ds.ecs_store if ds is not None else self._legacy_ecs_store

    @ecs_store.setter
    def ecs_store(self, value: Any) -> None:
        self._legacy_ecs_store = value

    @property
    def definer_stores(self) -> Any:
        """Convenience accessor for the definer corpus's CorpusStores bundle.

        ADR-008 Rev 3.1 §8 Chunk 3: returns the definer corpus's stores
        (turn_store, lexical_store, vector_store, graph_store, artifact_store,
        ecs_store) cached on the registry after startup(). Returns None if
        the registry isn't wired or the definer corpus isn't registered.

        This is a SYNC property (not async) so routes can use it without
        await. The registry caches _definer_stores during startup() so this
        is a simple attribute lookup.
        """
        registry = self.corpus_registry
        if registry is None:
            return None
        return registry._definer_stores

    def register_store(self, name: str, db_path: str) -> None:
        """Register a store's database path in the datastore registry.

        Called during lifespan startup for each initialized store so that
        ``datastore_summary()`` can report exactly where every store lives.
        """
        self._store_registry[name] = db_path

    def datastore_summary(self) -> dict[str, Any]:
        """Return a summary of all registered stores and their locations.

        This is the honest datastore truth: which files exist, which are
        shared, and what the backup story is for each.

        Product decision: AIP_Brain uses Option B — honest multi-file
        local datastore. This was chosen because:

        1. The data has fundamentally different access patterns (state.db
           is transactional, lexical.db is FTS5 read-heavy, vectors.db
           may use VSS virtual tables, quality/alert DBs are append-mostly).
        2. SQLite performance degrades with many concurrent connections to
           a single file; separate files allow independent WAL mode and
           connection pooling.
        3. Backup granularity: each .db can be backed up independently
           via VACUUM INTO without locking the others.
        4. Disaster recovery: a corrupt FTS index doesn't take down the
           entity store.

        The 7 DB files are:
          - state.db:     Core entity/canonical/event/artifact/budget/project/
                         ECS/review/graph/corpus/session/autonomy data
          - lexical.db:   FTS5 full-text search index
          - vectors.db:   Vector embeddings (VSS or brute-force)
          - vigil_quality.db: Vigil quality cycle history
          - alert_history.db: Alert/delivery/experiment/mute rule persistence
          - trace.db:     Trace events and routing outcomes
          - ace_playbook.db: ACE procedural intervention rules
        """
        from pathlib import Path

        stores: dict[str, Any] = {}
        shared_dbs: dict[str, list[str]] = {}

        for name, db_path in sorted(self._store_registry.items()):
            p = Path(db_path)
            exists = p.exists()
            size_mb = round(p.stat().st_size / (1024 * 1024), 2) if exists else 0
            stores[name] = {
                "db_path": db_path,
                "exists": exists,
                "size_mb": size_mb,
            }
            # Track which stores share a DB file
            shared_dbs.setdefault(db_path, []).append(name)

        # Identify shared databases
        shared_info = {db_path: names for db_path, names in shared_dbs.items() if len(names) > 1}

        return {
            "architecture": "multi-file local datastore (Option B)",
            "stores": stores,
            "shared_databases": shared_info,
            "backup_story": (
                "Each .db file can be backed up via 'aip backup' (uses VACUUM INTO "
                "for consistent snapshots) or file-level tar (deploy/backup.sh). "
                "WAL mode ensures read consistency during backup."
            ),
            "total_stores": len(stores),
            "total_db_files": len(set(self._store_registry.values())),
        }

    def set_embedding_provider(self, provider: "EmbeddingProvider | None") -> None:
        """Safely replace the embedding provider.

        ADR-008 Rev 3.1 §A6: when corpus_registry is wired, iterates all
        registered corpora and updates each corpus's vector_store +
        turn_store.mark_all_for_reembed(). Falls back to legacy singleton
        poking when the registry isn't wired (pre-Chunk-3 wiring).

        Updates the container reference and pokes private attributes on
        dependent components (beast, knowledge_store, sexton_actor) so that
        runtime changes (e.g. from PATCH /models/slots/embedding/model)
        take effect without requiring a full restart.
        """
        old_provider = self.embedding_provider
        if old_provider is not None and hasattr(old_provider, "close"):
            try:
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(old_provider.close())
                except RuntimeError:
                    pass
            except Exception:
                pass

        self.embedding_provider = provider

        # ADR-008 §A6: registry-aware path — iterate all corpora
        if self.corpus_registry is not None:
            try:
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._registry_reembed(provider))
                except RuntimeError:
                    asyncio.run(self._registry_reembed(provider))
            except Exception:
                pass
            # Still update beast/knowledge_store/sexton (not per-corpus)
            self._update_non_corpus_embed_dependents(provider)
            return

        # Legacy path — poke singletons directly (pre-Chunk-3 wiring)
        if self.vector_store is not None and hasattr(self.vector_store, "_embedding_provider"):
            self.vector_store._embedding_provider = provider

        self._update_non_corpus_embed_dependents(provider)

        # Legacy: trigger re-embedding on the singleton corpus_turn_store
        if provider is not None and self.corpus_turn_store is not None:
            try:
                new_model = ""
                for attr in ("model", "_model", "model_name", "_model_name"):
                    val = getattr(provider, attr, None)
                    if val and isinstance(val, str):
                        new_model = val
                        break
                if not new_model:
                    new_model = provider.__class__.__name__

                if hasattr(self.corpus_turn_store, "mark_all_for_reembed"):
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._trigger_reembed(new_model))
                    except RuntimeError:
                        try:
                            asyncio.run(self._trigger_reembed(new_model))
                        except Exception as _reembed_fallback_exc:
                            from aip.logging import get_logger as _get_logger

                            _get_logger(__name__).warning(
                                "reembed_trigger_fallback_failed",
                                error=str(_reembed_fallback_exc),
                            )
            except Exception as _reembed_outer_exc:
                from aip.logging import get_logger as _get_logger

                _get_logger(__name__).warning(
                    "reembed_trigger_setup_failed",
                    error=str(_reembed_outer_exc),
                )

    def _update_non_corpus_embed_dependents(self, provider: "EmbeddingProvider | None") -> None:
        """Update beast, knowledge_store, sexton_actor with the new provider.

        These are not per-corpus — they're global actors/stores that reference
        the embedding provider directly.
        """
        if self.beast is not None and hasattr(self.beast, "_embed"):
            self.beast._embed = provider
        if self.knowledge_store is not None and hasattr(self.knowledge_store, "_embedding_provider"):
            self.knowledge_store._embedding_provider = provider
        if self.sexton_actor is not None and hasattr(self.sexton_actor, "update_embedding_provider"):
            self.sexton_actor.update_embedding_provider(provider)
        elif self.sexton_actor is not None and hasattr(self.sexton_actor, "_embed"):
            self.sexton_actor._embed = provider

    async def _registry_reembed(self, provider: "EmbeddingProvider | None") -> None:
        """ADR-008 §A6: iterate all registered corpora, update vector_store
        and mark turns for re-embedding on each corpus."""
        if provider is None:
            return
        from aip.logging import get_logger as _get_logger

        _log = _get_logger(__name__)
        try:
            new_model = ""
            for attr in ("model", "_model", "model_name", "_model_name"):
                val = getattr(provider, attr, None)
                if val and isinstance(val, str):
                    new_model = val
                    break
            if not new_model:
                new_model = provider.__class__.__name__

            total_marked = 0
            for cid in await self.corpus_registry.list_corpora():
                try:
                    stores = await self.corpus_registry.get_stores(cid)
                    if stores.vector_store is not None and hasattr(stores.vector_store, "_embedding_provider"):
                        stores.vector_store._embedding_provider = provider
                    if stores.turn_store is not None and hasattr(stores.turn_store, "mark_all_for_reembed"):
                        count = await stores.turn_store.mark_all_for_reembed(except_model=new_model)
                        total_marked += count
                except Exception as exc:
                    _log.warning("registry_reembed_corpus_failed", corpus=cid, error=str(exc))

            _log.info("registry_reembed_triggered", new_model=new_model, turns_marked=total_marked)
        except Exception as exc:
            _log.warning("registry_reembed_failed", error=str(exc), exc_info=True)

    async def _trigger_reembed(self, new_model: str) -> None:
        """Mark corpus turns for re-embedding and log the trigger."""
        from aip.logging import get_logger as _get_logger

        _log = _get_logger(__name__)
        try:
            count = await self.corpus_turn_store.mark_all_for_reembed(except_model=new_model)
            _log.info(
                "reembed_triggered",
                new_model=new_model,
                turns_marked=count,
            )
        except Exception as exc:
            _log.warning(
                "reembed_trigger_failed",
                error=str(exc),
                new_model=new_model,
                exc_info=True,
            )


def get_container(request: "Request") -> AipContainer:
    """FastAPI dependency that returns the app's container (populated in lifespan)."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        # In test mode without lifespan, create a fresh container from any available config
        config = getattr(request.app.state, "raw_config", {}) or {}
        container = AipContainer(config)
        request.app.state.container = container
    return container


# Re-export auth dependencies so route modules can import from this single location
from aip.adapter.auth.dependencies import (  # noqa: E402
    get_current_identity,  # noqa: F401
    require_collaborator_or_above,  # noqa: F401
    require_definer,  # noqa: F401
)
