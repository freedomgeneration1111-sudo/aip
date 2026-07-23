# AIP Technical Debt Register

**Owner:** B. Moses Jorgensen  
**Last Updated:** 2026-06-26 (DEBT-020 through DEBT-024: ADR-015 fleet debt items + Type E substance score traceability)

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

## DEBT-012 — PyPDF2 → pypdf Package Rename (Resolved)

**Status:** Resolved — import fixed 2026-06-18
**Phase:** Ingestion
**Filed:** 2026-06-18

**What was deferred:**
`src/aip/orchestration/ingestion/parsers/document_parser.py:254` imported
`from PyPDF2 import PdfReader` — the old package name. The `pypdf` package
(5.x+) renamed from `PyPDF2` to `pypdf`, and the installed version
(pypdf 5.9.0) exports `from pypdf import PdfReader`. The old import
silently failed (caught by the `try/except` that falls through to
pdfplumber or returns empty), so PDF ingestion was broken without any
visible error — the function just returned an empty list.

**Resolution:**
Changed the import to `from pypdf import PdfReader`. The `PdfReader` API
is the same in both package names; only the import path changed.

**Related work:**
- `src/aip/orchestration/ingestion/parsers/document_parser.py:254` (the import)
- `pypdf` 5.9.0 (installed, exports `from pypdf import PdfReader`)

---

## DEBT-013 — ExtensionHost.stop() leaves actor scheduler coroutines un-awaited (RuntimeWarning in tests)

**Status:** Resolved — fixed 2026-06-19 (platform test suite, 0 warnings)
**Phase:** ADR-014 Phase 0 Extension Platform / test teardown
**Filed:** 2026-06-19

**What was broken:**
Both AIP_Brain's platform test suite (`tests/test_extension_lifecycle.py`)
and AIP_Aristotle's test suite emitted this warning on teardown:

```
RuntimeWarning: coroutine '_actor_scheduler_loop' was never awaited
  gc.collect()
RuntimeWarning: Enable tracemalloc to get traceback where the object was allocated
```

(Also surfaced as `PytestUnhandledThreadExceptionWarning` and, in some
configurations, `RuntimeError: Event loop is closed` from aiosqlite's
background worker thread.)

**Root cause (deeper than originally filed):**
The original filing attributed the warning to "test fixtures don't call
`await host.stop()` on teardown." The actual root cause is more subtle:

`ExtensionHost._start_actor_tasks()` calls `supervised_task(name,
coro=_actor_scheduler_loop(...))`. The `_actor_scheduler_loop(...)`
call creates a coroutine object. `supervised_task` then calls
`asyncio.create_task(_supervised_inner(name, coro))` — wrapping the
coroutine in a `_supervised_inner` coroutine, which is what `create_task`
actually schedules.

