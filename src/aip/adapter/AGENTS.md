# ============================================================

# Adapter Layer — Agent Navigation
> External interfaces and storage. Imports from foundation only. Never from orchestration.

## Purpose
The adapter layer is AIP's boundary with the outside world: HTTP API, CLI, all
storage backends, authentication, budget tracking, and vector/lexical search.
It translates between external protocols and foundation domain types.

## Architecture Constraints
- **Foundation imports only**: `from aip.foundation...` only. Zero orchestration imports.
  If you need orchestration behavior, inject it through a Protocol.
- **GUI communicates through API only**: The GUI never imports adapter internals
  directly — it calls FastAPI endpoints.

## Contracts (What This Module Promises to Consumers)

### API Response Contracts (Consumed by gui/ and external clients)

#### GET /corpus/embedding-progress
Returns dict with:
- `embedded` (int): count of embedded turns
- `total_turns` (int): total turn count
- `embed_coverage` (float): ratio embedded/total
- `backfill_state` (str): one of the Sexton state machine values
- `rate_limited` (bool, optional): True if Sexton is rate-limited
- `rate_limited_reason` (str, optional): reason for rate limit
- `cycle_active` (bool, optional): True if Sexton cycle is running

#### GET /corpus/status
Returns dict with:
- `turn_count` (int)
- `embed_coverage` (float)
- Plus any enrichment from embedding_progress

#### GET /actors/status
Returns dict with keys `sexton`, `beast`, `vigil`, each containing:
- `status` (str)
- `last_run` (optional datetime)
- Actor-specific fields

#### GET /models/slots
Returns list of model slot dicts, each with:
- `slot_name` (str): e.g. "synthesis", "evaluation", "sexton", "embedding", "beast"
- `provider` (str)
- `model` (str)
- `configured` (bool)

#### POST /beast/compare-models (Model Council)
Request body fields (all optional except `prompt`):
- `prompt` (str, required)
- `turn_id`, `session_id`, `existing_answer` (str)
- `sources` (list[dict])
- `selected_model_slots` (list[str]) — TOML slot names routed via `ModelSlotResolver`
- `selected_model_ids` (list[str]) — OpenRouter model IDs from the
  `enabled_models` SQLite library, routed via direct OpenRouter calls
  using `AIP_OPENAI_API_KEY` (or per-row `custom_api_key` if `is_custom=1`)
- `save_as_artifact` (bool)
- `skip_default_slots` (bool, default `False`) — when `True`, the
  resolver returns `[]` for `comparison_slots` even if
  `selected_model_slots` is empty — i.e. the panel is built ONLY
  from `selected_model_ids` (OpenRouter library IDs). This is the
  GUI's "models not tied to actor slots/roles" mode: the user picks
  N models from the unified dropdown, the backend calls those N
  models directly via OpenRouter, and the `beast` slot is used ONLY
  for the Judge+Synth synthesis stages. Default `False` preserves
  the existing fallback (`_DEFAULT_COMPARISON_SLOTS` =
  `["synthesis", "evaluation", "beast"]`) for external API clients
  and existing tests.
- `assemble_augmented_context` (bool, default `False`) — when `True`
  AND `turn_id` is non-empty, the endpoint calls the shared
  `routes/_augmented_context.py::assemble_augmented_context()` helper
  to build the augmented system messages (corpus turns + wiki + graph
  + definer profile) and PREPENDS them to each panel call's user
  prompt. This is the Phase 1 retrieval bridge — fixes the
  AIP-acronym bug where Multi-Cast panel models answered blind
  without seeing the corpus. The augmented context is computed ONCE
  per request (not N times) and is identical across panelists —
  diversity comes from the models themselves, not from differential
  context. The Judge and Synth calls do NOT receive the augmented
  prefix (the Judge reads panel outputs; the Synth reads only the
  Judge JSON). Default `False` preserves the existing bare-prompt
  behavior for external API clients and existing tests.

Response: `ModelCouncilResponse` with `selected_models: list[PerModelResult]`.
Each `PerModelResult` has a `source` field (`"slot"` or `"library"`) so
consumers can distinguish provenance. Library-sourced results carry
`model_slot=""` and `provider="openrouter"`.

