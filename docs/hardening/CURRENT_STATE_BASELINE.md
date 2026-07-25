# CURRENT_STATE_BASELINE.md

**Frozen at:** commit `efeb887c799aa5cefa1add1f011b7b8cf99bd83b`
**Date:** 2026-06-10
**Hardening Cycle:** Chunk 1 — Continuity Audit and Baseline Freeze

This document captures the verified state of the AIP_Brain codebase at the start of the
hardening cycle. Every subsequent chunk must reference this baseline when claiming a change
or fix. This is the single source of truth for "what the code was" before hardening began.

---

## 1. Repository Metadata

| Field | Value |
|---|---|
| Repository | `freedomgeneration1111-sudo/AIP_Brain` |
| Commit hash | `efeb887c799aa5cefa1add1f011b7b8cf99bd83b` |
| Commit message | `Sprint 11+12: Artifact lifecycle, CODEX/Librarian, test fix` |
| License | BUSL-1.1 (Change Date 2030-06-10) |
| Architecture Revision | 6.4 (per STATUS.md) |
| Project Mode | MAINTENANCE (per STATUS.md) |
| Version | 0.1.0-alpha |

## 2. Codebase Size and Shape

| Metric | Count |
|---|---|
| Source files (`src/aip/**/*.py`) | 233 |
| Test files (`tests/**/*.py`) | 169 |
| Total source lines | ~77,300 |
| Test count | 1002+ passing, 23 skipped, 2 pre-existing failures |
| Lint rules | ruff (E, F, W, I), line-length=120, target=py311 |

## 3. Architecture

Three-layer architecture with Protocol-based dependency injection:

```
foundation/     — Pure validation, schemas (11 modules), protocols (7 modules), ECS graph
orchestration/  — Business logic, evaluation, actors, pipelines, workflow
adapter/        — External interfaces (API, CLI, MCP, GUI), storage, auth
```

**Declared dependency direction:**
- foundation → stdlib/pydantic only (no upper layers)
- orchestration → foundation only (must not import adapter)
- adapter → foundation + orchestration (composition root)

**Actual dependency direction:**
- foundation → no upper layers (VERIFIED CLEAN)
- orchestration → adapter via 21+ function-local imports (KNOWN VIOLATION — see AIP-G-06/07 open finding in AIP_GOVERNANCE.md)
- adapter → orchestration as expected (correct direction)

## 4. Source File Distribution

### foundation/ (pure, no violations)

| Subdirectory | Files | Key Contents |
|---|---|---|
| `schemas/` | 13 | base, ingestion, review, ask, workflow, budget, trajectory, auth, config, surface, vector, codex, retrieval, evaluation, corpus_turn, artifact |
| `protocols/` | 8 | actors, knowledge, model, budget, auth, plugin, retrieval, storage |
| `ecs_graph.py` | 1 | Declarative ECS state machine (gold standard) |
| `validation.py` | 1 | Structural validation rules |

### orchestration/ (21 adapter import violations via function-local imports)

| Subdirectory | Files | Key Contents |
|---|---|---|
| `actors/` | 4 | beast.py, vigil.py, sexton.py, domain_registry.py |
| `sexton/` | 3 | sexton.py (old, currently wired), sexton_audit.py |
| `ingestion/` | 5 | corpus_ingest_pipeline, pipeline, chunker, parsers (markdown, chatgpt, claude, plaintext, document_parser) |
| `nodes/` | 6 | synthesis, faithfulness, domain_coherence, adversarial_eval, definer_gate, commit |
| `channels/` | 6 | vector, graph, corpus, lexical, wiki, procedural channel types |
| `workflow/` | 10 | engine, runner, context, node, definition, loader, instance, instance_store, workflow_01, workflow_registry |
| `trajectory/` | 3 | context_reset, regulator |
| `l4/` | 6 | monitor, regulator, reset, anxiety_detector, loop_detector, failure_streak |
| `codex/` | 2 | librarian, codex_store integration |
| Top-level | 16 | ask_pipeline, review_export_pipeline, artifact_lifecycle, canonical_pipeline, retrieval_orchestrator, retrieval, session, budget, router, channel_selector, compilation, recovery, perf, ace_playbook, embed_providers, llm_query_expansion, smart_context_packer, model_provider_proxy, entity_extractor, graph_retrieval, retrieval_eval, adaptive_budget, re_synthesize, plugins |

