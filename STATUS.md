# AIP Status

**Version:** 0.1.0-alpha
**Architecture Revision:** 6.4
**Last Updated:** 2026-07-23
**Release:** Alpha Test Release
**Project Mode:** ACTIVE DEVELOPMENT — Fusion pipeline + Phase 4.1 + ADR-014 Extension Platform + ADR-008 Multi-Corpus shipped; ARISTOTLE extracted to separate repo; see PLANNED_FEATURES.md

> This document reflects the state after the Fusion pipeline upgrade (Phases 1-3 + 4.1),
> the ADR-008 Multi-Corpus Chunks 1-9, the ADR-014 Phase 0 Extension Platform,
> and the 2026-07-23 tech-debt assessment quick-win pass. The Fusion pipeline is
> feature-complete: retrieval bridge, Judge/Synth split, per-model compression,
> per-model attribution badges, dedicated [models.judge] slot, panel dispatch
> remediation, provenance widget, Context Preparer visualizer, and Vigil
> consistency checker. The Extension Platform (ADR-014 steps 0-6) is shipped:
> ExtensionHost lifecycle, entry-point discovery, Actor Protocol, WorkflowEngine
> wired, `/health/extensions` endpoint. ARISTOTLE — the first extension — lives
> in a separate repo (`AIP_Aristotle`) and is installed via `pip install`.
>
> **Test count:** 4,384 tests collected (as of 2026-07-23). The 60-test platform
> suite (extension lifecycle + actor protocol + import boundary + extended
> workflows + workflow engine wiring + model slot resolver) passes with 0 warnings.
>
> See `PLANNED_FEATURES.md` for the canonical tracker of what's built / planned / deferred.
> See `ROADMAP.md` for the phase plan (note: ADR-015 fleet phases are spec only —
> zero fleet code today). See `TECH_DEBT.md` for the debt register.

## Production Safety Status

Production configuration is **enforced programmatically**. Unsafe configs fail at startup with clear error messages.

### Blocked Configurations (Hard Failures)

| # | Unsafe Configuration | Error Code | Enforced Since |
|---|---|---|---|
| 1 | Production + auth disabled | `PROD_AUTH_DISABLED` | 2026-05-29 |
| 2 | Production + missing POSTGRES_PASSWORD | `PROD_MISSING_DB_PASSWORD` | 2026-05-29 |
| 3 | Production + weak/default password | `PROD_WEAK_DB_PASSWORD` | 2026-05-29 |
| 4 | Production + fixture embedding provider | `PROD_FIXTURE_PROVIDER` | 2026-05-29 |
| 5 | Production + fixture model provider | `PROD_FIXTURE_MODEL_PROVIDER` | 2026-05-29 |
| 6 | Public bind + auth disabled | `PUBLIC_NO_AUTH` | 2026-05-29 |
| 7 | Public bind + weak database password | `PUBLIC_WEAK_SECRET` | 2026-05-29 |

### Allowed Configurations

| Configuration | Condition |
|---|---|
| Laptop + localhost + auth disabled | Default for local development |
| Laptop + localhost + auth enabled | Also valid |
| Production + auth enabled + strong secrets | Required for deployment |

### Unsafe Override

`AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH=true` — allows public-bind + auth-disabled in laptop mode only. Does NOT bypass production auth requirements.

## Module Status

- **Tests:** 1380+ passing (incl. 292 Fusion pipeline tests, 46 Chunk 5 tests, 42 Cycle 4.1 sovereignty tests, 48 Cycle 6 tests, 38 Cycle 6.1 tests), 23 skipped (sqlite_vss extension + pre-existing governance), 1 pre-existing failure (/graph nav route — unrelated)
- **Architecture:** Three-layer (foundation → orchestration → adapter)
- **Default DB path:** `db/state.db` (SQLite, laptop profile)
- **Scaffolding:** ~5-8% overall (MCP dispatch, adaptive router, ScriptNode sandbox)
- **Docker:** Laptop and production profiles with programmatic config validation
- **Lint:** ruff format + ruff check (E, F, W, I) — all passing, blocking in CI
- **Retrieval:** Hybrid (FTS5 + Vector + Corpus) with RRF fusion; configurable channel weights in `aip.config.toml` (`[retrieval.channel_weights]`)
- **Eval harness:** `aip eval retrieval` with --mode flag (hybrid / fts-only / all); baseline comparator available via `--save-baseline`

## UI Cycle 2 — Operator Console Shell (2026-06-11)

UI Cycle 2 created the Operator Console shell with three-region layout (top bar, left nav, main workspace, right rail). The Operator Console (`python -m gui.app`) is now the default GUI entry point. All start scripts (`scripts/start.sh`, `start.sh`, `start-aip.sh`) launch `gui.app`.

| Component | Status | Notes |
|-----------|--------|-------|
| gui/app.py (entry point) | ✅ Active | ui.run() guarded under if __name__; reload defaults to false |
| gui/theme.py (design tokens) | ✅ Active | Extracted from shell.py |
| gui/state.py (per-session state) | ✅ Active | Replaces module-level _state singleton |
| gui/components/ (reusable) | ✅ Active | layout, chat, pills, buttons, modals |
| gui/pages/ (8 pages) | ✅ Active | Dashboard, Ask, Corpus, Retrieval Lab, Wiki (full CODEX Home v1 — UI Cycle 7), Artifacts, Maintenance, Settings |
| gui/panels/ (right rail) | ✅ Active | Dogfood mode, actor status, retrieval health, gates, warnings |
| gui/shell.py (old) | 🧊 Frozen | FROZEN — no new features; preserved for reference only |
| gui/main.py (old) | 🔒 Preserved | PRESERVED — do not modify; retained until Ask Workbench proven |
| gui/archive/main.py | 📦 Archived | Archived copy of original chat frontend |
| GUI import boundary tests | ✅ 10/10 | No orchestration imports; no _state singleton; no silent exception catching |
| Start scripts | ✅ Updated | scripts/start.sh, start.sh, start-aip.sh all reference gui.app |

