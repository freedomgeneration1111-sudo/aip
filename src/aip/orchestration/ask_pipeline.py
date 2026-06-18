"""Source-grounded ask pipeline — retrieve, assemble, dispatch, persist.

Contract: resolve project → multi-channel retrieval (FTS + Vector + Graph +
Wiki + Procedural + Corpus via RetrievalOrchestrator with RRF fusion) →
SmartContextPacker for budget-aware context assembly → model dispatch →
source-grounded answer with provenance references → optional ECS artifact
save → session trace recording.

Decomposed helpers:

- :mod:`aip.orchestration.channels.retrieval_config` — orchestrator config
  construction, channel weights, coverage gating, adaptive channel selection.
- :mod:`aip.orchestration.channels.retrieval_trace_utils` — degradation
  dict assembly, retrieval warnings, vector trace enrichment.
- :mod:`aip.orchestration.channels.registry` — channel auto-discovery
  and registration.
- :mod:`aip.orchestration.channels.types` — ``safe_retriever`` wrapper,
  ``ChannelFailure``, ``ChannelResult``.

Retriever channels are registered via the channel registry.  Adding a new
channel means creating a module in ``aip.orchestration.channels/`` — no
edits to this file required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import (
    ArtifactStore,
    EcsStore,
    EmbeddingProvider,
    EventStore,
    LexicalStore,
    ModelProvider,
    ProjectStore,
    VectorStore,
)
from aip.foundation.schemas.ask import AskResult, AskSource, SourceReference
from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace
from aip.orchestration.channels.registry import register_all_channels
from aip.orchestration.channels.retrieval_trace_utils import (
    build_degradation_dict,
    build_retrieval_warnings,
    enrich_vector_trace_detail,
)
from aip.orchestration.channels.types import ChannelFailure
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorCache,
    RetrievalOrchestrator,
    get_orchestrator_cache,
)
from aip.orchestration.smart_context_packer import (
    PackedContext,
    PackerConfig,
    SmartContextPacker,
)

logger = logging.getLogger(__name__)

_orchestrator_cache: OrchestratorCache = get_orchestrator_cache()

# Channel registration failures from the most recent orchestrator creation.
# Populated by _register_retriever_channels() for trace visibility.
_last_registration_failures: list[ChannelFailure] = []


def _format_source_citations(sources: list[SourceReference]) -> list[str]:
    """Generate inline source citation strings for the answer.

    Format: [source: <conversation_title>/<chunk_id>]
    or the shorter [source: <source_id>] for non-conversation sources.
    """
    citations = []
    for src in sources:
        if src.source_type == "conversation_chunk" and src.metadata.get("conversation_id"):
            conv_id = src.metadata["conversation_id"]
            citations.append(f"[source: {conv_id}/{src.source_id}]")
        else:
            citations.append(f"[source: {src.source_id}]")
    return citations


def _hit_type_matches(hit: RetrievalHit, source_filter: AskSource) -> bool:
    """Check if a RetrievalHit's source type matches the requested filter.

    Ingested conversation chunks have metadata.type == "conversation_chunk".
    Other indexed content (compiled knowledge, generated artifacts, wiki
    articles, procedural guides) are considered "artifacts" for filtering.
    """
    meta = hit.metadata or {}
    hit_type = meta.get("type", "")

    if source_filter == "all":
        return True
    elif source_filter == "ingested":
        return hit_type == "conversation_chunk"
    elif source_filter == "artifacts":
        return hit_type != "conversation_chunk"
    return True


async def _resolve_project(
    project_name: str,
    project_store: ProjectStore,
) -> dict | None:
    """Resolve a project by name, then by project_id as fallback."""
    projects = await project_store.list_projects()
    for p in projects:
        if p.get("name") == project_name:
            return p
    for p in projects:
        if p.get("project_id") == project_name:
            return p
    return None


def _register_retriever_channels(
    orchestrator: RetrievalOrchestrator,
    stores: AskStores,
    config: dict | None = None,
) -> None:
    """Register all available retriever channels on an orchestrator.

    Delegates to the channel registry which auto-discovers all built-in
    and custom channel modules.  Channel registration failures are stored
    as structured ``ChannelFailure`` objects (not just log lines) so that
    downstream consumers can inspect which channels were skipped and why.
    """
    global _last_registration_failures
    _last_registration_failures = register_all_channels(orchestrator, stores, config)


def _retrieval_hit_to_source_ref(hit: RetrievalHit) -> SourceReference:
    """Convert a RetrievalHit to a SourceReference for backward compatibility.

    The ask() function still returns SourceReference in AskResult.sources
    for backward compatibility.  This conversion preserves provenance.
    """
    meta = hit.metadata or {}
    chunk_type = meta.get("type", "unknown")
    conv_id = meta.get("conversation_id", "")
    domain = hit.domain or meta.get("domain", "")

    if chunk_type == "conversation_chunk" and conv_id:
        title = f"conversation:{conv_id}"
    else:
        title = hit.id

    content = hit.content or ""
    snippet = content[:200].replace("\n", " ") if content else ""

    return SourceReference(
        source_id=hit.id,
        source_type=chunk_type,
        title=title,
        score=hit.rrf_score or hit.score,
        content_snippet=snippet,
        domain=domain,
        metadata={
            "conversation_id": conv_id,
            "source_format": meta.get("source_format", ""),
            "source_channel": hit.source_channel,
            "rrf_score": hit.rrf_score,
        },
    )


async def _search_sources_with_trace(
    query: str,
    stores: AskStores,
    source_filter: AskSource = "all",
    max_sources: int = 10,
    session_id: str = "",
    config: dict | None = None,
    enable_fts: bool = True,
    enable_vector: bool = True,
    enable_graph: bool = False,
    enable_wiki: bool = False,
    enable_procedural: bool = False,
    auto_channel_selection: bool = True,
) -> tuple[list[SourceReference], RetrievalTrace | None, PackedContext | None]:
    """Search for sources using the multi-channel RetrievalOrchestrator.

    Primary retrieval path: RetrievalOrchestrator with parallel dispatch
    and RRF fusion, followed by SmartContextPacker for budget-aware
    context assembly.  When ``auto_channel_selection`` is True (default),
    ChannelSelector auto-enables relevant channels based on query signals;
    explicit ``enable_*`` parameters always take precedence.

    The orchestrator instance is cached via OrchestratorCache so channels
    are registered only once.

    Returns:
        Tuple of (source_references, retrieval_trace, packed_context).
    """
    from aip.orchestration.channels.retrieval_config import (
        apply_channel_selector,
        apply_channel_weights,
        build_orchestrator_config,
        check_vector_coverage,
    )

    store_key = id(stores.lexical_store) ^ id(stores.vector_store) ^ id(stores.corpus_turn_store)

    orchestrator = _orchestrator_cache.get_or_create(
        store_key=store_key,
        register_fn=lambda orch: _register_retriever_channels(orch, stores, config),
    )

    # Coverage-aware vector enablement
    vector_available = enable_vector and stores.vector_store is not None and stores.embedding_provider is not None
    vector_enabled = await check_vector_coverage(
        corpus_turn_store=stores.corpus_turn_store,
        vector_available=vector_available,
    )

    # Build orchestrator config from channel flags
    orch_config = build_orchestrator_config(
        enable_fts=enable_fts,
        enable_vector=enable_vector,
        enable_graph=enable_graph,
        enable_wiki=enable_wiki,
        enable_procedural=enable_procedural,
        vector_enabled=vector_enabled,
        max_sources=max_sources,
    )

    # Apply channel weights from TOML config
    _effective_config = config
    if not _effective_config:
        try:
            _effective_config = _load_config()
        except Exception:
            _effective_config = {}
    apply_channel_weights(orch_config, _effective_config, vector_enabled)

    # Adaptive channel selection based on query signals
    apply_channel_selector(query, orch_config, auto_channel_selection)

    try:
        hits, trace = await orchestrator.retrieve(
            query=query,
            config=orch_config,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("Orchestrator retrieval failed: %s", exc)
        # Build a degraded trace instead of returning None — callers and
        # dashboards can distinguish "retrieval crashed" from "no results".
        degraded_trace = RetrievalTrace(
            session_id=session_id,
            query=query,
            verdict="NO_RESULTS",
        )
        degraded_trace.degradation_warnings.append(f"Orchestrator retrieval failed: {exc}")
        return [], degraded_trace, None

    if source_filter != "all":
        hits = [h for h in hits if _hit_type_matches(h, source_filter)]

    hits = hits[:max_sources]

    packer_config = PackerConfig(
        max_context_tokens=4000,
        max_hits=max_sources,
        include_metadata=True,
    )
    packer = SmartContextPacker(config=packer_config)
    packed = packer.pack(hits, query=query)

    sources = [_retrieval_hit_to_source_ref(h) for h in hits]

    # Enrich trace with vector-store detail — extracted helper keeps this
    # concern out of the main search flow.
    if trace is not None:
        await enrich_vector_trace_detail(
            trace,
            stores.vector_store,
            stores.embedding_provider,
        )

    # Populate final context info on the trace
    if trace is not None and packed is not None:
        trace.final_context_token_count = packed.token_count if hasattr(packed, "token_count") else 0
        trace.final_context_source_ids = [h.id for h in hits]

    return sources, trace, packed


class AskStores:
    """Container for the stores needed by the ask pipeline.

    Shares the same persistent stores as the ingestion pipeline so that
    ``aip ask`` reads from the same data that ``aip ingest`` wrote.
    """

    def __init__(
        self,
        artifact_store: ArtifactStore,
        lexical_store: LexicalStore,
        vector_store: VectorStore | None,
        event_store: EventStore | None,
        project_store: ProjectStore,
        ecs_store: EcsStore | None = None,
        model_provider: ModelProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        corpus_turn_store: Any = None,
        graph_store: Any = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.lexical_store = lexical_store
        self.vector_store = vector_store
        self.event_store = event_store
        self.project_store = project_store
        self.ecs_store = ecs_store
        self.model_provider = model_provider
        self.embedding_provider = embedding_provider
        self.corpus_turn_store = corpus_turn_store
        self.graph_store = graph_store

    async def close(self) -> None:
        """Close all stores that have a close method."""
        for store in (
            self.artifact_store,
            self.lexical_store,
            self.event_store,
            self.project_store,
            self.ecs_store,
            self.model_provider,
            self.embedding_provider,
            self.corpus_turn_store,
        ):
            if store is not None and hasattr(store, "close"):
                try:
                    await store.close()
                except Exception as close_exc:
                    logger.debug("Failed to close store: %s", close_exc)

    @classmethod
    def from_corpus_stores(
        cls,
        cs: Any,
        *,
        event_store: Any,
        project_store: Any,
        model_provider: Any = None,
        embedding_provider: Any = None,
    ) -> "AskStores":
        """Build AskStores from a CorpusStores bundle (ADR-008 Rev 3.1 §A1).

        event_store and project_store are REQUIRED keyword args — they're
        global/definer-scoped, not per-corpus, so the caller must pass them
        from container.definer_stores (or the container's surviving globals).

        The CorpusStores bundle provides: artifact_store, lexical_store,
        vector_store, ecs_store, turn_store (→ corpus_turn_store), graph_store.
        """
        return cls(
            artifact_store=cs.artifact_store,
            lexical_store=cs.lexical_store,
            vector_store=cs.vector_store,
            event_store=event_store,
            project_store=project_store,
            ecs_store=cs.ecs_store,
            corpus_turn_store=cs.turn_store,
            graph_store=cs.graph_store,
            model_provider=model_provider,
            embedding_provider=embedding_provider,
        )


async def create_ask_stores(db_path: str) -> AskStores:
    """Factory: create and initialize all stores needed for the ask pipeline.

    Uses the same database paths as ``create_ingestion_stores()`` so that
    ask reads from the same persistent stores that ingest writes to.
    All stores (including VectorStore) are SQLite-backed and persistent.
    """
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
    from aip.adapter.event_store_queryable import QueryableEventStore
    from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore
    from aip.adapter.model_slot_resolver import ModelSlotResolver
    from aip.adapter.project.sqlite_project_store import SqliteProjectStore
    from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()

    lexical_db = os.path.join(os.path.dirname(db_path), "lexical.db")
    lexical_store = SqliteFts5LexicalStore(lexical_db)
    await lexical_store.initialize()

    event_store = QueryableEventStore(db_path)
    await event_store.initialize()

    project_store = SqliteProjectStore(db_path)
    await project_store.initialize()

    ecs_store = PersistentEcsStore(db_path, event_store=event_store)
    await ecs_store.initialize()

    # Model provider — load from config if available
    model_provider = None
    try:
        config = _load_config()
        if config:
            model_provider = ModelSlotResolver(config)
    except Exception as exc:
        logger.info("No model provider configured: %s", exc)

    # Embedding provider — created BEFORE vector store so it can be passed
    # for real embedding generation in store().
    embedding_provider = None
    try:
        config = _load_config()
        if config:
            from aip.adapter.embedding.factory import create_embedding_provider

            embedding_provider = create_embedding_provider(config)
    except Exception:
        logger.debug("No embedding provider configured — lexical-only search")

    # Use persistent SqliteVssVectorStore so that vectors survive process
    # restarts.  The VSS extension may not be available, in which case the
    # store degrades to brute-force search — but data is still persistent.
    vector_db = os.path.join(os.path.dirname(db_path), "vectors.db")
    vector_store = SqliteVssVectorStore(
        db_path=vector_db,
        dimensions=768,
        embedding_provider=embedding_provider,
    )
    await vector_store.initialize()

    # CorpusTurnStore — the canonical corpus of ingested turns
    corpus_turn_store = None
    try:
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        corpus_turn_store = CorpusTurnStore(db_path)
        await corpus_turn_store.initialize()
    except Exception as exc:
        logger.info("CorpusTurnStore not available for ask pipeline: %s", exc)

    # GraphStore — SQLite-backed knowledge graph for graph retrieval channel
    graph_store = None
    try:
        from aip.adapter.graph_store import GraphStore

        graph_store = GraphStore(db_path)
        await graph_store.initialize()
    except Exception as exc:
        logger.info("GraphStore not available for ask pipeline: %s", exc)

    return AskStores(
        artifact_store=artifact_store,
        lexical_store=lexical_store,
        vector_store=vector_store,
        event_store=event_store,
        project_store=project_store,
        ecs_store=ecs_store,
        model_provider=model_provider,
        embedding_provider=embedding_provider,
        corpus_turn_store=corpus_turn_store,
        graph_store=graph_store,
    )


def _load_config() -> dict:
    """Load AIP config from default location.

    Delegates to the canonical config loader in ``aip.config.loader``
    which handles AIP_CONFIG_PATH, CWD-relative paths, and .env loading.
    Returns an empty dict (not None) when no config file is found.
    """
    from aip.config.loader import load_toml_config

    return load_toml_config()


async def ask(
    question: str,
    project_name: str,
    stores: AskStores,
    source: AskSource = "all",
    max_sources: int = 10,
    save_artifact: bool = False,
    model_slot: str = "synthesis",
    session_id: str | None = None,
    system_prompt_modifier: str = "",
) -> AskResult:
    """Execute a source-grounded ask query against the AIP knowledge substrate.

    This is the main entry point for the ask pipeline. It resolves the
    project, searches project memory for relevant sources via multi-channel
    retrieval, assembles context, dispatches to the configured model, and
    optionally saves the answer as a draft artifact.

    Failure modes are explicit and never silently produce fake results.
    """
    if session_id is None:
        session_id = f"ask:{uuid.uuid4()}"

    # Resolve project (soft — corpus is project-agnostic, so a missing
    # project does NOT block the ask)
    project = None
    project_id = project_name
    project_domain = project_name
    try:
        project = await _resolve_project(project_name, stores.project_store)
    except Exception as exc:
        logger.warning("Failed to resolve project '%s' (non-fatal): %s", project_name, exc)

    if project is not None:
        project_id = project.get("project_id", project_name)
        project_domain = project.get("domain") or project_name
    else:
        # Project not found — return explicit status so callers can distinguish
        # "project doesn't exist" from "project exists but has no memory".
        return AskResult(
            status="NO_PROJECT",
            answer=f"Project '{project_name}' not found. Create it first with: aip project create {project_name}",
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
        )

    # Multi-channel retrieval via RetrievalOrchestrator + SmartContextPacker
    retrieval_trace: RetrievalTrace | None = None
    packed_context: PackedContext | None = None
    try:
        sources, retrieval_trace, packed_context = await _search_sources_with_trace(
            query=question,
            stores=stores,
            source_filter=source,
            max_sources=max_sources,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("Source search failed: %s", exc)
        return AskResult(
            status="NO_PROJECT_MEMORY",
            answer=f"Error searching project memory: {exc}",
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            errors=[str(exc)],
        )

    if not sources:
        return AskResult(
            status="NO_PROJECT_MEMORY",
            answer=(
                f"No relevant sources found in project '{project_name}' "
                f"for query: '{question}'. "
                f"Try ingesting conversations first with: aip ingest file <path> --domain {project_domain}"
            ),
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            sources=[],
        )

    # Check model provider
    if stores.model_provider is None:
        return AskResult(
            status="NEEDS_CONFIGURATION",
            answer=(
                "NEEDS_CONFIGURATION: No model provider is configured. "
                "Set AIP_SYNTHESIS_BASE_URL and AIP_SYNTHESIS_MODEL environment "
                "variables, or configure the synthesis slot in config/aip.config.toml. "
                "Below are the retrieved sources that would have been used:"
            ),
            sources=sources,
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            model_slot=model_slot,
        )

    # Assemble context from packed retrieval results
    context = packed_context.context_text if packed_context else "No relevant sources found in project memory."

    # Dispatch to model
    model_provider_name = ""
    model_name = ""
    answer_content = ""
    model_errors: list[str] = []

    try:
        base_system = (
            "You are AIP, a source-grounded knowledge assistant. "
            "Answer the user's question based ONLY on the provided sources. "
            "Cite sources using [source: <source_id>] notation. "
            "If the sources do not contain enough information, say so explicitly. "
            "Do not fabricate information not present in the sources."
        )
        system_content = f"{system_prompt_modifier}\n\n---\n\n{base_system}" if system_prompt_modifier else base_system
        messages = [
            {
                "role": "system",
                "content": system_content,
            },
            {
                "role": "user",
                "content": f"Project: {project_name}\n\nSources:\n{context}\n\nQuestion: {question}",
            },
        ]

        result = await stores.model_provider.call(model_slot, messages, temperature=0.7)

        if result.get("error"):
            model_errors.append(result.get("error_message", "Model call failed"))
            answer_content = ""
        else:
            answer_content = result.get("content", "")
            model_provider_name = result.get("model", "")
            model_name = model_provider_name

    except Exception as exc:
        logger.error("Model call failed: %s", exc)
        model_errors.append(str(exc))
        answer_content = ""

    # Handle model failure
    if model_errors:
        await _record_trace(
            stores=stores,
            session_id=session_id,
            project_id=project_id,
            question=question,
            sources=sources,
            model_slot=model_slot,
            model_provider=model_provider_name,
            answer="",
            artifact_id="",
            errors=model_errors,
            status="MODEL_FAILURE",
        )

        return AskResult(
            status="MODEL_FAILURE",
            answer=(
                f"Model call failed: {'; '.join(model_errors)}. Retrieved sources are preserved below for inspection."
            ),
            sources=sources,
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            model_slot=model_slot,
            model_provider=model_provider_name,
            errors=model_errors,
        )

    # Append source citations if not already present
    citations = _format_source_citations(sources)
    if citations and "[source:" not in answer_content:
        answer_content += "\n\nSources:\n" + "\n".join(citations)

    # Optionally save as artifact
    artifact_id = ""
    artifact_errors: list[str] = []

    if save_artifact:
        try:
            artifact_id = f"ask:{hashlib.sha256(f'{project_id}:{question}'.encode()).hexdigest()[:24]}"

            artifact_metadata = {
                "artifact_type": "ask_answer",
                "project_id": project_id,
                "project_name": project_name,
                "prompt": question,
                "model_slot": model_slot,
                "model_name": model_name,
                "source_ids": [s.source_id for s in sources],
                "source_types": [s.source_type for s in sources],
                "session_id": session_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

            await stores.artifact_store.write(artifact_id, answer_content, artifact_metadata)

            # ECS transition: GENERATED (draft, pending review)
            if stores.ecs_store is not None:
                try:
                    await stores.ecs_store.transition(
                        artifact_id=artifact_id,
                        from_state=None,
                        to_state="GENERATED",
                        actor="ask_pipeline",
                        reason="Generated by ask pipeline — pending DEFINER review",
                    )
                except Exception as exc:
                    artifact_errors.append(f"ECS transition failed: {exc}")
                    logger.warning("ECS transition failed for artifact '%s': %s", artifact_id, exc)

            # Index the saved artifact in LexicalStore for future retrieval
            try:
                await stores.lexical_store.index_document(
                    doc_id=f"artifact:{artifact_id}",
                    content=answer_content[:2000],
                    domain=project_domain,
                    metadata={
                        "type": "ask_answer",
                        "artifact_id": artifact_id,
                        "project_id": project_id,
                        "model_slot": model_slot,
                    },
                )
            except Exception as exc:
                artifact_errors.append(f"Lexical indexing of saved artifact failed: {exc}")
                logger.debug("Failed to index saved artifact: %s", exc)

        except Exception as exc:
            logger.error("Artifact save failed: %s", exc)
            artifact_errors.append(f"Artifact save failed: {exc}")

            await _record_trace(
                stores=stores,
                session_id=session_id,
                project_id=project_id,
                question=question,
                sources=sources,
                model_slot=model_slot,
                model_provider=model_provider_name,
                answer=answer_content,
                artifact_id="",
                errors=artifact_errors,
                status="ARTIFACT_SAVE_FAILURE",
            )

            return AskResult(
                status="ARTIFACT_SAVE_FAILURE",
                answer=answer_content,
                sources=sources,
                model_slot=model_slot,
                model_provider=model_provider_name,
                session_id=session_id,
                project_id=project_id,
                project_name=project_name,
                prompt=question,
                errors=artifact_errors,
            )

    # Record successful trace
    await _record_trace(
        stores=stores,
        session_id=session_id,
        project_id=project_id,
        question=question,
        sources=sources,
        model_slot=model_slot,
        model_provider=model_provider_name,
        answer=answer_content,
        artifact_id=artifact_id,
        errors=artifact_errors,
        status="OK",
        retrieval_trace=retrieval_trace,
    )

    return AskResult(
        status="OK",
        answer=answer_content,
        sources=sources,
        model_slot=model_slot,
        model_provider=model_provider_name,
        artifact_id=artifact_id,
        session_id=session_id,
        project_id=project_id,
        project_name=project_name,
        prompt=question,
        errors=artifact_errors,
        retrieval_degradation=build_degradation_dict(
            retrieval_trace,
            _last_registration_failures,
        ),
        retrieval_warnings=build_retrieval_warnings(retrieval_trace),
    )


# Backward-compatible aliases — these delegate to the extracted module
# in ``channels.retrieval_trace_utils``.  They remain importable from
# ``ask_pipeline`` so that existing tests and callers are not broken.


def _build_degradation_dict(retrieval_trace: RetrievalTrace | None) -> dict:
    """Backward-compatible wrapper around build_degradation_dict."""
    return build_degradation_dict(retrieval_trace, _last_registration_failures)


def _build_retrieval_warnings(retrieval_trace: RetrievalTrace | None) -> list[str]:
    """Backward-compatible wrapper around build_retrieval_warnings."""
    return build_retrieval_warnings(retrieval_trace)


async def _record_trace(
    stores: AskStores,
    session_id: str,
    project_id: str,
    question: str,
    sources: list[SourceReference],
    model_slot: str,
    model_provider: str,
    answer: str,
    artifact_id: str,
    errors: list[str],
    status: str,
    retrieval_trace: RetrievalTrace | None = None,
) -> None:
    """Record the full ask session trace in EventStore.

    Records retrieval trace data (channel timing, RRF fusion stats,
    quality-gate verdict) and channel registration failures when available.
    """
    if stores.event_store is None:
        return

    retrieval_meta: dict[str, Any] = {}
    if retrieval_trace is not None:
        retrieval_meta = {
            "retrieval_round": retrieval_trace.round_number,
            "retrieval_channels": json.dumps(retrieval_trace.channels_queried),
            "retrieval_total_ms": retrieval_trace.total_elapsed_ms,
            "retrieval_per_channel_ms": json.dumps(retrieval_trace.per_channel_elapsed_ms),
            "retrieval_hits_before_fusion": retrieval_trace.hits_before_fusion,
            "retrieval_hits_after_fusion": retrieval_trace.hits_after_fusion,
            "retrieval_hits_after_gate": retrieval_trace.hits_after_quality_gate,
            "retrieval_verdict": retrieval_trace.verdict,
            "retrieval_channel_contributions": json.dumps(retrieval_trace.channel_contributions),
            "retrieval_llm_entity_extraction_ms": retrieval_trace.llm_entity_extraction_ms,
            "retrieval_llm_entity_extraction_status": retrieval_trace.llm_entity_extraction_status,
            "retrieval_llm_entity_count": retrieval_trace.llm_entity_count,
            "retrieval_vector_backend_status": retrieval_trace.vector_degradation.backend_status.value,
            "retrieval_vector_backend_name": retrieval_trace.vector_degradation.backend_name,
            "retrieval_vector_degraded": retrieval_trace.vector_degradation.backend_status.is_degraded,
            "retrieval_vector_brute_force_rows": retrieval_trace.vector_degradation.brute_force_rows_scanned,
            "retrieval_vector_embed_failures": retrieval_trace.vector_degradation.embed_failures,
            "retrieval_vector_metadata_only": retrieval_trace.vector_degradation.metadata_only_stored,
            "retrieval_degradation_summary": retrieval_trace.degradation_summary(),
            # Unified trace fields
            "retrieval_channel_health": json.dumps(retrieval_trace.channel_health),
            "retrieval_channel_health_reasons": json.dumps(retrieval_trace.channel_health_reasons),
            "retrieval_query_expansion": json.dumps(retrieval_trace.query_expansion),
            "retrieval_entities_extracted": json.dumps(retrieval_trace.entities_extracted),
            "retrieval_documents_retrieved_count": len(retrieval_trace.documents_retrieved_ids),
            "retrieval_top_scores": json.dumps(retrieval_trace.top_scores[:5]),
            "retrieval_final_context_token_count": retrieval_trace.final_context_token_count,
            "retrieval_degradation_warnings": json.dumps(retrieval_trace.degradation_warnings),
        }

    # Include channel registration failures in the trace for dashboard visibility
    if _last_registration_failures:
        retrieval_meta["channel_registration_failures"] = json.dumps([f.to_dict() for f in _last_registration_failures])

    try:
        await stores.event_store.write_event(
            event_type="ask_query",
            actor="ask_pipeline",
            artifact_id=artifact_id or f"session:{session_id}",
            from_state=None,
            to_state=status,
            session_id=session_id,
            project_id=project_id,
            prompt=question[:500],
            source_count=len(sources),
            source_ids=json.dumps([s.source_id for s in sources[:20]]),
            model_slot=model_slot,
            model_provider=model_provider,
            answer_digest=hashlib.sha256(answer.encode()).hexdigest()[:16] if answer else "",
            artifact_saved=bool(artifact_id),
            error_count=len(errors),
            errors=json.dumps(errors[:5]) if errors else "[]",
            **retrieval_meta,
        )
    except Exception as exc:
        logger.debug("Failed to record ask trace: %s", exc)


def format_context_display(sources: list[SourceReference], max_sources: int = 10) -> str:
    """Format the retrieved context for display via --show-context.

    Shows each source with its ID, type, score, domain, and a content
    snippet so the user can verify what AIP retrieved before generation.
    """
    if not sources:
        return "No sources retrieved."

    lines = ["=== Retrieved Context ===\n"]
    for i, src in enumerate(sorted(sources, key=lambda s: s.score, reverse=True)[:max_sources], 1):
        lines.append(f"Source {i}:")
        lines.append(f"  ID:    {src.source_id}")
        lines.append(f"  Type:  {src.source_type}")
        lines.append(f"  Score: {src.score:.4f}")
        lines.append(f"  Domain: {src.domain}")
        lines.append(f"  Title: {src.title}")
        lines.append(f"  Snippet: {src.content_snippet[:150]}...")
        lines.append("")

    lines.append(f"Total sources: {len(sources)} (showing top {min(len(sources), max_sources)})")
    return "\n".join(lines)