The `≥2 usable models` gate counts slots + library IDs combined.
If `model_provider` is None but ≥2 library IDs are supplied, the
comparison still runs (library path doesn't need the slot resolver) —
Beast synthesis is skipped with `synthesis_status="unavailable"` in
that case (no "beast" slot to synthesize with).

**GUI contract (current cycle):** the GUI's Multi-Model dropdown now
sends `selected_model_slots=[]` (always empty) + `selected_model_ids=<N
OpenRouter IDs from the unified dropdown>` + `skip_default_slots=True`.
The panel is built ONLY from the user's selection — the default TOML
slots (synthesis/evaluation/beast) are NOT auto-added. The `beast` slot
is used ONLY for the Judge+Synth synthesis stages (via
`_pick_fusion_engine`), not as a panel model. External API clients and
existing tests that omit `skip_default_slots` (default `False`) continue
to see the existing fallback behavior — backward compatible.

**Phase 1 Fusion pipeline (default Beast analysis):** the Beast
synthesis now runs as a two-stage OpenRouter Fusion pipeline —
Judge-Beast reads the panel outputs and produces a structured JSON
comparison, then Synth-Beast reads ONLY that JSON (no panel outputs,
no retrieval) and writes the final fused answer. The `beast` slot is
reused for both stages (no new slots). Response gains two new fields:

- `fusion_answer` (str) — the final Synth-Beast output (the fused
  answer). Mirrored to `beast_conclusion` for legacy consumers.
- `judge_analysis` (dict) — the full structured Judge JSON:
  `{status, analysis:{consensus[], contradictions[{topic, stances[{model, stance}]}], partial_coverage[{models[], point}], unique_insights[{model, insight}], blind_spots[]}, responses[{model, content}]}`.
  Empty dict if Judge call failed or JSON parse failed.

Legacy fields (`convergence`, `disagreements`, `unique_contributions`,
`risks`, `recommended_decision`) are still populated from the Judge
JSON — derived from the new `analysis.*` schema when present, falling
back to old top-level keys for backward compat with older Beast models
and the existing test mock. The per-model panel outputs remain in
`selected_models` so the human can compare them alongside the single
`fusion_answer`.

`synthesis_status` is `"completed"` only when BOTH Judge-Beast and
Synth-Beast succeed. If Judge succeeds but Synth fails,
`synthesis_status = "failed"` but `judge_analysis` is still populated
(the Judge output is still useful for audit).

**Phase 1 Fix A — per-call timeouts (this cycle):** every model call
in the Fusion pipeline is now wrapped in `asyncio.wait_for` with an
upper-bound timeout, so a single hung model cannot hold the entire
response hostage. Constants (module-level in `routes/model_council.py`):

- `_PANEL_CALL_TIMEOUT_S = 30.0` — each panel call (slot or library ID)
- `_JUDGE_CALL_TIMEOUT_S = 60.0` — Judge-Beast call (reads all panel outputs)
- `_SYNTH_CALL_TIMEOUT_S = 60.0` — Synth-Beast call (reads Judge JSON)

A timed-out panel call is captured by `asyncio.gather(return_exceptions=True)`
and recorded as `PerModelResult.status="failed"` with
`error="timed out after Ns"`. A timed-out Judge or Synth call is
caught by the existing `except asyncio.TimeoutError` clause (added
ahead of the generic `except Exception`), logged as
`council_judge_call_timed_out` / `council_synth_call_timed_out`, and
sets `synthesis_status="failed"`. The rest of the pipeline completes
with whatever succeeded.

**Phase 1 Fix C — MODEL LABEL CONTRACT (this cycle):** the Judge
system prompt now contains an explicit MODEL LABEL CONTRACT block
instructing the model to use the EXACT `<LABEL>` string from the
answers_block section header (`## <LABEL> (<model_id>)`) in every
`model` field of its JSON output — never invent generic labels like
`model_a`, never fall back to `beast` when `beast` isn't a section
label, never use the parenthesized `model_id`. The prompt includes a
concrete example showing correct vs. incorrect label usage. This
fixes the prior defect where the Judge emitted legacy slot names
(`synthesis=`, `beast=`) instead of the per-model identifiers the
human needs to attribute stances and insights.

**Phase 1 Fix D — graceful degradation when panel models fail (this
cycle):** the Fusion engine (Judge+Synth) is now picked from the
SUCCESSFUL panel models instead of always being the `beast` slot.
Previously, if `beast` was one of the OpenRouter free models that
timed out in the panel, the Judge call would also time out at
`_JUDGE_CALL_TIMEOUT_S` and the entire Fusion output was lost — the
user saw only per-model cards and no fusion/judge output at all.
Now ``_pick_fusion_engine(per_model_results)`` picks the engine with
this preference order: (1) `beast` slot IF it succeeded as a
panelist, (2) any other successful slot, (3) any successful library
model. The picked engine is then used for BOTH the Judge call and
the Synth call via ``_call_fusion_engine(engine_kind, engine_id,
messages, container, timeout)`` — a unified helper that routes slot
engines through ``container.model_provider.call`` and library engines
through ``_call_library_model_id(model_id, messages=messages)``.
This makes the Fusion pipeline resilient to individual model
failures: as long as ≥2 panel models answered, Fusion runs on one of
the answerers. ``_call_library_model_id`` gained an optional
``messages=`` parameter (backward-compatible — old positional
``user_prompt`` callers still work) so library models can receive
the full system+user message list required by the Judge/Synth
prompts. ``synthesis_status="unavailable"`` only when
``successful_count < 2``; the per-model results still record which
panelists failed so the human sees the full picture.

### Model Slot Resolver Contract
- `model_slot_resolver.py` resolves per-slot provider routing
- `_call_openai_compatible()` detects HTTP 429 before `raise_for_status()`
- On 429: returns error dict with `retry_after` from `Retry-After` header
- Logs `model_call_rate_limited` event
- **Consumers must check for rate limit response**, not just generic failure

### Shared Augmented-Context Helper Contract (Phase 1 retrieval bridge)
- `routes/_augmented_context.py::assemble_augmented_context()` is the
  SINGLE source of truth for augmented retrieval (corpus turns + wiki
  + graph + definer profile). Both `routes/chat.py` (WebSocket chat)
  and `routes/model_council.py` (Multi-Cast) call this helper — they
  do NOT duplicate retrieval logic.
- **Producer contract** (`AugmentedContext` dataclass):
  - `messages: list[dict]` — system-message dicts to PREPEND to the
    user message before model dispatch. Empty when no augmented
    context was assembled.
  - `sources: list[dict]` — source dicts for the response payload
    (`source_id`, `source_type`, `title`, `score`, `content_snippet`,
    `domain`). Empty when no sources found.
  - `source_turn_ids: list[str]` — turn_ids from corpus_turn sources,
    used by the auto-save ingestion path to propagate provenance to
    Vigil. Empty for the orchestrator path and the no-sources path.
  - `trace: RetrievalTrace | None` — populated when the
    RetrievalOrchestrator fallback ran; None otherwise.
  - `domain: str | None` — the resolved domain string.
  - `assembled: bool` — `True` if retrieval ran at all; `False` if
    the caller was in normal mode, no stores were available, or
    retrieval raised an exception (graceful degrade).
- **Helper NEVER raises** — all exceptions are caught, logged at
  WARNING level, and degraded to `AugmentedContext(assembled=False)`
  with empty `messages`. Callers can rely on this for graceful
  degradation.
- **Consumer contract**:
  - `chat.py` augmented branch: `messages.extend(aug.messages)`,
    `response_sources = aug.sources`, `ret_trace = aug.trace`,
    `_augmented_source_turn_ids = aug.source_turn_ids`. The auto-save
    path reads `_augmented_source_turn_ids` (NOT the old `source_dicts`
    local var, which now lives inside the helper).
  - `model_council.py::compare_models`: when
    `request.assemble_augmented_context=True` AND `request.turn_id` is
    non-empty, calls the helper and prepends `aug.messages` to each
    panel call's user prompt via `_call_model_slot(messages_prefix=...)`
    and `_call_library_model_id(messages=...)`. The Judge and Synth
    calls do NOT receive the prefix.
- **Backward compat**: `chat.py` re-exports the 4 retrieval helpers
  (`_get_graph_neighbors`, `_get_wiki_overview`, `_search_corpus_turns`,
  `_assemble_corpus_context`) from `_augmented_context` so any external
  caller that does `from aip.adapter.api.routes.chat import _search_corpus_turns`
  keeps working. New callers should import from `_augmented_context`
  directly or use the high-level `assemble_augmented_context()` function.

### Storage Contracts
- **aiosqlite ONLY**: No `sqlite3.connect()` in any async method, anywhere in this layer.
  This is the single most common source of async bugs. Check every new SQLite call.
- **Unified DB path**: All stores use the same `db/state.db` path from config.
  No store may initialize its own independent database file.
- **Version-preserved artifacts**: `artifact_store_versioned.py` never overwrites —
  it versions. Understand this before touching artifact storage.
- **Admin endpoints require auth**: Every route under `/admin/` must validate the
  auth token before processing. No exceptions.

### Embedding Provider Contract
- `adapter/embedding/factory.py` creates providers from config
- `openai_embed.py` for real embeddings, `ollama_embed.py` for local
- Provider type `"fake"` or `"mock"` is CI-only — detected by Sexton's
  `_compute_embedding_backfill_state()` which returns "degraded"

## Data Flows (In / Out)

### In (What the adapter receives from orchestration/foundation)
- Protocol interfaces from `foundation/protocols/` — the ONLY seam
- Config from `config/aip.config.toml` via `config/loader.py`
- Model dispatch requests via `ModelSlotResolver`

### Out (What the adapter provides)
- HTTP API: 29 route files in `api/routes/`
- CLI: 14 subcommands in `cli/`
- Storage: 15+ store backends
- MCP server for tool dispatch

### Cross-Folder Data Flows
```
sexton.py (_embedding_backfill_state, _rate_limited)
  → adapter/api/routes/corpus.py (enriches /corpus/embedding-progress)
    → gui/pages/corpus.py (reads backfill_state, rate_limited)

adapter/model_slot_resolver.py (429 detection)
  → orchestration/actors/sexton.py (handles rate limit in embedding pass)

config/aip.config.toml ([models] section)
  → adapter/model_slot_resolver.py (slot configuration)
    → gui/pages/settings.py (model slot display)
```

## Known Gotchas
- **aiosqlite is mandatory**: Using `sqlite3.connect()` in an async method
  blocks the event loop and causes random hangs. Always use `aiosqlite`.
- **429 must be detected before `raise_for_status()`**: If you call
  `resp.raise_for_status()` on a 429, it raises an exception before you
  can extract the `Retry-After` header. Check status code first.
- **Config key names must match Python attribute names**: The TOML key
  `sexton_api_key` must match the Python attribute `sexton_api_key` exactly.
  Mismatches are a known blocker class.
- **No store may create its own database**: All stores share `db/state.db`.
  A store that initializes `my_store.db` violates the unified DB contract.
- **23+ routers must be registered in app.py**: Adding a new route file
  without registering it in `api/app.py` means the route silently doesn't exist.
- **Wiki artifact_type must be `beast_wiki`**: The `/wiki/articles` route, wiki_channel,
  and chat route all filter on `artifact_type: "beast_wiki"`. Sexton must write this
  same value (was incorrectly `sexton_wiki`). Also, `/wiki/articles` SQL must include
  `sexton:wiki:%` in the LIKE conditions for existing artifacts with that ID prefix.
- **Corpus turn store must be initialized for wiki generation**: Sexton's
  `_run_wiki_generation` requires `corpus_turn_store` to be wired in the container.
  If `container.corpus_turn_store is None`, the entire wiki pass skips.
- **Chat WebSocket response MUST include `turn_id`**: The chat WebSocket
  route (`routes/chat.py`) computes a deterministic `turn_id` upfront via
  `make_turn_id(session_id, turn_index)` BEFORE building the response
  payload, and echoes it back in every `"type": "response"` message.
  The downstream `auto_save_chat_turn` uses the same `make_turn_id(session_id,
  turn_index)` so the surfaced ID matches the persisted turn. Without this,
  the GUI's per-turn actions (Beast Counsel, Link Wiki, Model Council turn
  linkage) all bail with "No turn ID available". Never send a `response`
  message without `turn_id`.

