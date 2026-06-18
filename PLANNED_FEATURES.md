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
> **Last Updated:** 2026-06-17
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

- **Graph A** (existing): Conversation knowledge graph — 36 nodes, 17 edges
- **Graph B** (new): Code dependency graph — modules, functions, classes, tests — with `imports`, `calls`, `tests`, `implements` edges
- **Cross-graph edges**: A conversation turn that *references* a specific function gets a `references` edge. An ADR that *decided* a code pattern gets a `decided` edge. The graph answers: "what conversations informed this function?" and "what code exists for this architectural decision?"
- **Implementation**: Python AST → CorpusTurn format parser. The underlying store, tagging, embedding, and graph infrastructure handle it without changes. Each "turn" = a function/class/module with `searchable_text` = module path + docstring + signature + inline comments + associated test names.
- **Critical detail**: code changes continuously (conversation corpus is append-only and stable). The code corpus needs a re-ingest trigger — CI hook on commit OR Sexton file-watcher that detects `.py` changes and queues a re-parse pass. Without this, the code corpus ages out of sync immediately.
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

---

## Operational (not code changes)

| Item | Action | Owner |
|------|--------|-------|
| Close 1.8% embedding gap | Run the server long enough for Sexton cycles to process the backlog | Operator |
| Re-run retrieval evaluation after embedding gap closes | Establish new baseline via `scripts/retrieval_weight_tuning.py` | Operator |
| Manual GUI review of Phase 3 polish | Verify colored badges + stance color-coding + Compress toggle render correctly | Operator |

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

---

## Cross-References

- **ROADMAP.md** → Phase 6 (Fusion Pipeline, ✅ COMPLETE) + Phase 1.6 (Codebase-as-Corpus, 💡 PROPOSED)
- **TECH_DEBT.md** → DEBT-006 (RESOLVED), DEBT-010 (TracePanel right_drawer, Active)
- **STATUS.md** → Fusion Pipeline section (2026-06-17) with full test inventory
- **DOGFOOD_READY.md** → Phase 4.1 capabilities in "What works well" section
- **AGENTS.md** → Root Status-Tracking Docs table + Docs Framework Rule 7 + Orient step requires reading this file
