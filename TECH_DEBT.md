# AIP Technical Debt Register

**Owner:** B. Moses Jorgensen  
**Last Updated:** 2026-06-18 (DEBT-011: branham deprecated aliases — one release cycle)

Each entry records a deliberate deferral — what was skipped, why, and what triggers remediation.

---

## DEBT-001 — `--merge-nodes aip_methodology aip` (Graph Node Alias Cleanup)

**Status:** Deferred  
**Phase:** 2B Knowledge Graph  
**Filed:** 2026-06-05

**What was deferred:**  
The bridge tag `aip_methodology->theology_research` references a domain node `aip_methodology`
that was renamed to `aip` in the domain registry before the knowledge graph was built.
`aip corpus graph --build-from-bridges` creates a node for `aip_methodology` as-is (from the
raw bridge tag data) because bridge tags in corpus_turns.bridges reflect the tag text at ingestion
time, not the current registry.

A `--merge-nodes aip_methodology aip` CLI command would merge the orphan node into the canonical
`aip` domain node, redirecting all edges to the target.

**Why deferred:**  
Only 5 bridge-tagged turns exist currently (sparse corpus). The `aip_methodology` node will be
one orphan node with 1 edge. The blast radius is minimal and the correct action is to retag
affected turns after the full corpus retag, then re-run `--build-from-bridges` with clean data.
Building a `--merge-nodes` command now would add complexity for a problem that self-corrects
after corpus retag.

**Remediation trigger:**  
After full corpus retag (2,649 currently untagged turns), re-run `aip corpus graph --build-from-bridges
--force`. If `aip_methodology` nodes persist at that point, implement `aip corpus graph --merge-nodes
<source_id> <target_id>` in GraphStore + CLI.

**Related work:**  
- `aip corpus graph --build-from-bridges` (current implementation creates as-is)
- `docs/entity_aliases.md` (canonical name registry — does not yet resolve old domain names)
- ROADMAP Phase 2B, Phase 3 (incremental graph updates)

---

## DEBT-002 — Full PPR Expansion in Augmented Chat (Phase 3 Deferral)

**Status:** Deferred  
**Phase:** 2B Knowledge Graph → Phase 3  
**Filed:** 2026-05-05

**What was deferred:**  
The full HippoRAG Personalized PageRank (PPR) expansion path in `chat.py` was deferred.
Current implementation in `_get_graph_neighbors()` does direct domain adjacency lookup only
(1-hop neighbors of the active domain). The `GraphRetriever.expand_query_via_graph()` method
with full PPR seeded on query entities is implemented but not wired into the chat path.

**Why deferred:**  
Query entity extraction from free-text requires either a fast NER pass or Beast LLM call —
both add latency to the chat response path. The constraint "DO NOT make graph retrieval block
the chat response path" applies. Domain neighbor lookup is synchronous and sub-millisecond.
Full PPR is valuable but the entity extraction step is the blocker.

**Remediation trigger:**  
Phase 3: Wire query entity extraction as a background pre-fetch (fire-and-forget before the
synthesis call, cache results by session). If the graph has >500 nodes and the extraction
pipeline can complete in <200ms, promote to full PPR path.

**Related work:**  
- `src/aip/orchestration/graph_retrieval.py` — `GraphRetriever.expand_query_via_graph()` is ready
- `src/aip/adapter/api/routes/chat.py` — `_get_graph_neighbors()` (current 1-hop implementation)

---

## DEBT-003 — MCP Tool Dispatch (Not Runtime-Wired + Fail-Open Risk)

**Status:** Active — non-live governance debt  
**Phase:** 0 (scaffolded), Phase 5 (full implementation)  
**Filed:** 2026-06-04 (pre-existing)

**What was deferred:**  
MCP tool dispatch performs real mutations (ECS transitions, canonical writes, search via Protocols) but is NOT wired into app.py runtime. The `autonomy_gate=None` escape hatch in `server.py:213` silently bypasses gate enforcement for write/admin tools — this must be hardened to fail-closed before MCP is wired.

**Remediation trigger:**  
Phase 5 multi-user deployment. Must harden `autonomy_gate=None` fail-closed before MCP is wired into runtime.

