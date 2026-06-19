"""FastAPI application factory + lifespan for AIP surfaces.

Wires adapters and actor layer into AipContainer.
All privileged writes go through AutonomyGate.

Startup Classification
----------------------
Components are classified as REQUIRED or OPTIONAL:

REQUIRED (startup fails if these cannot initialize):
  - Entity store: artifact metadata, collaborator data
  - Canonical store: canonical artifact storage
  - Event store: audit trail, trace events
  - Autonomy gate: authorization enforcement (core security)
  - Artifact store: artifact versioned storage

OPTIONAL (startup logs warning, service degrades gracefully):
  - Lexical store: FTS5 full-text search (degrades to no text search)
  - Vector store: semantic search (degrades to keyword-only)
  - Embedding provider: vector embedding (degrades to fake_embed)
  - Project store: project management (degrades to empty projects)
  - Budget store: token budget tracking (degrades to unlimited)
  - Vigil store: canonical health monitoring (degrades to no monitoring)
  - Model provider: LLM dispatch (degrades to stub responses)
  - Knowledge store: compiled knowledge with embeddings (degrades to no knowledge compilation)
  - Beast actor: background health + corpus maintenance (degrades to no background tasks)
  - ECS store: artifact lifecycle state (degrades to no ECS transitions; initialized before actors per BUG-003 fix)
"""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from aip.adapter.api import collaborators, performance, plugins
from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes import (
    actors,
    admin,
    artifacts,
    ask,
    beast_commentary,
    beast_scan,
    chat,
    corpus,
    ecs,
    graph,
    graph_viz,
    health,
    ingest,
    knowledge,
    links,
    maintenance,
    memory,
    model_council,
    models,
    models_library,
    projects,
    retrieval_dashboard,
    review,
    sessions,
    sources,
    turns,
    wiki,
)
from aip.adapter.embedding.factory import create_embedding_provider
from aip.config import validate_config
from aip.config.loader import load_dotenv, load_toml_config
from aip.foundation.schemas import BeastCadenceConfig, SurfaceConfig
from aip.logging import configure_logging, get_logger, new_correlation_id, set_correlation_id

log = get_logger(__name__)


# ------------------------------------------------------------------
# Config loader + embedding factory are now in canonical modules:
#   aip.config.loader       — load_toml_config, load_dotenv
#   aip.adapter.embedding.factory — create_embedding_provider
# ------------------------------------------------------------------

# Backward-compatible aliases so existing code (tests, scripts) that
# imports these from aip.adapter.api.app still works.
_load_dotenv = load_dotenv
_load_toml_config = load_toml_config
_create_embedding_provider = create_embedding_provider


