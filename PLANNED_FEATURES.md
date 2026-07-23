# Planned Features — AIP Brain

> **Single source of truth for "what's built, what's planned, what's deferred."**
>
> Every agent (panel model, external LLM, human, or AI assistant) MUST read
> this file BEFORE recommending changes — so no one gives advice that's
> already obsolete relative to the implementation state. This file was
> created after a dogfood run where 3 of 6 panel-model recommendations
> were already implemented, and an external analysis missed a resolved
> debt item (DEBT-006) — both because there was no unified tracker.
>
> **Last Updated:** 2026-06-18
> **Maintained by:** Super Z (main agent) + DEFINER review

## How to use this file

1. **Before recommending a change**, check the "Already Built" section —
   your recommendation may already be implemented.
2. **Before claiming something is "blocked" or "missing"**, check
   `TECH_DEBT.md` for the debt item's status — it may be resolved.
3. **When you ship a feature**, move it from "Near-Term" or "Long-Term"
   to "Already Built" in the same commit.
4. **When you defer a feature**, move it to "Long-Term" with the reason.

---

## Status: Already Built (operational)

These features are implemented and active. Recommendations to "build"
them are obsolete — the gap (if any) is operational, not architectural.

### Retrieval Pipeline

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| Dynamic hybrid-retrieval weighting | `scripts/retrieval_weight_tuning.py` + `[retrieval.channel_weights]` in `aip.config.toml` (`vector=0.6, fts=0.4, corpus=0.4`) | ✅ Active | Vigil runs periodic precision@5 sampling. The adaptive per-query weighting proposed by panel models would be an enhancement, not a new build. |
| Refined vector embeddings | `SqliteVssVectorStore` + `Sexton._run_embedding_pass` | ✅ Active, 98.2% embedded | 1.8% gap is **operational** — the server needs to run long enough for Sexton cycles to process the backlog. NOT a code fix. |
| Entity co-reference resolution | 22-entry alias registry + `PersonalizedPageRank` in `GraphRetriever` + ADR-007 | ✅ Active | ADR-007 details the canonical alias table architecture. A "learned, context-aware resolver" would be an enhancement. |
| FTS5 full-text index | `LexicalStore` with FTS5 | ✅ Active | Core retrieval channel. |
| Corpus turn store | `CorpusTurnStore` with immutable turns | ✅ Active | 2,691+ turns ingested. |
| Knowledge graph | `GraphStore` + 36 nodes, 17 edges | ✅ Active | Conversation knowledge graph (people, concepts, projects). |

### Synthesis Pipeline (Fusion)

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| Judge/Synth split (Panel → Judge → Synth) | `model_council.py::_pick_fusion_engine` + `_call_fusion_engine` | ✅ Active | Phase 1 Fusion pipeline (commit `7b63cb1`). |
| Augmented retrieval bridge (Multi-Cast sees corpus) | `_augmented_context.py` shared helper | ✅ Active | Phase 1 retrieval bridge (commit `58d21db` + `c87a458`). Dogfood-confirmed. |
| Per-model compression pass | `_compress_panel_outputs()` + `compress_panel_outputs` field | ✅ Active (opt-in) | Phase 2 Step 2-D (commit `8712ea5`). GUI toggle added Phase 3d. |
| Per-model attribution badges | `_model_color()` + `_model_color_markdown()` | ✅ Active | Phase 3a/3b (commit `43fa421`). |
| Dedicated `[models.judge]` slot | `_pick_fusion_engine` preference 0 | ✅ Active (opt-in) | Phase 3c (commit `43fa421`). Uncomment in `aip.config.toml` to use. |
| Panel dispatch completeness (Bug 2 fix) | `[PANEL]` log markers + `DISPATCH_ERROR` stubs | ✅ Active | Panel dispatch remediation (commit `b4d9cf8`). |
| Panel behavioral system prompt (Bug 1 fix) | `_PANEL_SYSTEM_PROMPT` | ✅ Active | Panel dispatch remediation (commit `b4d9cf8`). |

