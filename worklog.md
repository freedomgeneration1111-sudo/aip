---
Task ID: 10
Agent: Super Z (main)
Task: UI Cycle 10 — Corpus Workbench v1

Work Log:
- Read all required docs: UI_OPERATOR_CONSOLE_ARCHITECTURE, UI_DEVELOPMENT_PROMPT_SEQUENCE, UI_CURRENT_STATE_AUDIT, API_REFERENCE, STATUS, DOGFOOD_READY, ARCHITECTURE
- Inspected all frontend files: corpus.py (placeholder), api_client.py, status_types.py, link_panel.py, shared components, layout, theme
- Inspected all backend files: corpus.py route, ingest.py, sources.py, admin.py (embedding backfill), corpus_turn_store.py, dependencies.py, app.py container wiring
- Determined existing capabilities: 6 corpus endpoints existed (/stats, /embedding-progress, /status, /audit, /backfill-queue, /ingest), 2 admin embedding endpoints, 2 ingestion endpoints, 2 source endpoints
- Identified gaps: no document-level views, no problems aggregation, no corpus-scoped backfill trigger, no retry-failed, no duplicates/stale endpoints
- Added 4 new store methods on CorpusTurnStore: list_documents, count_documents, get_document_detail, get_corpus_problems
- Implemented 8 new backend endpoints in src/aip/adapter/api/routes/corpus.py: GET /corpus/documents, GET /corpus/documents/{source_path}, GET /corpus/problems, GET /corpus/unembedded, POST /corpus/backfill, POST /corpus/retry-failed, GET /corpus/duplicates, GET /corpus/stale
- Added require_definer auth to /corpus/ingest endpoint
- All endpoints return honest unavailable/not_wired when CorpusTurnStore not wired
- Implemented 5 new frontend components: corpus_summary, document_table, document_detail, corpus_actions, corpus_problems
- Replaced placeholder corpus.py page with full Corpus Workbench v1
- Added 12 API client methods in gui/api_client.py
- Added 15 TypedDict classes in gui/status_types.py
- Wrote 30 new tests in tests/test_corpus_workbench_cycle10.py (all passing)
- Verified 14 GUI import boundary tests pass (updated for new components)
- Verified 106 existing tests still pass (import boundary, crosslink, artifact)
- Ran post-execution sanitation: no blockers, 3 documented debt items (except Exception: pass in JSON parsing)
- Updated docs: UI_CURRENT_STATE_AUDIT.md, DOGFOOD_READY.md
- Committed and pushed to main

Stage Summary:
- Corpus Workbench v1 fully built — replaces placeholder with functional corpus management workbench
- Backend: 8 new endpoints + 4 new store methods, Frontend: 5 components + page wiring
- 30 new tests passing, 14 GUI boundary + 106 existing tests still passing
- No fake corpus counts, no fake embedding status, no silent mutation
- Ingest/backfill/retry are explicit DEFINER actions with confirmation dialogs
- Honest unavailable/not_wired/degraded states throughout
- No secrets exposed in any corpus response

Files changed:
- src/aip/adapter/corpus_turn_store.py (4 new methods)
- src/aip/adapter/api/routes/corpus.py (8 new endpoints, require_definer on ingest)
- gui/pages/corpus.py (replaced placeholder with full workbench)
- gui/components/corpus_summary.py (new)
- gui/components/document_table.py (new)
- gui/components/document_detail.py (new)
- gui/components/corpus_actions.py (new)
- gui/components/corpus_problems.py (new)
- gui/api_client.py (12 new methods)
- gui/status_types.py (15 new TypedDicts)
- tests/test_corpus_workbench_cycle10.py (new, 30 tests)
- tests/test_gui_import_boundary.py (updated for new components)
- docs/ui/UI_CURRENT_STATE_AUDIT.md (updated)
- DOGFOOD_READY.md (updated)

Behavior changed:
- Corpus page now shows full workbench instead of placeholder
- Document-level views available (list, detail)
- Problems visible (failed jobs, unembedded, stale, duplicates)
- Ingest/backfill/retry available as explicit DEFINER actions
- All corpus data comes from real backend queries

Corpus backend verdict: 8 new endpoints, all return honest unavailable/not_wired states. No fake data.

Corpus Workbench page verdict: Fully functional with summary cards, document table, detail panel, actions, problems panel. Handles empty corpus, populated corpus, backend unavailable, unembedded chunks, failed jobs, action unavailable states.

Ingest action verdict: Explicit DEFINER action with require_definer auth. Reports honestly. Not wired returns 503. No path returns 400. No silent overwrite.

Backfill action verdict: Explicit DEFINER action. Wraps existing admin backfill path. Returns not_wired if no provider, already_running if in progress, accepted when started. No fake success.

Embedding status honesty verdict: Computed from real store queries. Zero coverage returns 0.0%. No fake healthy.

Problems/failed jobs verdict: Visible via /corpus/problems. Failed ingest jobs, unembedded count, needs_reembed count, duplicate hashes, stale docs all shown. Honest empty lists when no problems.

Crosslink integration verdict: Deferred to integration pass. Link panel not yet integrated into document detail.

Secret exposure verdict: No API keys, passwords, or tokens in any corpus response. Verified by tests.

Import-boundary verdict: All GUI modules import only from gui.* No aip.orchestration imports. 14 GUI boundary + general import boundary tests all pass.

Remaining Corpus Workbench debt:
- Crosslink panel integration in document detail (deferred to integration pass)
- 3 except Exception: pass in corpus_turn_store.py JSON parsing (documented debt, should log at debug level)
- File upload from GUI for ingest (currently path-only; GUI file upload deferred)

Blockers or dependencies affecting Retrieval Lab: None. All corpus data is accessible for retrieval testing.

---
Task ID: 9
Agent: Super Z (main)
Task: UI Cycle 9 — Artifact Workbench v1

Work Log:
- Cloned and inspected AIP_Brain repository (full codebase analysis)
- Read all required docs: UI_OPERATOR_CONSOLE_ARCHITECTURE, UI_DEVELOPMENT_PROMPT_SEQUENCE, UI_CURRENT_STATE_AUDIT, API_REFERENCE, STATUS, DOGFOOD_READY, ARCHITECTURE
- Inspected all frontend files: artifacts.py (placeholder), link_panel.py, link_editor.py, answer_card.py, beast_panel.py, model_council_panel.py, api_client.py, status_types.py
- Inspected all backend files: artifacts.py route (scaffold), review.py route, ecs_graph.py, ecs_store_persistent.py, artifact_store_versioned.py, event_store_queryable.py, review_export_pipeline.py, artifact.py schemas, dependencies.py, app.py
- Analyzed artifact lifecycle: ECS states (SPECIFIED, GENERATED, REVIEWED, APPROVED, REJECTED, SUPERSEDED, FAILED), derived states (NEEDS_REVISION = verdict event, EXPORTED = event, FORCE_EXPORT = event)
- Implemented full backend in src/aip/adapter/api/routes/artifacts.py (12 endpoints replacing scaffold)
- Implemented 4 frontend components: artifact_list, artifact_detail, artifact_review_panel, artifact_state_badge
- Replaced placeholder artifacts.py page with full Artifact Workbench
- Added 10 API client methods in gui/api_client.py
- Added 7 TypedDicts in gui/status_types.py
- Wrote 40 new tests in tests/test_artifact_workbench_cycle9.py (all passing)
- Verified 151 existing tests still pass
- Ran full sanitation sweep — all hits classified as legitimate
- Updated docs: API_REFERENCE.md, UI_CURRENT_STATE_AUDIT.md
- Committed and pushed to main

Stage Summary:
- Artifact Workbench v1 fully built — replaces placeholder with functional lifecycle management
- Backend: 12 endpoints, Frontend: 4 components + page wiring
- 40 new tests passing, 151 existing tests still passing
- No auto-approve, no auto-export, no silent state changes, no fake data
- Force-export visibly exceptional with mandatory audit trail