### adapter/ (largest layer)

| Subdirectory | Files | Key Contents |
|---|---|---|
| `api/` | 20+ | app.py, dependencies.py, collaborators.py, plugins.py, performance.py + routes/ (17 routers) + static/ (5 HTML pages) |
| `cli/` | 17 | main, init, status, ingest, ask, review, export, project, session, config, corpus, history, artifact, codex, eval, backup, _db_path |
| `auth/` | 5 | middleware, dependencies, session_store, collaborator |
| `vector/` | 5 | factory, sqlite_vss_store, pgvector_store, _in_memory, connection_manager, migrate |
| `embedding/` | 4 | factory, ollama_embed, openai_embed |
| `lexical/` | 2 | sqlite_fts5_store |
| `canonical/` | 2 | sqlite_canonical_store |
| `entity/` | 2 | sqlite_entity_store |
| `project/` | 2 | sqlite_project_store |
| `session/` | 2 | sqlite_session_store |
| `vigil/` | 3 | sqlite_vigil_store, vigil_quality_store |
| `codex/` | 2 | codex_store |
| `mcp/` | 4 | server, tools/search, tools/artifacts |
| `plugins/` | 3 | plugin_loader, yaml_plugin_provider |
| `autonomy/` | 2 | autonomy_gate |
| Top-level | 15+ | health, alerting (9,133 lines!), alert_history_store (3,159 lines), read_pool, store_health, graph_store, model_slot_resolver, entity_alias_loader, config_watcher, definer_profile, event_store_queryable, trace_store_adapter, ecs_store_guardrailed, ecs_store_persistent, corpus_turn_store, review_queue_store, budget_store_sqlite, artifact_store_versioned, write_pool_evaluation, middleware/rate_limiter |

## 5. Data Flow

### Primary Databases

| Database | Purpose | Path |
|---|---|---|
| `state.db` | Main SQLite database: artifacts, projects, events, ECS state, canonicals, graph, sessions, budget, entity, codex, vigil | `db/state.db` |
| `lexical.db` | FTS5 full-text search index | `db/lexical.db` |
| `trace.db` | Trace events and routing outcomes | `db/trace.db` |

### Key API Routes

The FastAPI backend exposes ~17 routers under `/api/v1/`:
health, chat, ask, ingest, corpus, artifacts, review, knowledge, graph, graph_viz, models, models_library, sessions, projects, admin, ecs, actors, wiki, sources, beast_scan, vigil_quality, retrieval_dashboard, plugins, collaborators

### Primary Pipelines

1. **Ingest Pipeline:** `CLI/API → ingestion/pipeline → parsers → CorpusTurnStore → FTS5 + Vector indexing`
2. **Ask Pipeline:** `CLI/API → ask_pipeline → RetrievalOrchestrator (3 channels + RRF) → ModelProvider → ArtifactStore`
3. **Review/Export Pipeline:** `CLI/API → review_export_pipeline → ReviewNode → DefinerGate → CanonicalPipeline → Export`
4. **Sexton Classification:** `Old Sexton (currently wired) → run_classification_cycle() every 300s`
5. **Beast Actor:** `Health checks (60s) + Corpus maintenance (on-demand)`
6. **Vigil Actor:** `Citation quality (hourly) + Retrieval quality sampling (Sprint 6.4)`

## 6. Actor Status (ADR-011)

| Actor | Code State | Wired in app.py | Currently Running |
|---|---|---|---|
| Beast | Built + refactored | Scheduled (heartbeat only) | Health checks, context advisory |
| Sexton (old) | Built (sexton/sexton.py, ~220 lines) | Wired | Failure classification every 300s |
| Sexton (new) | Built (actors/sexton.py, 1,341 lines, 5 ops) | **NOT WIRED** — DEBT-006 | **DEAD CODE** |
| Vigil | Built + refactored | Scheduled (hourly) | Citation quality + retrieval quality sampling |

## 7. Corpus Status