---

## DEBT-007 — CLI Commands Using Blocking sqlite3.connect() (Async-Path Risk)

**Status:** Active — low priority  
**Phase:** Chunk 4 (Async-safe storage)  
**Filed:** 2026-06-10

**What was deferred:**  
Several CLI command files (`cli/init.py`, `cli/backup.py`, `cli/project.py`, `cli/ingest.py`,
`cli/history.py`, `cli/status.py`, `cli/session.py`, `cli/corpus.py`) use synchronous
`sqlite3.connect()` directly. This is acceptable in CLI context (no event loop to block),
but the `admin.py` route at `src/aip/adapter/api/routes/admin.py:308` also uses a blocking
`sqlite3.connect()` — this DOES run in the async FastAPI event loop and should be converted
to use the store layer.

**Why deferred:**  
CLI commands run synchronously (no event loop) so blocking sqlite3.connect() is correct there.
The admin.py route is the only remaining async-path offender and it's read-only with a short
query duration. The risk is low but should be addressed when the admin routes are next touched.

**Remediation trigger:**  
Next time admin.py routes are modified, convert the direct sqlite3.connect() call to use
the existing store methods (entity_store, event_store, etc.) or add a dedicated admin
query method to the appropriate store.

**Related work:**  
- Chunk 4 — resolved the same pattern in AcePlaybook, Beast, and VSS probe
- `src/aip/adapter/api/routes/admin.py:308` — remaining async-path blocking call

---

## DEBT-004 — GraphStore Connection Churn

**Status:** Resolved — Chunk 4 confirmed aiosqlite conversion is already complete  
**Phase:** 2B Knowledge Graph  
**Filed:** 2026-06-06

**What was deferred:**  
`adapter/graph_store.py` opens and closes a new `sqlite3.connect()` on every method call
(upsert_node, upsert_edge, get_neighbors, get_all_nodes, etc.). For the current graph size
(28-36 nodes, 5-17 edges) this is not a performance problem, but it is architecturally inconsistent
with the rest of the adapter layer which uses connection pools or persistent async connections
(aiosqlite via SqliteConcurrencyManager).

**Why deferred:**  
The graph is read-heavy and small. Per-call connection overhead is microseconds at this scale.
DEBT-005 (aiosqlite conversion) is the correct fix and subsumes this one — both will be resolved
together in BUG-004.

**Chunk 4 status:** GraphStore has been converted to aiosqlite with persistent connection
pattern (initialize() + _get_conn() + close()). This debt item is resolved.

**Related work:**  
- `src/aip/adapter/graph_store.py` — now uses aiosqlite with ReadPoolMixin
- DEBT-005 below — also resolved by the same conversion

---

## DEBT-005 — GraphStore Protocol Missing + Synchronous sqlite3

**Status:** Resolved — Chunk 4 confirmed both gaps are closed  
**Phase:** 2B Knowledge Graph  
**Filed:** 2026-06-06

**What was deferred:**  
Two related gaps:

1. **No `GraphStore` Protocol in `foundation/protocols/storage.py`.** All other stores in the
   adapter layer (VectorStore, LexicalStore, CanonicalStore, ArtifactStore, etc.) have Protocol
   declarations for dependency injection and structural typing. GraphStore was added in Phase 2B
   without a Protocol, making it un-swappable and invisible to the DI system.

2. **`adapter/graph_store.py` uses synchronous `sqlite3`** rather than `aiosqlite`. All other
   async-path SQLite stores use aiosqlite.

**Chunk 4 status:** Both gaps are resolved. GraphStore Protocol exists in
`foundation/protocols/storage.py`, and the implementation uses aiosqlite with
persistent connection + ReadPoolMixin. The store is wired into AipContainer and
registered in the store registry.

**Related work:**  
- `src/aip/adapter/graph_store.py` — now uses aiosqlite with ReadPoolMixin
- `src/aip/foundation/protocols/storage.py` — GraphStore Protocol added
- `src/aip/adapter/api/app.py` — GraphStore wired in lifespan startup

---