When `host.stop()` cancels a task that is still PENDING (i.e.,
`_supervised_inner` hasn't started executing yet), the task's coroutine
(`_supervised_inner`) is closed via `coro.close()`. But the `coro`
argument — the `_actor_scheduler_loop` coroutine object — is a local
variable inside `_supervised_inner`'s (never-executed) frame. It's
never touched, never awaited, never closed. Python's GC sees it as
"never awaited" and emits the RuntimeWarning.

`await asyncio.sleep(0)` before `host.stop()` was tried but does NOT
fix it — `sleep(0)` yields control but doesn't guarantee the scheduler
tasks transition from PENDING to actually-executing in pytest-asyncio's
event loop model.

**Resolution (Option 1 — test fixture level, as specified in the debt entry):**
Fixed in `tests/test_extension_lifecycle.py`. The `host` fixture now:

1. Patches `supervised_task` in `aip.adapter.extensions.host` to track
   the inner coroutine (`_actor_scheduler_loop(...)`) passed to each
   `supervised_task` call. The original `supervised_task` is still
   called — tracking is transparent.

2. On teardown, restores the original `supervised_task`, calls
   `await h.stop()` (which cancels + gathers all actor tasks), and
   then explicitly `coro.close()` on every tracked coroutine whose
   `cr_frame` is not None (i.e., still pending). `coro.close()` marks
   the coroutine as "handled" and suppresses the RuntimeWarning at
   GC time.

3. The `container` fixture now closes every corpus's stores via
   `stores.close_all()` on teardown, eliminating the parallel
   `PytestUnhandledThreadExceptionWarning` from aiosqlite's background
   worker thread hitting "Event loop is closed."

**Result:**
```
cd AIP_Brain
PYTHONPATH=src python -m pytest -q \
  tests/test_extension_lifecycle.py \
  tests/test_extension_import_boundary.py \
  tests/test_actor_protocol.py \
  tests/test_extended_workflows.py \
  tests/test_workflow_engine_wiring.py
# → 33 passed, 1 skipped, 0 warnings  (was: 2 warnings)
```

**Why not Option 2 (harden `host.stop()`):**
Option 2 — making `host.stop()` track + close the inner coroutines —
would fix the root cause in production code. But it touches
`src/aip/adapter/extensions/host.py` and `supervision.py`, which is
out of scope for "test fixture file(s) only" (the user's staging
constraint). The fixture-level fix (Option 1) achieves 0 warnings
without touching production code. A future hardening commit could
move the coroutine-tracking + close logic into `supervised_task`
itself — at which point the fixture patch can be removed.

**Remaining (Aristotle side):**
AIP_Aristotle's test suite still shows 3 warnings (down from 2 in the
prior session — the count varies with pytest's GC timing). The
Aristotle test fixtures in `tests/test_aristotle_tutoring.py` and
`tests/test_aristotle_extension.py` construct their own `ExtensionHost`
instances and don't use the Brain-side `host` fixture. Applying the
same fixture pattern there is a separate task.

**Related work:**
- `tests/test_extension_lifecycle.py` (the fix — `host` fixture patches
  `supervised_task` to track coros, `container` fixture closes stores)
- `src/aip/adapter/extensions/host.py` (the `_start_actor_tasks` method
  that creates the coroutines — unchanged)
- `src/aip/adapter/extensions/supervision.py` (`supervised_task` wraps
  the coro in `_supervised_inner` — unchanged; future hardening could
  close the coro on cancellation here)
- `AIP_Aristotle/tests/test_aristotle_tutoring.py` (same warning
  pattern — Aristotle fixtures construct ExtensionHost independently)

---

## DEBT-014 — Extension API routers never mounted (NameError in create_app)

**Status:** Resolved — fixed 2026-06-19 during ARISTOTLE dogfood
**Phase:** ADR-014 Phase 0 Extension Platform
**Filed:** 2026-06-19

**What was broken:**
`src/aip/adapter/api/app.py:1976` (in `create_app`) attempted:
```python
extensions_host = getattr(container, "extensions", None)
if extensions_host is not None:
    for router_info in extensions_host.registered_api_routers():
        app.include_router(router_info["router"], tags=[router_info["ext_id"]])
```
But `container` is a **local variable inside the `lifespan` async function**
— it does not exist in `create_app`'s scope. Result: `NameError` at backend
startup, the entire lifespan aborted, and Aristotle's `/aristotle/*` routes
were never mounted. The CLI's `aristotle health` would still succeed (it
hits `/api/v1/health/extensions`, which is platform-owned and routed
separately), masking the bug — but `aristotle ingest`, `list-concepts`,
and `session` all 404'd.

The 33-test extension suite (`tests/test_extension_lifecycle.py` etc.)
passed because those tests construct `ExtensionHost` directly and never
exercise `create_app()`'s router-mounting path.

**Resolution:**
Moved the router-mounting block into the `lifespan` function, immediately
after `await extensions_host.start()` succeeds (where `app` and
`container.extensions` are both in scope). Per-router `try/except` is
preserved so a bad router never blocks the host. `create_app` now carries
a comment explaining why this logic lives in lifespan, not at module
factory time.

**Related work:**
- `src/aip/adapter/api/app.py` (lifespan block, ~line 578; create_app comment, ~line 1988)
- `tests/test_extension_lifecycle.py` — does NOT cover this path; an
  end-to-end `create_app` + real HTTP request test should be added.

---

## DEBT-015 — ActorResult gains `data: Any = None` field (DEFINER decision ADR-002 §16 #4, resolved)

**Status:** Resolved — shipped 2026-06-19
**Phase:** Foundation Protocol (ADR-014 §5.2)
**Filed:** 2026-06-19

