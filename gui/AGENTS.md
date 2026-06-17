# ============================================================

# GUI — Agent Navigation
> NiceGUI Operator Console. ACTIVE DEBUGGING ZONE. Read this before touching anything.

## Purpose
The GUI provides the user-facing interface for AIP: the unified chat surface
where DEFINER interacts with Beast, Vigil, and Sexton actors. Built with NiceGUI.
This is the primary surface for the dogfood loop.

## Architecture Constraints
- GUI communicates with AIP capabilities through the **adapter API layer only**
  (FastAPI endpoints). It does not import from `src/aip/orchestration/` or
  `src/aip/foundation/` directly.
- State management stays local to GUI components — no shared mutable global state
  between NiceGUI pages.
- All API calls are async. NiceGUI's async event loop must be respected.
- Authentication state flows from `adapter/auth/` through the API — GUI does not
  implement its own auth logic.

## Contracts (What This Module Promises to Consumers)

### API Client Contract
- `gui/api_client.py` (`AipApiClient`) is the sole gateway to backend data
- All API methods return dicts or raise exceptions — callers must handle both
- API base URL configured via `gui/config.py`
- **Required methods that pages depend on**:
  - `get_corpus_status()` → dict with `turn_count`, `embed_coverage`, `backfill_state`
  - `get_embedding_progress()` → dict with `embedded`, `total_turns`, `backfill_state`, `rate_limited`
  - `trigger_corpus_backfill()` → dict with `status`
  - `trigger_corpus_ingest()` → dict with `status`
  - `get_text_generation_slots()` → list of model slot dicts
  - `get_actor_status()` → dict with `sexton`, `beast`, `vigil` keys
- **If adding a new API method**: add it to `AipApiClient`, add the corresponding
  FastAPI route in `adapter/api/routes/`, and document the contract here

### Status Types Contract
- `gui/status_types.py` defines the canonical status→label→color mapping
- All status badges and pills MUST use `status_types.py` mappings — never hardcode
- Backfill state labels: `not_configured` → "NOT CONFIGURED", `configured_idle` → "IDLE",
  `backfill_running` → "RUNNING", `partially_embedded` → "PARTIAL",
  `embedded` → "EMBEDDED", `degraded` → "DEGRADED", `failed` → "FAILED",
  `rate_limited` → "RATE LIMITED"

### Component Contract
- Shared components in `gui/components/` are the ONLY place for reusable UI primitives
- Pages in `gui/pages/` compose components — they do not re-implement them
- Component `render()` methods accept data dicts, not business objects

## Data Flows (In / Out)

### In (Data the GUI reads from backend)
- **Corpus page** reads from:
  - `GET /corpus/status` → `corpus_status` dict
  - `GET /corpus/embedding-progress` → `embedding_progress` dict
  - `GET /corpus/turns` → turn list
- **Dashboard page** reads from:
  - `GET /actors/status` → actor status dict
  - `GET /health` → system health dict
- **Settings page** reads from:
  - `GET /models/slots` → model slot list
- **Ask page** reads from:
  - `POST /ask` → answer with provenance

### Out (Actions the GUI triggers)
- Corpus page → `POST /corpus/ingest`, `POST /corpus/backfill`, `POST /corpus/retry`
- Ask page → `POST /ask` with query
- Settings page → `PUT /models/slots/{slot_id}`

### Cross-Folder Data Flows
```
sexton.py (_embedding_backfill_state, _rate_limited)
  → adapter/api/routes/corpus.py (/corpus/embedding-progress)
    → gui/pages/corpus.py (embedding_progress["backfill_state"])
      → gui/components/corpus_summary.py (status badge)
      → gui/components/corpus_actions.py (button state)
```

## Known Gotchas
- **UnboundLocalError on async handlers**: NiceGUI `ui.button(on_click=func)` requires
  `func` to be defined BEFORE the button references it. Always define `async def`
  handlers above their `ui.button()` calls. NEVER use `lambda: coroutine()` — use
  direct function references.
- **`sexton_pass.state` does not exist**: The backfill state is read from
  `embedding_progress["backfill_state"]`, NOT from any attribute on a sexton_pass
  object. The old code that read `sexton_pass.state` was a contract violation.