---
Task ID: 7.1
Agent: Super Z (main)
Task: UI Cycle 7.1 — Wiki Storage Boundary and Artifact Store Alignment

Work Log:
- Cloned and inspected AIP_Brain repository
- Read all specified files: wiki.py route, dependencies.py, app.py, artifact_store_versioned.py, ecs_store_guardrailed.py, ecs_store_persistent.py, beast_commentary.py route, model_council.py route, codex_store.py, status_types.py, api_client.py, wiki.py page, wiki_article_view.py, wiki_article_list.py, wiki_editor.py, test_wiki_ui_cycle7.py, test_layer_discipline.py
- Determined actual current storage path: wiki route used direct aiosqlite to state.db (same tables as container.artifact_store/ecs_store) but bypassed container entirely
- Implemented dual-path storage in wiki.py: preferred artifact_store + ecs_store path with sqlite_compat fallback
- Added _resolve_storage_backend() helper that checks container availability
- Added storage_backend field to all wiki response schemas (WikiArticle, WikiArticleListResponse, WikiArticleCreateResponse, WikiArticleUpdateResponse, WikiBacklinksResponse, WikiContradictionsResponse, WikiStaleResponse)
- Fixed backlinks endpoint to correctly return available=false when graph_edges table doesn't exist
- Updated wiki_article_view.py to show storage backend badge (green for artifact_store, amber for sqlite_compat)
- Wrote 40 new tests in test_wiki_storage_cycle71.py covering all Cycle 7.1 requirements
- All 40 Cycle 7.1 tests pass
- All 26 Cycle 7 wiki tests still pass (backward compatibility verified)
- All 15 import boundary/layer discipline tests pass
- Post-execution sanitation scan: CLEAN — no violations found
- Updated docs/API_REFERENCE.md with comprehensive Cycle 7.1 wiki endpoint documentation
- Updated STATUS.md with Cycle 7.1 wiki status, storage path details, and remaining debt
- Updated DOGFOOD_READY.md with Cycle 7.1 hardening summary
- Updated docs/ui/UI_CURRENT_STATE_AUDIT.md with Cycle 7.1 changes

Stage Summary:
- All Cycle 7.1 objectives achieved
- 66 wiki tests pass total (26 Cycle 7 + 40 Cycle 7.1)
- Sanitation scan clean
- Documentation updated
- Ready for Cycle 8 Crosslinks

---
Task ID: 7.1-verdicts
Agent: Super Z (main)

Wiki storage backend verdict:
  PARTIAL MIGRATION — wiki create/edit now routes through container.artifact_store +
  container.ecs_store when both are available. The sqlite_compat fallback is explicitly
  isolated, documented, and reported via storage_backend field. The migration is safe
  for Cycle 8 Crosslinks because article IDs are stable regardless of which path is used.

Artifact/ECS alignment verdict:
  ALIGNED (when container available) — When storage_backend="artifact_store":
  - CREATE uses container.artifact_store.write() for artifact persistence
  - CREATE uses container.ecs_store.transition() for ECS state (with guardrail validation)
  - CREATE records events via container.event_store.write_event()
  - EDIT uses container.artifact_store.write() for new version
  - EDIT does NOT call ecs_store.transition() (correctly — no state change on edit)
  When storage_backend="sqlite_compat":
  - Same behavioral guarantees but bypasses container's validated ECS transitions
  - Documented as debt with migration plan

Article identity / crosslink readiness verdict:
  READY — Article IDs follow stable format wiki:{domain}:{title_slug}:{timestamp}.
  These IDs are:
  - Deterministic (generated from title, domain, and UTC timestamp)
  - Unique (timestamp prevents collisions)
  - Survive server restarts (stored in artifacts table)
  - Crosslink-safe (no raw DB row IDs, no auto-increment exposure)
  Cycle 8 Crosslinks MUST target these article_id values.

Create/edit sovereignty verdict:
  PRESERVED — Both storage paths maintain all sovereignty guarantees:
  - No auto-approve: CREATE always sets GENERATED state
  - No silent mutation: every write is explicit and logged
  - No fake data: unavailable fields return empty/null honestly
  - No secret exposure: verified by sanitation scan
  - Edit does NOT change ECS state (verified by test)

Backward compatibility verdict:
  MAINTAINED — All 26 Cycle 7 wiki tests still pass without modification.
  The storage_backend field is additive (new field, not renamed/removed).
  API response schemas are backward-compatible superset of Cycle 7 schemas.

Remaining Wiki/CODEX debt:
  1. sqlite_compat fallback path — documented, isolated, with migration plan
     (remove once container is always available in production)
  2. CodexStore/Librarian not wired into container — not trivial, deferred
  3. Crosslink System not yet implemented — Cycle 8
  4. Article revision history browsing UI — version counter exists but no diff view

Blockers or dependencies affecting Cycle 8 Crosslinks:
  NONE — Article identity is stable, storage_backend is honestly reported,
  and the wiki route properly uses container stores when available.
  Crosslinks can safely reference article_id values.
---
Task ID: 11
Agent: main
Task: UI Cycle 11 — Retrieval Lab

Work Log:
- Read all 7 required documentation files
- Inspected all frontend files (retrieval_lab.py, source_panel.py, trace_panel.py, api_client.py, status_types.py, components)
- Inspected all backend retrieval code (routes, orchestrator, channels, registry, schemas, tests)
- Determined v1 scope: use existing _search_sources_with_trace pipeline with channel enable flags, add standalone retrieval test endpoint, add retrieval health endpoint
- Implemented POST /api/v1/retrieval/test — standalone retrieval test without synthesis, with channel selection, per-channel results, health, latency, fusion/ranking, selected context, degraded/failed channel warnings, lexical_only/vector_contributed flags
- Implemented GET /api/v1/retrieval/health — per-channel health (lexical, vector, graph, wiki, procedural, corpus), embedding coverage, vector fallback chain, summary counts
- Created 4 new frontend components: retrieval_query_panel.py, retrieval_channel_results.py, retrieval_health_cards.py, retrieval_ranked_context.py
- Replaced placeholder retrieval_lab.py with full v1 page: query input, channel toggles, health cards, per-channel results, ranked context, trace detail, warnings, honest empty/unavailable states
- Added 8 TypedDicts to status_types.py (RetrievalTestItem, RetrievalChannelResult, RetrievalTestScores, RetrievalTestResponse, RetrievalHealthChannel, RetrievalEmbeddingCoverage, RetrievalHealthSummary, RetrievalHealthResponse)
- Added 3 API client methods (retrieval_test, retrieval_health, get_retrieval_recent_traces)
- Updated gui_import_boundary tests to include new components
- Wrote 26 new tests in test_retrieval_lab_cycle11.py covering all 14 verification items
- All 26 new tests pass
- All 106 tests (Cycle 10 + Cycle 11 + GUI boundary + retrieval orchestrator) pass
- All 17 import boundary / layer discipline tests pass
- Post-execution sanitation search: 0 blockers, all 46 hits legitimate (safety docs, existing schemas, test assertions)
- Updated docs/API_REFERENCE.md, docs/ui/UI_CURRENT_STATE_AUDIT.md, STATUS.md, DOGFOOD_READY.md

Stage Summary:
- 2 new backend endpoints: POST /retrieval/test, GET /retrieval/health
- 4 new frontend components + 1 full page replacement
- 8 TypedDicts, 3 API client methods
- 26 new tests passing
- No blockers, no fake data, no secret exposure, no mutation, no synthesis
- Sanitation clean: 0 fixes needed

---
Task ID: wiki-contract-fix
Agent: Super Z (main)
Task: Diagnose and fix wiki articles not appearing on artifacts page (AGENTS.md Coding Cycle Protocol)