| Source | Turns | Tagged | Embedded |
|---|---|---|---|
| claude_export_june_2026 | 2,691 | 2,691 | 50 |
| claude_export_2024_2025 | 52 | 52 | 0 |
| aip_v0.1_seed | 23 | 23 | 0 |
| **Total** | **2,766** | **2,766** | **50** (~1.8%) |

## 8. Scaffolding Status

| Surface | What's Real | What's Scaffold |
|---|---|---|
| MCP tool dispatch | Tool listing, autonomy gate enforcement (fail-closed when gate is None), real search dispatch, real artifact approval (ECS transition + canonical write), layering discipline | MCP server not wired into `app.py` runtime (no transport, no API route); `start()` is direct-invocation mode only |
 Adaptive router | Budget enforcement, route existence | update_weights() is fully implemented but never called (dead code); exploration/exploitation is random |
| ScriptNode | Type declaration, fixture mode, YAML parsing | Production execution returns DISABLED |
| MCP start/stop | `_running` flag | No stdio/SSE transport |
| Workflow 0.1 | `_ReviewGateNode` with validation + eval gates, `_CommitNode` with approval check | `workflow_01` not wired into runtime; default gate mode changed to MANUAL in Chunk 2 |

> **Chunk 1.5 correction:** The original Chunk 1 baseline described MCP dispatch as "aip_search returns empty; aip_artifact_approve returns hardcoded True". This was stale — it reflected pre-`0d63e58` code. The current MCP dispatch performs real ECS transitions and canonical writes. The MCP server is not runtime-wired, so this is non-live governance debt, not a current runtime blocker.

## 9. Configuration System

Primary config: `config/aip.config.toml` (TOML format, loaded by `aip.config.loader`)
Key sections: database, vector_backend, embedding, auth, deployment, budget, beast, review, retrieval, models, sexton, vigil, alerting
Environment variable overrides: `AIP_DB_PATH`, `AIP_SYNTHESIS_*`, `AIP_OLLAMA_BASE_URL`, `AIP_<SLOT>_BASE_URL`, `CI=true`
Deployment profiles: laptop (SQLite, optional auth, localhost), production (PostgreSQL, required auth, 0.0.0.0)

## 10. Test Infrastructure

- **Framework:** pytest with `CI=true` for deterministic fixtures
- **Acceptance tests:** `tests/acceptance/` (6 test files covering budget, definer, ECS, vigil, canonical, multi-surface)
- **Governance test:** `tests/test_governance_conformance.py` (AIP-G-01 through AIP-G-11)
- **Layer discipline test:** `tests/test_layering.py`, `tests/test_layer_discipline.py`
- **Benchmarks:** `tests/benchmarks/` (retrieval, alerting, memory, vectorstore)
- **Golden queries:** `tests/retrieval_goldens/golden_queries.json`

## 11. Known Bugs and Pre-existing Failures

| ID | Description | Status |
|---|---|---|
| BUG-001 | `aip init` creates no default project | Fixed in app.py (auto-creates) |
| BUG-002 | chat.py uses wrong DB path for GraphStore | Fixed (uses container) |
| BUG-003/DEBT-006 | New Sexton not wired — DEBT-006 | Active, highest priority |
| BUG-004 | GraphStore had no Protocol + used sync sqlite3 | Fixed (DEBT-004/005 resolved) |

**Pre-existing test failures (2):**
- `test_model_slot_resolver.py`: 4 tests fail in full suite due to env var pollution (pass in isolation)
- `test_sqlite_vss_graceful_skip.py`: fails in full suite due to global state pollution (passes in isolation)

## 12. Grep Scan Results (Baseline Counts)