Ask page preservation verdict: Chat/WebSocket flow fully migrated from shell.py. Direct model fallback labeled "DIRECT MODEL ONLY — NOT DOGFOOD".

## UI Cycle 3 — Status Summary API and Dashboard (2026-06-11)

UI Cycle 3 wires the Operator Console Dashboard to the consolidated `GET /api/v1/status/summary` endpoint. The dashboard now answers "Can I trust AIP right now?" with real data from a single backend call instead of scattered individual API calls. All 9 required dashboard cards are implemented with honest degraded/unavailable states.

| Component | Status | Notes |
|-----------|--------|-------|
| GET /api/v1/status/summary | ✅ Active | Consolidated, secret-safe status summary endpoint (existed in health.py) |
| gui/api_client.get_status_summary() | ✅ Active | New client method calling /status/summary with 8s timeout |
| gui/state.refresh_status_summary() | ✅ Active | Single-call refresh populates status_summary + derived fields |
| gui/status_types.py | ✅ Active | TypedDict schema documenting the stable response shape |
| gui/pages/dashboard.py | ✅ Active | 9 dashboard cards: Dogfood Mode, Backend Health, Corpus Health, Retrieval Health, Actor Health, Embedding/Backfill, Review Queue, Wiki/CODEX, Model Slots, Warnings, Recent Activity |
| gui/panels/right_rail.py | ✅ Active | Right rail consumes status_summary for all sections |
| gui/components/layout.py | ✅ Updated | Delegates to panels.right_rail for consistent data source |
| GUI import boundary tests | ✅ 14/14 | No orchestration imports; no _state singleton; no silent exception catching |
| Layer/import boundary tests | ✅ 17/17 | All adapter/foundation/orchestration boundaries enforced |

Key design decisions:
- **Single-call architecture**: Dashboard and right rail both consume `state.status_summary` from `refresh_status_summary()`, replacing the previous multi-call approach
- **Backend is authoritative**: Dogfood mode comes from `/api/v1/status/summary` rather than client-side heuristic
- **Honest state visibility**: All cards show `UNAVAILABLE`, `NOT CONFIGURED`, `DEGRADED`, or `EMPTY` honestly — never fake healthy
- **No secret exposure**: Model slot API keys show "configured"/"missing" only
- **Import boundary preserved**: gui/ never imports from aip.* — all data comes through api_client

## UI Cycle 4 — Ask Workbench Upgrade (2026-06-11)

UI Cycle 4 upgrades the Ask page from a plain chat interface to an Ask Workbench with answer inspection, source detail drawers, retrieval trace drawers, and save-as-artifact capability.

| Component | Status | Notes |
|-----------|--------|-------|
| gui/components/answer_card.py | ✅ Active | Status strip (retrieval healthy, degraded, lexical only, no sources, direct model only, trace unavailable) + action bar (Show Sources, Show Trace, Save Artifact, Link Wiki [enabled via UI Cycle 7], Model Council [disabled]) |
| gui/components/source_panel.py | ✅ Active | Right drawer showing source title/path, snippet, score, channel per retrieval source |
| gui/components/trace_panel.py | ✅ Active | Right drawer showing retrieval trace details (channels, latency, verdict, degradation, warnings) |
| gui/pages/ask.py (upgraded) | ✅ Active | Uses answer_card instead of plain add_message; wires source_panel, trace_panel, save-artifact; preserves all existing chat/WebSocket/gate behavior |
| gui/api_client.py | ✅ Updated | Added get_retrieval_trace_by_session() and save_turn_as_artifact() |
| gui/status_types.py | ✅ Updated | Added SourceEntry, RetrievalTraceEntry, ChatResponseMetadata, SaveArtifactResponse, SessionTraceResponse TypedDicts |
| POST /api/v1/turns/save-artifact | ✅ Active | New endpoint — creates GENERATED artifact, never auto-approves |
| GET /api/v1/retrieval/traces/session/{session_id} | ✅ Active | New endpoint — fetches most recent trace for a session |
| chat.py response metadata | ✅ Updated | WebSocket chat response now includes trace_available, lexical_only, vector_contributed, direct_model fields |
| ask.py response metadata | ✅ Updated | Ask endpoint response now includes trace_available, lexical_only, vector_contributed fields |
| GUI import boundary tests | ✅ 14/14 | No orchestration imports; no _state singleton; no silent exception catching |
| Layer/import boundary tests | ✅ 17/17 | All adapter/foundation/orchestration boundaries enforced |

**Verdicts:**

- **Answer inspection verdict**: Every answer shows a status strip indicating retrieval health and an action bar with available operations. No answer appears without metadata context.
- **Source panel verdict**: Clicking "Show Sources" opens a right drawer with per-source detail (title/path, snippet, score, channel). Empty source lists show "No sources" honestly — never fake data.
- **Retrieval trace verdict**: Clicking "Show Trace" opens a right drawer with trace details when available. When no trace exists, the UI shows "Trace unavailable" honestly — no fake traces.
- **Save-as-artifact verdict**: "Save Artifact" creates a GENERATED artifact via `POST /api/v1/turns/save-artifact`. No auto-approve — DEFINER review is required before promotion to APPROVED. The response confirms the GENERATED state.
- **Direct model fallback verdict**: Still visibly labeled "DIRECT MODEL ONLY — NOT DOGFOOD" with banner when no model provider is configured.
- **Unwired action honesty verdict**: Model Council button is shown as disabled with "not yet implemented" tooltip. Link Wiki is now enabled via Wiki/CODEX Home v1 (UI Cycle 7). No backend endpoints are faked — the UI is honest about unimplemented features.
- **Import-boundary verdict**: All tests pass (14/14 GUI, 17/17 layer). GUI remains API-first — never imports from aip.orchestration.

## UI Cycle 4.1 — Ask Workbench API Verification and Sovereignty Tests (2026-06-11)

UI Cycle 4.1 is a verification/hardening cycle that adds focused API tests for the Cycle 4 Ask Workbench integration. No production code was changed — only test coverage expanded.

