# AIP Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-17
# Process: Update this document after each significant build session or architectural decision.
# Release: 0.1.0-alpha (Alpha Test Release)

---

## How to Read This Document

Status indicators:
- ✅ COMPLETE — built, tested, in production use
- ⏳ IN PROGRESS — actively being built
- 🔲 PLANNED — decided, not yet started
- 💡 PROPOSED — under consideration, not yet decided
- ❌ DEFERRED — decided to defer, reason noted

Architecture decisions are recorded in `docs/decisions/`. When a decision changes
the roadmap, update both documents.

---

## PHASE 0 — Foundation
*Core artifact lifecycle, storage, and evaluation pipeline.*
*Status: ✅ COMPLETE*

- ✅ Three-layer architecture (foundation → orchestration → adapter)
- ✅ ECS state machine (SPECIFIED→GENERATED→REVIEWED→APPROVED→SUPERSEDED)
- ✅ Persistent SQLite stores (artifacts, ECS, events, lexical, projects)
- ✅ FTS5 full-text search with domain filtering
- ✅ Model dispatch (Ollama + OpenAI-compatible, all slots)
- ✅ Review/approve/reject/export pipeline
- ✅ DEFINER sovereignty gates (no auto-approve in MANUAL mode)
- ✅ Auth system (laptop: disabled by default, production: required)
- ✅ FastAPI backend with 11+ routers
- ✅ Click CLI (init, status, ingest, ask, review, export, eval)
- ✅ CI gates (ruff format, ruff check, pytest 1000+ tests)
- ✅ Docker profiles (laptop + production)
- ✅ Beast actor (background scheduler, health checks, context advisory)
- ✅ Vigil actor (quality evaluation, retrieval quality gate, LLM faithfulness)
- ✅ Sexton actor (built with all 5 ops; wired into app.py — DEBT-006 resolved)
- ✅ Autonomy gate with audit trail
- ✅ Budget enforcement
- ✅ MCP server (scaffold — tool listing real, dispatch scaffold)
- ✅ Alerting system (webhook, email, WebSocket, SSE, digest, muting)
- ✅ VigilQualityStore (persistent quality history with retention and rollup)
- ✅ Read pool with auto-sizing
- ✅ Config hot-reload (safe keys)

---

## PHASE 1 — Corpus Intelligence
*Turn-level corpus ingestion, tagging, and retrieval.*
*Status: ✅ COMPLETE (core)*

### 1.1 Turn-Level Corpus Foundation
- ✅ CorpusTurn schema (atomic unit: user+assistant pair with thinking_text)
- ✅ CorpusTurnStore (SQLite + FTS5 + Beast tagging path)
- ✅ make_turn_id (deterministic, idempotent)
- ✅ thinking_text field (extended thinking preserved separately from assistant_text)

### 1.2 Source Parsers
- ✅ Claude export parser (conversations.json, handles all content block types)
- ✅ 2,691 turns ingested from claude_export_june_2026
- ✅ 1,743 turns with extended thinking blocks preserved
- 🔲 ChatGPT export parser (tree-structure conversation format)
- 🔲 DeepSeek export parser
- 🔲 GLM export parser
- 🔲 Gemini export parser
- 🔲 xAI/Grok export parser
- 🔲 Plain text / sermon transcript parser (for external corpora)
- 🔲 PDF parser (for academic papers and books)
- 🔲 Web crawl / sitestrip parser (for external research corpora)

### 1.3 Beast Turn Tagging
- ✅ Domain registry (docs/beast_domain_registry_v1.md)
- ✅ DomainRegistry loader (load_registry, DomainEntry, ConnectorEntry)
- ✅ Beast _run_turn_tagging (batch-8 LLM tagging)
- ✅ Domain proposal system (Beast proposes → DEFINER approves)
- ✅ Connector proposal system
- ✅ aip corpus tag CLI (--limit, --retag)
- ✅ 2,681 turns tagged (tagging_version > 0)
- ✅ Registry v1.0: 26 domains, 13 connectors
- ✅ Registry v1.1: aip hall model, ancient_archaeology, agi_philosophy
- 🔲 Registry v1.2: (future — based on Beast proposals and dogfood observations)