**DEFINER decision (ADR-002 §16 #4, resolved 2026-06-19):**
Add `data: Any = None` to the `ActorResult` dataclass in
`src/aip/foundation/protocols/actors.py`. Rationale: backwards-compatible
(defaults to None), every future extension will need a structured return
channel, and the error-as-payload pattern (using `error` to carry a
success payload) doesn't scale past one extension.

**What was the tension:**
The ARISTOTLE extension's SOCRATES/EXAMINER/MENTOR actors use the
`error` field to carry success payloads (e.g. EXAMINER.evaluate() returns
`ActorResult(ok=True, error=evaluation_text)` where `error` is the
evaluation JSON, not an error message). This works for one extension but
is semantically wrong and will collide as more extensions ship. The
proper channel for structured results is a dedicated `data` field.

**Resolution:**
Added `data: Any = None` as the last field on `ActorResult` (after `ok`,
`error`, `next_run_at`). Backwards-compatible:
- Every existing `ActorResult(ok=...)` construction continues to work
  (data defaults to None).
- Positional construction `ActorResult(True, "err", 1.5)` still works
  (data is 4th, defaults to None).
- Keyword construction `ActorResult(ok=True, error="msg")` still works.

ARISTOTLE's actors can now migrate from `error=<payload>` to
`data=<payload>` incrementally — the old pattern still works, the new
pattern is available. No actor is forced to migrate immediately; the
error-as-payload pattern is now soft-deprecated, not broken.

**Tests added** (`tests/test_actor_protocol.py`):
- `test_actor_result_defaults` updated to also assert `data is None`.
- `test_actor_result_with_error` updated to also assert `data is None`
  (data defaults to None even when other fields are set).
- `test_actor_result_data_field_round_trips_dict` — new test confirming
  `data` carries a dict payload and round-trips correctly (same object,
  fields accessible).
- `test_actor_result_data_field_with_none_explicit` — new test
  confirming `data=None` can be passed explicitly (e.g. to clear a
  prior value).

**Related work:**
- `src/aip/foundation/protocols/actors.py` (the field addition, ~line 116)
- `tests/test_actor_protocol.py` (4 tests pinning the new contract)
- `AIP_Aristotle/docs/decisions/ADR-002-intake-placement-learning-plan.md`
  §16 #4 (the DEFINER decision — lives in Aristotle because the ADR is
  about Aristotle's Phase B.5/D roadmap, but the Protocol change ships
  in AIP_Brain foundation)
- ARISTOTLE actor migration (soft-deprecated error-as-payload → data):
  follow-up, not blocking. Each actor can migrate when next touched.

---


## DEBT-016 — BUG-001/002/003 Reconciliation (Unregistered Bug Markers)

**Status:** Resolved — registered for traceability
**Phase:** Phase 0 Foundation
**Filed:** 2026-06-18

**What was deferred:**
Three BUG-xxx markers in code had no entry in the TECH_DEBT register:
- BUG-001 (`app.py:293`): Default project creation after init — ensures a default project exists so `aip ask` works without explicit `--project`.
- BUG-002 (`_augmented_context.py:96`): db_path resolution diverged from CLI's `_db_path.py` — unified.
- BUG-003 (`app.py:421,706,761`): ECS store must be initialized BEFORE Sexton actor creation — ordering fix + safety-net backfill.

All three are fixed in code (the markers are historical annotations, not active bugs). Registering them so the BUG-xxx namespace is reconciled with the DEBT-xxx namespace.

**Resolution:** All three bugs are fixed. The markers remain in code as historical annotations. No action needed.

---

## DEBT-017 — SessionManager CLI Wiring (Unregistered TODO)

**Status:** Active — low priority
**Phase:** Phase 0 CLI
**Filed:** 2026-06-18

**What was deferred:**
Two TODOs in `cli/session.py` (lines 39, 76) reference wiring through SessionManager API. The session CLI commands (`aip session start`, `aip session resume`) are stubs that don't connect to the SessionManager.

**Why deferred:** SessionManager is not yet fully wired into the CLI path. The API routes handle sessions; the CLI doesn't.

**Remediation trigger:** When CLI session management is needed (multi-session workflows from the terminal).