- **NiceGUI async event loop**: Blocking calls in async handlers freeze the UI.
  All API calls must use `await`, never synchronous requests.
- **Error visibility**: All `try/except Exception` blocks in dialog handlers must
  surface errors via `ui.notify(type="negative")`. Never silently swallow.
- **Session reset**: If a debugging session hits 50+ messages on the same issue,
  close and restart with a sharper success criterion.
- **`ui.left_drawer()` width is set via Quasar props, not CSS**: NiceGUI's
  `ui.left_drawer()` wraps Quasar's `q-drawer`, which re-applies its own inline
  pixel width on render. Setting `width:100px` via `.style()` gets overridden.
  Use `.props("width=100")` to tell Quasar at the component level so the drawer
  actually shrinks. **IMPORTANT: props must be SPACE-separated, NOT
  semicolon-separated** — NiceGUI's prop parser leaves a trailing `;` in the
  value when semicolons are used (e.g. `width='100;'`), which Quasar rejects,
  causing the drawer to fall back to its default 200px width. Use
  `.props("width=100 mini=false bordered=false")` (spaces), NOT
  `.props("width=100; mini=false; bordered=false")` (semicolons).
- **`ui.left_drawer(value=True)` is REQUIRED for push-mode (not overlay)**:
  NiceGUI's `ui.left_drawer()` defaults to `value=None`, which sets Quasar's
  `show-if-above=True` and leaves `model-value=None`. In that state the
  drawer's visibility is resolved by JavaScript AFTER the WebSocket connects
  (see `Drawer._request_value` in `nicegui/elements/drawer.py`). Until JS
  resolves, Quasar renders the drawer as an OVERLAY — it floats on top of
  the main content instead of offsetting it, clipping the left edge of the
  page (e.g. "Can I trust AIP" → "an I trust AIP"). Passing `value=True`
  sets `model-value=True` and `show-if-above=False`, so the drawer is open
  in push-mode from the very first paint. Do NOT remove the `value=True`
  argument from `build_left_nav()`.
- **Belt-and-suspenders CSS for the drawer**: Even with `value=True` and
  the correct `width` prop, `build_top_bar()` injects `_LAYOUT_CSS` that
  forces `.q-drawer.left` to 100px and `.q-page-container` to
  `padding-left:100px`. This ensures the page content is always offset
  to the right of the sidebar even if Quasar's drawer push-mode is flaky
  or the width prop doesn't parse. Do NOT remove these CSS rules.
- **`.q-page` must be `display:flex; flex-direction:column` or main content
  collapses to content-width**: Quasar's `.q-page` (the parent of every page's
  main content column) is `display:block` by default. NiceGUI's `ui.column()`
  defaults to `display:flex; flex-direction:column` but has NO default
  `width:100%`. So the column's `flex-1` class only stretches it along the
  MAIN axis (height) — the cross-axis (width) collapses to content width,
  leaving the browser's white body background visible on the right half of
  the viewport. `build_top_bar()` in `gui/components/layout.py` injects
  `_LAYOUT_CSS` on every page to force `.q-page` into flex-column mode, which
  makes `align-items:stretch` (the default) stretch the main column to full
  width. If you ever remove that CSS injection, EVERY page will regress to
  the narrow-content-with-whitespace-on-the-right bug. Do not remove it.
- **`ui.right_drawer()` is FORBIDDEN in this codebase**: Per the no-right-sidebar
  rule, no page or component may use `ui.right_drawer()`. The Beast Counsel and
  Model Council panels use `ui.dialog()` (centered modal) instead. The old
  `build_right_rail()` in `layout.py` is a no-op stub kept only for backward
  compatibility — do not call it from new code.
- **`element.style()` is additive, not replacing**: Calling `.style("X")` on a
  NiceGUI element APPENDS the CSS string to the element's existing style. To
  toggle visibility, use `element.visible = True/False` (or `.set_visibility()`),
  NOT `element.style("display:none;")` followed by `element.style("padding:...;")`
  — the latter leaves `display:none` in place forever.