## DEBT-006 — `actors/sexton.py` Not Wired into app.py (CRITICAL)

**Status:** Resolved — Chunk 3 confirmed wiring is already in place; docs were stale  
**Phase:** 3 Actor Intelligence  
**Filed:** 2026-06-06  
**Resolved:** 2026-06-11

**What was deferred:**  
ADR-011 (2026-06-06) drove a code refactor that built a full-maintenance Sexton actor at
`src/aip/orchestration/actors/sexton.py` (2,100+ lines, 5 operations: tagging, embedding,
wiki generation, graph extraction, failure classification).

DEBT-006 originally claimed app.py was NOT updated to wire the new actor. This was incorrect
at the time of Chunk 3 inspection (2026-06-11): the wiring was already in place:

- `app.py` lines 520-573 import `aip.orchestration.actors.sexton.Sexton` and instantiate it
  into `container.sexton_actor` with all required stores.
- `app.py` lines 1256-1313 create `_sexton_actor_scheduler()` that calls
  `container.sexton_actor.run_cycle()` on a 300s cadence.
- `app.py` lines 1319-1331 fire an immediate `run_cycle()` on startup.

**What Chunk 3 actually fixed:**

1. **L4 reset.py signature mismatch** — `Sexton(trace_store)` passed `trace_store` as the
   first positional arg (`config`) instead of as `trace_store=trace_store`. Fixed to use
   keyword arg.

2. **Honest state reporting** — `get_status_summary()` now returns a synthesized `state`
   field: `active`, `degraded`, `disabled`, or `failed`. Previously there was no top-level
   state; the `/health/dogfood` endpoint reported `"sexton": "active"` based solely on
   `container.sexton_actor is not None`, which was misleading when core deps were missing.

3. **Cycle failure recording** — The scheduler's `except` block now records failures in
   `container.sexton_actor._recent_errors` so status endpoints reflect the failure state.

4. **Stale docs** — STATUS.md, DOGFOOD_READY.md, and this entry all claimed Sexton was
   "NOT WIRED" when it was already wired. Updated to reflect reality.

**Chunk 3 status:** DEBT-006 is resolved. The Sexton actor is wired and scheduled. The
remaining gap is embedding coverage (~1.8%), which is an operational concern requiring
the embedding provider to be configured and the server to run long enough for Sexton
cycles to process the backlog.

**Related work:**  
- `src/aip/orchestration/actors/sexton.py` — the full-maintenance Sexton (wired, running)
- `src/aip/orchestration/sexton/sexton.py` — the old failure-classifier Sexton (delegated to by the new actor)
- `src/aip/adapter/api/app.py` — the wiring location (lines 520-573, 1256-1331)
- `src/aip/adapter/api/dependencies.py` — `container.sexton_actor` field (Any type)
- `src/aip/adapter/api/routes/health.py` — honest Sexton state in /health and /health/dogfood
- `src/aip/orchestration/l4/reset.py` — fixed signature mismatch

---

## DEBT-008 — ChannelHealthReport.format_warnings() Does Not Surface UNAVAILABLE or NOT_CONFIGURED States

**Status:** Active — by design, low priority
**Phase:** Chunk 5 (Retrieval honesty and vector health verification)
**Filed:** 2026-06-11

**What was deferred:**
`ChannelHealthReport.format_warnings()` only surfaces warnings for FAILED and DEGRADED channel
states. The new UNAVAILABLE and NOT_CONFIGURED states (added in Chunk 5) are not included in
the formatted warnings output. These states are visible through the structured `channel_details`
dict in `RetrievalTrace.to_diagnostic_dict()`, the `get_unavailable_channels()` /
`get_not_configured_channels()` accessors, and the `/health` and `/health/dogfood` endpoints,
but `format_warnings()` skips them.

**Why deferred:**
This is a deliberate scoping decision for Chunk 5. The `format_warnings()` method is used in
the retrieval trace summary to alert operators to active problems. UNAVAILABLE and NOT_CONFIGURED
are configuration/presence states, not runtime failures — they reflect missing infrastructure
rather than degraded operation. Including them in every retrieval warning would be noisy for
operators who already know those channels are absent. The structured data paths provide full
visibility for monitoring tools.