Work Log:
- Followed AGENTS.md Coding Cycle Protocol: Orient → Contract Check → Code → Verify → Document
- Orient: Read 6 AGENTS.md files + 12 source files across full wiki data flow chain
- Contract Check: Found 3 contract mismatches between producer (sexton.py) and consumers
- BUG #1: sexton.py wrote artifact_type="sexton_wiki" but wiki_channel.py and chat.py read "beast_wiki"
- BUG #2: /wiki/articles SQL LIKE patterns didn't include sexton:wiki:*, making existing articles invisible
- BUG #3: _row_to_article didn't classify sexton:wiki:* IDs as "wiki" type
- DB inspection revealed: main db/state.db has NO corpus_turns table at all; demo DB has 60 turns
- Wiki generation also requires: sexton model slot configured, tagged corpus turns, domain registry accessible
- Fixed sexton.py: Changed artifact_type from "sexton_wiki" to "beast_wiki" in writer and reader
- Fixed wiki.py: Added sexton:wiki:% to SQL LIKE conditions and artifact_type classification
- Created scripts/wiki_contract_fix.py: Diagnostic + backfill tool for existing DB
- Created tests/test_wiki_artifact_contract.py: 8 regression tests (all passing)
- Updated AGENTS.md for actors/, adapter/, and gui/ with contract gotchas
- Committed and pushed to fix/operator-console-status-seed-graph

Stage Summary:
- 3 contract mismatches fixed in code (sexton.py, wiki.py)
- 8 regression tests passing
- DB diagnostic revealed corpus not seeded in main DB (user needs to verify DB path)
- Wiki generation requires sexton model slot + tagged turns (may need additional investigation)
- Pushed as commit 401058b

---
Task ID: 11
Agent: Super Z (main)
Task: UI Layout Improvements — remove right sidebar globally, narrow left sidebar, improve artifact workbench + wiki page readability

Work Log:
- Explored codebase: layout.py (3-region shell), right_rail.py (5-section status panel), artifacts.py (workbench), wiki.py + wiki_article_view.py (article page with nested sidebar)
- Removed right rail globally:
  * Made build_right_rail() in layout.py a no-op stub (kept for backward compat)
  * Removed build_right_rail import + call from all 10 pages: dashboard, ask, models, corpus, graph, retrieval_lab, wiki, artifacts, maintenance, settings
  * Relocated right rail info to Maintenance page: added Retrieval Health, Pending Gates, Warnings sections (dogfood mode + actor status already there)
  * Added 3 module-level helpers in maintenance.py: _render_retrieval_health, _render_pending_gates, _render_warnings
- Narrowed left sidebar from 200px to 100px:
  * Changed layout from horizontal (icon+text side-by-side) to vertical (icon on top, small label below)
  * Reduced padding 10px 16px → 8px 4px, icon 18px → 20px, font 12px → 9px
  * This recovers ~100px of horizontal space on every page
- Improved Artifact Workbench readability:
  * artifact_detail.py: Removed content truncation (was 1000 chars), increased content preview max-height 200px → 60vh, font 10px → 13px
  * Removed max-width:300px constraint on title (was truncating with ellipsis)
  * Increased all section paddings from 8px 12px → 16px 20px for breathing room
  * Bumped section label font 8px → 10px, metadata font 9px → 11px
  * artifact_review_panel.py: Bumped button font 10px → 12px, padding 4px 10px → 6px 14px for better click targets
  * artifacts.py: Narrowed left list 360px → 280px, increased detail padding 16px → 24px
- Improved Wiki page readability:
  * wiki_article_view.py: Removed nested 2-column layout (was main content + sidebar)
  * Article content now uses full width
  * Backlinks/Related/Contradictions/Open Questions moved to horizontal card row BELOW the article content
  * Increased article title 20px → 24px, body font 11px → 13px, summary font 12px → 14px, line-height 1.6 → 1.7
  * wiki.py: Narrowed left article list 280px → 240px, reduced page padding 24px → 16px
  * Added _render_related_info_row() to replace _render_sidebar() (old function kept as deprecated wrapper)
- Updated test_ui_integration_cycle14.py: removed build_right_rail from required_calls (was enforcing every page call it)
- Verified: all 14 modified modules import cleanly; 21/22 UI integration tests pass (1 pre-existing failure in test_no_dead_nav_items confirmed unrelated by git stash test)

Stage Summary:
- Right sidebar removed from all 10 pages; status info relocated to Maintenance page
- Left sidebar halved (200px → 100px) with vertical icon+label layout
- Artifact workbench: content no longer truncated, much larger preview area, bigger fonts/buttons
- Wiki page: article content full-width, related info as horizontal cards below
- All changes are pure layout/CSS — no backend or API changes
- Pre-existing test_no_dead_nav_items failure is NOT caused by these changes (verified via git stash)


---
Task ID: 12
Agent: Super Z (main)
Task: Multi-Cast + turn_id + council dialogs — restore multi-model send, fix per-turn actions, kill right drawers

Work Log:
- Orient: read gui/AGENTS.md, src/aip/adapter/AGENTS.md, full chat WebSocket
  flow (chat.py:340-720), auto_save_chat_turn (ingest.py:178-300),
  make_turn_id (foundation/schemas/corpus_turn.py:164), ask.py on_response
  handler, BeastPanel + ModelCouncilPanel right_drawer call sites, answer_card
  action-button contracts, gui/state.py GuiState attributes
- Contract check (the bug is always in the gap):
  Producer: src/aip/adapter/api/routes/chat.py:557-583 built response_payload
  WITHOUT turn_id. Consumer: gui/pages/ask.py on_response built turn_data
  WITHOUT turn_id. Downstream consumers (_handle_beast_counsel,
  _handle_link_wiki, _handle_model_council) all read turn_data["turn_id"],
  got "", and bailed. Fixed both sides of the contract.
- Code change 1 (backend): chat.py now imports make_turn_id from
  aip.foundation.schemas.corpus_turn, computes chat_turn_id = make_turn_id(
  session_id, turn_index) BEFORE building response_payload, includes it in
  every "type":"response" message (both normal and no-provider degraded
  path). The downstream auto_save_chat_turn already uses the same
  make_turn_id(session_id, turn_index) so the surfaced ID matches the
  persisted turn.
- Code change 2 (frontend): ask.py on_response reads resp["turn_id"] into
  turn_data; direct-model fallback path explicitly leaves turn_id="" with a
  comment explaining why (no backend = no persisted turn to point at).
- Code change 3 (BeastPanel): replaced ui.right_drawer() in all three call
  sites (show_counsel, _refresh_for_mode, _run_counsel re-render) with
  ui.dialog() centered modal. Added _open_dialog() helper that creates the
  dialog, assigns it to self._drawer, creates an inner ui.column() with
  max-height:85vh; overflow-y:auto, assigns it to self._content_container,
  and calls dialog.open(). All subsequent render methods add to
  self._content_container (not self._drawer) so children appear inside the
  scrollable region rather than as siblings of it. close() clears both
  _drawer and _content_container.
- Code change 4 (ModelCouncilPanel): same ui.right_drawer() -> ui.dialog()
  conversion across all three call sites (show_council, _run_comparison
  loading state, _run_comparison results state). Same _open_dialog() helper
  pattern. _render_initial_state now opens with `with self._content_container:`
  instead of `with ui.column().classes("w-full").style("padding:16px;")`.