## Last Cycle
- **Phase 1 retrieval bridge (this cycle):** extracted the inline
  ~220-line augmented retrieval block from `routes/chat.py` L225-441
  into a shared helper at `routes/_augmented_context.py`. The helper
  (`assemble_augmented_context()`) encapsulates definer profile
  injection, domain resolution, corpus turn search (FTS5),
  RetrievalOrchestrator fallback (RRF), wiki overview injection,
  graph neighbors injection, sources assembly, and the synthesis
  instruction. It NEVER raises — on any failure it returns
  `AugmentedContext(assembled=False)` with empty messages, and the
  caller proceeds with the bare prompt (graceful degradation).
  The 4 retrieval helpers (`_get_graph_neighbors`, `_get_wiki_overview`,
  `_search_corpus_turns`, `_assemble_corpus_context`) were moved to
  `_augmented_context.py` and re-exported from `chat.py` for backward
  compat. `chat.py`'s augmented branch is now a 4-line helper call.
  `ModelCouncilRequest` gained `assemble_augmented_context: bool = False`
  field; when True + turn_id non-empty, `compare_models` calls the
  helper and PREPENDS the augmented system messages to each panel
  call's user prompt. `_call_model_slot` gained a `messages_prefix`
  param. The Judge and Synth calls do NOT receive the prefix (Judge
  reads panel outputs; Synth reads only Judge JSON). This fixes the
  AIP-acronym bug from the Fusion report's Part I — Multi-Cast in
  augmented mode no longer answers blind. 21 new tests in
  `tests/test_augmented_context_helper.py`. Default `False` preserves
  backward compat: existing tests, external API clients, and the
  current GUI (which doesn't send the flag yet — that's Step 2-B)
  see no behavior change.