**Remediation trigger:**
If operational feedback indicates that UNAVAILABLE or NOT_CONFIGURED channels should surface
in `format_warnings()`, add them with a distinct severity (e.g., informational vs. warning).
This would be a one-line change in `format_warnings()` but the threshold for inclusion should
be driven by actual operational need, not speculative completeness.

**Related work:**
- `src/aip/foundation/schemas/retrieval.py` — ChannelHealthState enum, ChannelHealthDetail dataclass
- `src/aip/foundation/schemas/retrieval.py` — ChannelHealthReport.format_warnings()
- `src/aip/adapter/api/routes/health.py` — retrieval_channel_health and channel_states sections
- `tests/test_chunk5_retrieval_honesty_v2.py` — 46 tests for Chunk 5 retrieval honesty
- `tests/test_chunk3_sexton_wiring.py` — 19 tests for honest state, startup, signatures
- ADR-011 — the architectural decision that drove the refactor

---

## DEBT-009 — Remaining Sprint/Step Scaffold Comments Outside Retrieval Scope

**Status:** Active — low priority
**Phase:** Chunk 7 (ask_pipeline decomposition and retrieval trace cleanup)
**Filed:** 2026-06-11

**What was deferred:**
The sanitation sweep in Chunk 7 cleaned scaffold comments ("Sprint N", "Step N", "Chunk N")
from the retrieval pipeline modules (ask_pipeline.py, retrieval_orchestrator.py, channels/*,
schemas/retrieval.py). However, many files outside the retrieval scope still contain these
comments: vigil.py, beast.py, sexton.py, alerting.py, config/__init__.py, cli modules,
adapter modules, and others. These comments are documentation markers that reference the
sprint/chunk in which a feature was added but are not misleading — they are just inconsistent
with the preferred style of describing features by purpose rather than by sprint number.

**Why deferred:**
The blast radius is large (20+ files, 200+ comments) and the changes are cosmetic — they
do not affect behavior, test outcomes, or operational correctness. Changing them all at once
would create a large diff with no functional value and risk merge conflicts with ongoing work.
The retrieval pipeline (the Chunk 7 scope) is now fully cleaned.

**Remediation trigger:**
When any of these files is next modified for functional reasons, clean up Sprint/Step/Chunk
comments in that file as a housekeeping step. Do not create a dedicated cleanup PR.

**Related work:**
- Chunk 5 — cleaned retrieval schema and orchestrator comments
- Chunk 6 — cleaned import boundary comments
- Chunk 7 — cleaned ask_pipeline, channels, and retrieval_trace_utils comments

---

## DEBT-010 — TracePanel Uses Forbidden `ui.right_drawer()` (GUI Consistency)

**Status:** Active — low priority
**Phase:** Phase 4.1 (identified during docs hardening)
**Filed:** 2026-06-17

**Problem:**
`gui/components/trace_panel.py` uses `ui.right_drawer()` (line 62) to display the
retrieval trace. Per `gui/AGENTS.md` Known Gotchas:

> `ui.right_drawer()` is FORBIDDEN in this codebase. Per the no-right-sidebar rule,
> no page or component may use `ui.right_drawer()`. The Beast Counsel and Model Council
> panels use `ui.dialog()` (centered modal) instead.

The BeastPanel and ModelCouncilPanel were converted from `ui.right_drawer()` to
`ui.dialog()` in a prior cycle. TracePanel was missed.

**Impact:**
The Trace panel renders as an overlay drawer on the right side, which clips content
on narrow viewports and is inconsistent with the BeastPanel + ModelCouncilPanel
which use centered modal dialogs. The `_render_context_composition` visualizer
(Phase 4.1) was added to this drawer, making the inconsistency more visible.

**Remediation:**
Convert `TracePanel.show_trace()` from `ui.right_drawer()` to `ui.dialog()` using
the same pattern as `ModelCouncilPanel._open_dialog()` — centered modal with
`_DIALOG_STYLE` (max-width:1000px, scrollable inner column, dark background).

**Why deferred:**
The drawer works functionally — the context composition visualizer renders correctly.
The conversion is a GUI consistency fix, not a functional bug. Low priority until the
next GUI modification pass on trace_panel.py.

**Related work:**
- `gui/AGENTS.md` → "ui.right_drawer() is FORBIDDEN" gotcha
- `gui/components/model_council_panel.py` → `_open_dialog()` pattern (reference implementation)
- `gui/components/beast_panel.py` → same dialog conversion pattern

---

## DEBT-011 — Branham Deprecated Aliases (One-Release-Cycle Removal)

**Status:** Active — scheduled removal after one release cycle  
**Phase:** ADR-014 Phase 0 Extension Platform (step 0)  
**Filed:** 2026-06-18

**What was deferred:**
The branham → sensitive generalization (commit `956f06f`) removed the
`corpus_id == "branham"` special-case branch but left behind a surface
of backward-compat shims. ADR-014 step 0 finished the half-done rename
of the audit action (`BRANHAM_POLICY_TRIGGERED` →
`RESTRICTED_CORPUS_ACCESS_DENIED`) — that part is DONE. The remaining
shims are deliberately kept for one release cycle:

1. **Exception alias**: `BranhamIsolationViolation = RestrictedCorpusAccessViolation`
   in `src/aip/foundation/corpus_exceptions.py:53`. Kept because the
   "1000-query acceptance test" imports it by name (per the comment at
   line 50-52).
2. **Deprecated parameter aliases**:
   - `session_branham_allowlist: bool | None` in `corpus_retrieval.py:161`
     and `corpus_registry.py:300` — translates to
     `allowed_restricted_corpora += ["branham"]`.
   - `branham_policy_enabled: bool | None` in `corpus_registry.py` `register()`
     and `startup()` — deprecated alias for `sensitive`.
3. **Structured log key**: `branham_isolation_suppressed` in
   `corpus_retrieval.py:250`. This is a log filter key operators may
   grep for; not an audit action.
4. **Backward-compat session metadata field**: `branham_allowlist: True`
   (boolean) read by `session_corpus_binding.py:69-70` to add `"branham"`
   to the allowed list for old sessions.

**Why deferred:**
The audit action rename was the user-facing surface that had to be clean
before the first ARISTOTLE audit entry. The exception alias and parameter
aliases are operator/developer-facing surfaces that are cheaper to keep
than to migrate in the same commit. The "1000-query acceptance test"
referenced in `corpus_exceptions.py:50-52` needs to be updated to use
`RestrictedCorpusAccessViolation` before the alias can be removed.

**Remediation trigger:**
After one release cycle (i.e. after ADR-014 steps 1–3 land and ARISTOTLE
Phase A is dogfoodable), in a single dedicated commit:
1. Update the 1000-query acceptance test to use `RestrictedCorpusAccessViolation`.
2. Remove `BranhamIsolationViolation` alias from `corpus_exceptions.py`.
3. Remove `session_branham_allowlist` parameter from `corpus_retrieval.py`
   and `corpus_registry.py`.
4. Remove `branham_policy_enabled` parameter from `corpus_registry.py`.
5. Remove the `branham_allowlist` boolean read in `session_corpus_binding.py:69-70`.
6. Rename the log key `branham_isolation_suppressed` →
   `restricted_corpus_suppressed` in `corpus_retrieval.py:250`.
7. Update the docstrings in `session_corpus_binding.py` and
   `corpus_retrieval.py` that reference the old name.

**Related work:**
- ADR-014 §1 (branham rename decision) and §10 (longevity hedges)
- `src/aip/foundation/corpus_exceptions.py:50-53` — exception alias
- `src/aip/adapter/corpus_retrieval.py:161,193-195,244,250` — deprecated
  param + audit action (audit action DONE; rest pending)
- `src/aip/adapter/corpus_registry.py:300,319` — deprecated param alias
- `src/aip/adapter/session_corpus_binding.py:60-81,131` — backward-compat
  session metadata field
- `src/aip/adapter/corpus_stores.py:104,170` — `_branham_policy_enabled`
  slot + health output alias

---