---

## DEBT-018 — AutonomyGate CLI Wiring (Unregistered TODO)

**Status:** Active — low priority
**Phase:** Phase 0 CLI
**Filed:** 2026-06-18

**What was deferred:**
Two duplicated TODOs in `cli/config.py:137` and `cli/project.py:81` reference wiring through AutonomyGate for admin-level write approval. Currently the CLI bypasses the autonomy gate (acceptable in local-first single-user mode).

**Why deferred:** AutonomyGate is wired in the API layer (FastAPI routes) but not in the CLI. For local-first single-user operation, the CLI bypass is acceptable. For multi-user or production CLI access, this needs wiring.

**Remediation trigger:** When CLI is used in a multi-user or production context.

---

## DEBT-019 — Remaining Ruff Lint Errors (64 non-auto-fixable)

**Status:** Active — low priority
**Phase:** Code hygiene
**Filed:** 2026-06-18

**What was deferred:**
After `ruff check --fix` (85 errors auto-fixed) + `ruff format` (43 files
reformatted), 64 non-auto-fixable errors remain. These are primarily:
- F841 (unused local variables) — need manual review
- F401 (unused imports in `__init__.py` re-exports) — intentional in some cases
- E501 (line too long) — need manual reformatting

**Why deferred:** Auto-fixable errors are resolved. The remaining 64 require
manual review to determine if the variable/import is truly unused or
intentionally re-exported. Low priority — doesn't affect runtime.

**Remediation trigger:** Next code hygiene pass or before open-sourcing.

---

## DEBT-020 — cadence=0 Startup Execution Runs Write-Capable Actors at Boot (ADR-015 §0)

**Status:** Active — BLOCKING for Phase 3A-0
**Phase:** Phase 3A-0 (pre-fleet)
**Filed:** 2026-06-26
**Source:** ADR-015 §0 (AgentRun Primitive — start_policy fix)

**What is broken:**
`src/aip/adapter/extensions/host.py:179-180` runs one cycle immediately
for ALL registered actors, including cadence=0 (manual-only) actors:
```python
# Run one cycle immediately (so manual-only actors do something on start).
await _run_one_cycle(actor, ctx, registration)
```
This is safe for read-only actors (SOCRATES, EXAMINER, MENTOR in
ARISTOTLE — they just log a health check). It is NOT safe for
write-capable agents (CODEFORGE with filesystem write, HERALD with web
search + corpus write). A write-capable agent running at boot before
DEFINER has issued any directive is a governance incident.

**Required fix (per ADR-015 §0):**
Add `start_policy` field to the Actor Protocol
(`src/aip/foundation/protocols/actors.py`) and to `register_actor()`
in `host.py`. Values: `scheduled` (run at startup + on cadence) |
`manual_only` (never run at startup; only via AgentRun). Default:
`manual_only` for safety. Change `host.py:179-180` to skip the startup
cycle when `start_policy == "manual_only"`.

**Remediation trigger:** Before Phase 3A-0 (before 2nd extension / any
write-capable agent). ADR-015 §0: "the fail-closed gate cannot be
retrofitted after agents are running."

---

## DEBT-021 — autonomy_gate=None Bypass in Container (ADR-015 §0)

**Status:** Active — HIGH for Phase 3D
**Phase:** Phase 3D
**Filed:** 2026-06-26
**Source:** ADR-015 §0 (fail-closed CapabilityGate)

**What is deferred:**
The `autonomy_gate` in the container can be None, which bypasses
capability checks. This is acceptable for local-first single-user
operation (current state) but must be closed before Phase 3D (full
MCP/tool integration behind CapabilityGate). ADR-015 §0: "Missing
AgentRun, capability, approval policy, trace_id, or budget = DENY. No
exceptions." A None autonomy_gate violates the fail-closed principle.

**Related:** DEBT-018 (AutonomyGate CLI Wiring) — the CLI-side bypass.
This debt item covers the container-level None bypass.

**Remediation trigger:** Before Phase 3D (full MCP/tool integration).

---

## DEBT-022 — AdaptiveRouter + update_weights() Dead Code (ADR-015 §5.7)