- **`turn_id` contract on chat WebSocket responses**: The backend chat route
  (`src/aip/adapter/api/routes/chat.py`) now echoes `turn_id` in every
  `"type": "response"` payload. The GUI's `on_response` handler in
  `gui/pages/ask.py` MUST read `resp.get("turn_id", "")` into `turn_data` —
  without it, every per-turn action (Beast Counsel, Link Wiki, Model Council
  turn linkage) bails with "No turn ID available".
- **Multi-Cast send path**: When 2+ models are selected in the unified
  multi-select dropdown, the send handler dispatches via
  `_send_multicast` → `api_client.run_model_council` →
  `POST /beast/compare-models`. The request payload uses
  `selected_model_slots=[]` and `selected_model_ids=<list of OpenRouter
  IDs from the dropdown>` with `skip_default_slots=True` so the backend
  does NOT auto-add the default TOML slots (synthesis/evaluation/beast).
  This bypasses the normal WebSocket chat path entirely. Per-model
  results render as separate answer cards; Beast Fusion synthesis
  renders as a final advisory card. The synthesis is ADVISORY ONLY.
- **Phase 1 retrieval bridge (Step 2-B — current cycle)**: when
  `state.current_mode == 'augmented'`, `_send_multicast` now sends
  `assemble_augmented_context=True` AND a non-empty `turn_id`
  (the `session_id`, used as a per-session signal). The backend's
  `compare_models` endpoint calls the shared
  `routes/_augmented_context.py::assemble_augmented_context()` helper
  to build the augmented system messages (corpus turns + wiki + graph
  + definer profile) and PREPENDS them to each panel call's user
  prompt. This fixes the AIP-acronym bug — Multi-Cast in augmented
  mode no longer answers blind. The `turn_id` is the `session_id`
  (not a per-turn `make_turn_id(session_id, turn_count)`) because the
  GUI layer discipline forbids importing `make_turn_id` from
  `aip.foundation.schemas.corpus_turn` directly. The helper itself
  uses `session_id` (not `turn_id`) for `session_meta` lookup, so
  `session_id` as the `turn_id` signal is sufficient. The `turn_id`
  only (a) gates the helper call (must be non-empty) and (b) computes
  the council `artifact_id` (per-session-deterministic). When
  `state.current_mode == 'normal'`, `_send_multicast` sends
  `assemble_augmented_context=False` and `turn_id=""` — the panel
  calls proceed with the bare prompt (existing behavior, backward
  compatible).
- **Multi-Model dropdown auto-routing (prior cycle)**: the Ask page
  chat header now uses a SINGLE multi-select checkbox dropdown
  (`ui.select(..., multiple=True)`) for picking N models from the
  unified "available models" pool. The send handler auto-routes based
  on count — no separate "Multi-Cast" button is required:
  - **0 selected** → notify "pick a model" and bail
  - **1 selected** → normal single-model chat (WS route, uses the
    synthesis slot's configured model — set via
    `set_role_model("synthesis", X)` when the dropdown changes)
  - **≥2 selected** → Multi-Cast Fusion (POST /beast/compare-models).
    The selected models are sent as `selected_model_ids` (OpenRouter
    IDs); `selected_model_slots` is always `[]` with
    `skip_default_slots=True` so the backend does NOT auto-add the
    default TOML slots (synthesis/evaluation/beast). The `beast` slot
    is used ONLY for the Judge+Synth synthesis stages, not as a panel
    model. **Models are NOT tied to actor slots/roles** — the user
    picks N models from the unified dropdown, and the backend calls
    those N models directly via OpenRouter.
  This restores the original "checkbox dropdown → auto-trigger
  synthesis" UX. The separate "Multi-Cast: ON/OFF" button and the
  second row of slot/library checkboxes were REMOVED. State fields
  `multicast_enabled` (now derived: `len(model_ids) >= 2`) and
  `multicast_selected_slots` (now always `[]`) are kept for back-compat
  but no longer drive the routing — `_dispatch_send` branches on the
  count of `state.multicast_selected_model_ids` directly.