| Test Category | Tests | Status | Notes |
|--------------|-------|--------|-------|
| Save-as-artifact sovereignty | 8 | ✅ ALL PASS | GENERATED only, never APPROVED, never EXPORTED, honest 400/503, stable schema, no secrets |
| Retrieval trace endpoint | 6 | ✅ ALL PASS | Honest not_found, no faking, degraded details preserved, event_store=None handled |
| Ask/chat metadata compatibility | 5 | ✅ ALL PASS | trace_available, lexical_only, vector_contributed, direct_model all present in responses |
| Answer card component | 8 | ✅ ALL PASS | Direct model warning, trace unavailable, lexical only, disabled actions, no auto-approve |
| Import boundary (new components) | 5 | ✅ ALL PASS | AST-verified: answer_card, source_panel, trace_panel, turns route, ask route |
| API client methods | 3 | ✅ ALL PASS | get_retrieval_trace_by_session, save_turn_as_artifact exist and documented |
| Turns route registration | 3 | ✅ ALL PASS | Router importable, mounted in app, correct endpoint path |
| Direct model fallback | 4 | ✅ ALL PASS | degraded=True, normal=False, banner displayed, error-level status |
| **TOTAL** | **42** | **✅ ALL PASS** | |

**Existing test verification:** 112 total tests verified across all categories (42 new + 14 GUI boundary + 8 import boundary + 48 ask/chat/definer).

**Sanitation scan results:**
- 25 pattern hits across 10 files: 17 LEGITIMATE, 8 DOCUMENTED DEBT, 0 BLOCKERS
- No fake trace/source/healthy data found
- No auto-approve mechanisms found
- No aip.orchestration import violations found
- 8 documented debt items: silent except handlers in chat.py and ask.py fallback paths should add logger.debug() (LOW risk, MEDIUM priority for hardening)

**Verdicts:**

- **Save-as-artifact sovereignty**: PASS — Artifacts always GENERATED, never APPROVED/EXPORTED, honest errors, no secret exposure
- **Retrieval trace endpoint**: PASS — Honest not_found when no trace, no data fabrication, degraded channel details preserved
- **Ask/chat metadata compatibility**: PASS — All metadata fields present in ask, retrieve, and WebSocket responses
- **Frontend answer-card**: PASS — Direct model warning at error level, trace unavailable state, disabled actions with tooltips, no auto-approve
- **Direct model fallback**: PASS — Correct True/False flags, banner visible, error-level status
- **Import-boundary**: PASS — All new components AST-verified, no orchestration imports

**Blockers for Beast Counsel**: NONE

## UI Cycle 5 — Beast Counsel Panel v1 (2026-06-11)

UI Cycle 5 adds Beast Counsel Panel v1 to the Ask Workbench. Beast provides an advisory second perspective on each assistant turn: continuity, critique, strategy, risk, librarian notes, and suggested actions. Beast must not silently mutate wiki, artifacts, config, approvals, exports, or gates.

| Component | Status | Notes |
|-----------|--------|-------|
| GET /api/v1/turns/{turn_id}/beast-commentary | ✅ Active | Retrieve existing Beast commentary for a turn + mode (mode query param, Cycle 5.1) |
| POST /api/v1/turns/{turn_id}/beast-commentary/run | ✅ Active | Generate Beast commentary for a turn via Beast model slot |
| BeastCommentaryRequest/Response schemas | ✅ Stable | Pydantic models with all required fields |
| gui/components/beast_panel.py | ✅ Active | Beast Counsel right drawer — 5 modes, mode-aware switching (Cycle 5.1) |
| gui/components/answer_card.py (updated) | ✅ Active | Added "Beast Counsel" button to action bar |
| gui/pages/ask.py (wired) | ✅ Active | BeastPanel instance + _handle_beast_counsel callback |
| gui/api_client.py (updated) | ✅ Active | Added get_beast_commentary(mode=) and run_beast_commentary() |
| gui/status_types.py (updated) | ✅ Active | Added BeastCommentaryResponse and BeastCommentarySuggestedAction TypedDicts |
| Commentary persistence | ✅ GENERATED only | Artifacts in GENERATED state — never auto-approved, never auto-exported |
| Mode persistence | ✅ Distinct per mode | Cycle 5.1: Each mode produces distinct artifact per turn (sha256(turn_id:mode)) |
| No fake commentary | ✅ Verified | Honest not_wired, unavailable, error, not_available states |
| Advisory-only actions | ✅ Verified | All suggested_actions include advisory_only=True, requires_DEFINER_approval=True |
| GUI import boundary | ✅ 16/16 | beast_panel + all GUI modules verified — no aip.orchestration imports |
| Backend import boundary | ✅ Verified | beast_commentary route never imports from aip.orchestration |
| Cycle 5 tests | ✅ 43/43 | Schema, GET, POST, sovereignty, secrets, panel, answer card, import boundary, mode persistence |

**Verdicts:**

- **Beast commentary backend**: PASS — Two stable endpoints with honest degradation. Commentary generated via Beast model slot, persisted as GENERATED artifacts, never auto-approved. Suggested actions are advisory-only with DEFINER approval required.
- **Beast Counsel panel**: PASS — Right drawer with 5 Beast modes. Handles all states: no commentary (with Run button), commentary available, not wired, unavailable, error. No fake commentary.
- **Persistence**: PASS — Commentary stored as GENERATED artifacts via VersionedArtifactStore with ECS state management. Requires DEFINER review before approval.
- **Mode persistence (Cycle 5.1)**: PASS — Each turn+mode produces a distinct artifact ID. Running continuity does not overwrite critique. Re-running same mode creates a new version; GET returns latest. Frontend mode selector fetches commentary for selected mode; switching modes fetches fresh data.
- **Sovereignty**: PASS — No auto-approve, no auto-export, no wiki mutation, no config changes, no model slot changes. Beast only suggests — DEFINER decides.
- **No-fake-commentary**: PASS — All states are honest. not_wired when no model provider, unavailable when no artifact store, error when generation fails, not_available when no commentary exists yet.
- **Ask/chat preservation**: PASS — All existing chat/WebSocket/gate/direct-model behavior preserved. Cycle 4.1 tests (42) still pass.
- **Import-boundary**: PASS — gui/ never imports from aip.orchestration. Backend route never imports from orchestration. All 17 layer boundary tests pass.

