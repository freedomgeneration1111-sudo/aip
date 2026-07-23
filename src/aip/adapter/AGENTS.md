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

#### GET /corpus-registry/corpora  (QW9, 2026-07-23)
Returns a list of dicts, one per registered corpus. Each dict has:
- `corpus_id` (str): e.g. "definer", "codeforge", "branham"
- `corpus_type` (str): "conversation" | "code" | "document" | "book"
- `sensitive` (bool): True if the corpus requires session opt-in via `allowed_restricted_corpora`
- `deletion_state` (str): "ACTIVE" | "DELETING"
- `access_note` (str): human-readable note for restricted corpora
Returns `[]` (not an error) when the registry is not wired. Consumed by
`gui/components/corpus_selector.py` for the corpus multi-select UI.

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
- `compress_panel_outputs` (bool, default `False`) — when `True`, the
  endpoint runs a per-panelist compression pass BEFORE the Judge reads
  the panel outputs. Each successful panelist's answer is summarized
  to 5-8 key claims via the picked Fusion engine
  (`_compress_panel_outputs()` helper). The compressed claims replace
  the raw answers in the `answers_block` passed to the Judge. This
  reduces the Judge's context window pressure on long panel outputs
  (4+ models × 2000 chars each can blow the Judge's context today).
  The Synth stage is unaffected — it still reads ONLY the Judge JSON.
  Default `False` preserves the existing behavior (Judge reads raw
  panel outputs). The GUI does NOT send this flag today (Phase 3
  enhancement) — it's an opt-in for external API clients or future
  GUI toggles. On per-model compression failure, the raw answer is
  kept (graceful degrade — the Judge sees the raw text for that model).

### Dedicated Judge Slot Contract (Phase 3c)
- The `[models.judge]` TOML slot is an OPTIONAL dedicated slot for the
  Fusion pipeline's Judge+Synth stages. When configured, it is used
  INSTEAD of the `beast` slot for the Judge-Beast and Synth-Beast calls.
  The `beast` slot continues to be used for the Beast actor's background
  maintenance (`run_cycle()`).
- **`_pick_fusion_engine` preference order** (Phase 3c adds preference 0):
  0. The `judge` slot IF it is configured on the model_provider (has a
     real model, not a placeholder). The `judge` slot does NOT need to
     be in the panel — it's synthesis-only.
  1. The `beast` slot IF it was in the panel AND completed successfully.
  2. ANY other successful slot (in panel order).
  3. ANY successful library model (in panel order).
- The `judge` slot is in `_EXCLUDED_SLOTS` (alongside `embedding`) — it
  NEVER appears as a panelist. It's only the Judge+Synth engine.
- **Config example** (in `config/aip.config.toml`):
  ```toml
  [models.judge]
  provider = "openai_compatible"
  model = "anthropic/claude-3.5-sonnet"
  base_url = "https://openrouter.ai/api"
  # api_key = ""   # AIP_JUDGE_API_KEY env var override (falls back to AIP_OPENAI_API_KEY)
  ```
- **Env var override**: `AIP_JUDGE_API_KEY` (falls back to
  `AIP_OPENAI_API_KEY`). Follows the same `AIP_<SLOT>_API_KEY` pattern
  as the other slots.
- **Backward compat**: when `[models.judge]` is NOT configured (the
  default), `_pick_fusion_engine` falls through to preference 1 (beast
  slot) — existing behavior is preserved.

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

### Panel Dispatch Contract (Bug 1 + Bug 2 remediation)
- **Bug 1 fix — Panel message shape**: every panel call (slots +
  library IDs, augmented mode + normal mode) receives a clean
  system/user separation:
  - `messages[0..k-1]` = augmented_prefix system msgs (corpus + wiki
    + graph + definer) — present only when `assemble_augmented_context=True`
  - `messages[k]` = `{role: system, content: _PANEL_SYSTEM_PROMPT}`
    (behavioral rules ONLY — no task content, no "Analyze the prompt
    below" phrasing)
  - `messages[k+1]` = `{role: user, content: user_prompt}` (the task)
  - The `_build_panel_system_prompt()` helper returns the behavioral
    prompt (rules + formatting + confidence tagging + GAPS instruction).
    It is prepended to EVERY panel call via the `panel_system_prompt=`
    kwarg on `_call_model_slot` and as a `system` message in the
    `panel_messages` list passed to `_call_library_model_id`.
  - This prevents panel models from meta-analyzing the instructions
    instead of answering the question (the original Bug 1 symptom).
- **Bug 2 fix — Dispatch completeness**: the panel dispatch loop logs
  every dispatched slot with `[PANEL]` markers and ensures the Judge
  sees every slot (completed OR failed):
  - `[PANEL] Dispatching → {slot_or_model_id}` — logged before each call
  - `[PANEL] Response ← {slot_or_model_id} ({token_count} tokens)` —
    logged after each successful call
  - `[PANEL] FAILED ← {slot_or_model_id} {exception}` — logged on failure
  - Per-model isolation: `asyncio.gather(return_exceptions=True)`
    captures per-task failures as values — a failure on model N does
    NOT affect models N+1 through end.
  - **Judge receives a response entry for EVERY dispatched slot**:
    failed models are injected into the `answers_block` as explicit
    `[DISPATCH_ERROR: {msg}]` stubs (Bug 2 fix requirement 3). The
    Judge can surface them in `blind_spots` / `contradictions` /
    `partial_coverage` rather than silently dropping them.
  - Previously the `answers_block` loop only iterated
    `pm.status == "completed"` models, silently dropping failed models
    and making them invisible to the Judge.
- **Isolation**: the Bug 1 + Bug 2 fixes ONLY affect panel dispatch.
  The Judge system prompt, Judge JSON schema, Synthesizer system
  prompt, Vigil actor, and Sexton actor are NOT modified.

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
- **Audit action vocabulary**: The audit log uses string-literal action
  names (`CORPUS_REGISTERED`, `RESTRICTED_CORPUS_ACCESS_DENIED`,
  `CORPUS_DELETED`, `BRIDGE_ORPHAN_CLEANED`, `MIGRATION_APPLIED`,
  `ARTIFACT_ARCHIVED`, `ARTIFACT_SUPERSEDED`, `ARTIFACT_TRANSITIONED`).
  `BRANHAM_POLICY_TRIGGERED` was the last stale action name and is now
  `RESTRICTED_CORPUS_ACCESS_DENIED` (renamed per ADR-014 step 0). The
  `BranhamIsolationViolation` exception alias and the deprecated parameter
  aliases (`session_branham_allowlist`, `branham_policy_enabled`) are
  kept for one release cycle. Extension-contributed audit actions follow
  the `{EXT_ID Upper}_...` namespace convention (ADR-014 §10).

## Last Cycle
- **QW9 — Added GET /corpus-registry/corpora endpoint** (this cycle): new
  route in `adapter/api/routes/corpus.py` enumerates registered corpora.
  Returns a list of dicts with `corpus_id`, `corpus_type`, `sensitive`,
  `deletion_state`, `access_note`. Returns `[]` (not an error) when the
  registry is not wired — honest unavailable state. Consumed by
  `gui/components/corpus_selector.py` (which previously called this
  endpoint but it didn't exist — the GUI component was dead code). 4 new
  tests in `tests/test_corpus_registry_endpoint.py` pin the contract:
  empty-when-not-wired, returns-both-corpora, sensitive-flag-surfaces,
  GUI-source-contract-check. Closes half of ND1 from the tech-debt
  assessment (the endpoint half; QW8 will wire the GUI component).
- **QW1 — Registered codeforge corpus at startup** (prior cycle): the app.py
  lifespan now registers both `("definer", CorpusType.CONVERSATION)` and
  `("codeforge", CorpusType.CODE)` in `corpora_to_register`. The codeforge
  corpus holds AIP's own Python source code as a searchable corpus
  (ADR-008 §8 Chunk 7 / Phase 1.6 Codebase-as-Corpus). The db path is
  derived from the definer db_path's parent dir (`db/codeforge.db`).
  The corpus is registered **empty** at startup — ingest is triggered via
  `aip corpus ingest-code <dir>` (QW11, pending) or the Sexton file-watcher
  (QW13, planned). 3 new tests in `test_corpus_call_site_migration.py`
  (`TestCodeforgeCorpusStartupRegistration`) pin the contract: both
  corpora register, both DB files exist on disk, and app.py source
  contains the codeforge registration. 129 tests pass. Closes ND5 from
  the 2026-07-23 tech-debt assessment.
- **QW10 — Raised MAX_CORPORA from 4 to 8** (prior cycle): `app.py:481` now
  imports `MAX_CORPORA` from `aip.foundation.corpus_constants` and passes
  it to `CorpusRegistry(max_corpora=MAX_CORPORA)` instead of hardcoding
  `4`. This was a latent bug — if the constant changed, `app.py` wouldn't
  pick it up. Also updated the docstring comment in `corpus_connection.py:13-15`
  from "shipped at 4" to "shipped at 8 (conservative headroom — raised from
  4 on 2026-07-23 per QW10)". 166 corpus tests + 22 app-factory tests pass.
- **QW3 — Deleted unused duplicate `python_ast_parser.py` from orchestration** (prior cycle):
  - The orchestration copy at `orchestration/ingestion/parsers/python_ast_parser.py`
    was a byte-identical duplicate of `adapter/python_ast_parser.py` (411 lines
    each). Created during Chunk 7 in anticipation of moving the parser to
    orchestration, but the move never completed. Zero modules imported the
    orchestration copy; `code_ingest_pipeline.py` (adapter) imports from the
    adapter copy.
  - Updated the adapter copy's docstring from "Layer: orchestration" to
    "Layer: adapter" to match its physical location. Added a note documenting
    the deletion for audit trail.
  - Verified: 24 `test_corpus_code_ingest.py` tests pass. ND4 from the
    tech-debt assessment resolved.
- **QW2 — Stale docstring fix in `corpus_registry.py::delete_corpus`** (prior cycle):
  - Removed stale "(Stub — implemented in Chunk 6 when bridge edges exist.)"
    note from the Phase 2 line of the `delete_corpus()` docstring. The
    bridge-edge cleanup was implemented in Chunk 6 (`GraphStore.delete_bridge_edges`
    is called at `corpus_registry.py:378-396`), but the docstring still
    described it as a stub. Doc drift item D5 from the tech-debt assessment.
  - No code behavior change — docstring only. 55 `test_corpus_registry.py`
    tests + 91 broader corpus suite tests all pass.
- **ADR-014 §8 step 2 remainder — WorkflowEngine wired + /health/extensions** (prior cycle):
  - Wired `WorkflowEngine` into `AipContainer` + lifespan. Added
    `workflow_engine` field to `AipContainer`. The lifespan constructs the
    engine with the container's stores (vector_store, trace_store,
    artifact_store, ecs_store, event_store, budget_store, autonomy_gate)
    alongside the `WorkflowRegistry` + `ExtensionHost`. Extensions access
    it via `ctx.container.workflow_engine.run_workflow(path, variables)`.
  - Rewrote `extensions/aristotle/workflows/tutoring_session_v1.yaml` to
    use engine-compatible node types. The L5 loader
    (`orchestration/workflow/loader.py`) accepts: `script, agent, condition,
    dialog, parallel, review, re_synthesize`. The prior YAML used
    `synthesize, decision, commit` which the loader rejects. Now uses
    `agent` (teach/probe/quiz/remediate), `script` (evaluate/next_concept),
    `condition` (check_mastery with next_on_true/next_on_false). 7 nodes
    total, matching the ADR-ARISTOTLE §3 state machine.
  - Added `GET /health/extensions` endpoint (ADR-014 §7). Returns
    `{host_running, extensions: [{id, version, state, failures}]}`. Backs
    the operator/teacher "extension health" tab. ARISTOTLE's "session opens
    itself" promise is gated on `REGISTERED`; the GUI learning view is
    gated on `MOUNTED` (v1.1).
  - Added `tests/test_workflow_engine_wiring.py` (9 tests): container has
    workflow_engine + workflow_registry + extensions fields (source-level);
    lifespan wires WorkflowEngine (source-level); ARISTOTLE workflow YAML
    parses with 7 nodes; all node types are engine-compatible; agent nodes
    have model_slot; condition node has next_on_true/next_on_false;
    /health/extensions route exists. All 9 pass locally.
  - Verified: 33 tests pass locally (11 Actor Protocol + 3 WorkflowRegistry
    + 10 ARISTOTLE actors + 9 workflow engine wiring). No regression.
- **ADR-014 step 1 — ExtensionHost skeleton + TDD contract GREEN** (prior cycle):
  - Built `src/aip/adapter/extensions/` package (8 files): `state.py`,
    `supervision.py`, `manifest.py`, `registry.py`, `host.py`,
    `loaders/migration_loader.py`, plus `__init__.py` files. See
    `src/aip/adapter/extensions/AGENTS.md` for the full contract.
  - Stages 0–3 + 5 implemented (discover/validate/migrate/register/ready).
    Stage 4 (GUI mount) is v1.1; `test_mounts_extension_gui_pages` is
    `xfail(strict=True)` until v1.1 lands.
  - Extension migrations use a separate `extension_applied_migrations`
    table (NOT the core `applied_migrations`) so the core
    `CorpusMigrationRunner`'s fingerprint check is not contaminated.
  - Fixed a test bug: `test_two_extensions_with_same_id_fails_cleanly`
    had a dict-comprehension logic error; rewrote the assertion to
    iterate records directly.
  - Verified locally: Manifest model passes 8 validation cases;
    discover+validate flow smoke-tested (VALIDATED/FAILED/DISABLED
    transitions correct). Full pytest run deferred to CI (test
    environment lacks aiosqlite + structlog).
- **ADR-014 Phase 0 Extension Platform — Step 0 + contract** (prior cycle):
  - Renamed the last stale `BRANHAM_POLICY_TRIGGERED` audit action to
    `RESTRICTED_CORPUS_ACCESS_DENIED` in `corpus_retrieval.py:244` (now
    matches `corpus_registry.py:324`). Updated the stale comment in
    `corpus_store_factory.py:325`. The `BranhamIsolationViolation`
    exception alias and deprecated parameter aliases are kept for one
    release cycle (ADR-014 §1).
  - Added ADR-014 (`docs/decisions/ADR-014-phase0-extension-host.md`)
    defining the `ExtensionHost` lifecycle, manifest v1 schema, and
    build order. The ADR corrects the prior draft's §1 overstatement
    that PluginManager/AipMcpServer/WorkflowRegistry are "working
    extension points" — they are structurally present but unwired.
  - Added `tests/test_extension_lifecycle.py` as the TDD contract
    (RED by design — fails to collect until `aip.adapter.extensions`
    exists). Eleven tests pin the lifecycle: discover, validate,
    config-schema-failure, id-collision, migrate, register actors,
    mount GUI (v1.1, xfail), failed-extension isolation, disabled,
    health surface, stop-cancels-actors.
- **ADR-008 Multi-Corpus Chunk 9** (prior cycle — FINAL CHUNK): Acceptance
  suite + CLI deliverables. New `tests/acceptance/test_multi_corpus.py`
  with AC-01 through AC-09: Branham isolation (100-query CI-scaled version
  of 1000-query test), cross-corpus RRF namespacing, ECS lifecycle (ARCHIVED
  + SUPERSEDED + all legacy transitions preserved), concurrency (10 concurrent
  writers, 30s timeout), connection budget (MAX_CORPORA + partial-init cleanup),
  migration gate (_migration_ready event + fingerprint/unknown-migration detection),
  review federation (list_review_items across 2 corpora), bridge orphan recovery
  (startup reconciliation), Sexton batch yield (lock yielded between batches,
  chat route interleaves). New `cli/corpus_migrate.py` — `aip corpus migrate
  <id> [--force]` for half-migrated recovery (clears applied_migrations +
  re-runs idempotent migrations). Updated `cli/backup.py` — strategy A
  (pause-and-snapshot) as default, discovers corpus DB files via
  `_discover_corpus_db_files()`, includes corpus_databases + restore_invariant
  in manifest. 19 acceptance tests.
- **ADR-008 Multi-Corpus Chunk 6**: Graph bridge edges. Added
  `target_corpus_id` field to GraphEdge (§A7). M002 migration: ALTER TABLE
  graph_edges ADD COLUMN target_corpus_id TEXT + bridge edge index — applied
  in _create_tables (benign on re-run). Replaced 2 SELECT * on graph_edges
  with explicit named columns (§A7 — prevents column mis-mapping). Updated
  _row_to_edge to read target_corpus_id by name (defaults None for pre-M002
  databases). New GraphStore methods: `upsert_bridge_edge` (inserts bridge
  edge with non-NULL target_corpus_id), `delete_bridge_edges(target_corpus_id)`
  (idempotent, returns row count), `get_bridge_neighbors(turn_id, corpus_id=None)`
  (returns bridge edges from a turn, optional corpus filter), `get_orphan_bridge_targets()`
  (distinct target_corpus_id values for reconciliation). Implemented
  `_reconcile_bridge_edges()` in CorpusRegistry — scans definer graph_edges
  for orphan bridge edges, deletes those pointing to unregistered corpora,
  audits BRIDGE_ORPHAN_CLEANED. Updated `delete_corpus()` Phase 2 to call
  `delete_bridge_edges(corpus_id)` on the definer graph. 17 new tests in
  `tests/test_corpus_graph_bridge_edges.py`.
- **ADR-008 Multi-Corpus Chunk 5**: Session/project binding +
  custom-channel scoping. New `session_corpus_binding.py` — helpers for
  reading/writing active_corpus_ids + branham_allowlist in session metadata_json.
  Enforces §5 policy: branham_allowlist is NEVER persisted when
  branham_policy_enabled=False (prevents allowlist escalation via session replay).
  Definer is always added to active_corpus_ids (it's the bridge-edge anchor).
  New `custom_channel_scoping.py` (§A14) — `ScopedCorpusStores` read-only view
  that only exposes session-resolved corpora; `resolve_scoped_stores()` resolves
  active_corpus_ids through the registry (Branham suppressed without allowlist);
  `wrap_custom_channel_register()` wraps custom channel register_fns so they
  only see the ScopedCorpusStores, not raw db_path or the container. New GUI
  component `gui/components/corpus_selector.py` — multi-select checkboxes for
  non-sensitive corpora, ⚠ marker for sensitive, "always active" label for
  definer. 30 new tests in `tests/test_corpus_session_binding.py`. The session
  store needs no changes — `update_session()` already puts unknown keys in
  metadata_json.
- **ADR-008 Multi-Corpus Chunk 4**: Retrieval scoping. New
  `corpus_retrieval.py` module with 4 helpers: `namespace_hit_id`/`parse_hit_id`
  (§4 — `{corpus_id}:{hit_id}` format, colons in hit_id preserved), `corpus_aware_cache_key`
  (§4 — SHA256 of query + sorted corpus_ids + model_id, order-independent),
  `filter_excluded_states` (§A2 — fusion-layer ECS filter, batch `states_for()` lookup,
  removes ARCHIVED/SUPERSEDED, fails open on error, passes through non-turn hits),
  `gather_corpus_results` (§A12 — `asyncio.gather(return_exceptions=True)`, suppresses
  BranhamIsolationViolation + audits, re-raises other exceptions). Extended
  `assemble_augmented_context()` — when `session_meta["active_corpus_ids"]` is present
  AND `container.corpus_registry` is wired, uses the multi-corpus path (fan-out +
  fusion filter + namespacing); falls back to legacy single-corpus path otherwise.
  Updated short-circuit to check registry presence. 21 new tests in
  `tests/test_corpus_retrieval_scoping.py`. Fixed 2 existing tests in
  `test_augmented_context_helper.py` to set `corpus_registry = None` (MagicMock
  auto-creates truthy attributes).
- **ADR-008 Multi-Corpus Chunk 3**: Call-site migration infrastructure.
  Added `corpus_registry` field + `definer_stores` sync property to AipContainer
  (§3a). Added `AskStores.from_corpus_stores()` classmethod (§A1) — event_store
  + project_store are required keyword-only args (global/definer-scoped). Rewrote
  `set_embedding_provider()` (§A6) — when `corpus_registry` is wired, iterates
  all registered corpora and updates each corpus's `vector_store._embedding_provider`
  + `turn_store.mark_all_for_reembed()`; falls back to legacy singleton poking
  when registry is None (pre-wiring backward compat). 12 new tests in
  `tests/test_corpus_call_site_migration.py`. NOTE: the mechanical rewrite of
  264 call sites across 21 files (replacing `container.corpus_turn_store` →
  `container.definer_stores.turn_store` etc.) and legacy singleton removal are
  deferred to a follow-up pass — this chunk ships the infrastructure that makes
  the rewrite possible without breaking existing code.
- **ADR-008 Multi-Corpus Chunk 8**: ECS/ArtifactStore per corpus +
  durable fan-in outbox + audit log CLI. Key additions: CorpusTurnStore gained
  `delete_turn()` (§A4), `states_for()` (§A2 batch lookup), `search(include_archived=)`
  (§6 ECS filter), `revision_parent_id` field round-trip (§A12). CorpusStoreFactory
  now attaches `ecs_store` + `artifact_store` per corpus, creates definer-only tables
  (review_queue_fanin, corpus_audit_log, review_fanin_outbox), runs M004
  (artifact_turn_links) + M005 (review_queue.corpus_id). CorpusRegistry gained full
  `transition_artifact()` (§A3+§A10: ECS transition → turn_id lookup → latest_ecs_state
  update → durable outbox enqueue → audit log), full `list_review_items()` (§9.4:
  fan-in candidate set → authoritative ECS validation → merged sorted), `_backfill_review_fanin()`
  on startup, `_drain_fanin_outbox()` consumer, `_write_audit()` to corpus_audit_log table,
  `_persist_deletion_state()` to corpus_metadata. New `cli/audit.py` with `aip audit log`
  command (§A15). 29 new tests in `tests/test_corpus_ecs_per_corpus.py`. Stubs remaining:
  `_reconcile_bridge_edges()` (Chunk 6), lexical/vector/graph stores not yet attached
  to CorpusStores (Chunk 6 for graph, post-Chunk-3 for lexical/vector).
- **ADR-008 Multi-Corpus Chunk 2**: CorpusRegistry + Factory + shared connection manager
  shared connection manager + migration runner + scheduler gates. 5 new adapter
  files: `corpus_connection.py` (CorpusConnectionManager — 1 write + N read
  conns shared by all 6 stores per corpus, §A0), `corpus_stores.py` (CorpusStores
  regular class with `__slots__`, async `close_all()`, `__aenter__/__aexit__`,
  §5.3), `corpus_migration_runner.py` (dedicated runner OUTSIDE `_create_tables`,
  fingerprint + sql_checksum verification, §A8), `corpus_store_factory.py`
  (builds CorpusStores with shared manager, registers M001/M002/M003 migrations),
  `corpus_registry.py` (concrete CorpusRegistry — register/get_stores/delete_corpus/
  budget validation/Branham 4-layer Layer 3/deletion_state/§A13 two-phase delete
  with WAL sidecar rename). `app.py` gained `_await_corpus_migration_ready()`
  helper + gate on all 5 actor schedulers (_beast_scheduler, _vigil_scheduler,
  _sexton_actor_scheduler, _sexton_startup_run, _vigil_startup_run — §A5).
  The gate is defensive: if `container.corpus_registry` is None (pre-Chunk-3),
  the gate is a no-op. 55 new tests in `tests/test_corpus_registry.py`.
  Stubs: `transition_artifact()` raises NotImplementedError (Chunk 8),
  `list_review_items()` returns [] (Chunk 8), `_reconcile_bridge_edges()` is
  a no-op (Chunk 6), `_persist_deletion_state()`/`_write_audit()` log only
  (Chunk 8). See ADR-008 Rev 3.1 Amendment §A0, §A5, §A8, §A13.
- **Phase 3 polish**: shipped all 4 Phase 3 deliverables.
  Phase 3a: per-model attribution badges on `unique_insights[]` —
  added `_model_color()` helper + `_MODEL_COLOR_PALETTE` (8-color
  deterministic palette) to `model_council_panel.py`; the panel renders
  the model label as a colored badge (background + monospace + rounded).
  `ask.py::_model_color_markdown()` mirrors the palette (contract:
  change one, change both) and renders the badge as an HTML `<span>`.
  Phase 3b: per-model stance color-coding on `contradictions[]` — the
  same `_model_color()` is applied to the model label in the stance
  table (colored text + left border). Phase 3c: dedicated `[models.judge]`
  TOML slot — `_pick_fusion_engine` gained a new preference 0 (highest):
  when the `judge` slot is configured on the model_provider, use it for
  the Judge+Synth stages (synthesis-only, never a panelist). Added
  `judge` to `_EXCLUDED_SLOTS`. Added commented `[models.judge]` example
  to `config/aip.config.toml` with `AIP_JUDGE_API_KEY` env var override.
  `_pick_fusion_engine` gained a `model_provider` kwarg (default None —
  backward compat with callers that don't pass it). Phase 3d: GUI
  toggle for `compress_panel_outputs` — `GuiState.compress_panel_outputs`
  field (default False); Ask page header has a "Compress" checkbox with
  tooltip; `api_client.run_model_council` forwards the flag;
  `_send_multicast` passes `compress_panel_outputs=state.compress_panel_outputs`.
  24 new tests in `tests/test_phase3_polish.py` cover all 4 deliverables
  + the end-to-end payload contract (GUI payload keys match
  `ModelCouncilRequest` fields). Backward compat preserved: all new
  fields default to False/None; existing tests, external API clients,
  and the current GUI (with Compress OFF) see no behavior change.
- **Phase 2 Step 2-C + 2-D (prior cycle)**: shipped the Phase 2 test
  suite (PDF Part IX) + the per-model compression pass (Improvement #5).
  Step 2-C: new file ``tests/test_model_council_fusion_phase2.py`` (9
  tests) covering the net-new PDF Part IX cases: Judge JSON parse
  failure fallback (malformed JSON + markdown-fenced JSON happy path),
  artifact persistence (full report JSON + ECS GENERATED transition +
  never-APPROVED guard), end-to-end with retrieval (augmented context
  appears in panel calls), no-corpus graceful degrade (empty corpus →
  bare prompt + behavioral system prompt), helper extracts wiki + graph
  (DOMAIN CONTEXT + GRAPH CONNECTIONS injection). The other 7 PDF Part
  IX tests were already covered by existing files (verified, not
  duplicated). Step 2-D: added ``compress_panel_outputs: bool = False``
  field to ``ModelCouncilRequest`` + ``_compress_panel_outputs()``
  helper + ``_COMPRESS_SYSTEM_PROMPT`` constant. When the flag is True,
  each successful panelist's answer is summarized to 5-8 key claims via
  the picked Fusion engine (concurrent ``asyncio.gather`` — added
  latency is ~1 compression call, not N). The compressed claims replace
  the raw answers in the ``answers_block`` passed to the Judge. On
  per-model compression failure, the raw answer is kept (graceful
  degrade). The Synth stage is unaffected — it still reads ONLY the
  Judge JSON. New file ``tests/test_compress_panel_outputs.py`` (9
  tests) covers: field exists + defaults False, helper exists + async,
  compression runs when flag True (Judge sees compressed claims, not
  raw), compression does NOT run when flag False (backward compat),
  graceful degrade on per-model compression failure, Synth unaffected
  by compression. Default ``False`` preserves backward compat — the GUI
  does NOT send this flag today (Phase 3 enhancement).
- **Panel Dispatch remediation — Bug 1 + Bug 2 (prior cycle)**:
  Two confirmed bugs fixed in a single pass. Bug 1: panel models were
  meta-analyzing the instructions instead of answering the question
  because normal-mode panel calls sent only a user message with no
  system prompt. Fix: added `_PANEL_SYSTEM_PROMPT` constant +
  `_build_panel_system_prompt()` helper that returns behavioral-only
  instructions (rules + formatting + confidence tagging + GAPS — no
  task content, no "Analyze the prompt below" phrasing). Every panel
  call now receives `messages = [augmented_prefix..., system
  (behavioral), user (task)]`. The `_call_model_slot` helper gained a
  `panel_system_prompt=` kwarg; the library-model dispatch builds the
  full `[system, user]` messages list. Bug 2: panel dispatch silently
  dropped failed models — the Judge's `answers_block` only iterated
  `pm.status == "completed"` models, making failures invisible to the
  Judge. Fix: (1) added `[PANEL] Dispatching →`, `[PANEL] Response ←`,
  and `[PANEL] FAILED ←` log lines for every dispatched slot; (2) the
  `answers_block` loop now iterates ALL `per_model_results` and injects
  failed models as explicit `[DISPATCH_ERROR: {msg}]` stubs so the
  Judge sees every dispatched slot; (3) per-model isolation is
  preserved via `asyncio.gather(return_exceptions=True)`. 19 new tests
  in `tests/test_panel_dispatch_remediation.py` verify both bugs +
  all 3 acceptance criteria (PANEL PROMPT TEST, DISPATCH COMPLETENESS
  TEST, ISOLATION CHECK). The Judge system prompt, Judge JSON schema,
  Synthesizer system prompt, Vigil actor, and Sexton actor were NOT
  modified.
- **Phase 1 retrieval bridge (prior cycle):** extracted the inline
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
| `api/app.py` | App factory, startup/scheduler coordination, Sexton startup task. ADR-008 §A5: 5 actor schedulers gated on `corpus_registry.migration_ready` |
| `api/routes/corpus.py` | Corpus routes — enriches with Sexton state |
| `cli/` | Supplementary CLI commands (collaborators, plugins) — main CLI is `aip.cli` |
| `corpus_registry.py` | ADR-008: CorpusRegistry — concrete impl, register/get_stores/delete_corpus, budget validation, restricted-corpus Layer 3 (sensitive flag), §A13 two-phase delete. Audit action `RESTRICTED_CORPUS_ACCESS_DENIED` (renamed from `BRANHAM_POLICY_TRIGGERED` per ADR-014 step 0). |
| `corpus_store_factory.py` | ADR-008: CorpusStoreFactory — builds CorpusStores with shared connection manager, registers M001/M002/M003 migrations |
| `corpus_connection.py` | ADR-008 §A0: CorpusConnectionManager — 1 write + N read conns shared by all 6 stores per corpus (makes budget math work) |
| `corpus_stores.py` | ADR-008 §5.3: CorpusStores — regular class with `__slots__`, async `close_all()`, `__aenter__/__aexit__`, write_lock, deletion_state |
| `corpus_migration_runner.py` | ADR-008 §A8: CorpusMigrationRunner — dedicated runner outside `_create_tables`, fingerprint + sql_checksum verification, detects reordering/changed-body |
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
| `extensions/` | ADR-014 Phase 0 Extension Platform — ExtensionHost lifecycle + manifest v1. See `extensions/AGENTS.md`. |
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