- **Phase 1 Fusion rendering (this cycle)**: The backend's Beast
  synthesis now runs as a two-stage Fusion pipeline (Judge-Beast →
  Synth-Beast). The response gains two new fields:
  - `fusion_answer` (str) — the final Synth-Beast output. This is the
    headline of the synthesis card; `_send_multicast` in `ask.py`
    renders it as `**Fusion Synthesis:** ...` and labels the card
    `model="Beast Fusion"`.
  - `judge_analysis` (dict) — the full structured Judge JSON. Both
    `ModelCouncilPanel._render_judge_analysis` and
    `ask.py::_format_judge_analysis_markdown` render this for audit:
    `analysis.consensus[]` as bullets, `analysis.contradictions[]` as
    a per-topic stance table (each row = one topic, with per-model
    stance cells), `analysis.partial_coverage[]` as per-model-attributed
    bullets, `analysis.unique_insights[]` as per-model-attributed
    bullets, `analysis.blind_spots[]` as italicized bullets (the gaps
    NO model addressed), plus a collapsible raw-JSON disclosure
    (`ui.expansion` + `ui.code` in the panel; `<details>` + fenced
    ```json``` block in the ask card) for full audit.
  Legacy fields (`convergence`, `disagreements`, `unique_contributions`,
  `risks`, `recommended_decision`) are still rendered by both
  `ModelCouncilPanel._render_synthesis` and `_send_multicast` as
  supporting detail below the fusion answer (and above the new
  judge_analysis rendering). `beast_conclusion` is mirrored to
  `fusion_answer` and only rendered separately when it differs
  (legacy fallback path).
  The synthesis card label changed from `"Beast Synthesis"` to
  `"Beast Fusion"` to reflect the new pipeline. The system messages
  on unavailable/failed paths now say "Beast Fusion synthesis
  unavailable/failed" instead of just "Beast synthesis".

## Last Cycle
- **Phase 1 retrieval bridge — Step 2-B GUI wiring (this cycle)**:
  `_send_multicast` in `gui/pages/ask.py` now sends
  `assemble_augmented_context=(state.current_mode == 'augmented')`
  AND `turn_id=session_id` (when augmented) / `turn_id=""` (when
  normal) to `api_client.run_model_council`. The backend's
  `compare_models` endpoint calls the shared
  `routes/_augmented_context.py::assemble_augmented_context()` helper
  when the flag is True + turn_id is non-empty, and PREPENDS the
  augmented system messages (corpus turns + wiki + graph + definer
  profile) to each panel call's user prompt. This activates the
  retrieval bridge end-to-end and fixes the AIP-acronym bug —
  Multi-Cast in augmented mode no longer answers blind. The `turn_id`
  is the `session_id` (not a per-turn `make_turn_id(session_id,
  turn_count)`) because the GUI layer discipline forbids importing
  `make_turn_id` from `aip.foundation.schemas.corpus_turn` directly.
  The helper itself uses `session_id` (not `turn_id`) for
  `session_meta` lookup, so `session_id` as the `turn_id` signal is
  sufficient. `gui/api_client.py::run_model_council` gained the
  `assemble_augmented_context: bool = False` param + payload key.
  13 new tests in `tests/test_send_multicast_retrieval_bridge.py`
  assert the GUI ↔ backend payload contract (every payload key
  matches a `ModelCouncilRequest` field — the bug is always in the
  gap). Default `False` preserves backward compat: existing callers
  that don't send the flag see no behavior change.
- **Phase 1 retrieval bridge — Step 2-A backend helper (prior cycle)**:
  extracted the inline ~220-line augmented retrieval block from
  `routes/chat.py` L225-441 into a shared helper at
  `routes/_augmented_context.py`. See `src/aip/adapter/AGENTS.md`
  for the full producer/consumer contract.