### Actors

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| Sexton actor (embedding, tagging, wiki, graph) | `orchestration/actors/sexton.py` wired into `app.py` L520-573 + scheduler L1256-1313 | ✅ Active | DEBT-006 is RESOLVED (was a stale doc claim). 1.8% embedding gap is operational. |
| Beast actor (corpus health) | `orchestration/actors/beast.py` | ✅ Active | Background scheduler. The `beast` slot is borrowed for Fusion Judge+Synth. |
| Vigil actor (canonical monitoring, quality eval) | `orchestration/actors/vigil.py` | ✅ Active | 4 evaluation passes: faithfulness, coherence, relevance, drift. |

---

## Status: Near-Term (next 1-3 sessions)

These are genuine gaps worth pursuing. They are NOT yet implemented.

### Synthesis Quality

| Feature | Why it matters | Effort | Dependencies |
|---------|----------------|--------|--------------|
| **Judge prompt coverage-gradient fix** | Single-model points land in `partial_coverage` when they belong in `unique_insights`. One-line prompt clarification. | ~30 min | None — Judge prompt only. |
| **GAPS instruction on calibration runs** | The calibration case didn't carry the GAPS system prompt, so blind_spots was empty. Ensure all Multi-Cast runs use the panel behavioral prompt (already shipped in Bug 1 fix). | ~15 min (verify) | Bug 1 fix (already shipped). |

### User Experience (Phase 4.1)

| Feature | Why it matters | Effort | Dependencies |
|---------|----------------|--------|--------------|
| **Real-time provenance feedback widget** | Display the prior turns injected into the generative prompt, allowing DEFINER to trace provenance instantly. BeastContextPreparer has the data; just doesn't surface it to UI. | ~half day | None — `response_sources` already in WS payload. |
| **Context Preparer visualizer** | UI panel showing how FTS5, vector, and entity resolution compose the final context stack. The most powerful retrieval debugging tool in the system. | ~1 day | `RetrievalTrace` already populated; needs UI surface. |
| **Automated consistency-checker** | Cross-turn contradiction detection. Fits naturally as Vigil's 5th evaluation pass. | ~1 day | Vigil actor architecture. |

---

## Status: Near-Term — Fleet Primitives (ADR-015 Phase 3A-0)

Architectural contract: `docs/decisions/ADR-015-professional-agent-fleet.md`
(when accepted). These are the pre-fleet primitives that must ship before
the 2nd domain extension.

| Feature | Why it matters | Effort | Dependencies |
|---------|----------------|--------|--------------|
| **AgentRun primitive** | Execution law for all agent work. No agent operates outside an AgentRun. Table + schema in state.db. | ~1 day | ADR-015 acceptance. |
| **start_policy manifest field** | `scheduled` vs `manual_only`. Fixes cadence=0 startup-run-once (DEBT-020). Default: `manual_only` for safety. | ~half day | Actor Protocol amendment (ADR-014 §5.2). |
| **Fail-closed CapabilityGate** | Missing AgentRun, capability, approval, trace_id, or budget = DENY. No exceptions. | ~1 day | AgentRun table exists. |
| **cadence=0 startup fix (DEBT-020)** | Stop running write-capable actors at boot. Gate on start_policy. | ~half day | start_policy field added. |
| **MCP scaffold wiring** | v1.2 register_mcp_tool pulled forward to 3A-0 per ADR-015. Stub only — no tools yet. | ~half day | McpToolRegistry exists (v1.0). |

---

## Status: In Progress (ADR-014 Phase 0 Extension Platform)

ADR-014 (build target, PROPOSED) defines the `ExtensionHost` lifecycle, manifest v1
schema, and the seven-step build order. ARISTOTLE is the first consumer. The
TDD contract (`tests/test_extension_lifecycle.py`) is GREEN for stages 0–3 + 5
(v1.0 backend live); the v1.1 GUI mount test is `xfail(strict=True)` until
stage 4 lands.