### 1.4 Embedding Pipeline & Hybrid Retrieval
- ✅ Embed corpus_turns.searchable_text using embedding slot (infrastructure complete)
- ✅ Store vectors keyed by turn_id in vector store (SqliteVssVectorStore)
- ✅ Hybrid FTS5+vector scoring via RRF fusion in RetrievalOrchestrator
- ✅ Channel weights configurable in aip.config.toml (vector=0.6, fts=0.4, corpus=0.4)
- ✅ Coverage-aware gating (min_vector_coverage=0.10, graceful FTS5 fallback)
- ✅ Background embedding pass in Sexton _run_embedding_pass (built, not wired — DEBT-006)
- ✅ Re-embedding on model slot change (infrastructure complete)
- ✅ Retrieval evaluation harness (`aip eval retrieval` with --mode flag)
- ✅ Channel weight tuning script (`scripts/retrieval_weight_tuning.py`)
- ✅ Vigil retrieval quality gate (periodic precision@5 sampling with alerting)
- ✅ Golden queries with corpus-mapped IDs (`tests/retrieval_goldens/golden_queries.json`)
- ✅ Baseline benchmark (`docs/retrieval_benchmark_baseline.json`)

**Remaining gap:** ~1.8% embedding coverage (50/2766 turns). Full pass requires DEBT-006 fix.

### 1.5 Multi-Corpus Architecture
- ⏳ **IN PROGRESS — ADR-008 Rev 3.1** (supersedes ADR-004). 9-chunk sequence:
  - ✅ Chunk 1: Foundation types + ECS ARCHIVED state (complete, 43 tests)
  - ✅ Chunk 2: CorpusRegistry + Factory (complete, 55 tests — includes 2a shared connection manager APPROVED, 2b migration runner, 2c 5-scheduler gate)
  - ✅ Chunk 8: ECS/ArtifactStore per corpus (complete, 29 tests — delete_turn, artifact_turn_links/M004, durable outbox, revision_parent_id, aip audit log)
  - ⏳ Chunk 3: Call-site migration (partial — infrastructure complete, mechanical rewrite deferred)
  - ✅ Chunk 4: Retrieval scoping (complete, 21 tests — fusion-layer ECS filter §A2, hit ID namespacing, cache key, Branham graceful degrade §A12, assemble_augmented_context multi-corpus path)
  - 🔲 Chunk 5: Session/project binding + custom-channel scoping
  - 🔲 Chunk 6: Graph bridge edges + actor GraphStore refactor
  - 🔲 Chunk 7: Code corpus ingest (AST parser — delivers Phase 1.6)
  - 🔲 Chunk 9: Acceptance suite + aip corpus migrate --force + aip backup (strategy A default)
- 🔲 Branham research corpus (1200 sermons + books + critic sites) — post-Chunk 9
- 🔲 NBCM citations corpus (academic papers across relevant domains) — post-Chunk 9
- SEE: `docs/decisions/ADR-008-multi-corpus-architecture-rev3.md` + Amendment (supersedes ADR-004)

---

## PHASE 2 — Knowledge Synthesis
*Beast-generated wiki, knowledge graph, and cross-corpus intelligence.*
*Status: ✅ COMPLETE (core)*

### 2.1 Beast Wiki Generation
- ✅ Domain article generation (300-500 words per active domain)
- ✅ Wiki articles as GENERATED artifacts → DEFINER review → APPROVED
- ✅ BeastContextPreparer reads approved wiki as domain overview
- ✅ Wiki update triggered by Sexton cycle (not on timer)
- ✅ Wiki versioning (new article supersedes old on regeneration)

### 2.2 Knowledge Graph
- ✅ Entity extraction from corpus_turns (people, concepts, projects, places)
- ✅ Relationship inference (bridge-tagged turns → graph edges)
- ✅ Graph store (SQLite, synchronous GraphStore)
- ✅ Graph-aware retrieval (PersonalizedPageRank in GraphRetriever)
- ✅ Graph visualization in UI (Cytoscape.js at /graph-viz)
- ✅ Entity alias registry (22 entries)
- SEE: ADR-007-knowledge-graph-architecture.md

### 2.3 Domain Export Packages
- 🔲 Export mechanism: filter corpus by domain → standalone package
- 🔲 Package format: db + wiki + graph + embeddings as archive
- 🔲 Versioned packages (v1.0, v2.0 as corpus grows)
- 🔲 Package recipient model (share without exposing personal corpus)
- SEE: ADR-004-multi-corpus-architecture.md

---

## PHASE 3 — Actor Intelligence
*Beast, Vigil, and Sexton functioning as genuine intelligence layer.*
*Status: ✅ COMPLETE (code); DEBT-006 wiring gap remains*

### 3.1 Beast (Corpus Intelligence)
- ✅ Background scheduler (health check, entity check, heartbeat)
- ✅ Beast LLM slot (nvidia/nemotron-3-super-120b-a12b)
- ✅ Domain summary generation (event-driven, not timer-driven)
- ✅ BeastContextPreparer (retrieval + domain overview in augmented chat)
- ✅ Context advisory injected into synthesis model system prompt
- 🔲 Beast reads wiki artifacts as enhanced domain overview (maintenance)
- 🔲 Beast corpus health reporting (coverage gaps, stale artifacts) (maintenance)
- 🔲 Beast re-tagging trigger (when registry changes, retag affected turns) (maintenance)