| Pattern | Count in src/ | Notes |
|---|---|---|
| `TODO` | 4 | All in CLI layer (session, config, project) |
| `FIXME` | 0 | Clean |
| `BUG-` | 7 | All documented in TECH_DEBT.md |
| `Sprint \d` | **300+** | Massive Sprint-number commentary in source (especially alerting.py, alert_history_store.py, read_pool.py) |
| `Step \d` | 22 | Step-number scaffold comments in definer_gate, context_reset, vigil, beast_scan, eval CLI |
| `except Exception: pass` | **21** | Silent exception swallowing in app.py, alerting.py, dependencies.py, vigil stores, etc. |
| `except Exception:` (total) | **~170** | Broad exception catching throughout codebase |
| `return []` | ~30 | Some potentially hiding failures in source; others legitimate in stores |
| `placeholder` | 2 meaningful | `projects.py:101` "List work units (placeholder)", `health.py:549` vector_backend "placeholder" |
| `vectors.db is empty` | 0 | Not in source (was in README, now accurate) |
| `not built` / `Not built` | 0 | Not in source |
| `aip.git` / `freedomgeneration1111-sudo/aip` | 0 | Clean |
| `CRITICAL:` | 1 | `cli/init.py:44` — narrates implementation history |
| `hardcoded` references | 12 | Mix of comments about removing hardcoded values and remaining hardcoded items |

## 13. Layer Violation Inventory

Orchestration importing adapter (21 function-local imports across 7 files):

| File | Imports from adapter | Justification |
|---|---|---|
| `ask_pipeline.py` | VersionedArtifactStore, PersistentEcsStore, QueryableEventStore, SqliteFts5LexicalStore, ModelSlotResolver, SqliteProjectStore, SqliteVssVectorStore, embedding.factory, CorpusTurnStore, GraphStore | Function-local to avoid import-time coupling |
| `review_export_pipeline.py` | VersionedArtifactStore, PersistentEcsStore, QueryableEventStore, SqliteProjectStore, SqliteCanonicalStore | Function-local |
| `artifact_lifecycle.py` | VersionedArtifactStore, PersistentEcsStore, QueryableEventStore, SqliteProjectStore | Function-local |
| `ingestion/corpus_ingest_pipeline.py` | CorpusTurnStore, QueryableEventStore | Function-local |
| `ingestion/pipeline.py` | embedding.factory, VersionedArtifactStore, QueryableEventStore, SqliteFts5LexicalStore, SqliteVssVectorStore | Function-local |
| `embed_providers.py` | embedding.factory (3 times) | Lazy import for layering workaround |
| `codex/librarian.py` | CodexStore, CorpusTurnStore (4 times) | Function-local |
| `channels/graph_channel.py` | GraphStore | Function-local |
| `actors/vigil.py` | Alert (2 times) | Function-local |
| `actors/beast.py` | GraphStore, EntityAliasRegistry | Function-local |
| `actors/sexton.py` | GraphStore, EntityAliasRegistry, Alert | Function-local |

**Governance status:** This is the AIP-G-06/07 open finding documented in AIP_GOVERNANCE.md.
Resolution path: relocate concrete wiring to a composition root so orchestration sees only Protocols,
or record each offender in the conformance suite's `acknowledged_import_violations`.

## 14. Production Safety Configuration

Seven hard-failure configurations enforced at startup:

1. Production + auth disabled → `PROD_AUTH_DISABLED`
2. Production + missing POSTGRES_PASSWORD → `PROD_MISSING_DB_PASSWORD`
3. Production + weak/default password → `PROD_WEAK_DB_PASSWORD`
4. Production + fixture embedding provider → `PROD_FIXTURE_PROVIDER`
5. Production + fixture model provider → `PROD_FIXTURE_MODEL_PROVIDER`
6. Public bind + auth disabled → `PUBLIC_NO_AUTH`
7. Public bind + weak database password → `PUBLIC_WEAK_SECRET`

Override: `AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH=true` (laptop mode only).

## 15. AIP Governance Conformance Status

| Invariant | aip_brain Status |
|---|---|
| AIP-G-01 DEFINER Authority | Partial |
| AIP-G-02 No Fake Success | Enforced |
| AIP-G-03 Provenance | Adopt |
| AIP-G-04 Governed Lifecycle | Partial |
| AIP-G-05 Reversibility | Adopt |
| AIP-G-06/07 Layer Separation | **Finding open** |
| AIP-G-07 Surface Isolation | Enforced |
| AIP-G-08 Validation-First | Partial |
| AIP-G-09 Sovereignty | Enforced |
| AIP-G-10 Auditability | Enforced |
| AIP-G-11 Conformance Tested | Enforced |