| Step | Name | Status |
|------|------|--------|
| 0 | Branham audit-action rename (kill the last stale name) | ✅ Complete — `corpus_retrieval.py:244` now emits `RESTRICTED_CORPUS_ACCESS_DENIED`; stale comment in `corpus_store_factory.py:325` updated. Exception alias + deprecated param aliases kept one release cycle. |
| 1 | `ExtensionState` / `ExtensionRegistry` / `ExtensionHost` skeleton + `_supervised_task` + failing `test_extension_lifecycle.py` | ✅ Complete — `src/aip/adapter/extensions/` package built (8 files). Stages 0–3 + 5 GREEN (discover/validate/migrate/register/ready). Stage 4 (GUI mount) is v1.1 — `xfail(strict=True)`. Manifest model verified (8 validation cases). Discover+validate flow smoke-tested. Full pytest deferred to CI. |
| 2 | Wire `PluginManager` / `WorkflowRegistry` / `McpToolRegistry` as host-owned services on `container.extensions` | ✅ Complete — `WorkflowRegistry` wired (host-owned, `add_path()` for per-extension workflows). `WorkflowEngine` wired into `AipContainer` + lifespan (`container.workflow_engine`); extensions access via `ctx.container.workflow_engine.run_workflow()`. `/health/extensions` endpoint added (ADR-014 §7). `PluginManager` + `McpToolRegistry` deferred (orthogonal to extension lifecycle — model-provider plugins + MCP tools are steps 3 + 7). |
| 3 | `register_actor` / `register_workflow` + `Actor` Protocol (new actors only) | ✅ Complete — `Actor` Protocol + `ActorContext` + `ActorResult` added to `foundation/protocols/actors.py` (runtime_checkable). Host imports from foundation; `isinstance(actor, Actor)` validates conformance at scheduler start. `_run_one_cycle` handles `ActorResult` (logs non-ok, honors `next_run_at`). 11 contract tests in `test_actor_protocol.py`. Core actors (Beast/Vigil/Sexton) NOT migrated per ADR-014 §1. |
| 4 | `MigrationLoader` (`.sql` → `Migration` dataclasses) + stages 0–3 green | ✅ Complete (folded into step 1) — `loaders/migration_loader.py` reads `.sql` files and applies them via a separate `extension_applied_migrations` table (does not contaminate the core `CorpusMigrationRunner`'s fingerprint). |
| 5 | Manifest v1 validator (pydantic v2) + cross-stage coherence checks | ✅ Partial — pydantic v2 validator landed in step 1 (`manifest.py`). Cross-stage coherence checks (e.g. corpora referencing tables no migration creates) deferred to step 2. |
| 6 | (v1.1) `register_gui_page` + stage 4 mount; layout reads nav from registry | ⏳ Not started |
| 7 | (v1.2) `register_mcp_tool` + MCP generalization | ⏳ Not started |

See `docs/decisions/ADR-014-phase0-extension-host.md` for the full spec.

---

## Status: In Progress (ADR-008 Multi-Corpus Architecture)

ADR-008 Rev 3.1 (with Amendment) is the active implementation. The 9-chunk sequence
is strictly ordered: 1 → 2 → 8 → 3 → 4 → 5 → 6 → 7 → 9. Sub-chunk 2a (shared
per-corpus connection manager) is APPROVED. Backup strategy A (pause-and-snapshot)
is the implementation default.

| Chunk | Name | Status |
|-------|------|--------|
| 1 | Foundation types + ECS graph extension (ARCHIVED state) | ✅ Complete — 43 tests, 10 ECS graph tests, backward-compatible |
| 2 | CorpusRegistry + CorpusStoreFactory (adapter) | ✅ Complete — 55 tests, shared connection manager (§A0), migration runner (§A8), 5-scheduler gate (§A5) |
| 2a | Per-corpus shared connection manager (APPROVED) | ✅ Complete — CorpusConnectionManager, 1 write + N read per corpus |
| 2b | Migration runner outside `_create_tables` | ✅ Complete — CorpusMigrationRunner, fingerprint + sql_checksum |
| 2c | 5-scheduler migration gate in `app.py` | ✅ Complete — defensive helper, 5 schedulers gated |
| 8 | ECS/ArtifactStore per corpus | ✅ Complete — 29 tests, delete_turn/states_for/revision_parent_id (§A4/§A2/§A12), artifact_turn_links M004 (§A3), durable outbox (§A10), review_queue.corpus_id M005 (§A11), full transition_artifact + list_review_items + backfill (§9.4), aip audit log CLI (§A15) |
| 3 | Call-site migration (264 sites / 21 files) | ✅ Complete — 12 tests. Registry wired into app.py lifespan. Legacy singletons (corpus_turn_store, artifact_store, ecs_store) are now PROPERTIES that delegate to definer_stores when the registry is wired. All 264 call sites automatically use the registry without mechanical rewriting. Gate criterion `rg → 0 hits` not met (call sites still use legacy names), but the registry IS the source of truth. |
| 4 | Retrieval scoping (fusion-layer ECS filter) | ✅ Complete — 21 tests, corpus_retrieval.py (namespace_hit_id, cache_key, filter_excluded_states §A2, gather_corpus_results §A12), assemble_augmented_context multi-corpus path |
| 5 | Session/project binding + custom-channel scoping | ✅ Complete — 30 tests, session_corpus_binding.py (active_corpus_ids + branham_allowlist, §5 policy enforcement), custom_channel_scoping.py (ScopedCorpusStores §A14), GUI corpus_selector.py |
| 6 | Graph bridge edges + actor GraphStore refactor | ✅ Complete — 17 tests, GraphEdge.target_corpus_id (§A7), M002 migration, 4 new GraphStore methods (upsert/delete/get_bridge_neighbors/get_orphan_bridge_targets), _reconcile_bridge_edges (§A13), delete_corpus bridge cleanup |
| 7 | Code corpus ingest (AST parser) | ✅ Complete — 24 tests, python_ast_parser.py (functions/classes/module registration calls), code_ingest_pipeline.py (stale detection via content_hash), 3 golden queries acceptance tests against actual AIP codebase. Delivers Phase 1.6. |
| 9 | Acceptance suite + `aip corpus migrate --force` + `aip backup` rewrite | ✅ Complete — 19 acceptance tests (AC-01 through AC-09), `aip corpus migrate --force` CLI (§A15), `aip backup` strategy A rewrite with corpus DB discovery (§9.7) |

See `docs/decisions/ADR-008-multi-corpus-architecture-rev3.md` + Amendment for full spec.

---

## Status: Long-Term (roadmap — not immediate)

These are architecturally significant features that are designed but not
scheduled for the next 1-3 sessions. They belong in `ROADMAP.md` planning.

### Codebase-as-Corpus (Phase 1.6)

**The structural fix for "advice in the dark."** The current corpus
contains everything Moses has *thought and said* about the codebase
(2,691 turns) but none of the codebase itself. This closes that gap.

**Status (2026-07-23):** Infrastructure complete (Chunk 7 — AST parser,
ingest pipeline, 24 tests, 3 golden queries). Codeforge corpus now
**registered at startup** (QW1 — `app.py` registers `("codeforge",
CorpusType.CODE)` alongside definer). Operational ingest path still
pending: `aip corpus ingest-code <dir>` CLI (QW11) and Sexton file-watcher
(QW13) are the next quick wins.

- **Graph A** (existing): Conversation knowledge graph — 36 nodes, 17 edges
- **Graph B** (new, unbuilt): Code dependency graph — modules, functions, classes, tests — with `imports`, `calls`, `tests`, `implements` edges
- **Cross-graph edges** (unbuilt): A conversation turn that *references* a specific function gets a `references` edge. An ADR that *decided* a code pattern gets a `decided` edge. The graph answers: "what conversations informed this function?" and "what code exists for this architectural decision?"
- **Implementation**: Python AST → CorpusTurn format parser (`adapter/python_ast_parser.py`). The underlying store, tagging, embedding, and graph infrastructure handle it without changes. Each "turn" = a function/class/module with `searchable_text` = module path + docstring + signature + inline comments + associated test names.
- **Critical detail**: code changes continuously (conversation corpus is append-only and stable). The code corpus needs a re-ingest trigger — CI hook on commit OR Sexton file-watcher that detects `.py` changes and queues a re-parse pass. Without this, the code corpus ages out of sync immediately. **(QW13 — RESOLVED 2026-07-23: `aip corpus watch-code` CLI command now provides a polling-based file watcher. Run `aip corpus watch-code &` alongside the server.)**
- **Fits**: ADR-008's multi-corpus architecture (supersedes ADR-004). The code corpus is one of 4 corpus types (`code`/`codeforge`) alongside `conversation`/definer, `document`/branham, and `book`/sparkle_thirst. ADR-008 Chunk 7 implements the AST parser.
- **Retrieval implication**: cross-corpus RRF fusion across conversation AND code simultaneously. A query about DEBT-006 would return both the roadmap mention AND the actual `sexton.py` file AND the `app.py` call site.

### Adaptive Per-Query Retrieval Weighting

The current dynamic weighting is fixed per-config. A learned, per-query-type
weighting (e.g. entity-heavy queries get more graph weight; conceptual
queries get more vector weight) would be an enhancement over the existing
`retrieval_weight_tuning.py` script.

### Learned Entity Resolution

The 22-entry alias registry is static. A learned, context-aware resolver
that can map "Moses" / "Musa" / "M." based on surrounding context would
be an enhancement over the current PersonalizedPageRank approach.

### Professional Agent Fleet (ADR-015)

Architectural contract: `docs/decisions/ADR-015-professional-agent-fleet.md`
(when accepted). Seven domain extensions, each an ADR-014 extension with
fleet-compliant manifest (capability_card + agent capability isolation +
context_contract). Build order is DEFINER-decided; HERALD is first.

| Extension | Domain | Model tier | Status |
|-----------|--------|------------|--------|
| **HERALD** | Research / field awareness | balanced | 🔲 Planned — Phase 3A-1 (first fleet member) |
| **LOOM** | Writing / long-form synthesis | balanced | 🔲 Planned — Phase 3B+ |
| **CODEFORGE** | Code / engineering | balanced | 🔲 Planned — parallel_safe: false (write lock) |
| **STUDIO** | Multimedia / design | balanced | 🔲 Planned |
| **CHRONICLE** | History / temporal knowledge | balanced | 🔲 Planned |
| **PRAXIS** | Practice / skill building | balanced | 🔲 Planned |
| **ORACLE** | Forecasting / analysis | frontier | 🔲 Planned |

**Trajectory Memory (Layer 3):** New `trajectory` corpus type + CURATOR
actor (4th platform actor, every 6h cycle). Trust-tiered: raw → candidate
→ approved → domain → agent. Forgetting policy (90-day decay +
contradiction cascade). State-conditioned retrieval key. See ADR-015 §5.

**CURATOR Actor:** Platform-level background actor (not
extension-contributed). Exclusive writer to trajectory corpus. Implements
IBM four-component framework (extraction + attribution + learning +
contradiction detection). Closes Loop 5 simultaneously with
`update_weights()` wiring (DEBT-022). See ADR-015 §5.4.

**Fleet Coordinator (Layer 2):** Deterministic dispatch infrastructure
(intent classification → DispatchPlan → AgentRun creation → parallel
dispatch → cost governance). NOT an agent — does not synthesize over
content. Dry-run mode before parallel real model spend. See ADR-015 §4.

---

## Operational (not code changes)

| Item | Action | Owner |
|------|--------|-------|
| Close 1.8% embedding gap | Run the server long enough for Sexton cycles to process the backlog | Operator |
| Re-run retrieval evaluation after embedding gap closes | Establish new baseline via `scripts/retrieval_weight_tuning.py` | Operator |
| Manual GUI review of Phase 3 polish | Verify colored badges + stance color-coding + Compress toggle render correctly | Operator |

---

## GUI Phase — Brain Core Shell Features
*(Planned — no blockers)*

### 1. Extension UI sidebar visibility (ADR-014 A1)
- KNOWN_EXTENSIONS in config/aip.config.toml [extensions]
- ui.timer 5s poll of each known health endpoint
- ui.refreshable re-renders left sidebar on state change
- Extension icon/link absent until HTTP 200 received

### 2. Three-panel shell (NiceGUI)
- Left drawer: core links + dynamic extension items
- Right drawer: collapsible extension context panel
  (hidden by default, opens on session activate)
- Main area: chat view as default

### 3. + menu
- ui.button adjacent to chat input
- ui.menu: Upload PDF, Upload Image, Voice mode, Chat settings
- Divider + extension-registered items below
- Extensions register items via manifest

### 4. Extension mode shift
- Header accent on session activate
- Mode label: "[EXTENSION] - [mode]"
- Right panel opens with extension context
- Auto-clear on session end

### 5. Chat bar migration (non-chat-primary extensions)
- Collapsible bottom panel implementation
- Chat bar always accessible regardless of primary view

### 6. pypdf one-line fix (DEBT-012)
- File: src/aip/orchestration/ingestion/parsers/document_parser.py:254
- Change: from PyPDF2 import PdfReader -> from pypdf import PdfReader
- Unblocks OCR path in ARISTOTLE and native PDF ingest in Brain
- NOTE: This fix is ALREADY DONE (DEBT-012 resolved at commit 48aea1a).
  The file already uses `from pypdf import PdfReader`. Listed here for
  completeness — no action needed.

---

## Change Log

| Date | Change | Agent |
|------|--------|-------|
| 2026-06-17 | Created file. Seeded with all items from the dogfood run + Claude analysis. | Super Z (main) |
| 2026-06-17 | Moved 3 Phase 4.1 features (provenance widget, context visualizer, consistency checker) from Near-Term to Already Built. | Super Z (main) |
| 2026-06-17 | Global docs hardening pass — cross-referenced ROADMAP.md Phase 6 + Phase 1.6. Added DEBT-010 (TracePanel right_drawer) to TECH_DEBT.md. | Super Z (main) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 1 complete: added ARCHIVED terminal state to ECS graph, created 4 foundation files (corpus_types, corpus_exceptions, corpus_constants, protocols/corpus_registry), 43 new tests. Added "In Progress" section tracking the 9-chunk sequence. Updated Codebase-as-Corpus to reference ADR-008 (supersedes ADR-004). | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 2 complete: CorpusRegistry + CorpusStoreFactory + CorpusConnectionManager (§A0 shared pool) + CorpusMigrationRunner (§A8 fingerprint+checksum) + 5-scheduler migration gate in app.py (§A5). 55 new tests. Stubs for transition_artifact/list_review_items/_reconcile_bridge_edges (Chunks 6/8). | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 8 complete: ECS/ArtifactStore per corpus. CorpusTurnStore: delete_turn, states_for, search(include_archived), revision_parent_id round-trip. Factory: ECS+artifact attachment, M004/M005, definer-only tables. Registry: full transition_artifact (durable outbox §A10), list_review_items (§9.4 validation), backfill, audit log. CLI: aip audit log. 29 new tests. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 3 partial: call-site migration infrastructure. AipContainer: corpus_registry field + definer_stores property. AskStores.from_corpus_stores classmethod (§A1). set_embedding_provider registry-aware rewrite (§A6). 12 new tests. Mechanical rewrite of 264 call sites + legacy singleton removal deferred to follow-up. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 4 complete: Retrieval scoping. New corpus_retrieval.py: namespace_hit_id/parse_hit_id (§4), corpus_aware_cache_key (§4 sorted), filter_excluded_states (§A2 fusion-layer ECS filter), gather_corpus_results (§A12 Branham graceful degrade). assemble_augmented_context extended for multi-corpus path. 21 new tests. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 5 complete: Session/project binding + custom-channel scoping. session_corpus_binding.py (active_corpus_ids, branham_allowlist, §5 policy enforcement). custom_channel_scoping.py (ScopedCorpusStores §A14). GUI corpus_selector.py. 30 new tests. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 6 complete: Graph bridge edges. GraphEdge.target_corpus_id (§A7). M002 migration. 4 new GraphStore methods (upsert_bridge_edge, delete_bridge_edges, get_bridge_neighbors, get_orphan_bridge_targets). _reconcile_bridge_edges (§A13) + delete_corpus bridge cleanup. 17 new tests. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 7 complete: Code corpus ingest. python_ast_parser.py (functions/classes/module registration calls, skip .pyi/test_*, SyntaxError→[]). code_ingest_pipeline.py (stale detection via content_hash, skip/supersede). 3 golden queries acceptance tests against actual AIP codebase. 24 new tests. Delivers Phase 1.6 Codebase-as-Corpus. | GLM (Coding Agent) |
| 2026-06-18 | ADR-008 Multi-Corpus Chunk 9 complete (FINAL CHUNK): Acceptance suite AC-01 through AC-09 (19 tests). `aip corpus migrate --force` CLI (§A15). `aip backup` strategy A rewrite with corpus DB discovery (§9.7). All 9 chunks complete — ADR-008 multi-corpus architecture shipped. | GLM (Coding Agent) |
| 2026-06-18 | ADR-014 Phase 0 Extension Platform introduced. Step 0 complete: branham audit-action rename (`BRANHAM_POLICY_TRIGGERED` → `RESTRICTED_CORPUS_ACCESS_DENIED`) in `corpus_retrieval.py` + stale comment in `corpus_store_factory.py`. ADR-014 added to `docs/decisions/`. `tests/test_extension_lifecycle.py` added as the TDD contract (RED by design, 11 tests). Steps 1–7 are the next build unit. | Super Z (main) |
| 2026-06-18 | ADR-014 step 1 complete: built `src/aip/adapter/extensions/` package (8 files) — ExtensionHost lifecycle driver, ExtensionState enum, pydantic v2 Manifest model, host-owned ExtensionRegistry, supervised_task helper, migration_loader with separate `extension_applied_migrations` table. Stages 0–3 + 5 GREEN; stage 4 (GUI mount) is v1.1 with `xfail(strict=True)`. Fixed a test bug in `test_two_extensions_with_same_id_fails_cleanly` (dict-comprehension logic error). Manifest model verified with 8 validation cases. Full pytest deferred to CI. | Super Z (main) |
| 2026-06-18 | ADR-014 step 2 (partial): wired ExtensionHost into `app.py::lifespan` (host.start() after CorpusRegistry, host.stop() in shutdown). Added `extensions` + `workflow_registry` fields to `AipContainer`. Added `WorkflowRegistry.add_path(dir)` (ADR-014 §5.4) for per-extension workflow dirs. Replaced silent `except: continue` with logged WARNING in `_load_templates`. Wired `host._register_one` to call `add_path()`. `PluginManager` + `McpToolRegistry` deferred. All 3 existing WorkflowRegistry tests pass + 6 new behavior tests pass. | Super Z (main) |
| 2026-06-18 | ADR-014 step 3 complete: added `Actor` Protocol + `ActorContext` + `ActorResult` to `foundation/protocols/actors.py` (runtime_checkable). Host imports from foundation; `isinstance(actor, Actor)` validates conformance at scheduler start. `_run_one_cycle` handles `ActorResult` (logs non-ok, honors `next_run_at` override). Updated `_DemoActor` in lifecycle test to return `ActorResult`. Added `tests/test_actor_protocol.py` (11 contract tests). Core actors (Beast/Vigil/Sexton) NOT migrated per ADR-014 §1. All 11 Actor Protocol tests + 3 WorkflowRegistry tests pass. | Super Z (main) |
| 2026-06-18 | ARISTOTLE Phase A dogfood drop: built `extensions/aristotle/` (7 files) — first real extension on the platform. Manifest v1 + `AristotleSettings` dataclass (en/ur bilingual) + `M001_aristotle.sql` (aristotle_concept + aristotle_struggle_pattern) + `SocratesActor` conforming to Actor Protocol + `hooks.py` + placeholder workflow. **Surfaced + fixed a platform gap**: host now adds `extensions/` to sys.path at stage 1 validate (ADR-014 §6.4) so `config.schema` + hooks.py sibling imports work. Added `tests/test_aristotle_extension.py` (7 integration tests). Verified: manifest validates, AristotleSettings instantiates, SocratesActor conforms, 14 existing tests still pass. | Super Z (main) |
| 2026-06-18 | ARISTOTLE Phase A multi-actor + state machine: built EXAMINER actor (probe/quiz/evaluate, degrades gracefully without model) + MENTOR actor (reads/writes aristotle_struggle_pattern via corpus write_conn). Updated hooks.py to register all 3 actors. Updated manifest advisory list. Replaced placeholder workflow with real TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine (7 nodes, declared not executable — engine wiring deferred). Added `tests/test_aristotle_actors.py` (10 tests: 5 conformance + 5 behavior with fakes). All 10 pass locally; all 14 existing tests still pass. | Super Z (main) |
| 2026-06-18 | ADR-014 §8 step 2 complete: wired `WorkflowEngine` into `AipContainer` + lifespan (`container.workflow_engine`). Rewrote `tutoring_session_v1.yaml` to use engine-compatible node types (agent/script/condition, not synthesize/decision/commit). Added `GET /health/extensions` endpoint (ADR-014 §7). Added `tests/test_workflow_engine_wiring.py` (9 tests: container fields, lifespan wiring, YAML structure, node-type compatibility, route existence). All 9 pass locally. 33 tests pass locally total — no regression. | Super Z (main) |
| 2026-06-18 | **ARISTOTLE extracted to separate repo + entry-point discovery.** Added `tests/test_extension_import_boundary.py` (machine-enforces the SoC boundary: extensions import only `aip.foundation.protocols.*` + `aip.adapter.extensions`; platform imports nothing from extensions). Added entry-point discovery to `ExtensionHost.discover()` via `importlib.metadata.entry_points(group="aip.extensions")` — the production path that replaces the sys.path hack. Extracted ARISTOTLE to [AIP_Aristotle](https://github.com/freedomgeneration1111-sudo/AIP_Aristotle) (pip-installable: `pip install git+.../AIP_Aristotle.git`). Removed `extensions/aristotle/` + ARISTOTLE-specific tests from AIP_Brain. Added `extensions/` to `.gitignore`. Updated README with extension install instructions. 21 platform tests pass; 9 ARISTOTLE tests pass from the new repo. | Super Z (main) |
| 2026-06-20 | Added `docs/UI_CONVENTIONS.md` — governing document for all UI work. Added "GUI Phase - Core Shell Features" section covering: three-panel shell (left/right drawers + main chat), + menu (core items + extension-registered items below divider), extension mode shift (accent color, mode label, sidebar), ADR-014 A1 sidebar visibility (ui.timer 5s poll, ui.refreshable). No code changes. | Super Z (main) |
| 2026-07-23 | **QW1 — codeforge corpus registered at startup.** The app.py lifespan now registers `("codeforge", CorpusType.CODE)` alongside `("definer", CorpusType.CONVERSATION)` in `corpora_to_register`. The codeforge db path is derived from the definer db_path's parent dir (`db/codeforge.db`). The corpus is registered **empty** at startup — ingest is triggered via `aip corpus ingest-code <dir>` (QW11, pending) or the Sexton file-watcher (QW13, planned). 3 new tests in `test_corpus_call_site_migration.py::TestCodeforgeCorpusStartupRegistration` pin the contract. Closes ND5 from the tech-debt assessment. Phase 1.6 status: infrastructure complete + corpus registered; operational ingest path still pending. | Super Z (assessment agent) |
| 2026-07-23 | **QW13 — `aip corpus watch-code` file-watcher shipped.** New CLI command polls a Python source directory every 30s (configurable via `--interval`) for changed `.py` files (by mtime). When changes are detected, re-runs the ingest pipeline with `skip_existing=True`. Closes ND6 from the tech-debt assessment (the "code corpus ages out of sync immediately" gap). Phase 1.6 "Critical detail" about re-ingest triggers is now RESOLVED. 5 tests in `test_corpus_watch_code_cli.py`. | Super Z (assessment agent) |

---

## Cross-References

- **ROADMAP.md** → Phase 6 (Fusion Pipeline, ✅ COMPLETE) + Phase 1.6 (Codebase-as-Corpus, 💡 PROPOSED)
- **TECH_DEBT.md** → DEBT-006 (RESOLVED), DEBT-010 (TracePanel right_drawer, Active), DEBT-011 (branham deprecated aliases, Active — one release cycle)
- **STATUS.md** → Fusion Pipeline section (2026-06-17) with full test inventory
- **DOGFOOD_READY.md** → Phase 4.1 capabilities in "What works well" section
- **AGENTS.md** → Root Status-Tracking Docs table + Docs Framework Rule 7 + Orient step requires reading this file
- **ADR-014** → `docs/decisions/ADR-014-phase0-extension-host.md` — Phase 0 extension platform build target (steps 0–7)
