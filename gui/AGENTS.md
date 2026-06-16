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
- **Multi-Cast send path**: When `state.multicast_enabled` is True, the send
  handler dispatches via `_send_multicast` → `api_client.run_model_council`
  → `POST /beast/compare-models`. This bypasses the normal WebSocket chat
  path entirely. Per-model results render as separate answer cards; Beast
  synthesis renders as a final advisory card. The synthesis is ADVISORY ONLY.
- **Multi-Cast now accepts TWO parallel sources** (library bridge):
  - `state.multicast_selected_slots` — TOML slot names (synthesis,
    evaluation, beast, …) routed via `ModelSlotResolver`
  - `state.multicast_selected_model_ids` — OpenRouter model IDs from the
    `enabled_models` SQLite library (managed by the Models page), routed
    via direct OpenRouter calls using `AIP_OPENAI_API_KEY`
  Both lists are sent in the `run_model_council` call. The backend's
  `≥2 usable models` gate counts the combined total. Library results
  in the response carry `source="library"` and empty `model_slot` —
  the GUI's per-model card renders them with the model_id as the label.

## Last Cycle
- **Multi-Cast library bridge (this cycle)**: The `/models` page checkboxes
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