**Status:** Active — HIGH (documentation-vs-code discrepancy, settled)
**Phase:** Phase 3C (CURATOR + trajectory memory)
**Filed:** 2026-06-26
**Source:** ADR-015 §5.7 (Closing Loop 5)
**Verification:** 2026-06-26 — DEFINER-confirmed + codebase-verified

**What is wrong:**
ADR-015 §5.7 states: "`update_weights()` in `orchestration/adaptive_budget.py`
is currently a no-op stub (Loop 5 — dormant adaptive routing)." This claim
is **wrong on three counts**, all verified by codebase inspection:

1. **Wrong file:** `update_weights()` does NOT exist in
   `adaptive_budget.py`. That file contains `AdaptiveBudgetTuner` with
   `tune()` and `apply()` methods. The actual `update_weights()` lives in
   `src/aip/orchestration/router.py:104-164`.

2. **Wrong implementation status:** `router.py:104-164`
   `AdaptiveRouter.update_weights()` is **FULLY IMPLEMENTED but DEAD CODE**
   — 60 lines of recency-weighted exponential decay logic (70% success
   rate + 30% latency score). The function is never called: zero call
   sites exist anywhere in the codebase.

3. **Deeper than missing call site:** `AdaptiveRouter` is never
   instantiated. `container.adaptive_router` is declared as `Any = None`
   in `dependencies.py:73` and nothing ever sets it. `admin.py:182` reads
   it with an `if container.adaptive_router:` guard — always None, always
   skipped. `plugins.py:36` accepts it as a constructor param but the
   comment at line 58-60 says "AdaptiveRouter does not yet support
   register_provider(); skip silently." The entire router is unwired.

**Six documentation locations falsely describe the effect as "no-op":**
- `STATUS.md:409` — "update_weights() is no-op"
- `STATUS.md:453` — "Adaptive router does not adapt"
- `docs/implementation_status.md:160` — "'Adaptive' router is not adaptive"
- `docs/implementation_status.md:431` — "update_weights() is [pass]"
- `docs/hardening/CURRENT_STATE_BASELINE.md:149`
- `docs/hardening/CODE_DEBT_REGISTER.md:237`

These describe the **effect** (dead = effectively no-op) not the code.
The substance — "Loop 5 is dormant" — is correct.

**Required resolution (the good news):**
The heavy lifting is already written. Closing Loop 5 is NOT a
reimplementation — it is wiring:
1. Instantiate `AdaptiveRouter()` and assign to `container.adaptive_router`
   in `app.py` lifespan (alongside other orchestration components).
2. Add one call site: `await router.update_weights()` at the end of each
   CURATOR cycle (Phase 3C). This feeds trajectory marginal-utility
   scores into the adaptive router — ADR-015 §5.7's "close both gaps
   simultaneously" requirement.
3. Update the 6 stale doc locations to reflect "dead code, not stub" so
   future readers don't waste time looking for a stub to implement.

**ADR-015 §5.7 correction (before ADR-015 enters repo):**
Change: "`update_weights()` in `orchestration/adaptive_budget.py` is
currently a no-op stub"
To: "`update_weights()` in `orchestration/router.py:104` is fully
implemented but never called — `AdaptiveRouter` is never instantiated
and the function has no call site anywhere in the codebase. Loop 5 is
dormant not for lack of implementation but for lack of invocation.
Closing it requires: (1) instantiate AdaptiveRouter in the container,
(2) one call: `await router.update_weights()` at the end of each
CURATOR cycle."

**Remediation trigger:** Before CURATOR implementation (Phase 3C).

---

## DEBT-023 — trajectory/ Directory Naming Collision Risk (ADR-015 §Related)

**Status:** Active — MEDIUM (before Phase 3C)
**Phase:** Phase 3C (Trajectory Memory)
**Filed:** 2026-06-26
**Source:** ADR-015 §Related

**What is at risk:**
`src/aip/orchestration/trajectory/` currently contains L4 trajectory
*regulation* (monitoring/intervention): `context_reset.py`,
`regulator.py`, `__init__.py`. Its docstring confirms: "L4 trajectory
regulation and context reset."

ADR-015 §5 introduces a new trajectory *corpus* (trajectory memory
storage — a completely different concern). If both exist under
`trajectory/`, the naming collision will cause confusion: "trajectory
package" could mean regulation (existing) or storage (new).