### 3.2 Vigil (Quality Evaluation)
- ✅ Vigil scheduler (runs every 3600s)
- ✅ Vigil model slot (openai/gpt-oss-20b)
- ✅ Model slot change → mark canonicals for re-evaluation
- ✅ Faithfulness scorer (LLM-powered faithfulness checking, graduated Sprint 5.24)
- ✅ Citation rate scoring (pure-Python, always runs)
- ✅ Quality gate (flag responses that cite sources poorly)
- ✅ Vigil evaluation report as reviewable artifact
- ✅ Retrieval quality gate (precision@5 sampling with alerting, Sprint 6.4)
- ✅ VigilQualityStore (persistent history with retention/rollup)
- ✅ Trend tracking and degradation alerting

### 3.3 Sexton (Background Maintenance)
- ✅ Deterministic rules for failure types A-F + 7 special conditions
- ✅ Full Sexton actor (actors/sexton.py, 5 operations: tagging, embedding, wiki, graph, classification)
- ✅ Sexton model slot (google/gemma-4-26b-a4b-it)
- ❌ **NOT WIRED** — DEBT-006: app.py still calls old Sexton. All maintenance ops are dead code until wired.

---

## PHASE 4 — UI and Experience
*Making the knowledge engine usable and transparent.*
*Status: PARTIAL*

### 4.1 Augmented Chat UI
- ✅ Basic chat working (CHAT and AUGMENTED tabs)
- ✅ Auto-save to corpus on chat turn completion
- ✅ Beast context advisory injected in augmented mode
- 🔲 Show retrieved domain in chat
- 🔲 Show source citations inline in response
- 🔲 Show domain overview in chat (collapsible Beast summary)
- 🔲 Corpus selector in UI

### 4.2 Corpus Browser
- ✅ aip history list / aip history show (CLI)
- 🔲 Domain distribution view
- 🔲 Turn browser (search by domain, filter by importance)
- 🔲 Turn detail view
- 🔲 Domain proposal review UI

### 4.3 Knowledge Graph UI
- ✅ Interactive graph visualization (/graph-viz, Cytoscape.js)
- 🔲 Entity search and navigation
- 🔲 Relationship explorer

### 4.4 Slot and Model Management
- ✅ Actor Roles panel in GUI
- ✅ Five slots visible (synthesis, beast, vigil, sexton, embedding)
- 🔲 Per-slot model selector in Actor Roles panel
- 🔲 Slot health indicator

---

## PHASE 5 — Production and Scale
*Multi-user deployment, hardening, and sharing.*
*Status: DEFERRED (maintenance mode)*

- 🔲 Multi-user support (per-user corpora, shared canonicals)
- 🔲 Real MCP tool dispatch (search, approve, config via MCP)
- 🔲 Adaptive router (weight routes from outcomes, not random)
- 🔲 ScriptNode sandbox (safe execution environment)
- 🔲 Streaming model support
- 🔲 PostgreSQL migration for production
- 🔲 Review queue web UI for MANUAL mode
- 🔲 Per-component performance metrics (not estimated)
- 🔲 Onboarding flow for new users (export import wizard)

---

## PHASE 6 — Fusion Pipeline (2026-06-17)
*Multi-model synthesis upgrade — OpenRouter Fusion-style architecture.*
*Status: ✅ COMPLETE*

- ✅ Phase 1: Retrieval bridge — shared `_augmented_context.py` helper, both chat.py and model_council.py call the same retrieval pipeline (fixes the AIP-acronym bug)
- ✅ Phase 1: Two-stage Fusion pipeline (Judge-Beast → Synth-Beast) with per-call timeouts + engine fallback
- ✅ Phase 1: Multi-select dropdown (models NOT tied to actor slots/roles) + `skip_default_slots` flag
- ✅ Phase 1: `assemble_augmented_context` flag (GUI sends when augmented mode is on) — dogfood-confirmed
- ✅ Phase 1: Panel dispatch remediation (Bug 1: behavioral system prompt + Bug 2: [PANEL] log markers + DISPATCH_ERROR stubs)
- ✅ Phase 2: `blind_spots[]`, `partial_coverage[{models[], point}]` (2 to N-1 boundary), `unique_insights[{model, insight}]` attribution
- ✅ Phase 2: Per-model compression pass (`compress_panel_outputs` flag — opt-in)
- ✅ Phase 2: PDF Part IX test suite (9 net-new tests)
- ✅ Phase 3: Per-model attribution badges (deterministic 8-color palette in panel + markdown)
- ✅ Phase 3: Per-model stance color-coding on contradictions
- ✅ Phase 3: Dedicated `[models.judge]` TOML slot (preference 0 in `_pick_fusion_engine`)
- ✅ Phase 3: GUI toggle for `compress_panel_outputs`
- ✅ Phase 4.1: Real-time provenance feedback widget (inline source strip on answer cards)
- ✅ Phase 4.1: Context Preparer visualizer (4-step fusion flow diagram in trace panel)
- ✅ Phase 4.1: Automated consistency-checker (Vigil 5th evaluation pass — cross-turn contradiction detection)