- **Multi-Model dropdown auto-routing (prior cycle)**: replaced the
  separate "Multi-Cast: ON/OFF" toggle button + the second row of
  slot/library checkboxes with a SINGLE multi-select checkbox dropdown
  in the Ask page chat header. The send handler now auto-routes based
  on the count of selected models — no separate "Multi-Cast" button
  click is required:
  - 0 selected → notify "pick a model" and bail
  - 1 selected → normal single-model chat (WS route, synthesis slot's
    configured model)
  - ≥2 selected → Multi-Cast Fusion (POST /beast/compare-models with
    `skip_default_slots=True`)
  Per the user's "models NOT tied to actor slots/roles" requirement:
  the GUI sends `selected_model_slots=[]` (always empty) and
  `selected_model_ids=<user's dropdown picks>` (OpenRouter IDs only).
  The `beast` slot is used ONLY for the Judge+Synth synthesis stages
  on the backend, NOT as a panel model. The `skip_default_slots=True`
  flag (new field on `ModelCouncilRequest`, see
  `src/aip/adapter/AGENTS.md`) prevents the backend from auto-adding
  the default TOML slots (synthesis/evaluation/beast) when the GUI's
  `selected_model_slots` is empty. State field `multicast_enabled` is
  now a derived property (`len(model_ids) >= 2`) — kept for back-compat
  but no longer drives routing. State field `multicast_selected_slots`
  is kept (always `[]`) for back-compat with the request payload shape.
  Helpers `_toggle_multicast`, `_toggle_multicast_slot`,
  `_toggle_multicast_model_id` were removed. New handler
  `_on_chat_models_changed` drives the multi-select dropdown. The
  back-compat single-model `_on_chat_model_changed` is preserved as a
  shim (still awaited by the cycle-14 test). 37 new tests in
  `tests/test_ask_multiselect_dropdown.py` assert the new pattern.
- **Phase 1 Fix D — backend engine fallback (prior cycle, no GUI
  change):** the backend's Fusion pipeline now picks the Judge+Synth
  engine from the SUCCESSFUL panel models (preference: beast slot if
  it succeeded → any other successful slot → any successful library
  model) instead of always using the `beast` slot. This fixes the
  second dogfood run's symptom: when 2 of 4 OpenRouter free models
  timed out, the 2 successful responses returned but NO fusion synth
  or judge response was produced — because the engine was always the
  just-failed `beast` slot. The GUI consumers (`ask.py`,
  `model_council_panel.py`) required NO changes: the API response
  contract (`fusion_answer` str + `judge_analysis` dict) is
  unchanged; Fix D only makes those fields more reliably populated.
  The GUI will now see fusion output in scenarios where it
  previously saw only per-model cards + a "synthesis failed" system
  message. See `src/aip/adapter/AGENTS.md` for the backend contract.
- **Phase 1 Fix B — render `judge_analysis` in GUI (prior cycle)**: the
  rich structured Judge JSON was previously returned by the backend
  but never surfaced in the GUI — only the flattened legacy strings
  (`convergence`, `disagreements`, etc.) were rendered, losing the
  per-model attribution that the new schema provides. Two new
  renderers fix this:
  - `gui/components/model_council_panel.py::_render_judge_analysis`
    (called from `_render_report` after the legacy Convergence /
    Disagreements / Unique Contributions / Risks / Recommended
    Decision sections) renders `analysis.consensus[]` as a bulleted
    list, `analysis.contradictions[]` as a per-topic stance table
    (each row = one topic, with per-model stance cells in a row with
    the model label in `F_MONO` + `C_AMBER` and the stance in
    `F_SANS` + `C_INK60`), `analysis.partial_coverage[]` as
    per-model-attributed bullets, `analysis.unique_insights[]` as
    per-model-attributed bullets, `analysis.blind_spots[]` as
    italicized `C_ERR_FG` bullets (the gaps NO model addressed — the
    most important field for the human), plus a collapsible raw-JSON
    disclosure (`ui.expansion` + `ui.code` with `language="json"`)
    for full audit. Empty/missing dict → nothing rendered.
  - `gui/pages/ask.py::_format_judge_analysis_markdown` (called from
    `_send_multicast` after the legacy fields in the synthesis card
    content) renders the equivalent as markdown: a stance table
    (`| Topic | Model | Stance |`), bulleted lists for consensus /
    partial_coverage / unique_insights / blind_spots, plus a
    `<details><summary>Judge Analysis (raw JSON)</summary>` block
    with a fenced ```json``` code block for full audit.
  Both renderers tolerate missing/empty fields (no empty sections
  rendered) and tolerate the Judge returning the old top-level schema
  (no `analysis` key) by falling back to just the raw-JSON disclosure.
  Two new AST/string-contract tests in
  `tests/test_model_council_fusion.py::TestFusionGuiRendersJudgeAnalysis`
  assert the GUI files read `judge_analysis` and define the renderer
  helpers.