- **Multi-Model dropdown auto-routing (prior cycle)**: added the
  `skip_default_slots: bool = False` field to `ModelCouncilRequest`
  and a corresponding `skip_default_slots` kwarg to
  `_resolve_comparison_slots`. When the GUI sends
  `selected_model_slots=[]` + `selected_model_ids=[X, Y, …]` +
  `skip_default_slots=True`, the resolver returns `[]` for
  `comparison_slots` (no fallback to `_DEFAULT_COMPARISON_SLOTS`) —
  the panel is built ONLY from the user's multi-select dropdown
  choices. The `beast` slot is used ONLY for the Judge+Synth
  synthesis stages (via `_pick_fusion_engine`), not as a panel
  model. This implements the user's "models NOT tied to actor
  slots/roles" requirement: the GUI's unified dropdown picks N
  OpenRouter IDs, and the backend calls those N models directly via
  `_call_library_model_id` (direct OpenRouter HTTP). External API
  clients and existing tests that omit `skip_default_slots` (default
  `False`) continue to see the existing fallback behavior — backward
  compatible. The `compare_models` endpoint passes the flag through
  to `_resolve_comparison_slots`. See `gui/AGENTS.md` for the GUI
  side of the contract (multi-select dropdown, auto-routing based
  on count, no separate "Multi-Cast" button).