class StartupError(Exception):
    """Raised when a required component fails to initialize on startup."""


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Injects a correlation ID into every request context.

    If the client sends an X-Request-ID header, that value is used.
    Otherwise a new ID is generated. The ID is stored in a contextvar
    so that all structlog log calls within the request automatically
    include it, and it is echoed back as X-Request-ID in the response.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or new_correlation_id()
        set_correlation_id(request_id)

        log.debug(
            "request_started",
            method=request.method,
            path=request.url.path,
        )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all adapters and orchestration components on startup.

    Required components cause startup to fail fast with a clear error.
    Optional components log warnings and degrade gracefully.
    """
    # Initialize structured logging before anything else
    configure_logging()

    config: dict = app.state.raw_config or {}
    container = AipContainer(config)

    # --- Wire adapter stores from config ---
    db_path = config.get("database", {}).get("db_path", "db/state.db")

    # --- Ensure DB parent directories exist before opening connections ---
    # A clean checkout should not require manual "mkdir -p db" before startup.
    # This mirrors what "aip init" does (cli/init.py) but is safe to call
    # idempotently from the API server startup path as well.
    _db_parent = Path(db_path).parent
    if _db_parent and str(_db_parent) != ".":
        try:
            _db_parent.mkdir(parents=True, exist_ok=True)
            log.info("db_parent_directory_ensured", path=str(_db_parent))
        except PermissionError as exc:
            raise StartupError(
                f"Cannot create database directory {_db_parent}: permission denied. "
                f"Ensure the directory is writable or configure [database].db_path "
                f"to point to a writable location. Error: {exc}"
            ) from exc
        except OSError as exc:
            raise StartupError(f"Cannot create database directory {_db_parent}: {exc}") from exc

    # =====================================================================
    # REQUIRED COMPONENTS — startup fails if these cannot initialize
    # =====================================================================

    # Entity store — artifact metadata, collaborator data
    try:
        from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore

        container.entity_store = SqliteEntityStore(db_path)
        await container.entity_store.initialize()
        log.info("component_initialized", component="entity_store", required=True)
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Entity store could not initialize. "
            f"Reason: {exc}. The application cannot start without entity storage.",
        ) from exc

    # Canonical store — canonical artifact storage
    try:
        from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore

        container.canonical_store = SqliteCanonicalStore(db_path)
        await container.canonical_store.initialize()
        log.info("component_initialized", component="canonical_store", required=True)
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Canonical store could not initialize. "
            f"Reason: {exc}. The application cannot start without canonical storage.",
        ) from exc

    # Event store — audit trail, trace events
    try:
        from aip.adapter.event_store_queryable import QueryableEventStore

        container.event_store = QueryableEventStore(db_path)
        await container.event_store.initialize()
        log.info("component_initialized", component="event_store", required=True)
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Event store could not initialize. "
            f"Reason: {exc}. The application cannot start without event storage.",
        ) from exc

    # Autonomy gate — authorization enforcement (core security)
    try:
        _ag_mod = importlib.import_module("aip.adapter.autonomy.autonomy_gate")
        _AutonomyGateImpl = _ag_mod.AutonomyGateImpl
        container.autonomy_gate = _AutonomyGateImpl(config={**config, "db_path": db_path})
        await container.autonomy_gate.initialize()
        log.info("component_initialized", component="autonomy_gate", required=True)
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Autonomy gate could not initialize. "
            f"Reason: {exc}. The application cannot start without authorization enforcement.",
        ) from exc

    # Artifact store (versioned) — artifact versioned storage
    try:
        _as_mod = importlib.import_module("aip.adapter.artifact_store_versioned")
        _VersionedArtifactStore = _as_mod.VersionedArtifactStore
        container.artifact_store = _VersionedArtifactStore(db_path)
        await container.artifact_store.initialize()
        log.info("component_initialized", component="artifact_store", required=True)
    except Exception as exc:
        raise StartupError(
            f"REQUIRED component failed: Artifact store could not initialize. "
            f"Reason: {exc}. The application cannot start without artifact storage.",
        ) from exc

    # =====================================================================
    # OPTIONAL COMPONENTS — startup logs warning, service degrades gracefully
    # =====================================================================

    # Lexical store — FTS5 full-text search (degrades to no text search)
    # Use the same lexical.db derivation as CLI ingestion (cli/_db_path + ingestion/pipeline)
    # so that backfill, augmented chat, etc. see chunks written by `aip ingest` / scripts/ingest_claude.py.
    try:
        _ls_mod = importlib.import_module("aip.adapter.lexical.sqlite_fts5_store")
        _SqliteFts5LexicalStore = _ls_mod.SqliteFts5LexicalStore
        lexical_db = os.path.join(os.path.dirname(db_path), "lexical.db")
        container.lexical_store = _SqliteFts5LexicalStore(lexical_db, config=config)
        await container.lexical_store.initialize()
        log.info("component_initialized", component="lexical_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="lexical_store", degradation="no_text_search", error=str(exc))

    # CorpusTurnStore — corpus turn FTS5 search for augmented chat (degrades to no corpus search)
    try:
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        container.corpus_turn_store = CorpusTurnStore(db_path, config=config)
        await container.corpus_turn_store.initialize()
        log.info("component_initialized", component="corpus_turn_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="corpus_turn_store", degradation="no_corpus_search", error=str(exc))

    # Embedding provider — vector embedding (degrades to fake_embed)
    # NOTE: Initialized before vector store so it can be passed to the factory
    # for SqliteVssVectorStore's store() compat method.
    #
    # The embedding provider is created from the [models.embedding] slot config
    # (resolved via ModelSlotResolver) when available, falling back to the
    # [embedding] section for backward compatibility. This ensures the embedding
    # provider uses the same model/API key selected via the UI on /models.
    try:
        prov = _create_embedding_provider(config)
        container.set_embedding_provider(prov)
        provider_type = config.get("embedding", {}).get("provider", "mock")
        embed_slot = config.get("models", {}).get("embedding", {})
        if isinstance(embed_slot, dict) and embed_slot.get("provider"):
            provider_type = embed_slot.get("provider")
        log.info("component_initialized", component="embedding_provider", required=False, provider=provider_type)
    except Exception as exc:
        log.warning("component_failed", component="embedding_provider", degradation="fake_embed", error=str(exc))

    # Vector store — semantic search (degrades to keyword-only)
    # Passes embedding_provider so SqliteVssVectorStore.store() can generate
    # real embeddings instead of inserting zero vectors.
    try:
        _vs_mod = importlib.import_module("aip.adapter.vector.factory")
        _create_vector_store = _vs_mod.create_vector_store
        container.vector_store = await _create_vector_store(config, embedding_provider=container.embedding_provider)
        log.info("component_initialized", component="vector_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="vector_store", degradation="keyword_only", error=str(exc))

    # Project store — project management (degrades to empty projects)
    try:
        from aip.adapter.project.sqlite_project_store import SqliteProjectStore

        container.project_store = SqliteProjectStore(db_path)
        await container.project_store.initialize()

        # BUG-001: Ensure a default project exists after initialization so that
        # the system has at least one project after a fresh start or restart.
        # create_project() is idempotent (returns existing if already present),
        # so this is safe to call on every startup.
        try:
            await container.project_store.create_project("default", "Default", "")
            log.info("default_project_ensured", project_id="default")
        except Exception as dp_exc:
            log.warning("default_project_creation_failed", error=str(dp_exc))

        log.info("component_initialized", component="project_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="project_store", degradation="empty_projects", error=str(exc))

    # Budget store — token budget tracking (degrades to unlimited)
    try:
        _bs_mod = importlib.import_module("aip.adapter.budget_store_sqlite")
        _SqliteBudgetStore = _bs_mod.SqliteBudgetStore
        container.budget_store = _SqliteBudgetStore(db_path)
        await container.budget_store.initialize()
        log.info("component_initialized", component="budget_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="budget_store", degradation="unlimited", error=str(exc))

    # Vigil store — canonical health monitoring (degrades to no monitoring)
    try:
        _vs2_mod = importlib.import_module("aip.adapter.vigil.sqlite_vigil_store")
        _SqliteVigilStore = _vs2_mod.SqliteVigilStore
        container.vigil_store = _SqliteVigilStore(db_path)
        await container.vigil_store.initialize()
        log.info("component_initialized", component="vigil_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="vigil_store", degradation="no_monitoring", error=str(exc))

    # Model provider — LLM dispatch (degrades to stub responses)
    try:
        from aip.adapter.model_slot_resolver import ModelSlotResolver

        container.model_provider = ModelSlotResolver(config)
        log.info("component_initialized", component="model_provider", required=False)
    except Exception as exc:
        log.warning("component_failed", component="model_provider", degradation="stub_responses", error=str(exc))

    # Knowledge store — compiled knowledge with embeddings
    # Requires vector_store + lexical_store + optional embedding_provider.
    # When embedding_provider is available, APPROVED compiled knowledge gets
    # real embeddings for semantic search. Without it, degrades to lexical-only.
    if container.vector_store is not None and container.lexical_store is not None:
        try:
            _ks_mod = importlib.import_module("aip.adapter.knowledge.sqlite_knowledge_store")
            _SqliteKnowledgeStore = _ks_mod.SqliteKnowledgeStore
            container.knowledge_store = _SqliteKnowledgeStore(
                db_path=db_path,
                vector_store=container.vector_store,
                lexical_store=container.lexical_store,
                embedding_provider=container.embedding_provider,
            )
            await container.knowledge_store.initialize()
            has_embed = container.embedding_provider is not None
            log.info(
                "component_initialized",
                component="knowledge_store",
                required=False,
                semantic_search=has_embed,
            )
        except Exception as exc:
            log.warning(
                "component_failed",
                component="knowledge_store",
                degradation="no_knowledge_compilation",
                error=str(exc),
            )
    else:
        log.info("component_skipped", component="knowledge_store", reason="missing_vector_or_lexical_store")

    # Definer profile — optional profile for augmented chat system prompt injection
    # (degrades gracefully to no injection if missing/empty/disabled)
    try:
        _dp_mod = importlib.import_module("aip.adapter.definer_profile")
        _DefinerProfile = _dp_mod.DefinerProfile
        definer_cfg = config.get("definer", {})
        profile_path = definer_cfg.get("profile_path", "examples/seed_corpus/definer_profile_v1.md")
        container.definer_profile = _DefinerProfile(profile_path)
        log.info("component_initialized", component="definer_profile", required=False)
    except Exception as exc:
        log.warning("component_failed", component="definer_profile", degradation="no_profile_injection", error=str(exc))

    # --- Wire BudgetManager ---
    if container.budget_store is not None:
        try:
            _bm_mod = importlib.import_module("aip.orchestration.budget")
            _BudgetManager = _bm_mod.BudgetManager
            _BudgetConfig = importlib.import_module("aip.foundation.schemas.budget").BudgetConfig
            budget_cfg = _BudgetConfig(
                **{k: v for k, v in config.get("budget", {}).items() if k in _BudgetConfig.__dataclass_fields__},
            )
            container.budget_manager = _BudgetManager(
                config=budget_cfg,
                budget_store=container.budget_store,
                event_store=container.event_store,
            )
            log.info(
                "component_initialized",
                component="budget_manager",
                required=False,
                hard_stop=budget_cfg.budget_hard_stop,
            )
        except Exception as exc:
            log.warning(
                "component_failed", component="budget_manager", degradation="no_budget_enforcement", error=str(exc)
            )

    # --- Wire TraceStoreAdapter ---
    # Adapts QueryableEventStore to TraceStore protocol so that orchestration
    # modules (Sexton, L4, perf) can use write_event(session_id, node_type, ...)
    # without the signature mismatch that would cause TypeError at runtime.
    if container.event_store is not None:
        try:
            from aip.adapter.trace_store_adapter import TraceStoreAdapter

            container.trace_store = TraceStoreAdapter(container.event_store)
            log.info("component_initialized", component="trace_store", adapter="TraceStoreAdapter")
        except Exception as exc:
            log.warning(
                "component_failed", component="trace_store", degradation="trace_events_unavailable", error=str(exc)
            )

    # ECS store — use PersistentEcsStore for state that survives restart.
    # IMPORTANT (BUG-003 fix): Must be initialized BEFORE Sexton actor creation
    # because Sexton requires ecs_store for writing tagging proposals, wiki
    # articles, and graph extraction artifacts via ECS transitions.  Previously
    # ECS was initialized after the actors, causing Sexton to receive None.
    try:
        _ecs_mod = importlib.import_module("aip.adapter.ecs_store_persistent")
        _PersistentEcsStore = _ecs_mod.PersistentEcsStore
        container.ecs_store = _PersistentEcsStore(
            db_path=db_path,
            event_store=container.event_store,
        )
        await container.ecs_store.initialize()
        log.info("component_initialized", component="ecs_store", persistent=True)
    except Exception as exc:
        log.warning("component_failed", component="ecs_store", degradation="in_memory_fallback", error=str(exc))

    # ReviewQueueStore — optional, for MANUAL mode review queue persistence.
    # Also initialized before actors so it's available if needed.
    try:
        _rqs_mod = importlib.import_module("aip.adapter.review_queue_store")
        _ReviewQueueStore = _rqs_mod.ReviewQueueStore
        container.review_queue_store = _ReviewQueueStore(db_path=db_path)
        await container.review_queue_store.initialize()
        log.info("component_initialized", component="review_queue_store", required=False)
    except Exception as exc:
        log.warning(
            "component_failed",
            component="review_queue_store",
            degradation="manual_review_degraded",
            error=str(exc),
        )

    # GraphStore — knowledge graph nodes and edges (degrades to no graph retrieval)
    try:
        from aip.adapter.graph_store import GraphStore

        container.graph_store = GraphStore(db_path, config=config)
        await container.graph_store.initialize()
        log.info("component_initialized", component="graph_store", required=False)
    except Exception as exc:
        log.warning(
            "component_failed",
            component="graph_store",
            degradation="no_graph_retrieval",
            error=str(exc),
        )

    # --- ADR-008 Multi-Corpus: wire the CorpusRegistry ---
    # The registry is the primary store-access interface. It creates per-corpus
    # stores (turn_store, ecs_store, artifact_store) for the definer corpus
    # pointing at the same db_path as the legacy stores. After the registry is
    # wired, the legacy container attributes (corpus_turn_store, artifact_store,
    # ecs_store) are overwritten to point to the registry's stores — so all
    # existing call sites automatically use the registry without code changes.
    try:
        from pathlib import Path as _Path

        from aip.adapter.corpus_registry import CorpusRegistry
        from aip.foundation.corpus_types import CorpusType

        _registry = CorpusRegistry(max_corpora=4)
        await _registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, _Path(db_path)),
            ],
        )
        container.corpus_registry = _registry

        # Fix contract gaps: the factory's ECS store doesn't have event_store
        # set (the legacy code passed event_store=container.event_store).
        # Fix it here so ECS transitions write events.
        if container.definer_stores is not None and container.definer_stores.ecs_store is not None:
            container.definer_stores.ecs_store._event_store = container.event_store

        # Overwrite legacy attributes with the registry's stores.
        # This makes all 264 call sites automatically use the registry.
        if container.definer_stores is not None:
            container.corpus_turn_store = container.definer_stores.turn_store
            container.artifact_store = container.definer_stores.artifact_store
            container.ecs_store = container.definer_stores.ecs_store

        log.info(
            "component_initialized",
            component="corpus_registry",
            definer_wired=container.definer_stores is not None,
        )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="corpus_registry",
            degradation="legacy_singletons_only",
            error=str(exc),
        )

    # --- ADR-014 Phase 0: wire the ExtensionHost ---
    # The host discovers, validates, migrates, registers, and (v1.1) mounts
    # every extension under the operator-owned extensions/ dir. It runs AFTER
    # CorpusRegistry.startup (so contributed corpora can register) and BEFORE
    # the actor schedulers (so extension actors are registered before the first
    # Beast/Vigil/Sexton cycle). A broken extension never takes down the host —
    # per-stage sandbox transitions the extension to DEGRADED/FAILED.
    extensions_host: Any = None
    try:
        from pathlib import Path as _Path

        from aip.adapter.extensions import ExtensionHost
        from aip.orchestration.workflow.engine import WorkflowEngine
        from aip.orchestration.workflow_registry import WorkflowRegistry

        _extensions_dir = _Path(
            config.get("extensions", {}).get("dir", "extensions")
        )
        _manifest_range = tuple(
            config.get("extensions", {}).get("manifest_version_range", (1, 1))
        )
        # ADR-014 §5.4: WorkflowRegistry is host-owned. Construct it with the
        # default workflows/ dir (backward compat), then let the host call
        # add_path() for each extension's workflows_dir at stage 3.
        _workflow_registry = WorkflowRegistry(
            workflows_dir=config.get("workflows", {}).get("dir", "workflows")
        )
        container.workflow_registry = _workflow_registry

        # ADR-014 §8 step 2: WorkflowEngine is host-owned. Construct it with
        # the container's stores so workflows can retrieve/synthesize/review.
        # The engine executes YAML workflows (including extension-contributed
        # ones discovered via WorkflowRegistry.add_path). Extensions access it
        # via ctx.container.workflow_engine.run_workflow(path, variables).
        _workflow_engine = WorkflowEngine(
            vector_store=container.vector_store,
            trace_store=container.trace_store,
            artifact_store=getattr(container, "artifact_store", None),
            ecs_store=getattr(container, "ecs_store", None),
            event_store=container.event_store,
            config=config,
            budget_store=container.budget_store,
            autonomy_gate=container.autonomy_gate,
        )
        container.workflow_engine = _workflow_engine

        extensions_host = ExtensionHost(
            extensions_dir=_extensions_dir,
            container=container,
            manifest_version_range=_manifest_range,  # type: ignore[arg-type]
            workflow_registry=_workflow_registry,
        )
        container.extensions = extensions_host
        await extensions_host.start()
        log.info(
            "component_initialized",
            component="extension_host",
            extensions_dir=str(_extensions_dir),
            extension_count=len(extensions_host.health()),
            workflow_templates=len(_workflow_registry.list_templates()),
            workflow_engine_wired=True,
        )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="extension_host",
            degradation="no_extensions_loaded",
            error=str(exc),
        )

    # --- Wire orchestration components (lazy import to preserve layer discipline) ---
    # Beast actor — requires vector_store + embedding_provider at minimum
    if container.vector_store is not None and container.embedding_provider is not None:
        try:
            _beast_mod = importlib.import_module("aip.orchestration.actors.beast")
            _Beast = _beast_mod.Beast
            beast_config = BeastCadenceConfig(
                **{k: v for k, v in config.get("beast", {}).items() if k in BeastCadenceConfig.__dataclass_fields__},
            )
            container.beast = _Beast(
                config=beast_config,
                vector_store=container.vector_store,
                embedding_provider=container.embedding_provider,
                project_store=container.project_store,
                event_store=container.event_store,
                entity_store=container.entity_store,
                canonical_store=container.canonical_store,
                beast_provider=container.model_provider,  # Sprint 8: "beast" model slot for domain summaries
                artifact_store=container.artifact_store,  # Sprint 8: for writing domain summary artifacts
                ecs_store=container.ecs_store,  # Sprint 8: for ECS transitions on artifacts
                lexical_store=getattr(container, "lexical_store", None),  # Sprint 8: for sampling chunks
                corpus_turn_store=getattr(container, "corpus_turn_store", None),  # Sprint 8: for turn tagging
            )
            log.info("component_initialized", component="beast", required=False)
        except Exception as exc:
            log.warning("component_failed", component="beast", degradation="no_background_tasks", error=str(exc))
    else:
        log.info("component_skipped", component="beast", reason="missing_vector_or_embedding")

    # Vigil actor — requires vigil_store, canonical_store, entity_store, model_provider, trace_store
    if (
        container.vigil_store is not None
        and container.canonical_store is not None
        and container.entity_store is not None
        and container.model_provider is not None
    ):
        try:
            _vigil_mod = importlib.import_module("aip.orchestration.actors.vigil")
            _Vigil = _vigil_mod.Vigil
            _VigilConfig = importlib.import_module("aip.foundation.schemas.review").VigilConfig
            # Flatten nested [vigil.retrieval_quality] TOML section into VigilConfig fields.
            # TOML uses short names (sampling_enabled, sample_size, precision_threshold,
            # sample_interval_cycles) while VigilConfig uses retrieval_quality_ prefixed names.
            _vigil_flat = {k: v for k, v in config.get("vigil", {}).items() if k in _VigilConfig.__dataclass_fields__}
            _rq = config.get("vigil", {}).get("retrieval_quality", {})
            if _rq:
                _vigil_flat["retrieval_quality_sampling_enabled"] = _rq.get("sampling_enabled", True)
                _vigil_flat["retrieval_quality_sample_size"] = _rq.get("sample_size", 5)
                _vigil_flat["retrieval_quality_threshold"] = _rq.get("precision_threshold", 0.3)
                _vigil_flat["retrieval_quality_sample_interval_cycles"] = _rq.get("sample_interval_cycles", 6)
            vigil_config = _VigilConfig(**_vigil_flat)
            # trace_store: use event_store if it supports write_event (TraceStore protocol)
            # The event_store and trace_store share the same interface in the current impl
            trace_store = container.event_store
            container.vigil = _Vigil(
                config=vigil_config,
                vigil_store=container.vigil_store,
                canonical_store=container.canonical_store,
                entity_store=container.entity_store,
                model_provider=container.model_provider,
                trace_store=container.trace_store or trace_store,
                artifact_store=container.artifact_store,  # Sprint 8: for writing evaluation artifacts
                ecs_store=container.ecs_store,  # Sprint 8: for ECS transitions on evaluation artifacts
                event_store=container.event_store,  # Sprint 8: for emitting vigil events
                corpus_turn_store=getattr(container, "corpus_turn_store", None),  # Sprint 8: for augmented chat turns
                alert_manager=getattr(container, "_alert_manager", None),  # Sprint 8: for quality degradation alerts
                quality_store=getattr(
                    container, "_vigil_quality_store", None
                ),  # Sprint 8: for persistent quality history
            )
            log.info("component_initialized", component="vigil", required=False)
        except Exception as exc:
            log.warning("component_failed", component="vigil", degradation="no_canonical_monitoring", error=str(exc))
    else:
        log.info("component_skipped", component="vigil", reason="missing_required_stores_or_model_provider")

    # Sexton maintenance actor (ADR-011) — full vigil cycle:
    # tagging, embedding, wiki, graph extraction, failure classification.
    # Requires: model_provider (sexton slot), corpus_turn_store, embedding_provider,
    # vector_store, artifact_store, ecs_store, event_store, trace_store.
    # Gracefully degrades: if any required store is missing, the actor is None
    # and individual operations skip when their dependencies are absent.
    if container.event_store is not None:
        try:
            _sexton_actor_mod = importlib.import_module("aip.orchestration.actors.sexton")
            _SextonActor = _sexton_actor_mod.Sexton
            _SextonActorConfig = importlib.import_module("aip.foundation.schemas.evaluation").SextonConfig
            sexton_actor_config = _SextonActorConfig(
                **{k: v for k, v in config.get("sexton", {}).items() if k in _SextonActorConfig.__dataclass_fields__},
            )
            container.sexton_actor = _SextonActor(
                sexton_provider=container.model_provider,
                corpus_turn_store=getattr(container, "corpus_turn_store", None),
                embedding_provider=container.embedding_provider,
                vector_store=container.vector_store,
                artifact_store=container.artifact_store,
                ecs_store=container.ecs_store,
                event_store=container.event_store,
                trace_store=getattr(container, "trace_store", None) or container.event_store,
                lexical_store=getattr(container, "lexical_store", None),
                config=sexton_actor_config,
                graph_store=getattr(container, "graph_store", None),
                alert_manager=getattr(container, "_alert_manager", None),  # Sprint 8: for batch reduction alerts
            )
            # BUG-003 safety net: backfill _ecs if actor was created before
            # ECS store was available (shouldn't happen now, but defensive)
            if container.sexton_actor is not None and container.ecs_store is not None:
                if getattr(container.sexton_actor, "_ecs", None) is None:
                    container.sexton_actor._ecs = container.ecs_store
                    log.info("sexton_actor_ecs_backfill", reason="ecs_store_was_none_at_creation")
            log.info("component_initialized", component="sexton_actor", required=False)

            # Sprint 6.3: Restart recovery — check for interrupted cycle on startup
            try:
                interrupted = await container.sexton_actor.detect_interrupted_cycle()
                if interrupted is not None:
                    log.warning(
                        "sexton_interrupted_cycle_on_startup",
                        cycle=interrupted.get("cycle"),
                        cycle_id=interrupted.get("cycle_id"),
                        note="next_regular_cycle_will_handle_pending_work",
                    )
            except Exception as recovery_exc:
                log.warning("sexton_startup_recovery_check_failed", error=str(recovery_exc))
        except Exception as exc:
            log.warning(
                "component_failed", component="sexton_actor", degradation="no_background_maintenance", error=str(exc)
            )
    else:
        log.info("component_skipped", component="sexton_actor", reason="missing_event_store")

    # PerformanceProfiler — optional, only wired when profiling_enabled=True
    try:
        _perf_mod = importlib.import_module("aip.orchestration.perf")
        _PerformanceProfiler = _perf_mod.PerformanceProfiler
        _PerfConfig = importlib.import_module("aip.foundation.schemas.config").PerformanceConfig
        perf_cfg = _PerfConfig(
            **{k: v for k, v in config.get("performance", {}).items() if k in _PerfConfig.__dataclass_fields__},
        )
        if perf_cfg.profiling_enabled and container.event_store is not None:
            container.performance_profiler = _PerformanceProfiler(
                config=perf_cfg,
                trace_store=getattr(container, "trace_store", None) or container.event_store,
            )
            log.info("component_initialized", component="performance_profiler", profiling_enabled=True)
        else:
            log.info(
                "component_skipped",
                component="performance_profiler",
                profiling_enabled=perf_cfg.profiling_enabled,
            )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="performance_profiler",
            degradation="performance_api_disabled",
            error=str(exc),
        )

    # ECS store and ReviewQueueStore are initialized before actors (BUG-003 fix).

    # Session store — chat session persistence (degrades to in-memory)
    try:
        _ss_mod = importlib.import_module("aip.adapter.session.sqlite_session_store")
        _SqliteSessionStore = _ss_mod.SqliteSessionStore
        container.session_store = _SqliteSessionStore(db_path)
        await container.session_store.initialize()
        log.info("component_initialized", component="session_store", required=False)
    except Exception as exc:
        log.warning("component_failed", component="session_store", degradation="in_memory_sessions", error=str(exc))

    # SessionManager — orchestration session lifecycle
    if container.session_store is not None:
        try:
            _sm_mod = importlib.import_module("aip.orchestration.session")
            _SessionManager = _sm_mod.SessionManager
            container.session_manager = _SessionManager(config=config)
            log.info("component_initialized", component="session_manager", required=False)
        except Exception as exc:
            log.warning(
                "component_failed", component="session_manager", degradation="no_trajectory_regulation", error=str(exc)
            )

    # ACE Playbook — async-safe procedural intervention rules (Chunk 4)
    # Uses ace_playbook.db in the same directory as state.db.
    try:
        _ace_mod = importlib.import_module("aip.orchestration.ace_playbook")
        AcePlaybook = _ace_mod.AcePlaybook

        ace_db_path = os.path.join(os.path.dirname(db_path), "ace_playbook.db")
        ace_cfg = config.get("ace_playbook", {})
        container.ace_playbook = AcePlaybook(ace_db_path, config=ace_cfg)
        await container.ace_playbook.initialize()
        log.info("component_initialized", component="ace_playbook", required=False, db_path=ace_db_path)
    except Exception as exc:
        log.warning("component_failed", component="ace_playbook", degradation="no_intervention_rules", error=str(exc))

    # =====================================================================
    # Sprint 5.27: OPERATIONAL COMPONENTS — alerting, persistence, policy
    # =====================================================================

    # VigilQualityStore — persistent quality history (SQLite-backed)
    # Survives process restarts and supports longer time-range queries.
    # When unavailable, Vigil falls back to in-memory _cycle_report_history.
    # Sprint 5.27: Retention/rollup config driven from [vigil_quality] TOML section.
    try:
        from aip.adapter.vigil.vigil_quality_store import VigilQualityStore

        quality_db_path = os.path.join(os.path.dirname(db_path), "vigil_quality.db")
        quality_cfg = config.get("vigil_quality", {})
        container._vigil_quality_store = VigilQualityStore(
            quality_db_path,
            max_history_rows=int(quality_cfg.get("max_history_rows", 10000)),
            retention_days=int(quality_cfg.get("retention_days", 90)),
            rollup_age_days=int(quality_cfg.get("rollup_age_days", 7)),
            weekly_rollup_age_weeks=int(quality_cfg.get("weekly_rollup_age_weeks", 4)),
        )
        await container._vigil_quality_store.initialize()
        log.info(
            "component_initialized",
            component="vigil_quality_store",
            required=False,
            db_path=quality_db_path,
            max_history_rows=container._vigil_quality_store._max_history_rows,
            retention_days=container._vigil_quality_store._retention_days,
            rollup_age_days=container._vigil_quality_store._rollup_age_days,
        )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="vigil_quality_store",
            degradation="in_memory_quality_history",
            error=str(exc),
        )

    # AlertManager — operator notifications for quality degradation, pool
    # adjustments, and batch reductions.  Configured via [alerting] in TOML.
    # When disabled (default), no alerts are sent; all events are still logged.
    try:
        from aip.adapter.alerting import AlertConfig, AlertManager

        alert_cfg_dict = config.get("alerting", {})
        # Sprint 5.29: Parse alert routing config from [alerting.routes]
        routes_raw = alert_cfg_dict.get("routes", {})
        routes = {}
        if isinstance(routes_raw, dict):
            for alert_type, transport_list in routes_raw.items():
                if isinstance(transport_list, list):
                    routes[alert_type] = transport_list
                elif isinstance(transport_list, str):
                    routes[alert_type] = [transport_list]
        alert_config = AlertConfig(
            enabled=alert_cfg_dict.get("enabled", False),
            webhook_url=alert_cfg_dict.get("webhook_url", ""),
            email_to=alert_cfg_dict.get("email_to", ""),
            email_from=alert_cfg_dict.get("email_from", "aip-brain@localhost"),
            smtp_host=alert_cfg_dict.get("smtp_host", ""),
            smtp_port=int(alert_cfg_dict.get("smtp_port", 587)),
            smtp_username=alert_cfg_dict.get("smtp_username", ""),
            smtp_password=alert_cfg_dict.get("smtp_password", "") or os.environ.get("AIP_SMTP_PASSWORD", ""),
            smtp_use_tls=bool(alert_cfg_dict.get("smtp_use_tls", True)),
            alert_on_quality_degradation=bool(alert_cfg_dict.get("alert_on_quality_degradation", True)),
            alert_on_pool_adjustment=bool(alert_cfg_dict.get("alert_on_pool_adjustment", True)),
            alert_on_batch_reduction=bool(alert_cfg_dict.get("alert_on_batch_reduction", True)),
            min_alert_interval_seconds=int(alert_cfg_dict.get("min_alert_interval_seconds", 300)),
            webhook_max_retries=int(alert_cfg_dict.get("webhook_max_retries", 3)),
            webhook_retry_base_delay_seconds=float(alert_cfg_dict.get("webhook_retry_base_delay_seconds", 1.0)),
            routes=routes,
            # Sprint 5.45: A/B experiment configuration
            ab_experiment_enabled=bool(alert_cfg_dict.get("ab_experiment_enabled", False)),
            ab_auto_promote_interval_seconds=int(alert_cfg_dict.get("ab_auto_promote_interval_seconds", 300)),
            ab_auto_promote_confidence_threshold=float(
                alert_cfg_dict.get("ab_auto_promote_confidence_threshold", 0.95)
            ),
            ab_auto_promote_min_samples=int(alert_cfg_dict.get("ab_auto_promote_min_samples", 50)),
            # Sprint 5.46: Experiment expiry/cleanup
            ab_experiment_ttl_hours=int(alert_cfg_dict.get("ab_experiment_ttl_hours", 168)),
            ab_stopped_experiment_retention_hours=int(alert_cfg_dict.get("ab_stopped_experiment_retention_hours", 72)),
            ab_cleanup_interval_seconds=int(alert_cfg_dict.get("ab_cleanup_interval_seconds", 3600)),
            # Sprint 5.46: Promotion rollback
            ab_rollback_enabled=bool(alert_cfg_dict.get("ab_rollback_enabled", False)),
            ab_rollback_observation_window_seconds=int(
                alert_cfg_dict.get("ab_rollback_observation_window_seconds", 1800)
            ),
            ab_rollback_accuracy_drop_threshold=float(alert_cfg_dict.get("ab_rollback_accuracy_drop_threshold", 0.05)),
            # Sprint 5.46: Decay recovery
            decay_recovery_enabled=bool(alert_cfg_dict.get("decay_recovery_enabled", False)),
            decay_recovery_threshold=float(alert_cfg_dict.get("decay_recovery_threshold", 0.15)),
            # Sprint 5.47: Rollback + live config reversion
            ab_rollback_revert_live_config=bool(alert_cfg_dict.get("ab_rollback_revert_live_config", True)),
            # Sprint 5.47: Statistical significance testing
            ab_statistical_significance_enabled=bool(alert_cfg_dict.get("ab_statistical_significance_enabled", False)),
            ab_statistical_significance_p_value=float(alert_cfg_dict.get("ab_statistical_significance_p_value", 0.05)),
            ab_statistical_significance_method=alert_cfg_dict.get("ab_statistical_significance_method", "z_test"),
            ab_statistical_significance_min_samples=int(
                alert_cfg_dict.get("ab_statistical_significance_min_samples", 30)
            ),
            # Sprint 5.47: Cleanup alerting
            ab_cleanup_alert_on_ttl_expiry=bool(alert_cfg_dict.get("ab_cleanup_alert_on_ttl_expiry", True)),
            # Sprint 5.47: Confidence calibration
            ab_confidence_calibration_enabled=bool(alert_cfg_dict.get("ab_confidence_calibration_enabled", False)),
            # Sprint 5.48: Rollback dry-run mode
            ab_rollback_dry_run=bool(alert_cfg_dict.get("ab_rollback_dry_run", False)),
            # Sprint 5.48: Multi-armed bandit
            ab_bandit_enabled=bool(alert_cfg_dict.get("ab_bandit_enabled", False)),
            ab_bandit_method=alert_cfg_dict.get("ab_bandit_method", "thompson"),
        )
        container._alert_manager = AlertManager(alert_config)
        # Validate config at startup and log any warnings
        warnings = container._alert_manager.validate_config()
        if warnings:
            for w in warnings:
                log.warning("alerting_config_warning", warning=w)
        log.info(
            "component_initialized",
            component="alert_manager",
            required=False,
            enabled=alert_config.enabled,
        )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="alert_manager",
            degradation="no_operator_alerts",
            error=str(exc),
        )

    # Sprint 5.29: AlertHistoryStore — persistent alert history (SQLite-backed).
    # When available, attaches to the AlertManager so alert history and
    # delivery failures survive process restarts.
    try:
        from aip.adapter.alert_history_store import AlertHistoryStore

        alert_db_path = os.path.join(os.path.dirname(db_path), "alert_history.db")
        container._alert_history_store = AlertHistoryStore(alert_db_path)
        await container._alert_history_store.initialize()

        # Wrap in SyncAlertHistoryBridge for AlertManager compatibility
        from aip.adapter.alert_history_store import SyncAlertHistoryBridge

        container._alert_history_bridge = SyncAlertHistoryBridge(container._alert_history_store)

        # Attach to AlertManager if initialized
        if container._alert_manager is not None:
            container._alert_manager.attach_history_store(container._alert_history_bridge)

        log.info(
            "component_initialized",
            component="alert_history_store",
            required=False,
            db_path=alert_db_path,
        )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="alert_history_store",
            degradation="in_memory_alert_history",
            error=str(exc),
        )

    # ReadPoolAutoSizer — monitors pool exhaustion and auto-applies changes.
    # The auto-sizer is not attached to any background scheduler; instead,
    # the Sexton actor observes pool health on each cycle and triggers
    # adjustments when sustained high/low exhaustion is detected.
    try:
        from aip.adapter.read_pool import ReadPoolAutoSizer

        container._read_pool_auto_sizer = ReadPoolAutoSizer(
            auto_apply_enabled=bool(config.get("read_pool", {}).get("auto_apply_enabled", True)),
        )
        log.info(
            "component_initialized",
            component="read_pool_auto_sizer",
            required=False,
        )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="read_pool_auto_sizer",
            degradation="no_auto_pool_sizing",
            error=str(exc),
        )

    # AutoTuningPolicy — configurable thresholds for auto-tuning behavior.
    # Loaded from [auto_tuning_policy] section and applied to the auto-sizer
    # and Sexton actor at startup.  The policy can be hot-reloaded later.
    try:
        from aip.adapter.auto_tuning_policy import (
            apply_policy_to_auto_sizer,
            apply_policy_to_sexton,
            load_policy_from_config,
        )

        policy = load_policy_from_config(config)
        container._auto_tuning_policy = policy

        # Apply policy to auto-sizer
        if container._read_pool_auto_sizer is not None and policy.is_valid():
            applied = apply_policy_to_auto_sizer(policy, container._read_pool_auto_sizer)
            log.info(
                "auto_tuning_policy_applied_to_sizer",
                applied_params=applied,
            )

        # Apply policy to Sexton actor
        if container.sexton_actor is not None and policy.is_valid():
            applied = apply_policy_to_sexton(policy, container.sexton_actor)
            log.info(
                "auto_tuning_policy_applied_to_sexton",
                applied_params=applied,
            )

        log.info(
            "component_initialized",
            component="auto_tuning_policy",
            required=False,
            valid=policy.is_valid(),
        )
    except Exception as exc:
        log.warning(
            "component_failed",
            component="auto_tuning_policy",
            degradation="default_thresholds",
            error=str(exc),
        )

    # Sprint 5.48: Wire live config reverter callbacks into AlertManager.
    # Connects rollback to ModelSlotResolver (model config) and
    # AutoTuningPolicy (tuning parameters) so that rollback automatically
    # reverts live system configuration to the pre-promotion baseline.
    if container._alert_manager is not None:
        # Wire ModelSlotResolver reverter — reverts model slot configuration
        # on rollback by restoring the pre-promotion baseline config.
        _model_resolver = getattr(container, "model_provider", None)
        if _model_resolver is not None and hasattr(_model_resolver, "resolve"):

            def _make_live_config_reverter(resolver):
                """Create a closure that reverts model slot config via the resolver."""

                def _revert_model_config(experiment_name: str, baseline_config: dict) -> bool:
                    try:
                        # Apply baseline config to the resolver's slot configuration
                        if hasattr(resolver, "_models_config"):
                            for slot_name, slot_cfg in baseline_config.items():
                                if isinstance(slot_cfg, dict) and slot_name in (resolver._models_config or {}):
                                    resolver._models_config[slot_name].update(slot_cfg)
                                    log.info(
                                        "model_slot_config_reverted",
                                        experiment=experiment_name,
                                        slot=slot_name,
                                    )
                        log.info("live_config_reverted", experiment=experiment_name)
                        return True
                    except Exception as exc:
                        log.warning("live_config_revert_failed", experiment=experiment_name, error=str(exc))
                        return False

                return _revert_model_config

            container._alert_manager.set_live_config_reverter(_make_live_config_reverter(_model_resolver))
            log.info("alert_manager_wired", component="live_config_reverter")

        # Wire AutoTuningPolicy reverter — restores auto-tuning policy parameters
        # on rollback from the snapshot captured at promotion time.
        _tuning_policy = getattr(container, "_auto_tuning_policy", None)
        if _tuning_policy is not None and hasattr(_tuning_policy, "to_dict"):

            def _make_auto_tuning_reverter(policy):
                """Create a closure that reverts auto-tuning policy from a snapshot."""

                def _revert_auto_tuning(snapshot_dict: dict) -> bool:
                    try:
                        # Restore policy fields from the snapshot
                        if hasattr(policy, "__dataclass_fields__"):
                            for field_name in policy.__dataclass_fields__:
                                if field_name.startswith("_"):
                                    continue
                                if field_name in snapshot_dict:
                                    try:
                                        setattr(policy, field_name, snapshot_dict[field_name])
                                    except (TypeError, AttributeError):
                                        pass
                        # Re-apply to auto-sizer and sexton
                        if hasattr(container, "_read_pool_auto_sizer") and container._read_pool_auto_sizer is not None:
                            try:
                                from aip.adapter.auto_tuning_policy import apply_policy_to_auto_sizer

                                apply_policy_to_auto_sizer(policy, container._read_pool_auto_sizer)
                            except Exception:
                                pass
                        if hasattr(container, "sexton_actor") and container.sexton_actor is not None:
                            try:
                                from aip.adapter.auto_tuning_policy import apply_policy_to_sexton

                                apply_policy_to_sexton(policy, container.sexton_actor)
                            except Exception:
                                pass
                        log.info("auto_tuning_policy_reverted")
                        return True
                    except Exception as exc:
                        log.warning("auto_tuning_revert_failed", error=str(exc))
                        return False

                return _revert_auto_tuning

            container._alert_manager.set_auto_tuning_reverter(_make_auto_tuning_reverter(_tuning_policy))
            log.info("alert_manager_wired", component="auto_tuning_reverter")

    # Sprint 5.48: Restore A/B experiments from persistent store on startup,
    # and start the auto-promotion and cleanup checkers if configured.
    if container._alert_manager is not None and container._alert_history_store is not None:
        try:
            stored_experiments = await container._alert_history_store.get_ab_experiments()
            if stored_experiments:
                for exp in stored_experiments:
                    name = exp.get("name", "")
                    if name and name not in container._alert_manager.ab_experiment_mgr._ab_experiments:
                        container._alert_manager.ab_experiment_mgr._ab_experiments[name] = exp
                log.info("ab_experiments_restored", count=len(stored_experiments))

            # Restore statistical test results
            if hasattr(container._alert_history_store, "get_statistical_test_results"):
                stored_stats = await container._alert_history_store.get_statistical_test_results()
                for result in stored_stats:
                    exp_name = result.get("experiment_name", "")
                    if exp_name:
                        container._alert_manager.ab_experiment_mgr._statistical_test_results[exp_name] = result

            # Restore accuracy timeseries
            if hasattr(container._alert_history_store, "get_accuracy_timeseries"):
                for exp in container._alert_manager.ab_experiment_mgr._ab_experiments.values():
                    ts = await container._alert_history_store.get_accuracy_timeseries(exp.get("name", ""))
                    if ts:
                        exp["accuracy_timeseries"] = ts

            # Sprint 5.49: Restore confidence calibration
            # Use the sync bridge (not the raw async store) for sync restore methods
            _sync_store = container._alert_history_bridge or container._alert_history_store
            calib_count = container._alert_manager.restore_confidence_calibration(_sync_store)
            if calib_count > 0:
                log.info("confidence_calibration_restored_on_startup", count=calib_count)

            # Sprint 5.49: Restore pre-promotion config snapshots
            snapshot_count = container._alert_manager.restore_pre_promotion_snapshots(_sync_store)
            if snapshot_count > 0:
                log.info("pre_promotion_snapshots_restored_on_startup", count=snapshot_count)

            # Sprint 5.50: Start snapshot GC if configured
            if hasattr(container._alert_manager, "start_snapshot_gc"):
                container._alert_manager.start_snapshot_gc()
                log.info("snapshot_gc_started")

            # Sprint 5.50: Run initial calibration drift check
            if hasattr(container._alert_manager, "check_calibration_drift"):
                try:
                    drifted = container._alert_manager.check_calibration_drift()
                    if drifted:
                        log.info("calibration_drift_detected_on_startup", count=len(drifted))
                except Exception as exc:
                    log.warning("calibration_drift_check_failed_on_startup", error=str(exc))

            # Start auto-promotion checker
            container._alert_manager.start_ab_promotion_checker()
            # Start cleanup checker
            container._alert_manager.start_ab_cleanup_checker()
            log.info("ab_experiment_checkers_started")
        except Exception as exc:
            log.warning("ab_experiment_restore_failed", error=str(exc))

    # Wire alert_manager and quality_store into Vigil actor (if initialized)
    if container.vigil is not None:
        if container._alert_manager is not None:
            container.vigil._alert_manager = container._alert_manager
            log.info("vigil_wired", component="alert_manager")
        if container._vigil_quality_store is not None:
            container.vigil._quality_store = container._vigil_quality_store
            # Pre-populate in-memory history from persistent store
            # Uses _load_quality_history() which properly awaits the
            # async get_cycles() call — never assigns a coroutine.
            try:
                await container.vigil._load_quality_history()
            except Exception as exc:
                log.warning(
                    "vigil_quality_history_load_failed_during_startup",
                    error=str(exc),
                )
            log.info("vigil_wired", component="quality_store")

    # Wire alert_manager into Sexton actor (if initialized)
    if container.sexton_actor is not None:
        if container._alert_manager is not None:
            container.sexton_actor._alert_manager = container._alert_manager
            log.info("sexton_actor_wired", component="alert_manager")

    # Wire alert_manager into ReadPoolAutoSizer (if initialized)
    if container._read_pool_auto_sizer is not None and container._alert_manager is not None:
        container._read_pool_auto_sizer._alert_manager = container._alert_manager
        log.info("read_pool_auto_sizer_wired", component="alert_manager")

    # ConfigWatcher — hot-reload for safe config changes without restart.
    # Monitors aip.config.toml for changes to [read_pool], [sexton],
    # and [auto_tuning_policy] sections.  Runs as a background task.
    config_watcher_task: asyncio.Task | None = None
    try:
        from aip.adapter.config_watcher import ConfigWatcher

        # Resolve the config file path (same logic as _load_toml_config)
        config_path_str = os.environ.get("AIP_CONFIG_PATH", "")
        if config_path_str:
            config_file_path = Path(config_path_str)
        else:
            candidates = [
                Path.cwd() / "config" / "aip.config.toml",
                Path(__file__).resolve().parent.parent.parent.parent / "config" / "aip.config.toml",
            ]
            config_file_path = None
            for candidate in candidates:
                if candidate.is_file():
                    config_file_path = candidate
                    break

        if config_file_path is not None:
            container._config_watcher = ConfigWatcher(
                config_path=config_file_path,
                container=container,
            )
            log.info(
                "component_initialized",
                component="config_watcher",
                required=False,
                config_path=str(config_file_path),
            )
        else:
            log.info("component_skipped", component="config_watcher", reason="no_config_file_found")
    except Exception as exc:
        log.warning(
            "component_failed",
            component="config_watcher",
            degradation="no_hot_reload",
            error=str(exc),
        )

    # --- Store registry: register all initialized stores with their db_path ---
    # This is the honest datastore truth. Each store tells us where it lives.
    lexical_db = os.path.join(os.path.dirname(db_path), "lexical.db")
    vector_db = os.path.join(os.path.dirname(db_path), "vectors.db")
    quality_db = os.path.join(os.path.dirname(db_path), "vigil_quality.db")
    alert_db = os.path.join(os.path.dirname(db_path), "alert_history.db")

    if container.entity_store is not None:
        container.register_store("entity_store", db_path)
    if container.canonical_store is not None:
        container.register_store("canonical_store", db_path)
    if container.event_store is not None:
        container.register_store("event_store", db_path)
    if container.autonomy_gate is not None:
        container.register_store("autonomy_gate", db_path)
    if container.artifact_store is not None:
        container.register_store("artifact_store", db_path)
    if container.lexical_store is not None:
        container.register_store("lexical_store", lexical_db)
    if container.corpus_turn_store is not None:
        container.register_store("corpus_turn_store", db_path)
    if container.vector_store is not None:
        container.register_store("vector_store", vector_db)
    if container.project_store is not None:
        container.register_store("project_store", db_path)
    if container.budget_store is not None:
        container.register_store("budget_store", db_path)
    if container.vigil_store is not None:
        container.register_store("vigil_store", db_path)
    if container.knowledge_store is not None:
        container.register_store("knowledge_store", db_path)
    if container.ecs_store is not None:
        container.register_store("ecs_store", db_path)
    if container.review_queue_store is not None:
        container.register_store("review_queue_store", db_path)
    if container.graph_store is not None:
        container.register_store("graph_store", db_path)
    if container.session_store is not None:
        container.register_store("session_store", db_path)
    if container._vigil_quality_store is not None:
        container.register_store("vigil_quality_store", quality_db)
    if container._alert_history_store is not None:
        container.register_store("alert_history_store", alert_db)
    if container.ace_playbook is not None:
        ace_db_path = os.path.join(os.path.dirname(db_path), "ace_playbook.db")
        container.register_store("ace_playbook", ace_db_path)

    # Log the datastore summary at startup — the honest truth about where data lives
    ds_summary = container.datastore_summary()
    log.info(
        "datastore_summary",
        architecture=ds_summary["architecture"],
        total_stores=ds_summary["total_stores"],
        total_db_files=ds_summary["total_db_files"],
        shared_databases=ds_summary["shared_databases"],
    )
    for store_name, store_info in ds_summary["stores"].items():
        log.info(
            "datastore_store",
            store=store_name,
            db_path=store_info["db_path"],
            exists=store_info["exists"],
            size_mb=store_info["size_mb"],
        )

    app.state.container = container
    app.state.start_time = time.time()
    container._app_start_time = time.time()

    # =====================================================================
    # Sprint 8: Dogfood mode startup validation
    # =====================================================================
    # After all components are initialized but before schedulers start,
    # validate dogfood readiness and log the results. If FULL mode is
    # requested but components are missing, enter loud degraded mode
    # (warnings, not fatal — the system still starts).
    # Full dogfood mode is not a slogan — it is a boot-validated operating state.
    try:
        from aip.config import DogfoodMode, get_dogfood_mode, validate_dogfood_readiness

        dogfood_mode = get_dogfood_mode(config)
        readiness = validate_dogfood_readiness(config, container)

        log.info(
            "dogfood_readiness",
            mode=dogfood_mode.value,
            is_ready=readiness.is_ready,
            degraded=readiness.degraded_components,
        )

        # For FULL mode: log the full readiness summary
        if dogfood_mode == DogfoodMode.FULL:
            log.info("dogfood_full_mode_summary\n%s", readiness.summary)

            if not readiness.is_ready:
                log.warning(
                    "dogfood_FULL_mode_degraded",
                    degraded_components=readiness.degraded_components,
                    note="System started in degraded mode. Missing components: %s",
                    missing=", ".join(readiness.degraded_components),
                )

        # For DIAGNOSTIC mode: always log the full summary
        if dogfood_mode == DogfoodMode.DIAGNOSTIC:
            log.info("dogfood_diagnostic_summary\n%s", readiness.summary)
    except Exception as exc:
        log.warning("dogfood_readiness_check_failed", error=str(exc))

    # --- ADR-008 Multi-Corpus: migration gate helper (§A5) ---
    # Actors MUST await the registry's _migration_ready event before their
    # first write, so they don't write during schema migration. This is
    # defensive: if the registry isn't wired yet (pre-Chunk-3), the gate
    # is a no-op and actors proceed (backward-compatible with single-corpus).
    async def _await_corpus_migration_ready() -> None:
        registry = getattr(container, "corpus_registry", None)
        if registry is not None:
            await registry.migration_ready.wait()

    # --- Beast background scheduler ---
    beast_task: asyncio.Task | None = None
    if container.beast is not None:

        async def _beast_scheduler():
            """Background loop that runs beast.run_cycle() periodically.

            Each cycle gets its own correlation ID so that all log messages
            within a single cycle can be traced back to that cycle.
            """
            await _await_corpus_migration_ready()  # ADR-008 §A5
            interval = container.beast._config.health_check_interval_seconds
            # Enforce a reasonable minimum to avoid busy-looping
            if interval < 60:
                interval = 300
                log.warning("beast_cadence_clamped", original_interval=interval, clamped_to=300)
            log.info("beast_scheduler_starting", interval_s=interval)
            cycle_num = 0
            while True:
                cycle_num += 1
                cycle_id = f"beast-{new_correlation_id()}"
                set_correlation_id(cycle_id)
                try:
                    log.info("beast_cycle_start", cycle=cycle_num)
                    summary = await container.beast.run_cycle()
                    log.info(
                        "beast_cycle_complete",
                        cycle=cycle_num,
                        health=summary.get("health_overall", "unknown"),
                        stale_vectors=summary.get("corpus", {}).get("stale_vectors_found", "?"),
                        reembedded=summary.get("corpus", {}).get("vectors_reembedded", 0),
                        elapsed_s=summary.get("cycle_elapsed_seconds", 0),
                    )
                except asyncio.CancelledError:
                    log.info("beast_scheduler_cancelled", cycle=cycle_num)
                    raise
                except Exception as exc:
                    log.error("beast_cycle_failed", cycle=cycle_num, error=str(exc), exc_info=True)
                finally:
                    set_correlation_id(None)
                await asyncio.sleep(interval)

        beast_task = asyncio.create_task(_beast_scheduler(), name="beast-scheduler")
        log.info("beast_scheduler_created")

    # --- Vigil background scheduler ---
    vigil_task: asyncio.Task | None = None
    if container.vigil is not None:

        async def _vigil_scheduler():
            """Background loop that runs vigil.run() periodically.

            Vigil monitors canonical health and detects stale items.
            Runs on a configurable interval (default: 3600s = 1 hour).
            """
            await _await_corpus_migration_ready()  # ADR-008 §A5
            interval = container.vigil.config.canonical_health_check_interval_seconds
            if interval < 60:
                interval = 3600
                log.warning("vigil_cadence_clamped", clamped_to=3600)
            log.info("vigil_scheduler_starting", interval_s=interval)
            cycle_num = 0
            while True:
                cycle_num += 1
                cycle_id = f"vigil-{new_correlation_id()}"
                set_correlation_id(cycle_id)
                try:
                    log.info("vigil_cycle_start", cycle=cycle_num)
                    await container.vigil.run()
                    log.info("vigil_cycle_complete", cycle=cycle_num)
                except asyncio.CancelledError:
                    log.info("vigil_scheduler_cancelled", cycle=cycle_num)
                    raise
                except Exception as exc:
                    log.error("vigil_cycle_failed", cycle=cycle_num, error=str(exc), exc_info=True)
                finally:
                    set_correlation_id(None)
                await asyncio.sleep(interval)

        vigil_task = asyncio.create_task(_vigil_scheduler(), name="vigil-scheduler")
        log.info("vigil_scheduler_created")

    # --- Sexton maintenance actor background scheduler (ADR-011) ---
    # Runs the full vigil cycle: tagging → embedding → wiki → graph → classification
    sexton_actor_task: asyncio.Task | None = None
    if container.sexton_actor is not None:

        async def _sexton_actor_scheduler():
            """Background loop that runs sexton_actor.run_cycle() periodically.

            Sexton maintenance actor performs the ADR-011 vigil cycle:
            1. Turn tagging (max 200/cycle)
            2. Embedding pass (max 50/cycle)
            3. Wiki generation (max 3 domains/cycle)
            4. Graph extraction (if bridge-tagged turns exist)
            5. Failure classification

            Runs on a 300s cadence per ADR-011.
            """
            await _await_corpus_migration_ready()  # ADR-008 §A5
            interval = 300  # ADR-011: vigil cycle every 300s
            # Allow config override via sexton.classification_interval_seconds
            try:
                cfg_interval = container.sexton_actor._config.classification_interval_seconds
                if cfg_interval >= 60:
                    interval = cfg_interval
            except Exception:
                pass
            if interval < 60:
                interval = 300
                log.warning("sexton_actor_cadence_clamped", clamped_to=300)
            log.info("sexton_actor_scheduler_starting", interval_s=interval)
            cycle_num = 0
            while True:
                cycle_num += 1
                cycle_id = f"sexton-actor-{new_correlation_id()}"
                set_correlation_id(cycle_id)
                try:
                    log.info("sexton_actor_cycle_start", cycle=cycle_num)
                    summary = await container.sexton_actor.run_cycle()
                    log.info(
                        "sexton_actor_cycle_complete",
                        cycle=cycle_num,
                        tagging=summary.get("tagging", {}).get("turns_tagged", 0),
                        embedding=summary.get("embedding", {}).get("embedded", 0),
                        wiki=summary.get("wiki", {}).get("domains_generated", 0),
                        graph_entities=summary.get("graph", {}).get("entities_created", 0),
                        classification=summary.get("classification", {}).get("classified", 0),
                        elapsed_s=summary.get("cycle_elapsed_seconds", 0),
                    )
                except asyncio.CancelledError:
                    log.info("sexton_actor_scheduler_cancelled", cycle=cycle_num)
                    raise
                except Exception as exc:
                    log.error("sexton_actor_cycle_failed", cycle=cycle_num, error=str(exc), exc_info=True)
                    # Record failure in actor state so status endpoints reflect it
                    try:
                        container.sexton_actor._recent_errors.append(f"cycle_{cycle_num}: {exc}")
                        container.sexton_actor._recent_errors = container.sexton_actor._recent_errors[-10:]
                    except Exception:
                        pass
                finally:
                    set_correlation_id(None)
                await asyncio.sleep(interval)

        sexton_actor_task = asyncio.create_task(_sexton_actor_scheduler(), name="sexton-actor-scheduler")
        log.info("sexton_actor_scheduler_created")

    # --- Startup immediate runs ---
    # Fire Sexton and Vigil once immediately on startup (background tasks)
    # so they process any backlog from the previous session without waiting
    # for the first scheduled interval.
    if container.sexton_actor is not None:

        async def _sexton_startup_run():
            await _await_corpus_migration_ready()  # ADR-008 §A5
            try:
                log.info("sexton_actor_startup_run_start")
                await container.sexton_actor.run_cycle()
                log.info("sexton_actor_startup_run_complete")
            except Exception as exc:
                log.warning("sexton_actor_startup_run_failed", error=str(exc))

        _sexton_startup_task = asyncio.create_task(_sexton_startup_run(), name="sexton-actor-startup")
        container._sexton_startup_task = _sexton_startup_task
        log.info("sexton_actor_startup_run_scheduled")

    if container.vigil is not None:

        async def _vigil_startup_run():
            await _await_corpus_migration_ready()  # ADR-008 §A5
            try:
                log.info("vigil_startup_run_start")
                await container.vigil.run()
                log.info("vigil_startup_run_complete")
            except Exception as exc:
                log.warning("vigil_startup_run_failed", error=str(exc))

        _vigil_startup_task = asyncio.create_task(_vigil_startup_run(), name="vigil-startup")
        container._vigil_startup_task = _vigil_startup_task
        log.info("vigil_startup_run_scheduled")

    # --- ConfigWatcher background scheduler (Sprint 5.27) ---
    if container._config_watcher is not None:

        async def _config_watcher_scheduler():
            """Background loop that checks for config file changes.

            Polls the config file periodically and applies hot-reload
            when changes are detected.  Uses the ConfigWatcher's built-in
            rate-limiting and debounce.
            """
            poll_interval = 5.0  # Check every 5 seconds
            log.info("config_watcher_scheduler_starting", interval_s=poll_interval)
            while True:
                try:
                    events = container._config_watcher.check_and_reload()
                    if events:
                        log.info(
                            "config_watcher_reloaded",
                            changes=len(events),
                            keys=[e.key for e in events],
                        )
                except asyncio.CancelledError:
                    log.info("config_watcher_scheduler_cancelled")
                    raise
                except Exception as exc:
                    log.warning("config_watcher_check_failed", error=str(exc))
                await asyncio.sleep(poll_interval)

        config_watcher_task = asyncio.create_task(_config_watcher_scheduler(), name="config-watcher-scheduler")
        log.info("config_watcher_scheduler_created")

    # --- Quality Store Rollup scheduler (Sprint 5.27) ---
    # Runs rollup once per day to aggregate older quality data, keeping
    # the vigil_quality_history table from growing indefinitely while
    # preserving long-term trend data.
    quality_rollup_task: asyncio.Task | None = None
    if container._vigil_quality_store is not None:

        async def _quality_rollup_scheduler():
            """Background loop that runs daily rollup on the quality store.

            Aggregates individual cycle rows that are older than
            ``rollup_age_days`` into daily summary rows.  Runs once
            every 24 hours.
            """
            rollup_interval = 86400  # 24 hours
            log.info("quality_rollup_scheduler_starting", interval_s=rollup_interval)
            while True:
                try:
                    result = await container._vigil_quality_store.run_rollup()
                    if result.get("rolled_up_days", 0) > 0:
                        log.info(
                            "quality_rollup_completed",
                            days=result.get("rolled_up_days", 0),
                            aggregated=result.get("rows_aggregated", 0),
                            deleted=result.get("rows_deleted", 0),
                        )
                except asyncio.CancelledError:
                    log.info("quality_rollup_scheduler_cancelled")
                    raise
                except Exception as exc:
                    log.warning("quality_rollup_failed", error=str(exc))
                await asyncio.sleep(rollup_interval)

        quality_rollup_task = asyncio.create_task(_quality_rollup_scheduler(), name="quality-rollup-scheduler")
        log.info("quality_rollup_scheduler_created")

    # --- Quality Store Weekly Rollup scheduler (Sprint 5.28) ---
    # Runs weekly rollup once per week to aggregate daily rollups into
    # weekly summaries for long-term trend visibility with minimal storage.
    quality_weekly_rollup_task: asyncio.Task | None = None
    if container._vigil_quality_store is not None:

        async def _quality_weekly_rollup_scheduler():
            """Background loop that runs weekly rollup on the quality store.

            Aggregates daily rollup rows that are older than
            ``weekly_rollup_age_weeks`` into weekly summary rows.
            Runs once every 7 days.
            """
            weekly_rollup_interval = 604800  # 7 days
            log.info("quality_weekly_rollup_scheduler_starting", interval_s=weekly_rollup_interval)
            while True:
                try:
                    result = await container._vigil_quality_store.run_weekly_rollup()
                    if result.get("rolled_up_weeks", 0) > 0:
                        log.info(
                            "quality_weekly_rollup_completed",
                            weeks=result.get("rolled_up_weeks", 0),
                            aggregated=result.get("rows_aggregated", 0),
                            deleted=result.get("rows_deleted", 0),
                        )
                except asyncio.CancelledError:
                    log.info("quality_weekly_rollup_scheduler_cancelled")
                    raise
                except Exception as exc:
                    log.warning("quality_weekly_rollup_failed", error=str(exc))
                await asyncio.sleep(weekly_rollup_interval)

        quality_weekly_rollup_task = asyncio.create_task(
            _quality_weekly_rollup_scheduler(), name="quality-weekly-rollup-scheduler"
        )
        log.info("quality_weekly_rollup_scheduler_created")

    # --- Wire orchestration function references into container ---
    # Routes access these through the container instead of importing
    # orchestration directly, preserving layer discipline.
    try:
        _ask_mod = importlib.import_module("aip.orchestration.ask_pipeline")
        container._ask_fn = _ask_mod.ask
        container._ask_stores_class = _ask_mod.AskStores
        container._search_sources_fn = _ask_mod._search_sources_with_trace
        try:
            from aip.foundation.sanitize_fts import sanitize_fts_query

            container._sanitize_fts_query_fn = sanitize_fts_query
        except ImportError:
            container._sanitize_fts_query_fn = None
        log.info("orchestration_functions_wired", module="ask_pipeline")
    except Exception as exc:
        log.warning("orchestration_functions_wiring_failed", module="ask_pipeline", error=str(exc))

    try:
        _ingest_mod = importlib.import_module("aip.orchestration.ingestion.pipeline")
        container._ingest_conversation_fn = _ingest_mod.ingest_conversation
        container._ingest_file_fn = _ingest_mod.ingest_file
        log.info("orchestration_functions_wired", module="ingestion.pipeline")
    except Exception as exc:
        log.warning("orchestration_functions_wiring_failed", module="ingestion.pipeline", error=str(exc))

    # Chunk 6: Wire corpus ingest functions (avoids route→orchestration import)
    try:
        _corpus_ingest_mod = importlib.import_module("aip.orchestration.ingestion.corpus_ingest_pipeline")
        container._corpus_ingest_config_class = _corpus_ingest_mod.CorpusIngestConfig
        container._ingest_directory_to_corpus_fn = _corpus_ingest_mod.ingest_directory_to_corpus
        container._ingest_file_to_corpus_fn = _corpus_ingest_mod.ingest_file_to_corpus
        log.info("orchestration_functions_wired", module="ingestion.corpus_ingest_pipeline")
    except Exception as exc:
        log.warning("orchestration_functions_wiring_failed", module="ingestion.corpus_ingest_pipeline", error=str(exc))

    # Chunk 6: Wire retrieval orchestrator access (avoids route→orchestration import)
    try:
        _retr_orch_mod = importlib.import_module("aip.orchestration.retrieval_orchestrator")
        container._get_orchestrator_cache_fn = _retr_orch_mod.get_orchestrator_cache
        container._orchestrator_config_class = _retr_orch_mod.OrchestratorConfig
        log.info("orchestration_functions_wired", module="retrieval_orchestrator")
    except Exception as exc:
        log.warning("orchestration_functions_wiring_failed", module="retrieval_orchestrator", error=str(exc))

    try:
        _channels_mod = importlib.import_module("aip.orchestration.channels.registry")
        container._builtin_channels = _channels_mod.BUILTIN_CHANNELS
        log.info("orchestration_functions_wired", module="channels.registry")
    except Exception as exc:
        log.warning("orchestration_functions_wiring_failed", module="channels.registry", error=str(exc))

    # Chunk 6: Wire adaptive budget tuner (avoids route→orchestration import)
    try:
        _budget_mod = importlib.import_module("aip.orchestration.adaptive_budget")
        container._adaptive_budget_tuner_class = _budget_mod.AdaptiveBudgetTuner
        log.info("orchestration_functions_wired", module="adaptive_budget")
    except Exception as exc:
        log.warning("orchestration_functions_wiring_failed", module="adaptive_budget", error=str(exc))

    log.info(
        "startup_complete",
        required_initialized=5,
        lexical=container.lexical_store is not None,
        vector=container.vector_store is not None,
        embedding=container.embedding_provider is not None,
        project=container.project_store is not None,
        budget=container.budget_store is not None,
        vigil_store=container.vigil_store is not None,
        vigil_actor=container.vigil is not None,
        sexton_actor=container.sexton_actor is not None,
        model=container.model_provider is not None,
        knowledge=container.knowledge_store is not None,
        beast=container.beast is not None,
        session_store=container.session_store is not None,
        session_manager=container.session_manager is not None,
        corpus_turn_store=getattr(container, "corpus_turn_store", None) is not None,
        # Sprint 5.27: Operational components
        quality_store=getattr(container, "_vigil_quality_store", None) is not None,
        alert_manager=getattr(container, "_alert_manager", None) is not None,
        config_watcher=getattr(container, "_config_watcher", None) is not None,
        auto_sizer=getattr(container, "_read_pool_auto_sizer", None) is not None,
        auto_tuning_policy=getattr(container, "_auto_tuning_policy", None) is not None,
        # Sprint 8: Dogfood mode
        dogfood_mode=getattr(dogfood_mode, "value", "unknown") if "dogfood_mode" in dir() else "unknown",
    )

    yield

    # --- Shutdown ---
    # Cancel scheduler tasks first (long-running loops)
    for task_name, task in [
        ("beast", beast_task),
        ("vigil", vigil_task),
        ("sexton_actor", sexton_actor_task),
        ("config_watcher", config_watcher_task),
        ("quality_rollup", quality_rollup_task),
        ("quality_weekly_rollup", quality_weekly_rollup_task),
    ]:
        if task is not None:
            log.info(f"{task_name}_scheduler_cancelling")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Cancel one-shot startup tasks (may still be running if shutdown happens quickly)
    for task_name, task in [
        ("sexton_startup", getattr(container, "_sexton_startup_task", None)),
        ("vigil_startup", getattr(container, "_vigil_startup_task", None)),
    ]:
        if task is not None:
            log.info(f"{task_name}_task_cancelling")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ADR-014: stop the ExtensionHost (cancels extension actor schedulers,
    # calls on_unload hooks sandboxed, marks every extension DISABLED).
    # The host manages its own task lifecycle internally — this is a single
    # await, not a loop. Errors are logged but never propagated (the host's
    # own stop() is already sandboxed per extension).
    if extensions_host is not None:
        try:
            await extensions_host.stop()
            log.info("extension_host_stopped")
        except Exception as exc:
            log.warning("extension_host_stop_failed", error=str(exc))

    # Sprint 5.46: Graceful shutdown persistence — persist all A/B experiments and stop checkers
    # Sprint 5.48: Also persist statistical test results and accuracy timeseries
    if hasattr(container, "_alert_manager") and container._alert_manager is not None:
        try:
            count = container._alert_manager.persist_all_ab_experiments()
            log.info("ab_experiments_persisted_on_shutdown", count=count)
        except Exception as exc:
            log.warning("ab_experiments_persist_failed", error=str(exc))

        # Sprint 5.48: Persist statistical test results
        # Use the sync bridge (not the raw async store) for sync persist methods
        _shutdown_sync_store = getattr(container, "_alert_history_bridge", None) or getattr(
            container, "_alert_history_store", None
        )
        if _shutdown_sync_store is not None:
            try:
                stat_count = container._alert_manager.persist_statistical_test_results(_shutdown_sync_store)
                log.info("statistical_test_results_persisted_on_shutdown", count=stat_count)
            except Exception as exc:
                log.warning("statistical_test_results_persist_failed", error=str(exc))

        # Sprint 5.50: Persist confidence calibration and snapshots on shutdown
        if _shutdown_sync_store is not None:
            try:
                calib_count = container._alert_manager.persist_confidence_calibration(_shutdown_sync_store)
                log.info("confidence_calibration_persisted_on_shutdown", count=calib_count)
            except Exception as exc:
                log.warning("confidence_calibration_persist_failed", error=str(exc))

            try:
                snap_count = container._alert_manager.persist_pre_promotion_snapshots(_shutdown_sync_store)
                log.info("pre_promotion_snapshots_persisted_on_shutdown", count=snap_count)
            except Exception as exc:
                log.warning("pre_promotion_snapshots_persist_failed", error=str(exc))

        # Sprint 5.50: Stop snapshot GC thread
        if hasattr(container._alert_manager, "stop_snapshot_gc"):
            try:
                container._alert_manager.stop_snapshot_gc()
            except Exception:
                pass

    # Close SyncAlertHistoryBridge (stops background thread)
    if getattr(container, "_alert_history_bridge", None) is not None:
        try:
            container._alert_history_bridge.close()
        except Exception:
            pass

    # shutdown: close any open connections (the individual stores implement close())
    for store_name, store in [
        ("knowledge_store", container.knowledge_store),
        ("vector_store", container.vector_store),
        ("lexical_store", container.lexical_store),
        ("canonical_store", container.canonical_store),
        ("entity_store", container.entity_store),
        ("event_store", container.event_store),
        ("project_store", container.project_store),
        ("budget_store", container.budget_store),
        ("vigil_store", getattr(container, "vigil_store", None)),
        ("autonomy_gate", container.autonomy_gate),
        ("artifact_store", container.artifact_store),
        ("model_provider", container.model_provider),
        ("ecs_store", container.ecs_store),
        ("review_queue_store", container.review_queue_store),
        ("session_store", container.session_store),
        ("corpus_turn_store", getattr(container, "corpus_turn_store", None)),
        ("graph_store", getattr(container, "graph_store", None)),
        ("ace_playbook", getattr(container, "ace_playbook", None)),
        ("vigil_quality_store", getattr(container, "_vigil_quality_store", None)),
        ("alert_history_store", getattr(container, "_alert_history_store", None)),
    ]:
        if store and hasattr(store, "close"):
            try:
                await store.close()
            except Exception as exc:
                log.warning("store_close_failed", store=store_name, error=str(exc))

    log.info("shutdown_complete")


def create_app(config: dict | None = None) -> "FastAPI":
    # If no config dict was passed, load from the TOML file.
    # This is the normal path when uvicorn calls create_app() via --factory
    # with no arguments. Without this, the entire config/aip.config.toml
    # is ignored and all model slots are empty (ci_mode=True).
    if config is None:
        config = _load_toml_config()
    cfg = config or {}

    # Config validation runs BEFORE the app is created.
    # Unsafe production configs fail fast with a clear error message.
    validation = validate_config(cfg)
    if not validation.is_valid:
        # Initialize logging early so critical messages are structured
        configure_logging()
        for err in validation.errors:
            log.critical("config_validation_error", error=str(err))
        raise validation.errors[0]

    surface_cfg = SurfaceConfig(**{k: v for k, v in cfg.items() if k in SurfaceConfig.__dataclass_fields__})

    app = FastAPI(title="AIP 0.1 Surfaces", version="0.1", lifespan=lifespan)

    # Global exception handler — returns structured JSON for unhandled exceptions
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        log.error(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error_type": type(exc).__name__,
                "path": request.url.path,
            },
        )

    app.state.raw_config = cfg
    app.state.container = None  # populated in lifespan
    app.state.start_time = time.time()

    # CORS from SurfaceConfig
    app.add_middleware(
        CORSMiddleware,
        allow_origins=surface_cfg.api_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Correlation ID middleware — runs early so all downstream logs have the ID
    app.add_middleware(CorrelationIdMiddleware)

    # Wire AuthMiddleware + RateLimitMiddleware
    from aip.adapter.auth.middleware import AuthMiddleware
    from aip.adapter.middleware.rate_limiter import RateLimitMiddleware, TokenBucketRateLimiter
    from aip.foundation.schemas import AuthConfig, RateLimitConfig

    auth_cfg = AuthConfig(**{k: v for k, v in cfg.get("auth", {}).items() if k in AuthConfig.__dataclass_fields__})
    rl_cfg = RateLimitConfig(
        **{k: v for k, v in cfg.get("rate_limit", {}).items() if k in RateLimitConfig.__dataclass_fields__},
    )

    # Auth store will be wired in lifespan; middleware references container
    # We use a lightweight factory that defers to the container
    class _AuthStoreProxy:
        """Proxy that delegates to the container's auth_store once available."""

        def __getattr__(self, name):
            container = getattr(app.state, "container", None)
            if container is not None:
                auth_store = getattr(container, "auth_store", None)
                if auth_store is not None:
                    return getattr(auth_store, name)
            return None

    _proxy = _AuthStoreProxy()
    app.add_middleware(AuthMiddleware, auth_store=_proxy, config=auth_cfg)

    rate_limiter = TokenBucketRateLimiter(rl_cfg)
    app.add_middleware(RateLimitMiddleware, rate_limiter=rate_limiter, config=rl_cfg)

    # Route modules
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(projects.router, prefix="/api/v1", tags=["projects"])
    app.include_router(sessions.router, prefix="/api/v1", tags=["sessions"])
    app.include_router(review.router, prefix="/api/v1", tags=["review"])
    app.include_router(artifacts.router, prefix="/api/v1", tags=["artifacts"])
    app.include_router(admin.router, prefix="/api/v1", tags=["admin"])
    app.include_router(memory.router, prefix="/api/v1", tags=["memory"])
    app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
    app.include_router(models.router, prefix="/api/v1", tags=["models"])
    app.include_router(models_library.router, prefix="/api/v1", tags=["models_library"])
    app.include_router(actors.router, prefix="/api/v1", tags=["actors"])
    app.include_router(ingest.router, prefix="/api/v1", tags=["ingest"])
    app.include_router(ask.router, prefix="/api/v1", tags=["ask"])
    app.include_router(beast_scan.router, prefix="/api/v1", tags=["beast"])
    app.include_router(knowledge.router, prefix="/api/v1", tags=["knowledge"])
    app.include_router(wiki.router, prefix="/api/v1", tags=["wiki"])
    app.include_router(ecs.router, prefix="/api/v1", tags=["ecs"])
    app.include_router(sources.router, prefix="/api/v1", tags=["sources"])
    app.include_router(corpus.router, prefix="/api/v1", tags=["corpus"])
    app.include_router(maintenance.router, prefix="/api/v1", tags=["maintenance"])
    app.include_router(turns.router, prefix="/api/v1", tags=["turns"])
    app.include_router(beast_commentary.router, prefix="/api/v1", tags=["beast_commentary"])
    app.include_router(model_council.router, prefix="/api/v1", tags=["model_council"])
    app.include_router(links.router, prefix="/api/v1", tags=["links"])
    app.include_router(graph.router, prefix="/api/v1", tags=["graph"])
    app.include_router(graph_viz.router, tags=["graph"])

    # Additional routers
    app.include_router(collaborators.router, prefix="/api/v1", tags=["collaborators"])
    app.include_router(plugins.router, prefix="/api/v1", tags=["plugins"])
    app.include_router(performance.router, prefix="/api/v1", tags=["performance"])
    # Retrieval dashboard — lightweight observability
    app.include_router(retrieval_dashboard.router, tags=["retrieval"])

    # Sprint 5.25: Vigil quality dashboard
    from aip.adapter.api.routes import vigil_quality

    app.include_router(vigil_quality.router, prefix="/api/v1", tags=["vigil"])

    # ADR-014 v1.1: include extension API routers.
    # Extensions register their API routers via host.register_api_router()
    # in their on_load hook. The host stores them; app.py reads them after
    # host.start() and includes them. This preserves the boundary: the
    # platform never imports an extension by name.
    extensions_host = getattr(container, "extensions", None)
    if extensions_host is not None:
        for router_info in extensions_host.registered_api_routers():
            try:
                app.include_router(router_info["router"], tags=[router_info["ext_id"]])
                log.info("extension_api_router_mounted ext=%s", router_info["ext_id"])
            except Exception as exc:
                log.warning("extension_api_router_mount_failed ext=%s error=%s", router_info["ext_id"], exc)

    # Web UI static (HTMX dashboard)
    import pathlib

    from fastapi.staticfiles import StaticFiles

    _static_dir = pathlib.Path(__file__).parent / "static"
    if _static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    # Backend root endpoint — the Operator Console (gui.app) runs as a
    # separate NiceGUI process on port 8080.  The backend must not mount
    # the frozen legacy gui/shell.py module.
    @app.get("/")
    async def root() -> dict[str, str]:
        """Backend root: returns basic service info.

        The Operator Console is started separately via ``python -m gui.app``
        or ``scripts/start.sh`` and runs on its own port (default 8080).
        """
        return {"status": "ok", "service": "aip-surfaces"}

    return app