---

## PHASE 1.6 — Codebase-as-Corpus (FUTURE)
*Parse the codebase itself into a queryable corpus — closes the "advice in the dark" gap.*
*Status: 💡 PROPOSED*

- 💡 Python AST → CorpusTurn format parser (functions, classes, modules as "turns")
- 💡 Code dependency graph (Graph B): modules, functions, classes, tests — with `imports`, `calls`, `tests`, `implements` edges
- 💡 Cross-graph edges: conversation turns that reference code get `references` edges; ADRs that decided code patterns get `decided` edges
- 💡 Re-ingest trigger: CI hook on commit OR Sexton file-watcher that detects `.py` changes and queues a re-parse pass
- 💡 Cross-corpus RRF fusion: a query about DEBT-006 would return both the roadmap mention AND the actual `sexton.py` file AND the `app.py` call site

See `PLANNED_FEATURES.md` → "Codebase-as-Corpus" for the full architectural sketch.

---

## Maintenance Mode → Active Development Transition

**Effective:** 2026-06-17 (post Fusion pipeline)

The project transitioned from maintenance mode back to active development
for the Fusion pipeline upgrade (Phases 1-3 + 4.1). The Fusion pipeline is
now feature-complete. The system is stable for local development, evaluation,
and dogfood usage. Future work is tracked in `PLANNED_FEATURES.md`.

Remaining items:
1. **Operational** — Run the server long enough for Sexton to close the 1.8% embedding gap
2. **Long-term** — Codebase-as-corpus (Phase 1.6, proposed)
3. **Long-term** — Adaptive per-query retrieval weighting (enhancement over existing fixed weights)
4. **Long-term** — Learned entity resolution (enhancement over static alias registry)

---

## Ongoing / Evergreen

- 🔄 Domain registry maintenance (review Beast proposals, update registry)
- 🔄 Corpus retag passes (after registry updates)
- 🔄 Monthly Claude export ingest
- 🔄 Other platform exports as parsers are built
- 🔄 STATUS.md kept current after each build session
- 🔄 ADRs written for each significant architectural decision
- 🔄 Re-run retrieval evaluation after significant corpus changes
- 🔄 PLANNED_FEATURES.md kept current (move items from Near-Term to Already Built when shipped)

---

## Version History

| Date       | Change                                      | Author  |
|------------|---------------------------------------------|---------|
| 2026-06-04 | Initial roadmap created from repo audit     | Claude + Moses |
| 2026-06-04 | Phase 1 corpus work reflected               | Claude + Moses |
| 2026-06-10 | Sprint 6.4 completion; maintenance mode     | Claude + Moses |
| 2026-06-10 | Alpha test release; documentation refresh   | Claude + Moses |
| 2026-06-17 | Phase 6: Fusion pipeline complete; Phase 1.6 proposed; DEBT-006 reference fixed | Super Z |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 1 complete: ARCHIVED terminal state added to ECS graph, 4 foundation files created, 43 tests. Phase 1.5 marked IN PROGRESS. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 2 complete: CorpusRegistry + Factory + shared connection manager (§A0) + migration runner (§A8) + 5-scheduler gate (§A5). 55 tests. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 8 complete: ECS/ArtifactStore per corpus. delete_turn, states_for, revision_parent_id, M004/M005, durable outbox, transition_artifact, list_review_items, backfill, aip audit log CLI. 29 tests. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 3 partial: call-site migration infrastructure. corpus_registry field + definer_stores property on AipContainer. AskStores.from_corpus_stores (§A1). set_embedding_provider registry-aware (§A6). 12 tests. Mechanical rewrite of 264 sites deferred. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 4 complete: Retrieval scoping. corpus_retrieval.py: namespace_hit_id, corpus_aware_cache_key, filter_excluded_states (§A2), gather_corpus_results (§A12). assemble_augmented_context multi-corpus path. 21 tests. | GLM (Coding Agent) |