- **Phase 1 Fix D — graceful degradation when panel models fail (prior
  cycle):** the Fusion engine (Judge+Synth) is now picked from the
  SUCCESSFUL panel models instead of always being the `beast` slot.
  Previously, if `beast` was one of the OpenRouter free models that
  timed out in the panel, the Judge call would also time out at
  `_JUDGE_CALL_TIMEOUT_S` and the entire Fusion output was lost —
  the user saw only per-model cards and no fusion/judge output at
  all (the exact symptom reported in the second dogfood run: "got
  responses from two models, no fusion synth or judge response at
  all"). Now `_pick_fusion_engine(per_model_results)` picks the
  engine with this preference order: (1) `beast` slot IF it
  succeeded as a panelist, (2) any other successful slot, (3) any
  successful library model. The picked engine is then used for BOTH
  the Judge call and the Synth call via the new
  `_call_fusion_engine(engine_kind, engine_id, messages, container,
  timeout)` helper (routes slot engines through
  `container.model_provider.call`, library engines through
  `_call_library_model_id(model_id, messages=messages)`). This
  makes the Fusion pipeline resilient to individual model failures:
  as long as ≥2 panel models answered, Fusion runs on one of the
  answerers. `_call_library_model_id` gained an optional `messages=`
  parameter (backward-compatible — old positional `user_prompt`
  callers still work) so library models can receive the full
  system+user message list required by the Judge/Synth prompts.
  `synthesis_status="unavailable"` only when `successful_count < 2`;
  the per-model results still record which panelists failed so the
  human sees the full picture. 3 new regression tests in
  `tests/test_model_council_fusion.py::TestFusionFixDEngineFallback`
  (beast panel failure still produces fusion; all-panel-fail guard;
  `_pick_fusion_engine` preference order unit test). Existing test
  mocks updated to add a beast panel-answer branch (so the mock
  correctly differentiates panel calls from Judge/Synth calls when
  beast is the picked engine). 138 council + import/layering tests
  pass (was 141 — count shifted because the cycle6 beast-fails test
  was retightened to reflect Fix D's "completed" instead of "failed"
  outcome, but the test still exists and asserts the fix).
- **Phase 1 Fix A/B/C (prior cycle)**: three fixes to the Phase 1 Fusion
  pipeline based on the first dogfood run:
  - **Fix A — per-call timeouts**: every model call in the Fusion
    pipeline (panel gather, Judge-Beast, Synth-Beast) is now wrapped
    in `asyncio.wait_for` with module-level timeout constants
    (`_PANEL_CALL_TIMEOUT_S=30`, `_JUDGE_CALL_TIMEOUT_S=60`,
    `_SYNTH_CALL_TIMEOUT_S=60`). A single hung model no longer holds
    the entire response hostage — it gets cut loose at its timeout,
    recorded as `PerModelResult.status="failed"` (panel) or
    `synthesis_status="failed"` (Judge/Synth), and the rest of the
    pipeline completes. This fixes the 4-model timeout the user
    observed on the first dogfood run.
  - **Fix B — render `judge_analysis` in GUI**: the rich structured
    Judge JSON was previously returned by the backend but never
    surfaced in the GUI — only the flattened legacy strings
    (`convergence`, `disagreements`, etc.) were rendered, losing the
    per-model attribution that the new schema provides.
    `gui/components/model_council_panel.py::_render_judge_analysis`
    now renders `analysis.consensus[]` (bulleted), `contradictions[]`
    (per-topic stance table with per-model stance cells),
    `partial_coverage[]` (per-model-attributed bullets),
    `unique_insights[]` (per-model-attributed bullets), `blind_spots[]`
    (italicized bullets — the gaps NO model addressed), plus a
    collapsible raw-JSON disclosure (`ui.expansion` + `ui.code`) for
    full audit. `gui/pages/ask.py::_format_judge_analysis_markdown`
    renders the equivalent as markdown (stance table + bullets +
    `<details>`/fenced JSON block) in the Multi-Cast synthesis card.
  - **Fix C — MODEL LABEL CONTRACT in Judge prompt**: the Judge system
    prompt now contains an explicit MODEL LABEL CONTRACT block
    instructing the model to use the EXACT `<LABEL>` string from the
    answers_block section header (`## <LABEL> (<model_id>)`) in every
    `model` field of its JSON output — never invent generic labels
    like `model_a`, never fall back to `beast` when `beast` isn't a
    section label, never use the parenthesized `model_id`. The prompt
    includes a concrete example showing correct vs. incorrect label
    usage. This fixes the prior defect where the Judge emitted legacy
    slot names (`synthesis=`, `beast=`) instead of the per-model
    identifiers the human needs to attribute stances and insights.
  - 6 new regression tests in `tests/test_model_council_fusion.py`
    (panel timeout, Judge timeout, Synth timeout, Judge prompt
    contract, ask.py reads judge_analysis, panel reads judge_analysis).
    All 141 council + import/layering tests pass (was 135 before).
- **Phase 1 Fusion pipeline (prior cycle)**: `routes/model_council.py`
  `POST /beast/compare-models` Beast synthesis now runs as a two-stage
  OpenRouter Fusion pipeline by default (replaces the legacy single-call
  bare comparison). Stage 1 (Judge-Beast): single `beast` slot call with
  the panel outputs, produces structured JSON
  `{status, analysis:{consensus[], contradictions[], partial_coverage[],
  unique_insights[], blind_spots[]}, responses[]}`. Stage 2 (Synth-Beast):
  second `beast` slot call reading ONLY the Judge JSON (no panel outputs,
  no retrieval — asymmetric information contract), produces the final
  fused answer. Response gains `fusion_answer` (str, the Synth output,
  mirrored to `beast_conclusion` for legacy consumers) and
  `judge_analysis` (dict, full Judge JSON for audit). Legacy fields
  (`convergence`, `disagreements`, `unique_contributions`, `risks`,
  `recommended_decision`) are still populated from the Judge JSON —
  derived from `analysis.*` when present, falling back to old top-level
  keys for backward compat with the existing test mock and older Beast
  models. `synthesis_status = "completed"` only when both stages
  succeed; `"failed"` if either fails (but `judge_analysis` is still
  populated when Judge alone succeeds). The `beast` slot is reused for
  both stages — no new model slots added. Per-model panel outputs
  remain in `selected_models` for parallel human comparison. 22 new
  tests in `tests/test_model_council_fusion.py`; 97 existing council
  tests continue to pass.
- **Multi-Cast library bridge (prior cycle)**: `routes/model_council.py`
  `POST /beast/compare-models` now accepts a second model source:
  `selected_model_ids: list[str]` (OpenRouter model IDs from the
  `enabled_models` SQLite library). Each ID is routed via the new
  `_call_library_model_id` helper, which looks up the row in
  `enabled_models` for `display_name` + optional `custom_api_key`, then
  POSTs to OpenRouter's `/v1/chat/completions` using
  `AIP_OPENAI_API_KEY` (or the per-row custom key). The existing
  `selected_model_slots` path is unchanged (backward compat). The
  `≥2 usable models` gate now counts slots + library IDs combined.
  `PerModelResult` gained a `source` field (`"slot"` default,
  `"library"` for library-sourced). If `model_provider` is None but
  ≥2 library IDs are supplied, comparison still runs; Beast synthesis
  is skipped with `synthesis_status="unavailable"` (no "beast" slot).
- **Commit 14d3a73**: `model_slot_resolver.py` now detects HTTP 429 before
  `raise_for_status()`, returns error dict with `retry_after`. Added
  `backfill_state`, `rate_limited`, `rate_limited_reason`, `cycle_active`
  to `/corpus/embedding-progress` response.
- **Multi-cast + turn_id cycle**: `routes/chat.py` now echoes `turn_id` in
  every WebSocket `response` payload (including the no-provider degraded
  path). The existing `POST /beast/compare-models` endpoint already
  supported the no-existing-answer case, so the GUI's Multi-Cast feature
  reuses it directly — no new endpoint needed.

## Ownership
Each storage adapter owns its database schema and migration path.
The API owns its router definitions. The CLI owns its command structure.

## Key Files / Subdirectories
| Path | Role |
|------|------|
| `api/` | FastAPI app + 29 route files — HTTP interface to all AIP capabilities |
| `api/app.py` | App factory, startup/scheduler coordination, Sexton startup task |
| `api/routes/corpus.py` | Corpus routes — enriches with Sexton state |
| `cli/` | Supplementary CLI commands (collaborators, plugins) — main CLI is `aip.cli` |
| `artifact_store_versioned.py` | Version-preserved artifact storage (never overwrites) |
| `ecs_store_persistent.py` | Persistent ECS state machine backed by SQLite |
| `event_store_queryable.py` | Append-only event store with query capability |
| `lexical/` | FTS5 full-text search store |
| `vector/` | Vector search factory — pgvector / sqlite-vss / in-memory |
| `canonical/` | Canonical store with DEFINER enforcement |
| `project/` | SQLite project store |
| `auth/` | Authentication store and session management |
| `budget_store_sqlite.py` | Token budget tracking and warning thresholds (SQLite) |
| `vigil/` | Vigil storage (canonical drift records) |
| `embedding/` | Embedding provider abstraction |
| `autonomy/` | Autonomy gate with audit trail |
| `mcp/` | Model Context Protocol server and tool dispatch |
| `codex/` | Codex store for structured knowledge |
| `entity/` | Entity store (SQLite) |
| `middleware/` | Rate limiter and other middleware |
| `graph_store.py` | Graph store for knowledge graph |
| `corpus_turn_store.py` | Corpus turn storage (canonical DDL) |
| `model_slot_resolver.py` | Model slot dispatch — per-slot provider routing |
| `config_watcher.py` | Hot config reload watcher |

## Work Guidance
- Adding an API endpoint: create in the appropriate router file, ensure auth gate
  if admin-scoped, add integration test, update AGENTS.md Contracts section.
- Adding a new CLI command: add to `cli/`, register in the command group,
  verify it uses the shared DB path, add smoke test path if dogfood-critical.
- Modifying a store: never change the schema without a migration plan.
  Add a migration, not an in-place schema change. Test with existing `state.db`.
- Vector backend changes: the factory in `vector/` determines the backend from config.
  Adding a new backend requires factory registration + test for all modes.

## How to Test
```bash
uv run pytest tests/test_api.py
uv run pytest tests/test_cli.py
uv run pytest tests/test_stores.py
# Smoke test the full adapter layer:
bash scripts/dogfood_smoke_test.sh
```


# ============================================================