- Code change 5 (Multi-Cast UI + handler): added GuiState.multicast_enabled
  and multicast_selected_slots fields. Added Multi-Cast toggle button in
  Ask page chat header. When toggled on, a slot selection row appears
  below the header with checkboxes for every text-gen slot (excluding
  embedding). Defaults pre-populated to ["synthesis", "evaluation", "beast"]
  if those slot names are present. send_fn now routes through
  _dispatch_send which checks state.multicast_enabled: if True and ≥2 slots
  selected and backend reachable, calls _send_multicast; otherwise falls
  back to the normal _send_prompt. _send_multicast calls
  api_client.run_model_council(prompt=prompt, turn_id="", session_id=...,
  existing_answer="", sources=[], selected_model_slots=selected_slots),
  then renders each per-model answer as its own answer card and a final
  Beast synthesis card (markdown with sections for Convergence,
  Disagreements, Unique Contributions, Risks, Beast Conclusion,
  Recommended Decision). The synthesis is ADVISORY ONLY — never auto-saved
  or auto-approved.
- Verify: all 5 modified modules (chat.py, ask.py, state.py, beast_panel.py,
  model_council_panel.py) compile cleanly. UI integration test suite: 21/22
  pass (the 1 failure is test_no_dead_nav_items, pre-existing and confirmed
  unrelated via git stash). Layer discipline + GUI import boundary tests
  show only pre-existing aiosqlite-not-installed environment failures.