**Required fix:**
Rename `src/aip/orchestration/trajectory/` to
`src/aip/orchestration/l4_regulation/` before beginning Layer 3
(trajectory corpus) implementation. Update all import sites.

**Remediation trigger:** Before Phase 3C work begins.

---

## DEBT-024 — Type E Substance Score Silent Fix (Documentation Traceability)

**Status:** Resolved — fix is live, this entry is for traceability
**Phase:** L4 Trajectory Regulation
**Filed:** 2026-06-26
**Discovered:** ADR-015 consistency check (ANOMALY-3)

**What was broken:**
The `FailureStreakDetector` (Type E — False Success Reporting) was
completely non-functional. Trace events that lacked a `substance_score`
field fell back to a hardcoded default of `0.5`. The detection threshold
was also `0.4`. Since `0.5 >= 0.4`, every outcome without an explicit
substance_score was treated as "high substance" — Type E detection could
never fire on missing data, which was the common case.

**What was fixed (silently, no debt entry at the time):**
The default `substance_score` was changed from hardcoded `0.5` to a
configurable `0.3` (below the `0.4` threshold). Both the default and
the threshold are now constructor parameters on `FailureStreakDetector`.

**Files documenting the fix:**
- `src/aip/orchestration/trajectory/regulator.py:15-18` — module docstring
  + `_DEFAULT_SUBSTANCE_SCORE = 0.3` at line 42
- `src/aip/orchestration/l4/failure_streak.py:6-11` — module docstring
  + `default_substance_score: float = 0.3` at line 41
- `tests/test_failure_streak.py:35-51` — `test_default_substance_score_below_threshold`
  explicitly tests the missing-field case (the regression guard)

**Why this entry exists:**
The fix was applied without a corresponding TECH_DEBT entry, so there
was no traceability from the debt register to the bug. A future reader
looking at the `0.3` default + the "previously hardcoded at 0.5" comment
had no debt item to cross-reference. This entry closes that gap.

**No action needed.** The fix is live, tested, and documented in three
places. This entry is purely for audit trail completeness.

---

## DEBT-025 — MAX_CORPORA Raised from 4 to 8 (Fleet Budget Headroom)

**Status:** Resolved — 2026-07-23 (QW10)
**Phase:** Pre-fleet (ADR-015 Phase 3A-0 prerequisite)
**Filed:** 2026-07-23

**What was deferred:**
The conservative cap on registered corpora (`MAX_CORPORA` in
`foundation/corpus_constants.py`) was set to 4 — enough for definer +
ARISTOTLE + 2 future extensions. The ADR-015 fleet vision names 6+
domain extensions (HERALD, LOOM, CodeForge, Praxis, Chronicle, Oracle),
so the original cap would have blocked the fleet at the 3rd or 4th
extension. The cap also had a latent bug: `app.py:481` hardcoded
`max_corpora=4` instead of importing the `MAX_CORPORA` constant, so
changing the constant alone wouldn't propagate.

**Why it mattered:**
With definer already registered at startup, only 3 slots remained for
extensions. ARISTOTLE uses 1, leaving 2 for the entire future fleet.
Hitting the cap would have forced a corpus-lifecycle feature (unload
unused corpora) as an emergency fix, which is much harder than raising
the constant.

**Resolution:**
- `MAX_CORPORA` raised from 4 to 8 in `foundation/corpus_constants.py`.
  Budget arithmetic: 8 × 3 = 24 connections, leaving 12 of headroom
  under the 36-connection corpus budget (theoretical max is 12).
- `app.py:481` now imports `MAX_CORPORA` and passes it to
  `CorpusRegistry(max_corpora=MAX_CORPORA)` instead of hardcoding `4`.
- `corpus_connection.py:13-15` docstring updated from "shipped at 4"
  to "shipped at 8".
- `test_corpus_foundation.py::test_connection_budget_formula_constants`
  updated to assert `MAX_CORPORA == 8` with explicit headroom checks.

**Verified:** 166 corpus tests + 22 app-factory tests pass. No regressions.

**Related work:**
- ND11 from the 2026-07-23 tech-debt assessment
- R2 (fleet budget risk) from the same assessment — now mitigated

---