**Blockers for Model Council / Wiki / Crosslinks**: None new. Model Council and Wiki CRUD remain out of scope for this cycle.

## UI Cycle 6 — Model Council (2026-06-11)

UI Cycle 6 implements the Model Council feature — an advisory multi-model comparison tool for the Ask Workbench. The Model Council runs the same prompt across all configured text-generation model slots and produces a structured comparison report.

| Component | Status | Notes |
|-----------|--------|-------|
| POST /api/v1/beast/compare-models | ✅ Active | New endpoint — runs multi-model comparison, returns ModelCouncilResponse |
| gui/panels/model_council.py | ✅ Active | Model Council right drawer — side-by-side model responses, consensus/disagreement highlights, advisory recommendations |
| gui/components/answer_card.py (updated) | ✅ Active | "Model Council" button now enabled (was disabled with "not yet implemented" tooltip) |
| gui/pages/ask.py (wired) | ✅ Active | ModelCouncilPanel instance + _handle_model_council callback |
| gui/api_client.py (updated) | ✅ Active | Added compare_models() method |
| gui/status_types.py (updated) | ✅ Active | Added ModelCouncilResponse and ModelSlotComparison TypedDicts |
| Advisory-only enforcement | ✅ Verified | All reports include advisory_only=True, requires_DEFINER_approval=True |
| Graceful degradation | ✅ Verified | insufficient_models when <2 text-gen slots; partial when one model fails |
| Optional artifact persistence | ✅ Active | save_as_artifact=true persists as GENERATED artifact — no auto-approve |
| Embedding slot exclusion | ✅ Verified | Embedding slot excluded from text generation comparison |
| GUI import boundary tests | ✅ Passing | No orchestration imports |
| Layer/import boundary tests | ✅ 17/17 | All adapter/foundation/orchestration boundaries enforced |

**Verdicts:**

- **Model Council backend**: PASS — Stable endpoint with honest degradation. Returns `completed`, `partial`, `insufficient_models`, `unavailable`, or `error` as appropriate. Reports are ADVISORY ONLY.
- **Model Council panel**: PASS — Right drawer with side-by-side comparison. Handles all states: completed, partial, insufficient_models, unavailable, error. No fake comparison data.
- **Advisory-only sovereignty**: PASS — No auto-approve, no auto-export, no config changes, no model slot changes. The council only advises — DEFINER decides.
- **Graceful degradation**: PASS — Fewer than 2 text-gen slots yields `insufficient_models`. One model failure yields `partial` report, not total failure. Embedding slot excluded from comparison.
- **Ask/chat preservation**: PASS — All existing chat/WebSocket/gate/direct-model/Beast Counsel behavior preserved.
- **Import-boundary**: PASS — gui/ never imports from aip.orchestration. All 17 layer boundary tests pass.

**Blockers for Wiki / Crosslinks**: None new. Wiki CRUD and Crosslink System remain out of scope for this cycle.

## UI Cycle 6.1 — Model Council Slot Selector (2026-06-11)

UI Cycle 6.1 adds explicit model slot selection to the Model Council panel, allowing the DEFINER to choose which text-generation model slots participate in the council comparison.

| Component | Status | Notes |
|-----------|--------|-------|
| GET /api/v1/models/text-generation-slots | ✅ Active | New endpoint — returns only text-gen slots (excludes embedding), with sufficient_for_council flag |
| gui/components/model_council_panel.py (updated) | ✅ Active | Slot selector with checkboxes, min-2 enforcement, insufficient_models inline notice |
| gui/api_client.py (updated) | ✅ Active | Added list_text_generation_slots() method |
| gui/status_types.py (updated) | ✅ Active | Added TextGenerationSlotEntry TypedDict |
| Backend selected_model_slots honored | ✅ Verified | Only specified slots are called; embedding excluded even if requested; invalid slots filtered honestly |
| Backend defaults preserved | ✅ Verified | Empty selected_model_slots uses default text-gen slots (synthesis, evaluation, beast) |
| Advisory labeling | ✅ Verified | Reports labeled "ADVISORY ONLY — requires DEFINER review" in header and footer |
| GUI import boundary tests | ✅ Passing | No orchestration imports |
| Cycle 6.1 tests | ✅ 38/38 | All new tests pass; existing Cycle 6 tests (48) still pass |

**Verdicts:**

- **Model slot selector**: PASS — Panel shows checkboxes for each available text-generation slot. At least 2 must be selected to run. Embedding never shown. Unconfigured slots marked "(unconfigured)".
- **Selected slot backend**: PASS — `selected_model_slots` is honored by the compare-models endpoint. Only specified slots are called. Default selection preserved when empty.
- **Embedding exclusion**: PASS — Embedding slot excluded even if explicitly included in `selected_model_slots`. Verified by dedicated test.
- **Insufficient models**: PASS — Honest `insufficient_models` when fewer than 2 text-gen slots available. No faking of available models.
- **Partial failure**: PASS — One model failure yields `partial`/degraded report, not total failure. Per-model results still available.
- **Secret exposure**: PASS — Text-generation-slots endpoint never exposes API keys. No `api_key` field in response.
- **Import-boundary**: PASS — gui/ never imports from aip.orchestration. Backend routes never import from orchestration.
- **Sanitation**: PASS — No fake council/comparison, no auto-approve, no wiki mutation, no bare except-pass, no TODO/FIXME/placeholder.

**Remaining Model Council debt**: Persisted GET endpoint for prior reports (deferred per spec).

**Blockers or dependencies affecting Wiki/CODEX or Crosslinks**: None.

## Fusion Pipeline (2026-06-17) — Feature-Complete

The Fusion pipeline (Panel → Judge-Beast → Synth-Beast) is feature-complete across all 3 phases + Phase 4.1:

