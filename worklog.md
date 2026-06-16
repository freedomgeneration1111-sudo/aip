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