- Document: updated src/aip/adapter/AGENTS.md (added "Chat WebSocket
  response MUST include turn_id" gotcha + Multi-Cast Last Cycle entry).
  Updated gui/AGENTS.md with five new gotchas: left_drawer width prop,
  right_drawer forbidden, element.style() additivity, turn_id contract,
  Multi-Cast send path. Updated Last Cycle section.

Stage Summary:
- 5 concerns addressed in one pass per the coding protocol
- turn_id contract gap fixed on both producer (chat.py) and consumer (ask.py)
  sides — Beast Counsel, Link Wiki, and Model Council turn linkage all work now
- BeastPanel + ModelCouncilPanel converted from right_drawer to centered dialog
  (max-width:900px / 1000px, scrollable inner column, no right sidebar anywhere)
- Multi-Cast pre-send UX restored: toggle in chat header, slot checkboxes,
  _dispatch_send routes to _send_multicast which calls run_model_council
  directly and renders per-model answer cards + Beast synthesis card
- All changes are advisory-only by default; no auto-approve, auto-save, or
  auto-export. Multi-Cast synthesis explicitly marked ADVISORY ONLY.
- Pre-existing test_no_dead_nav_items failure confirmed unrelated (git stash)
- All 5 modified modules compile cleanly

Files changed:
- src/aip/adapter/api/routes/chat.py (turn_id in response_payload, both paths)
- gui/pages/ask.py (turn_data turn_id, Multi-Cast toggle + slot row,
  _toggle_multicast, _toggle_multicast_slot, _dispatch_send, _send_multicast,
  updated docstring)
- gui/components/beast_panel.py (_open_dialog helper, all 3 right_drawer
  sites converted to ui.dialog, _content_container pattern)
- gui/components/model_council_panel.py (same dialog conversion pattern)
- gui/state.py (multicast_enabled + multicast_selected_slots fields)
- src/aip/adapter/AGENTS.md (turn_id gotcha + Last Cycle entry)
- gui/AGENTS.md (5 new gotchas + Last Cycle entry)

---
Task ID: fusion-phase-status-2026-06-17
Agent: Super Z (main)
Task: Take stock of multi-model synthesis upgrade — create fusion phase checklist (Phase 1/2/3) with done/not-done status, write Phase 2 coding plan, report in-thread.

Work Log:
- Re-read PDF report (Fusion for AIP Multimodel Synthesis, 28 pages) parts V/IX/X for the original phase definitions
- Verified current code state against the PDF's plan:
  * `routes/_augmented_context.py` — DOES NOT EXIST (Phase 1 retrieval bridge was never extracted)
  * `gui/pages/ask.py::_send_multicast` still sends `turn_id=""` and `sources=[]`
  * `model_council.py` has no retrieval call — `compare_models` runs the panel with the bare prompt
  * BUT: Phase 1 Fusion pipeline (Judge-Beast → Synth-Beast) IS implemented (`_pick_fusion_engine`, `_call_fusion_engine`, `_JUDGE_CALL_TIMEOUT_S`, `_SYNTH_CALL_TIMEOUT_S`)
  * AND: Judge JSON schema (consensus/contradictions/partial_coverage/unique_insights/blind_spots) IS in the prompt and parsed into `judge_analysis`
  * AND: GUI rendering of `judge_analysis` IS in `model_council_panel.py::_render_judge_analysis` and `ask.py::_format_judge_analysis_markdown`
  * AND: Per-model attribution rendering IS in place (model labels with the LABEL CONTRACT from Fix C)
- Phase 2 gap analysis: the Judge/Synth SPLIT is already done (Phase 1 shipped it as part of the Fusion pipeline). What's actually missing for Phase 2 is:
  * Augmented retrieval bridge (was supposed to be Phase 1 but never shipped — the AIP-acronym bug is STILL OPEN)
  * Per-model compression pass before the Judge reads panel outputs (Improvement #5 from the PDF)
  * Phase 2 unit/integration tests from the PDF's Part IX testing strategy
- Phase 3 status: nothing done (compression pass, dedicated `[models.judge]` slot, per-model attribution badges polish)
- Read root AGENTS.md coding cycle protocol (Orient → Contract Check → Code → Verify → Document)
- Read adapter AGENTS.md for the POST /beast/compare-models contract
- Read actors AGENTS.md for the beast-slot-vs-Beast-actor distinction

Stage Summary:
- Phase 1 Fusion pipeline: SHIPPED (commits 7b63cb1, f01428e, 828bbd4, 2a1fd05)
- Phase 1 retrieval bridge: NEVER SHIPPED — the AIP-acronym bug from the PDF's Part I is still open. Multi-Cast in augmented mode still sends `sources=[]` and `turn_id=""`. This is the single highest-leverage fix still pending.
- Phase 2 Judge/Synth split: ALREADY DONE as part of Phase 1's Fusion pipeline (the pipeline already does Panel → Judge → Synth with strict JSON contract). What PDF called "Phase 2" was structurally merged into the Phase 1 Fusion commit.
- Phase 2 remaining work: (a) augmented retrieval bridge, (b) per-model compression pass, (c) PDF Part IX test suite
- Phase 3: NOT STARTED (per-model attribution badges polish, dedicated `[models.judge]` slot, optional config)
- Detailed Phase 2 coding plan written below in the "Phase 2 Coding Plan" section of this entry.

---

# Fusion Phase Checklist (as of 2026-06-17, commit 2a1fd05)

## Phase 1 — Retrieval Bridge + Fusion Pipeline (per PDF Part X)

### Retrieval Bridge (PDF File A + File C — the AIP-acronym bug fix)
- [ ] **NOT DONE** — Extract `_assemble_augmented_context()` shared helper from `chat.py` L225-441 into `src/aip/adapter/api/routes/_augmented_context.py` (NEW file). The 220-line inline retrieval block (domain resolution, `_search_corpus_turns`, `_get_wiki_overview`, `_get_graph_neighbors`, definer profile, orchestrator fallback) is still inline in `chat.py`.
- [ ] **NOT DONE** — Refactor `chat.py` augmented branch to call the new helper (4-line replacement per PDF Part VI).
- [ ] **NOT DONE** — Wire `model_council.py::compare_models` to call the helper when `request.assemble_augmented_context=True` and `request.turn_id` is set, prepending the augmented system messages to each panel call.
- [ ] **NOT DONE** — Add `assemble_augmented_context: bool = False` field to `ModelCouncilRequest` (additive, safe default).
- [ ] **NOT DONE** — Add `assemble_augmented_context=(state.current_mode == 'augmented')` to `_send_multicast` in `gui/pages/ask.py`. Currently passes nothing (defaults False).
- [ ] **NOT DONE** — Add `turn_id=<real turn id>` to `_send_multicast` in `gui/pages/ask.py`. Currently passes `turn_id=""`.
- [ ] **NOT DONE** — Update `gui/api_client.py::run_model_council` to forward `assemble_augmented_context`.
- [ ] **NOT DONE** — Update `src/aip/adapter/AGENTS.md` with the new field + retrieval bridge contract.
- [ ] **NOT DONE** — Update `gui/AGENTS.md` Multi-Model dropdown section to note augmented bridge.
- [ ] **NOT DONE** — Tests from PDF Part IX: `test_assemble_augmented_context_helper_extracts_corpus_wiki_graph`, `test_assemble_augmented_context_returns_empty_when_no_stores`, `test_assemble_augmented_context_skipped_when_turn_id_missing`.

### Fusion Pipeline (PDF File B — Panel → Judge → Synth)
- [x] **DONE** — Two-stage pipeline (Judge-Beast reads panel outputs, Synth-Beast reads Judge JSON only) — commit `7b63cb1`
- [x] **DONE** — `fusion_answer` field on `ModelCouncilResponse` — commit `7b63cb1`
- [x] **DONE** — `judge_analysis` field on `ModelCouncilResponse` — commit `7b63cb1`
- [x] **DONE** — `_PANEL_CALL_TIMEOUT_S`, `_JUDGE_CALL_TIMEOUT_S`, `_SYNTH_CALL_TIMEOUT_S` per-call timeouts — commit `f01428e` (Fix A)
- [x] **DONE** — Engine fallback when panel models fail (`_pick_fusion_engine` picks successful panelist for Judge+Synth) — commit `828bbd4` (Fix D)
- [x] **DONE** — Model Label Contract in Judge prompt (Fix C) — commit `f01428e`

### Phase 1 GUI rendering (PDF File D)
- [x] **DONE** — `model_council_panel.py::_render_judge_analysis` renders the 6 Judge fields (consensus, contradictions stance table, partial_coverage, unique_insights, blind_spots, raw JSON) — commit `f01428e` (Fix B)
- [x] **DONE** — `ask.py::_format_judge_analysis_markdown` renders the same in markdown for the answer card — commit `f01428e` (Fix B)
- [x] **DONE** — `fusion_answer` headline rendering in both panel and answer card — commit `7b63cb1`

### Phase 1 tests (PDF Part IX)
- [x] **DONE** — `test_model_council_fusion.py` (22+ tests covering schema, two-stage call, fusion_answer, judge_analysis, beast_conclusion mirror, synth reads only JSON, Judge/Synth failures, single-model-success guard, advisory_only, no auto-approve, no secret exposure) — commit `7b63cb1`, expanded `f01428e`
- [x] **DONE** — `test_model_council_library_ids.py` (11 tests covering the OpenRouter library bridge) — commit `628d300`
- [x] **DONE** — `test_ask_multiselect_dropdown.py` (37 tests covering the multi-select dropdown + skip_default_slots) — commit `2a1fd05`

## Phase 2 — Judge/Synth Split (per PDF Part X)

The PDF's Phase 2 was "ship the three-role Panel → Judge → Synth pipeline as an opt-in alternative to bare Beast synthesis." **The structural work is already done** — Phase 1's Fusion commit (`7b63cb1`) shipped the Judge/Synth split as the DEFAULT behavior (not opt-in). What's left:

### Already done (was Phase 2 scope, shipped as Phase 1)
- [x] **DONE** — Split Judge and Synthesizer into two sequential `model_provider.call()` invocations (PDF Improvement #1) — commit `7b63cb1`
- [x] **DONE** — `blind_spots[]` as mandatory Judge JSON field (PDF Improvement #2) — commit `7b63cb1`
- [x] **DONE** — `partial_coverage[{models[], point}]` Judge JSON field (PDF Improvement #3) — commit `7b63cb1`
- [x] **DONE** — `unique_insights[{model, insight}]` with model attribution (PDF Improvement #4) — commit `7b63cb1`

### Still pending (Phase 2 scope, NOT shipped)
- [ ] **NOT DONE** — Augmented retrieval bridge (was Phase 1 scope but never shipped; Phase 2 doesn't make sense without it — the panel still answers blind without corpus context). See Phase 1 checklist above.
- [ ] **NOT DONE** — Per-model compression pass before Judge reads panel outputs (PDF Improvement #5 — Phase 3 in PDF but functionally belongs with Phase 2 because long panel outputs blow the Judge's context window today).
- [ ] **NOT DONE** — PDF Part IX Phase 2 tests:
  - [ ] `test_fusion_mode_judge_json_parse` (mock valid 6-field judge JSON, verify JudgeAnalysis parsed, fusion_status='completed')
  - [ ] `test_fusion_mode_judge_json_parse_failure_fallback` (mock malformed JSON, verify raw_judge_text + degraded status, synth still runs)
  - [ ] `test_fusion_mode_passes_augmented_context_to_each_panel_model` (mock panel calls, verify each gets the same augmented prefix) — blocked on retrieval bridge
  - [ ] `test_fusion_mode_per_model_results_still_in_response` (verify selected_models is populated alongside judge_analysis + fusion_answer)
  - [ ] `test_compare_mode_unchanged_when_mode_compare` (verify default mode='compare' uses bare Beast synthesis — N/A since mode='fusion' is now the default; need to update test to reflect the merged pipeline)
  - [ ] `test_fusion_artifact_persistence` (save_as_artifact=True with mode='fusion', verify council artifact stores panel_results + judge_analysis + fusion_answer; ECS transition to GENERATED only)
- [ ] **NOT DONE** — PDF Part IX integration tests:
  - [ ] `test_fusion_end_to_end_with_real_retrieval` — blocked on retrieval bridge
  - [ ] `test_fusion_with_no_corpus` — blocked on retrieval bridge
  - [ ] `test_fusion_with_partial_panel_failure` (4 selected models, 1 fails, verify successful_count=3, fusion_status='completed', failed_models has 1 entry, response.status='partial')

## Phase 3 — Polish (per PDF Part X)

- [ ] **NOT DONE** — Per-model attribution badges on `unique_insights[]` in `ModelCouncilPanel` (render model label as a colored badge next to each insight)
- [ ] **NOT DONE** — Per-model stance tables on `contradictions[]` in `ModelCouncilPanel` (already rendered as a table; polish = color-code stances, sort by topic)
- [ ] **NOT DONE** — Compression pass before Judge (improvement #5 from PDF; functionally Phase 2 but listed as Phase 3 in the PDF's rollout)
- [ ] **NOT DONE** — Optional dedicated `[models.judge]` TOML slot (config-only change; `ModelSlotResolver` already handles new slots). Would let the user pick a different model for judging vs the Beast actor's maintenance calls.
- [ ] **NOT DONE** — Manual review of GUI rendering on a real fusion run (PDF Phase 3 ship criteria)

---

# Phase 2 Coding Plan

## Goal
Ship the remaining Phase 2 deliverables so Multi-Cast in augmented mode actually sees the corpus (fixes the original AIP-acronym bug from the PDF's Part I) AND the Judge can handle long panel outputs without context overflow.

## Sequencing (dependency-ordered, each step independently shippable + revertable)

### Step 2-A — Extract the retrieval helper (the AIP-acronym bug fix)
**Why first:** This is the single highest-leverage change. It fixes the AIP-acronym bug AND unlocks augmented Multi-Cast without duplicating retrieval logic. Everything else in Phase 2 depends on this being available.

**Files:**
- NEW: `src/aip/adapter/api/routes/_augmented_context.py`
- MODIFY: `src/aip/adapter/api/routes/chat.py` (replace the inline 220-line block with 4-line helper call)
- MODIFY: `src/aip/adapter/api/routes/model_council.py` (call helper when `request.assemble_augmented_context=True` and `request.turn_id` set)
- MODIFY: `src/aip/adapter/api/routes/model_council.py` — add `assemble_augmented_context: bool = False` to `ModelCouncilRequest` (additive, safe default)

**Helper signature (per PDF Part VI):**

    # src/aip/adapter/api/routes/_augmented_context.py
    from dataclasses import dataclass, field
    from typing import Any

    @dataclass
    class AugmentedContext:
        messages: list[dict] = field(default_factory=list)  # system msgs to PREPEND
        sources: list[dict] = field(default_factory=list)   # for response payload
        trace: Any = None                                    # RetrievalTrace | None
        domain: str | None = None
        assembled: bool = False                              # False = caller proceeds with bare prompt

    async def assemble_augmented_context(
        content: str,
        session_id: str,
        container: Any,
        *,
        session_meta: dict | None = None,
    ) -> AugmentedContext:
        '''Assemble augmented context (corpus + wiki + graph + definer).
        Shared helper used by both routes/chat.py and routes/model_council.py.
        Mirrors the inline block that lived at chat.py L225-441 before extraction.
        Behavior is identical to that block; this is a pure refactor.
        '''

**Layer discipline (per root AGENTS.md):** the new module lives in `adapter/api/routes/` alongside `chat.py` and `model_council.py`. Imports only from `adapter` and `foundation`, matching the existing route module pattern. The helper accesses the container's stores via the existing Protocol interfaces — no new orchestration imports.

**Contract check (per root AGENTS.md step 2):**
- Producer (`_augmented_context.py`) exposes: `AugmentedContext.messages: list[dict]`, `AugmentedContext.sources: list[dict]`, `AugmentedContext.assembled: bool`
- Consumer 1 (`chat.py`): currently builds `messages: list[dict]`, `response_sources: list[dict]` inline → must read `aug.messages`, `aug.sources`
- Consumer 2 (`model_council.py`): currently builds `augmented_messages: list[dict]` from `request.sources` → must call the helper and prepend `aug.messages` to each panel call's user prompt

**Verify:**
- Run `pytest tests/test_ask.py -v` (the retrieval helper extraction must NOT break single-model augmented chat — this is the existing regression surface)
- Run `pytest tests/test_model_council_*.py -v` (existing tests must still pass — they don't exercise augmented context, so default `assemble_augmented_context=False` keeps them green)
- New tests (Step 2-C) will cover the helper directly

**Document:**
- `src/aip/adapter/AGENTS.md`: add `assemble_augmented_context` field to the `POST /beast/compare-models` contract section; document the new shared helper contract + the AugmentedContext dataclass fields
- `src/aip/orchestration/AGENTS.md`: note that the RetrievalOrchestrator is now consumed by both `chat.py` (via the helper) and `model_council.py` (via the helper) — no orchestration code changes, documentation update only

### Step 2-B — Wire the GUI to pass turn_id + assemble_augmented_context flag
**Why second:** depends on Step 2-A's helper being callable from the backend. Once 2-A ships, the GUI needs to send the flag + a real turn_id so the backend actually runs retrieval.

**Files:**
- MODIFY: `gui/pages/ask.py::_send_multicast` — compute a real turn_id from the session (e.g. `make_turn_id(session_id, turn_index)` or just `session_id` if no per-turn counter exists), pass `assemble_augmented_context=(state.current_mode == 'augmented')` and `turn_id=<computed>`
- MODIFY: `gui/api_client.py::run_model_council` — add `assemble_augmented_context: bool = False` param, include in POST payload

**Contract check:**
- Producer (`gui/api_client.py`) payload includes `"assemble_augmented_context": True/False` and `"turn_id": "<real id>"`
- Consumer (`model_council.py::compare_models`) reads `request.assemble_augmented_context` and `request.turn_id` — both already on the request model after Step 2-A

**Verify:**
- Manual dogfood: with Multi-Cast ON + Augmented ON + corpus ingested, send "What does AIP stand for?" — panel models should now correctly identify AIP as AI Poiesis (currently they answer blind). This is the PDF's Phase 1 ship criteria.
- New test (Step 2-C): `test_fusion_mode_passes_augmented_context_to_each_panel_model` (mock `_call_model_slot` and `_call_library_model_id` to capture their messages arg, verify each panel call's messages list starts with the same augmented_messages prefix, verify the user message is the bare prompt)

**Document:**
- `gui/AGENTS.md`: update the Multi-Model dropdown section — `assemble_augmented_context=True` is now sent when state.current_mode == 'augmented'; `turn_id` is now populated for Multi-Cast (previously empty) so per-turn actions on Multi-Cast results work

### Step 2-C — Phase 2 test suite (PDF Part IX)
**Why third:** depends on 2-A and 2-B being shippable. The PDF's Part IX testing strategy is the contract for Phase 2 acceptance.

**Files:**
- NEW: `tests/test_model_council_fusion_phase2.py` (or extend `tests/test_model_council_fusion.py` — prefer a new file to keep the Phase 1 tests stable)

**Tests to add (per PDF Part IX):**
1. `test_assemble_augmented_context_helper_extracts_corpus_wiki_graph` — mock `corpus_turn_store`, `artifact_store`, `ecs_store`, `graph_store`; verify the helper returns messages containing corpus, wiki, and graph blocks; verify sources list matches
2. `test_assemble_augmented_context_returns_empty_when_no_stores` — when `container.corpus_turn_store` and `container.lexical_store` are both None, helper returns `AugmentedContext(assembled=False)` with empty messages/sources; no exception
3. `test_assemble_augmented_context_skipped_when_turn_id_missing` — `assemble_augmented_context=True` but `turn_id=""`: retrieval does NOT run; panel calls proceed with bare prompt; fusion still runs (judge + synth over bare-prompt panel outputs); graceful degradation
4. `test_fusion_mode_judge_json_parse` — mock valid 6-field judge JSON; verify `JudgeAnalysis` parsed correctly; `fusion_status='completed'`; `fusion_answer` is the synth output
5. `test_fusion_mode_judge_json_parse_failure_fallback` — mock judge returning malformed JSON; verify raw text stored in `judge_analysis.raw_judge_text` (NOTE: this field may not exist yet — `JudgeAnalysis` is currently just `dict[str, Any]`; may need to add it as a top-level field on the response, OR document that the raw text lives under `judge_analysis["raw_judge_text"]`); `fusion_status='degraded'`; synth still runs on raw text; pipeline does not raise
6. `test_fusion_mode_passes_augmented_context_to_each_panel_model` — mocked per Step 2-B verify section above
7. `test_fusion_mode_per_model_results_still_in_response` — verify `selected_models` is populated alongside `judge_analysis` and `fusion_answer`; each `PerModelResult` has `status='completed'` and answer text; the user's parallel-comparison requirement is structurally enforced
8. `test_compare_mode_unchanged_when_mode_compare` — N/A in the current architecture (Phase 1 already made `mode='fusion'` the default; there's no separate `mode='compare'` path). UPDATE this test's intent to: "verify default behavior is the Fusion pipeline; legacy `beast_conclusion` field is mirrored from `fusion_answer` for back-compat with old consumers" — this is already covered by `test_beast_conclusion_mirrored_to_fusion_answer` in `test_model_council_fusion.py`; skip
9. `test_fusion_artifact_persistence` — `save_as_artifact=True` with mode='fusion'; verify the council artifact stores `panel_results + judge_analysis + fusion_answer` in its content JSON; verify ECS transition to GENERATED (never APPROVED — DEFINER gate still required)
10. `test_fusion_end_to_end_with_real_retrieval` — use the existing CI fixture corpus (the same one used by `tests/test_ask.py`); run a fusion request; verify augmented context appears in the panel call messages; mock `model_provider.call` but use real retrieval; verifies the retrieval bridge works against real stores
11. `test_fusion_with_no_corpus` — fresh DB with no ingested turns; run a fusion request with `assemble_augmented_context=True`; verify the helper returns `assembled=False`; panel calls proceed with bare prompt; fusion still produces a synthesis
12. `test_fusion_with_partial_panel_failure` — 4 selected models, 1 fails; verify `successful_count=3`, `fusion_status='completed'` (judge + synth run on 3 successful outputs), `failed_models` list has 1 entry, `response.status='partial'` (NOTE: this may already be covered by Fix D tests — verify before writing a new one)

**Verify:**
- `pytest tests/test_model_council_fusion_phase2.py -v` — all new tests pass
- `pytest tests/test_model_council_fusion.py tests/test_model_council_cycle6.py tests/test_model_council_cycle6_1.py tests/test_model_council_library_ids.py tests/test_ask_multiselect_dropdown.py -v` — no regressions

**Document:**
- `tests/AGENTS.md`: add the new test file to the test inventory; note that Phase 2 acceptance = all 12 tests pass

### Step 2-D — Per-model compression pass (PDF Improvement #5)
**Why last:** Phase 2 ships without this if Step 2-A/B/C are done — compression is an enhancement, not a blocker. The PDF lists it as Phase 3, but functionally it belongs with Phase 2 because long panel outputs (4+ models × 2000 chars each) can blow the Judge's context window today.

**Files:**
- MODIFY: `src/aip/adapter/api/routes/model_council.py` — add a private `_compress_panel_outputs(per_model_results, container) -> list[dict]` helper that runs a single `model_provider.call()` per panelist to summarize each output to 5-8 key claims before the Judge reads them; gate behind a `compress_panel_outputs: bool = False` field on `ModelCouncilRequest` (additive, safe default — opt-in to preserve current behavior)
- MODIFY: `model_council.py::compare_models` — when `request.compress_panel_outputs=True`, run the compression pass after the panel gather but before the Judge call; pass the compressed claims to the Judge instead of the raw outputs

**Contract check:**
- Producer (`_compress_panel_outputs`) returns `list[dict]` with shape `[{model, claims: list[str]}]`
- Consumer (Judge prompt construction in `compare_models`) reads the compressed claims when present, falls back to raw `panel_results` answers when absent

**Verify:**
- New test: `test_compress_panel_outputs_summarizes_each_model` — mock the compression model call, verify each panelist's output is reduced to 5-8 claims, verify the Judge prompt contains the compressed claims (not the raw outputs)
- New test: `test_compress_panel_outputs_disabled_by_default` — `compress_panel_outputs=False` (default); verify the Judge reads the raw panel outputs (current behavior preserved)
- Manual dogfood: run a Multi-Cast with 4+ models that produce long answers; verify the Judge still produces a valid 6-field JSON (currently at risk of context overflow on long panel outputs)

**Document:**
- `src/aip/adapter/AGENTS.md`: add `compress_panel_outputs` field to the `POST /beast/compare-models` contract; document the compression pass behavior

## What needs to be done (summary, in priority order)

1. **Step 2-A** (highest leverage, fixes the AIP-acronym bug): extract `_augmented_context.py` shared helper from `chat.py` L225-441; add `assemble_augmented_context: bool = False` field to `ModelCouncilRequest`; wire `model_council.py::compare_models` to call the helper. NEW file + 3 file modifications. Estimated effort: ~half day.
2. **Step 2-B** (unlocks augmented Multi-Cast): wire `gui/pages/ask.py::_send_multicast` to send `turn_id=<real>` + `assemble_augmented_context=(state.current_mode == 'augmented')`; add the param to `gui/api_client.py::run_model_council`. 2 file modifications. Estimated effort: ~1 hour.
3. **Step 2-C** (Phase 2 acceptance): write the 12-test suite from PDF Part IX (skipping #8 which is N/A and verifying #12 isn't already covered). NEW test file. Estimated effort: ~half day.
4. **Step 2-D** (optional enhancement, can defer to Phase 3): per-model compression pass before Judge. 1 file modification + 2 new tests. Estimated effort: ~2 hours.

**Recommended ship order:** 2-A → 2-B → manual dogfood (verify AIP-acronym fix) → 2-C → 2-D (or defer to Phase 3).

## References (per coding protocol step 1: Orient)
- Root `AGENTS.md` — coding cycle protocol (Orient → Contract Check → Code → Verify → Document)
- `src/aip/adapter/AGENTS.md` — `POST /beast/compare-models` contract; Phase 1 Fusion pipeline doc; Fix A/B/C/D history
- `src/aip/orchestration/actors/AGENTS.md` — `beast` slot vs Beast actor distinction (slot is routing key, actor is process)
- `gui/AGENTS.md` — Multi-Model dropdown contract; Phase 1 Fusion rendering; the `turn_id` gotcha
- `src/aip/adapter/api/routes/chat.py` L225-441 — the inline retrieval block to extract (the source of truth for the helper's behavior)
- `src/aip/adapter/api/routes/model_council.py` — current `compare_models` endpoint, `_pick_fusion_engine`, `_call_fusion_engine`, `_call_library_model_id`
- `gui/pages/ask.py::_send_multicast` — currently sends `turn_id=""` + `sources=[]`; the GUI side of the bridge
- `gui/api_client.py::run_model_council` — POST payload construction
- `tests/test_model_council_fusion.py` — Phase 1 test patterns to follow for Phase 2 tests
- `tests/test_ask_multiselect_dropdown.py` — current-cycle test patterns (AST + end-to-end with mocked `_call_fusion_engine`)
- PDF report "Fusion for AIP Multimodel Synthesis" (28 pages, 2026-06-16) — Parts V/VI/IX/X are the authoritative spec for Phase 2 scope and tests

---
Task ID: phase1-retrieval-bridge-2026-06-17
Agent: Super Z (main)
Task: Finish Phase 1 (retrieval bridge) + Step 2-A — extract the shared augmented-context helper from chat.py, refactor chat.py to call it, add assemble_augmented_context field to ModelCouncilRequest, wire compare_models to call the helper + prepend augmented messages to each panel call. Follow the coding protocol strictly: Orient → Contract Check → Code → Verify → Document.

Work Log:
- Orient: read root AGENTS.md coding cycle protocol; read src/aip/adapter/AGENTS.md for the POST /beast/compare-models contract; read src/aip/orchestration/actors/AGENTS.md for the beast-slot-vs-Beast-actor distinction; read src/aip/orchestration/AGENTS.md for the RetrievalOrchestrator ownership; verified the 4 retrieval helpers (_get_graph_neighbors, _get_wiki_overview, _search_corpus_turns, _assemble_corpus_context) are ONLY used inside chat.py (no external consumers — safe to move); verified no tests import from chat.py directly
- Contract Check: identified producer (AugmentedContext dataclass with messages, sources, source_turn_ids, trace, domain, assembled) ↔ consumer 1 (chat.py reads aug.messages, aug.sources, aug.trace, aug.source_turn_ids; auto-save path reads _augmented_source_turn_ids instead of the old source_dicts local var) ↔ consumer 2 (model_council.py prepends aug.messages to each panel call via _call_model_slot messages_prefix param + _call_library_model_id messages param)
- Code Step 1: created src/aip/adapter/api/routes/_augmented_context.py (NEW, ~370 lines) — contains the AugmentedContext dataclass, the assemble_augmented_context() async function (extracted from chat.py L225-441), and the 4 retrieval helpers moved from chat.py. The helper NEVER raises — all exceptions are caught, logged at WARNING level, and degraded to AugmentedContext(assembled=False)
- Code Step 2: refactored src/aip/adapter/api/routes/chat.py — removed the 4 inline helper definitions (124 lines), added imports from _augmented_context, added backward-compat re-exports of the 4 helpers, replaced the 220-line inline augmented block with a ~40-line helper call (the reduction is smaller than 220→4 because the helper-call site still has the role-hint fallback + the normal-mode else branch + comments). Updated the auto-save path to read _augmented_source_turn_ids instead of source_dicts
- Code Step 3: added assemble_augmented_context: bool = False field to ModelCouncilRequest (additive, safe default); added messages_prefix param to _call_model_slot (backward compatible — defaults None); wired compare_models to call assemble_augmented_context() when request.assemble_augmented_context=True AND request.turn_id is non-empty, and prepend aug.messages to each panel call. Library models receive the prefix via the messages= param; slot models via the messages_prefix= param. The Judge and Synth calls do NOT receive the prefix (Judge reads panel outputs; Synth reads only Judge JSON)
- Verify: ran pytest on tests/test_augmented_context_helper.py (21 new tests, all pass) + tests/test_model_council_fusion.py + tests/test_model_council_cycle6.py + tests/test_model_council_cycle6_1.py + tests/test_model_council_library_ids.py + tests/test_ask_multiselect_dropdown.py + tests/test_ask.py + tests/test_ask_workbench_cycle41.py + tests/test_ui_integration_cycle14.py — 280 passed, 1 pre-existing failure (test_no_dead_nav_items /graph route — unrelated, verified pre-existing via git stash earlier)
- Document: updated src/aip/adapter/AGENTS.md — added assemble_augmented_context field to the POST /beast/compare-models contract; added new "Shared Augmented-Context Helper Contract" section documenting the producer/consumer contract; added Last Cycle entry. Updated src/aip/orchestration/AGENTS.md — added Last Cycle entry noting the RetrievalOrchestrator is now consumed by both routes via the helper (no orchestration code change, documentation-only update)

Stage Summary:
- Phase 1 retrieval bridge: SHIPPED. The AIP-acronym bug from the Fusion report's Part I is now structurally fixed — the shared helper is available to both routes. The backend wiring is complete (ModelCouncilRequest.assemble_augmented_context field + compare_models calls helper + _call_model_slot messages_prefix param). The GUI side (Step 2-B — sending turn_id + assemble_augmented_context=True from _send_multicast) is the next step and will activate the bridge end-to-end.
- Files changed:
  - NEW: src/aip/adapter/api/routes/_augmented_context.py (~370 lines — the helper + 4 moved helpers)
  - NEW: tests/test_augmented_context_helper.py (21 tests)
  - MODIFY: src/aip/adapter/api/routes/chat.py (removed 124 lines of inline helpers + 220 lines of inline retrieval block; added imports + re-exports + ~40-line helper call)
  - MODIFY: src/aip/adapter/api/routes/model_council.py (added assemble_augmented_context field + messages_prefix param to _call_model_slot + helper call in compare_models)
  - MODIFY: src/aip/adapter/AGENTS.md (new field doc + new helper contract section + Last Cycle entry)
  - MODIFY: src/aip/orchestration/AGENTS.md (Last Cycle entry noting RetrievalOrchestrator now consumed by both routes)
- Backward compat: all new fields default to False/None; existing tests, external API clients, and the current GUI (which doesn't send the flag yet) see no behavior change. 280/281 tests pass (the 1 failure is pre-existing and unrelated).
- Next step: Step 2-B — wire gui/pages/ask.py::_send_multicast to send turn_id=<real> + assemble_augmented_context=(state.current_mode == 'augmented'), and add the param to gui/api_client.py::run_model_council. This will activate the retrieval bridge end-to-end and let the user verify the AIP-acronym fix with a real dogfood test.

---
Task ID: phase1-retrieval-bridge-step-2b-2026-06-17
Agent: Super Z (main)
Task: Step 2-B — wire the GUI to send turn_id + assemble_augmented_context flag, activating the Phase 1 retrieval bridge end-to-end. Follow the coding protocol strictly: Orient → Contract Check → Code → Verify → Document.

Work Log:
- Orient: re-read gui/AGENTS.md (Multi-Model dropdown contract + Layer discipline: GUI imports from adapter API ONLY, never foundation/orchestration directly); verified _send_multicast current state (sends turn_id="" + sources=[]); verified run_model_council in api_client.py (has skip_default_slots but not assemble_augmented_context); verified ModelCouncilRequest on backend already has assemble_augmented_context field (shipped in Step 2-A commit 58d21db); checked make_turn_id availability (lives in aip.foundation.schemas.corpus_turn — GUI CANNOT import it per layer discipline); verified get_session_context API method exists for turn_count lookup
- Contract Check: producer (gui/api_client.py run_model_council payload) must add "assemble_augmented_context" key ↔ consumer (model_council.py ModelCouncilRequest.assemble_augmented_context field — already exists). Payload key name must match Pydantic field name exactly. turn_id must be non-empty when augmented mode is on (gates the backend helper call). The helper itself uses session_id (not turn_id) for session_meta lookup, so session_id as the turn_id signal is sufficient.
- Code Step 1: added assemble_augmented_context: bool = False param to gui/api_client.py::run_model_council + included "assemble_augmented_context" key in the POST payload dict. Updated the docstring to document the Phase 1 retrieval bridge activation.
- Code Step 2: wired gui/pages/ask.py::_send_multicast to compute is_augmented = (state.current_mode == "augmented") and pass turn_id=session_id if is_augmented else "" + assemble_augmented_context=is_augmented. Added a detailed inline comment explaining the layer-discipline constraint (GUI can't import make_turn_id from foundation) and why session_id as the turn_id signal is sufficient (the helper uses session_id for session_meta lookup, not turn_id).
- Verify: ran pytest on the full focused suite (test_send_multicast_retrieval_bridge.py + test_augmented_context_helper.py + test_model_council_fusion.py + test_model_council_cycle6.py + test_model_council_cycle6_1.py + test_model_council_library_ids.py + test_ask_multiselect_dropdown.py + test_ask.py + test_ask_workbench_cycle41.py + test_ui_integration_cycle14.py) — 293 passed, 1 pre-existing failure (test_no_dead_nav_items /graph route — unrelated, verified pre-existing via git stash earlier)
- Document: updated gui/AGENTS.md — added "Phase 1 retrieval bridge (Step 2-B — current cycle)" contract section documenting the turn_id=session_id + assemble_augmented_context wiring + the layer-discipline constraint; added Last Cycle entry. Updated worklog.md (this entry).

Stage Summary:
- Phase 1 retrieval bridge: ACTIVATED END-TO-END. The GUI now sends assemble_augmented_context=True + a non-empty turn_id (session_id) when state.current_mode == 'augmented'. The backend calls the shared helper and prepends corpus/wiki/graph/definer context to each panel call's user prompt. The AIP-acronym bug is now structurally fixed AND wired end-to-end — ready for dogfood verification.
- Files changed:
  - MODIFY: gui/api_client.py (added assemble_augmented_context param + payload key to run_model_council)
  - MODIFY: gui/pages/ask.py (_send_multicast now sends turn_id=session_id + assemble_augmented_context=is_augmented)
  - MODIFY: gui/AGENTS.md (new contract section + Last Cycle entry)
  - NEW: tests/test_send_multicast_retrieval_bridge.py (13 tests — payload contract, augmented flag wiring, turn_id wiring, end-to-end payload key match)
- Layer discipline: the GUI does NOT import make_turn_id from aip.foundation.schemas.corpus_turn (forbidden by root AGENTS.md). Instead, the GUI passes session_id as the turn_id signal — the backend's helper uses session_id (not turn_id) for session_meta lookup, so this is sufficient. A future step can add a backend endpoint that returns a per-turn turn_id if per-send artifact uniqueness becomes needed.
- Backward compat: assemble_augmented_context defaults to False. Existing callers that don't send the flag see no behavior change. Normal mode (state.current_mode == 'normal') sends assemble_augmented_context=False + turn_id="" — the backend's helper gate doesn't fire, panel calls proceed with the bare prompt.
- Next step: manual dogfood verification — with Multi-Cast ON + Augmented ON + corpus ingested, send "What does AIP stand for?" — panel models should now correctly identify AIP as AI Poiesis (previously they answered blind). This is the PDF's Phase 1 ship criteria.