### Phase 1 — Retrieval Bridge + Fusion Pipeline
- ✅ Shared `_augmented_context.py` helper — both chat.py and model_council.py call the same retrieval pipeline (fixes the AIP-acronym bug)
- ✅ Two-stage Fusion pipeline (Judge-Beast reads panel outputs → Synth-Beast reads Judge JSON only)
- ✅ Per-call timeouts (panel 30s, Judge 60s, Synth 60s)
- ✅ Engine fallback (`_pick_fusion_engine` picks from successful panel models)
- ✅ Model Label Contract in Judge prompt
- ✅ Multi-select dropdown (models NOT tied to actor slots/roles)
- ✅ `skip_default_slots` flag (panel built ONLY from user's dropdown picks)
- ✅ `assemble_augmented_context` flag (GUI sends when augmented mode is on)
- ✅ Panel dispatch remediation (Bug 1: behavioral system prompt + Bug 2: [PANEL] log markers + DISPATCH_ERROR stubs)

### Phase 2 — Judge/Synth Split + Compression
- ✅ `blind_spots[]` mandatory Judge field
- ✅ `partial_coverage[{models[], point}]` (2 to N-1 models — explicit boundary)
- ✅ `unique_insights[{model, insight}]` with per-model attribution
- ✅ Per-model compression pass (`compress_panel_outputs` flag + `_compress_panel_outputs` helper)
- ✅ Phase 2 test suite (PDF Part IX — 9 net-new tests)

### Phase 3 — Polish
- ✅ Per-model attribution badges on `unique_insights[]` (deterministic 8-color palette)
- ✅ Per-model stance color-coding on `contradictions[]`
- ✅ Dedicated `[models.judge]` TOML slot (preference 0 in `_pick_fusion_engine`)
- ✅ GUI toggle for `compress_panel_outputs` (Compress checkbox in Ask page header)

### Phase 4.1 — UX Features
- ✅ Real-time provenance feedback widget (inline collapsible source strip on answer cards)
- ✅ Context Preparer visualizer (4-step fusion flow diagram in trace panel)
- ✅ Automated consistency-checker (Vigil 5th evaluation pass — cross-turn contradiction detection)

### Test Inventory (13 files, 292+ tests)
| File | Tests | Coverage |
|------|-------|----------|
| `test_model_council_fusion.py` | 22+ | Phase 1 Fusion pipeline |
| `test_model_council_fusion_phase2.py` | 9 | PDF Part IX Phase 2 |
| `test_compress_panel_outputs.py` | 9 | Compression pass |
| `test_phase3_polish.py` | 24 | Badges + judge slot + compress toggle |
| `test_phase4_features.py` | 22 | Provenance + visualizer + consistency |
| `test_panel_dispatch_remediation.py` | 19 | Bug 1 + Bug 2 + acceptance criteria |
| `test_augmented_context_helper.py` | 21 | Retrieval bridge helper |
| `test_send_multicast_retrieval_bridge.py` | 13 | GUI wiring Step 2-B |
| `test_ask_multiselect_dropdown.py` | 37 | Multi-select dropdown |
| `test_coverage_gradient_fix.py` | 10 | Judge prompt boundary + PLANNED_FEATURES |
| `test_model_council_cycle6.py` | 48 | Cycle 6 baseline |
| `test_model_council_cycle6_1.py` | 38 | Cycle 6.1 extensions |
| `test_model_council_library_ids.py` | 11 | OpenRouter library bridge |

## Actor Status (post ADR-011 refactor, post Sprint 6.4)

ADR-011 (2026-06-06) redefined actor role boundaries. All three actors are built and wired.
DEBT-006 (Sexton wiring) is resolved — the new Sexton actor was already wired in app.py; docs
were stale. Chunk 3 (2026-06-11) added honest state reporting and fixed an L4 signature mismatch.

| Actor | Role (ADR-011) | Code State | Wired in app.py | Notes |
|-------|---------------|------------|-----------------|-------|
| Beast | Active synthesis support — context advisory, on-demand wiki draft | ✅ Refactored | ✅ Scheduled (heartbeat only) | Maintenance ops removed per ADR-011 |
| Sexton | Background maintenance — tagging, embedding, wiki, graph, classification | ✅ Built (actors/sexton.py, 2,100+ lines, all 5 ops) | ✅ Scheduled (300s) | All 5 vigil ops wired and running. Reports honest state (active/degraded/disabled/failed). Embedding backfill state machine added (Chunk 4) |
| Vigil | Quality evaluation — synthesis citation quality, retrieval quality gate | ✅ Refactored + retrieval quality gate (Sprint 6.4) | ✅ Scheduled (hourly) | Now includes retrieval quality sampling with alerting |

## Retrieval Quality (Sprint 6.4)

| Component | Status | Notes |
|-----------|--------|-------|
| RetrievalEvalHarness | ✅ Complete | `aip eval retrieval` CLI with --mode flag |
| Golden queries | ✅ Updated | `tests/retrieval_goldens/golden_queries.json` with corpus-mapped IDs |
| Channel weight tuning | ✅ Script | `scripts/retrieval_weight_tuning.py` grid search |
| Config weights | ✅ Wired | `[retrieval.channel_weights]` in `aip.config.toml` → `OrchestratorConfig` |
| Baseline benchmark | ✅ Saved | `docs/retrieval_benchmark_baseline.json` |
| Vigil quality gate | ✅ Wired | Periodic precision@5 sampling with alerting on degradation |
| A/B comparison | ✅ Available | `aip eval retrieval-ab` for side-by-side config comparison |
| Budget tuning | ✅ Available | `aip eval budget-tune` for per-channel budget adjustments |

**Current channel weight defaults:** vector=0.6, fts=0.4, corpus=0.4
**Embedding coverage:** ~1.8% (50/2766 turns). Hybrid improvement over FTS5-only will be
measurable after full embedding pass completes (requires embedding provider configuration and
sustained server uptime for Sexton cycles to process the backlog).
**Embedding backfill state:** Sexton now tracks explicit backfill state (not_configured,
configured_idle, backfill_pending, backfill_running, partially_embedded, embedded, degraded,
failed). Runtime model assignment propagates to Sexton and triggers re-embedding.
Mock/fake providers are reported as "degraded" rather than "healthy".

## Retrieval Honesty (Chunk 5)

Chunk 5 hardens the retrieval pipeline with honest per-channel health reporting. Previously,
unregistered enabled channels reported "failed" and channels returning 0 results reported
"active" with 0-result reasons — both were misleading. The system now distinguishes between
channels that are unavailable, not configured, and genuinely empty.

| Component | Status | Notes |
|-----------|--------|-------|
| ChannelHealthState | ✅ Extended | 3 new states: UNAVAILABLE, NOT_CONFIGURED, EMPTY |
| ChannelHealthDetail | ✅ New | Per-channel structured detail: state, attempted, succeeded, result_count, latency_ms, degradation_reason, error_summary, backend_type, vss_available, vector_count, embedding_provider_configured |
| RetrievalTrace extensions | ✅ Complete | channel_details, lexical_only, vector_contributed, channels_attempted, channels_used |
| Unavailable channel detection | ✅ Wired | get_unavailable_channels(), get_not_configured_channels(), get_empty_channels() |
| Degradation summary | ✅ Updated | Handles unavailable, not_configured, empty states |
| /health retrieval_channel_health | ✅ New | Per-channel registration status, vector backend detail, embedding provider status |
| /health/dogfood channel_states | ✅ New | Per-channel state dict (available, unavailable, not_configured, degraded) |
| Ask pipeline honesty flags | ✅ Wired | lexical_only and vector_contributed flags set per retrieval round |

**Key semantics:**
- Unregistered enabled channels → `NOT_CONFIGURED` (not "failed")
- Channels returning 0 results → `EMPTY` (not "active" with 0-result reason)
- If embedding provider is missing, vector channel state upgraded from DISABLED to `NOT_CONFIGURED`
- `lexical_only=true` when only FTS5 contributed results; `vector_contributed=true` when vector channel returned results

**Tests:** 46 new tests in `tests/test_chunk5_retrieval_honesty_v2.py`

## Runtime Gap Closure (P9)

All P9 runtime gaps have been addressed. No known gap returns fake success from core runtime paths.

| Gap | Status | Implementation | Tests |
|---|---|---|---|
| A. Collaborator password transport | ✅ Fixed | Password moved from query param to request body | test_collaborator_secret_transport.py |
| B. Performance API | ✅ Fixed | Returns BACKEND_UNAVAILABLE when not configured | test_performance_api_contract.py |
| C. Vector migration cursor scan | ✅ Fixed | list_all_ids() added to VectorStore protocol | test_vector_migration_cursor_scan.py |
| D. ECS persistent store | ✅ Fixed | PersistentEcsStore with SQLite backend | test_ecs_persistent_store.py |
| E. MANUAL review queue | ✅ Fixed | ReviewQueueStore with SQLite persistence | test_manual_review_queue.py |
| F. ScriptNode.run | ✅ Disabled | Production mode returns DISABLED; fixture mode safe no-op | test_workflow_script_node_contract.py |
| G. Vigil model-slot re-evaluation | ✅ Fixed | on_model_slot_change marks canonicals for re-evaluation | test_vigil_model_slot_re_evaluation.py |
| H. Sexton intervention derivation | ✅ Fixed | Deterministic rules for A-F + 7 special conditions | test_sexton_intervention_derivation.py |

## Corpus Status (as of 2026-06-10)

| Source Account | Turns | Tagged | Embedded | Notes |
|----------------|-------|--------|----------|-------|
| claude_export_june_2026 | 2,691 | 2,691 | 50 | Primary corpus |
| claude_export_2024_2025 | 52 | 52 | 0 | Previous account |
| aip_v0.1_seed | 23 | 23 | 0 | AIP self-knowledge Q&A |
| **Total** | **2,766** | **2,766** | **50** | 100% tagged, ~1.8% embedded |

Beast domain registry: v1.1 — 28 domains, 17 connectors
Vector store: 50 vectors (from `embed --limit 50`)
Knowledge graph: 36 nodes, 17 edges (worktree)

### Embedding Gap

2,716 turns remain unembedded. The Sexton actor is wired and will process embedding batches
automatically when an embedding provider is configured and the server is running. At ~50 turns
per cycle (every 300s), completing the full embedding pass requires approximately 17 hours of
continuous operation.

## Known Scaffolding

| Surface | What's Real | What's Scaffold |
|---|---|---|
| MCP tool dispatch | Tool listing, autonomy gate enforcement, layering discipline, real dispatch via Protocols | MCP server not wired into runtime; autonomy_gate=None fail-open risk for write/admin tools |
| Adaptive router | Budget enforcement, route existence | update_weights() is fully implemented but never called (dead code); exploration/exploitation is random |
| ScriptNode | Type declaration, fixture mode, YAML parsing | Production execution disabled (returns DISABLED) |
| MCP start/stop | _running flag | No stdio/SSE transport implementation |

## Deployment Profiles

| Profile | Database | Auth | Vector | Models | Bind |
|---|---|---|---|---|---|
| laptop | SQLite | Optional (default: off) | sqlite_vss | Ollama / OpenRouter | 127.0.0.1 |
| production | PostgreSQL | Required | pgvector | API | 0.0.0.0 |

## Required Environment Variables (Production)

- `POSTGRES_PASSWORD` — **Required**. No default fallback.
- `AIP_PROFILE=production` — Activates production validation rules.

## Lint & Formatting Baseline

**Rules:** E (errors), F (pyflakes), W (warnings), I (import sorting)
**Formatter:** ruff format (line-length=120, target=py311)
**CI:** Both `ruff format --check .` and `ruff check .` are blocking gates.

### Contributor Workflow

```bash
uv run ruff format .
uv run ruff check . --fix
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pytest -q --tb=short
```

## Production-Readiness Statement

AIP v0.1 is **alpha software** released for testing and evaluation. It is suitable for local
development, single-user dogfood usage, and alpha tester evaluation. It is **not** production-ready
for deployment with real user data. Known limitations that alpha testers should be aware of:

1. **Embedding coverage is ~1.8%** (50/2,766 turns) — retrieval quality is limited until full
   embedding pass completes (requires embedding provider configuration and sustained uptime).
   FTS5 search works well; hybrid retrieval improvement will be measurable after full embedding.
   Sexton actor is wired and will process embeddings automatically when the provider is available.
2. **MCP tool dispatch is built but not runtime-wired** — real search and approval dispatch exists but is not reachable via API/CLI; autonomy_gate=None fail-open risk must be hardened before wiring
3. **Adaptive router is dead code (update_weights() implemented but never called); exploration/exploitation is random
4. **No sandbox for ScriptNode execution** — production mode returns DISABLED
5. **No review queue web UI for MANUAL mode** — CLI review works (`aip review list/approve/reject`)
6. **Per-component performance metrics are estimated**, not measured

## Pre-existing Test Failures

Two test files have known failures when run in the full suite (they pass in isolation):

- `test_model_slot_resolver.py`: 4 tests fail in full suite due to env var pollution (pass in isolation)
- `test_sqlite_vss_graceful_skip.py`: fails in full suite due to global state pollution (passes in isolation)

## Dogfood Loop

AIP runs its own ingest → ask → review → export pipeline on AIP development
conversations. The project eats its own dog food: architecture decisions, design
discussions, and meeting transcripts are ingested into `db/state.db` and queried
via `aip ask` to ground future design work in prior decisions.

## Knowledge Graph Status

| Component | Status | Notes |
|-----------|--------|-------|
| graph_nodes / graph_edges tables | COMPLETE | state.db, synchronous GraphStore |
| Bridge seed (`--build-from-bridges`) | COMPLETE | 36 nodes, 17 edges (worktree) |
| Entity alias registry | COMPLETE | 22 entries from entity_aliases.md |
| Beast extraction (`--extract`) | COMPLETE (infra) | Requires active Beast LLM API key |
| PPR retrieval | COMPLETE (infra) | GraphRetriever with networkx pagerank |
| Graph API endpoints | COMPLETE | /api/v1/graph/data, /neighbors, /stats |
| Cytoscape.js visualization | COMPLETE | /graph-viz standalone dark-mode page |
| Chat augmentation | COMPLETE | Domain neighbor injection in augmented chat |

## Wiki / CODEX Home Status (UI Cycle 7 + 7.1)

Wiki/CODEX Home v1 is implemented with full article CRUD, backlinks, contradictions, and stale article detection. Cycle 7.1 hardens the storage boundary by routing wiki create/edit through `container.artifact_store` + `container.ecs_store` when available, with an explicitly isolated `sqlite_compat` fallback.

| Component | Status | Notes |
|-----------|--------|-------|
| GET /api/v1/wiki/articles (enhanced) | ✅ Built | Added `search` param, stable WikiArticle schema, `storage_backend` indicator |
| GET /api/v1/wiki/articles/{id} | ✅ Built | Single article with full schema, `storage_backend` indicator |
| POST /api/v1/wiki/articles | ✅ Hardened | Uses `container.artifact_store.write()` + `container.ecs_store.transition()` when available; falls back to `sqlite_compat` |
| PATCH /api/v1/wiki/articles/{id} | ✅ Hardened | Uses `container.artifact_store.write()` when available; does NOT change ECS state |
| GET /api/v1/wiki/backlinks/{id} | ✅ Built | Returns honest empty list when no backlinks or `graph_edges` table doesn't exist (`available: false`) |
| GET /api/v1/wiki/stale | ✅ Built | Returns honest empty list when no stale articles or CODEX tables don't exist |
| GET /api/v1/wiki/contradictions | ✅ Built | Returns honest empty list when no contradictions or CODEX tables don't exist |
| GET /api/v1/wiki/stats (enhanced) | ✅ Built | Now includes `storage_backend` indicator |
| WikiArticle schema | ✅ Complete | Includes `storage_backend` field (artifact_store / sqlite_compat / unavailable) |
| gui/pages/wiki.py | ✅ Active | Full CODEX Home with storage backend indicator in article view |
| gui/components/wiki_article_view.py | ✅ Updated | Shows `Storage: artifact_store` or `Storage: sqlite_compat` badge |

**Storage path (Cycle 7.1):**
- **Preferred**: `container.artifact_store.write()` + `container.ecs_store.transition()` — shared connection pool, validated ECS transitions, event provenance
- **Fallback**: Direct `aiosqlite` to `state.db` (`sqlite_compat`) — explicitly isolated compatibility path
- **Migration plan**: Once container is always available in production, the `sqlite_compat` path can be removed

**Sovereignty guarantees (unchanged):**
- CREATE always sets state to GENERATED — never auto-approved
- EDIT creates new version but does NOT change ECS state
- No fake article content — honest empty/unavailable states
- No secret exposure in wiki responses
- Backlinks/contradictions/stale return empty lists honestly when CODEX tables don't exist
- `storage_backend` reported honestly in every response

**Article identity / crosslink readiness:**
- Article IDs follow stable format: `wiki:{domain}:{title_slug}:{timestamp}`
- Cycle 8 Crosslinks MUST target `article_id`, never raw DB row IDs
- IDs are deterministic, unique, and survive server restarts

**Remaining Wiki/CODEX debt:**
- `sqlite_compat` fallback path (documented, isolated, with migration plan)
- CodexStore/Librarian not wired into container (not trivial — deferred)
- ~~Crosslink System not yet implemented~~ — **RESOLVED (UI Cycle 8)** — full Crosslink System v1 now implemented

## UI Cycle 8 — Crosslink System v1 (2026-06-13)

UI Cycle 8 implements the Crosslink System v1 — knowledge links between first-class objects with full CRUD API, reusable UI components, and wiki sidebar integration. Links default to `suggested`/`approved_by_definer=false` — no auto-approve. Creating a link never mutates linked objects, approves artifacts, or triggers exports.

| Component | Status | Notes |
|-----------|--------|-------|
| GET /api/v1/links | ✅ Built | List knowledge links with optional filters (source/target type/id, relation_type, status, pagination) |
| POST /api/v1/links | ✅ Built | Create knowledge link; status defaults to `suggested`; no linked object mutation |
| PATCH /api/v1/links/{link_id} | ✅ Built | Update link status/relation/metadata; approve requires explicit DEFINER action |
| DELETE /api/v1/links/{link_id} | ✅ Built | Delete link; no linked object mutation |
| GET /api/v1/links/backlinks/{target_type}/{target_id} | ✅ Built | Get all links pointing to an object |
| GET /api/v1/links/forward/{source_type}/{source_id} | ✅ Built | Get all links pointing from an object |
| KnowledgeLinkStore | ✅ Built | aiosqlite adapter-layer helper with dedicated `knowledge_links` table in state.db |
| Valid object types (10) | ✅ Defined | wiki_article, artifact, turn, source, conversation, domain, entity, canonical, graph_node, project |
| Valid relation types (12) | ✅ Defined | supports, contradicts, derives_from, related_to, references, prerequisite_of, supersedes, elaborates, summarizes, context_for, answer_to, question_about |
| storage_backend field | ✅ Built | `"knowledge_link_store"` | `"unavailable"` in all responses |
| gui/components/link_panel.py | ✅ Active | Reusable Link Panel component with status badges, approve/reject/delete actions |
| gui/components/link_editor.py | ✅ Active | Manual link creation dialog with object type/relation type dropdowns |
| Link panel in wiki article view sidebar | ✅ Active | Integrated into wiki article view |
| Answer card "Link Wiki" button | ✅ Wired | No longer "not yet implemented" — opens link creation dialog |
| gui/api_client.py (6 methods) | ✅ Active | list_knowledge_links, create_knowledge_link, update_knowledge_link, delete_knowledge_link, get_link_backlinks, get_link_forward_links |
| gui/status_types.py (6 TypedDicts) | ✅ Active | KnowledgeLink, KnowledgeLinkListResponse, KnowledgeLinkCreateResponse, KnowledgeLinkUpdateResponse, KnowledgeLinkBacklinksResponse, KnowledgeLinkForwardLinksResponse |

**Tests:** 56 tests in `tests/test_crosslink_system_cycle8.py`

**Verdicts:**

- **No auto-approve**: PASS — Links default to `suggested` with `approved_by_definer=false`; approving requires explicit DEFINER action via PATCH.
- **No linked object mutation**: PASS — Creating/updating/deleting links never mutates linked objects, approves artifacts, or triggers exports.
- **Sovereignty**: PASS — Link approval does not approve, export, or mutate any linked objects.
- **Honest unavailable state**: PASS — `storage_backend: "unavailable"` when KnowledgeLinkStore not configured; no fake link data.
- **Import-boundary**: PASS — gui/ never imports from aip.orchestration. All existing layer boundary tests still pass.
- **Existing tests**: PASS — All wiki, import-boundary, and ECS graph tests still pass.

**Remaining Crosslink debt:**
- Auto-suggest links from Beast/Librarian (not yet implemented)
- Bulk link operations (not yet implemented)
- Link visualization in graph view (not yet implemented)

## UI Cycle 11 — Retrieval Lab v1 (2026-06-14)

UI Cycle 11 builds the Retrieval Lab v1 — a standalone retrieval testing interface that runs the retrieval pipeline without dispatching to any model for answer synthesis. The DEFINER can test queries, toggle channels, inspect per-channel results and health, and view fusion/ranking output — all without mutating artifacts, wiki, corpus, or any system state.

| Component | Status | Notes |
|-----------|--------|-------|
| POST /api/v1/retrieval/test | ✅ Built | Standalone retrieval test without answer synthesis. Per-channel results, health, latency, fusion/ranking, selected context. No mutation. |
| GET /api/v1/retrieval/health | ✅ Built | Per-channel retrieval health and availability snapshot |
| gui/pages/retrieval_lab.py | ✅ Active | Full Retrieval Lab page with query panel, channel toggles, health cards, per-channel results, ranked context |
| gui/components/retrieval_query_panel.py | ✅ Active | Query input with channel toggle checkboxes |
| gui/components/retrieval_channel_results.py | ✅ Active | Per-channel results display with latency and hit counts |
| gui/components/retrieval_health_cards.py | ✅ Active | Per-channel health state/availability cards |
| gui/components/retrieval_ranked_context.py | ✅ Active | Ranked context view showing fusion output |
| gui/status_types.py (8 TypedDicts) | ✅ Active | RetrievalTestRequest, RetrievalTestResponse, RetrievalChannelResult, RetrievalHealthResponse, RetrievalChannelHealth, RetrievalEmbeddingCoverage, RetrievalHealthSummary, RetrievalScores |
| gui/api_client.py (3 methods) | ✅ Active | run_retrieval_test, get_retrieval_health, get_retrieval_test_result |

**Tests:** 26 new tests passing

**Verdicts:**

- **No-synthesis verification**: PASS — `POST /retrieval/test` never dispatches to any model for answer synthesis; retrieval only, no mutation of artifacts, wiki, corpus, or system state.
- **No-secret exposure verification**: PASS — No API keys, passwords, or tokens in any retrieval test or health response.
- **Honest empty/unavailable states**: PASS — Unavailable channels report honestly; empty result sets are surfaced as empty, not faked.
- **Sanitation scan**: 0 blockers, all hits legitimate.
- **Existing tests**: PASS — All prior cycle tests still pass.

**Remaining Retrieval Lab debt:**
- `context_budget` parameter not yet wired
- Auto-suggest channel configuration based on corpus state (not yet implemented)

## Bug Registry

All known bugs have been documented. See TECH_DEBT.md for the full debt register including
bug cross-references. DEBT-006/BUG-003 (Sexton not wired) is resolved as of Chunk 3.
Chunk 4 resolved: fake "healthy" embedding status in adapter/health.py now reports honest
not_configured/degraded/failed states based on actual provider type.
Chunk 5 resolved: unregistered channels no longer report "failed" (now "not_configured");
channels returning 0 results no longer report "active" (now "empty"); per-channel structured
health details surfaced in /health and /health/dogfood endpoints.