- **Phase 1 Fusion rendering (prior cycle)**: The backend's Beast
  synthesis now runs as a two-stage OpenRouter Fusion pipeline
  (Judge-Beast → Synth-Beast), reusing the `beast` slot for both
  stages. The GUI consumers were updated additively to surface the
  new `fusion_answer` field as the headline of the synthesis surface:
  - `gui/components/model_council_panel.py::_render_synthesis` now
    renders `data["fusion_answer"]` as a prominent "Fusion Synthesis"
    section (color `C_OK_FG`) above the legacy Convergence /
    Disagreements / Unique Contributions / Risks sections, which
    remain as supporting detail. The "Beast Conclusion" section is
    only rendered when it differs from `fusion_answer` (legacy
    fallback path).
  - `gui/pages/ask.py::_send_multicast` now reads
    `result["fusion_answer"]` and renders it as the headline of the
    synthesis answer card: `**Fusion Synthesis:** ...`. The card
    `model` label changed from `"Beast Synthesis"` to `"Beast Fusion"`
    to reflect the new pipeline. System messages on the unavailable
    and failed paths now say "Beast Fusion synthesis unavailable /
    failed" instead of just "Beast synthesis".
  - `gui/api_client.py::run_model_council` is unchanged — it passes
    the request payload through and returns the response dict, so
    the new fields flow through automatically.
  No GUI state fields were added; no API client method signatures
  changed. 22 new fusion tests in `tests/test_model_council_fusion.py`
  assert the GUI consumers read `fusion_answer` (AST contract check).
- **Multi-Cast library bridge (prior cycle)**: The `/models` page checkboxes
  now feed Multi-Cast. Previously Multi-Cast only accepted TOML-configured
  slot names (`synthesis`, `evaluation`, `beast`, …) — max ~4 options. Now
  the Ask page's Multi-Cast row shows a second checkbox group labeled
  "Library:" populated from `get_backend_enabled_models()` (the
  `enabled_models` SQLite table managed by the Models page). Selected
  library model IDs are tracked in `state.multicast_selected_model_ids`
  and sent alongside `state.multicast_selected_slots` in the
  `run_model_council` call. The backend's `compare_models` endpoint
  accepts a new `selected_model_ids: list[str]` field and routes each
  via direct OpenRouter calls (`_call_library_model_id` helper) using
  `AIP_OPENAI_API_KEY`. Each `PerModelResult` now has a `source` field
  (`"slot"` default, `"library"` for library-sourced) so the GUI can
  distinguish provenance. The `≥2 usable models` gate counts both
  sources combined.
- **Left drawer overlay fix (prior)**: The left sidebar was rendering as
  an OVERLAY on top of the main content instead of pushing/offsetting it,
  clipping the left edge of every page (e.g. "Can I trust AIP" → "an I trust
  AIP"). Root cause: NiceGUI's `ui.left_drawer()` defaults to `value=None`,
  which sets Quasar's `show-if-above=True` and leaves `model-value=None` —
  the drawer's visibility is resolved by JavaScript AFTER the WebSocket
  connects, and until then Quasar renders it as an overlay. Fix: pass
  `value=True` to `ui.left_drawer()` so `model-value=True` and
  `show-if-above=False`, putting the drawer in push-mode from first paint.
  Verified via rendered HTML: `"show-if-above":false`, `"model-value":true`.
  Combined with the prior `.q-page` flex-column CSS fix, the main content
  now fills the full viewport width to the right of the 100px sidebar.
- **q-page flex-column fix (prior)**: All 11 pages were rendering their
  main content column at content-width (~500px) with the browser's white body
  background filling the right half of the viewport. Root cause: Quasar's
  `.q-page` is `display:block` by default, so the main column's `flex-1`
  class only stretched its height, not its width (NiceGUI's `ui.column()` has
  no default `width:100%`). Fix: `build_top_bar()` now injects `_LAYOUT_CSS`
  on every page, forcing `.q-page` into `display:flex; flex-direction:column`
  so `align-items:stretch` (the default) stretches the main column to full
  viewport width. One-line CSS change in `gui/components/layout.py` fixes
  all 11 pages — no per-page changes needed.
- **Layout pass + turn_id + Multi-Cast + dialogs**:
  - Right sidebar (`build_right_rail`) removed from all pages — info relocated
    to Maintenance page. The function is kept as a no-op stub for backward compat.
  - Left sidebar (`build_left_nav`) width set via Quasar `width=100` prop
    instead of CSS (which Quasar overrode on render).
  - `BeastPanel` and `ModelCouncilPanel` converted from `ui.right_drawer()`
    to `ui.dialog()` (centered modal with max-width:900px / 1000px and a
    scrollable inner column).
  - Backend chat WebSocket route now echoes `turn_id` in every `response`
    message — the GUI reads it into `turn_data` so Beast Counsel, Link Wiki,
    and Model Council turn linkage all work.
  - Multi-Cast toggle added to Ask page chat header: when on, the send
    handler fans the prompt out to every selected text-gen slot via
    `POST /beast/compare-models` and renders per-model answer cards + a
    Beast synthesis card. Defaults: synthesis + evaluation + beast.
  - Ask page state: `GuiState.multicast_enabled` and
    `multicast_selected_slots` added.
- **Commit 14d3a73**: Fixed backfill state reading (was `sexton_pass.state`, now
  `embedding_progress["backfill_state"]`). Added `rate_limited` flag to
  CorpusActions. Added `try/except Exception` guards to all dialog handlers.
  Fixed `_do_ingest`/`_do_backfill`/`_do_retry` definition order before button references.
- **Commit 4c9e94d**: Fixed UnboundLocalError on `_do_backfill` by reordering
  inner async function definitions above `ui.button(on_click=...)` calls.
- **Wiki contract fix**: The artifacts page and wiki page now correctly display
  Sexton-generated wiki articles (IDs matching `sexton:wiki:*`). The
  `artifact_type` contract was fixed upstream in `sexton.py` from `"sexton_wiki"`
  to `"beast_wiki"` so all consumers see the artifacts.

## Brand System (applies here)
- Background: `#0d1117` | Accent: `#4A9B8E` slate-teal | Amber: `#D4A843`
- Fonts: Inter for UI text, IBM Plex Mono for code blocks, Fraunces for display
- Dark field only — no light mode variants at this stage

## Key Structural Rules
- Page components live in individual files, one page per file
- Shared UI primitives go in a `components/` subfolder — do not duplicate
- No inline business logic — all data operations go through API calls
- Error states must be visible — no silent failures in the UI

## Key Files
| File | Role |
|------|------|
| `app.py` | NiceGUI app entry point |
| `api_client.py` | AipApiClient — sole gateway to backend API |
| `config.py` | GUI configuration (API base URL, etc.) |
| `status_types.py` | Canonical status→label→color mappings |
| `theme.py` | Brand system (colors, fonts, spacing) |
| `state.py` | Shared reactive state (minimal — prefer local state) |
| `pages/corpus.py` | Corpus management page — ACTIVE DEBUGGING |
| `pages/ask.py` | Chat/ask page |
| `pages/dashboard.py` | System dashboard |
| `pages/settings.py` | Model and configuration settings |
| `pages/graph.py` | Knowledge graph visualization |
| `pages/wiki.py` | Wiki article browser/editor |
| `pages/artifacts.py` | Artifact lifecycle management |
| `pages/retrieval_lab.py` | Retrieval quality testing |
| `pages/maintenance.py` | Maintenance center |
| `pages/models.py` | Model management |
| `components/corpus_summary.py` | Corpus status badge with backfill state |
| `components/corpus_actions.py` | Corpus action buttons (ingest, backfill, retry) |

## Work Guidance
- For any UI change: identify the specific component, read this file,
  make the minimal change, verify in browser, check for console errors.
- For API-wiring changes: trace from the UI event handler → API call →
  router in `adapter/api/` → foundation type. Verify the full chain.
- If you're getting a state/render inconsistency: check NiceGUI's async
  event loop handling — blocking calls in async handlers are the most
  common root cause.

## How to Test
```bash
# Start the app
uv run aip init
python -m gui.app  # Operator Console entry point

# API integration check
uv run pytest tests/test_api.py -k "gui"

# Smoke test (verifies API surface GUI depends on)
bash scripts/dogfood_smoke_test.sh
```


# ============================================================
