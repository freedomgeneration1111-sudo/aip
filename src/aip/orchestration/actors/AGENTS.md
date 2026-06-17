# ============================================================

# Actors — Agent Navigation
> Beast, Vigil, Sexton. Three actors, non-overlapping domains.

## Purpose
Actors are background scheduled processes that maintain system health,
canonical integrity, and intervention readiness. They do not process
user requests — they monitor and intervene on the system's behalf.

## Actor Roles (Strict Separation)

### Beast
- **Domain**: Corpus health and entity integrity
- **Runs**: Background scheduler, configurable interval via `[beast]` config
- **Responsibilities**: Health checks, corpus maintenance, entity integrity checks,
  L4 trajectory monitoring
- **Does NOT**: Handle user requests, make canonical decisions, classify failures

### Vigil
- **Domain**: Canonical monitoring and model-slot re-evaluation
- **Runs**: Background scheduler
- **Responsibilities**: Monitor canonical store, trigger model-slot re-evaluation,
  flag canonical drift
- **Does NOT**: Modify corpus, classify failures, handle ingestion

### Sexton
- **Domain**: Background maintenance — tagging, embedding, wiki, graph, failure classification (ADR-011)
- **Runs**: Background scheduler (300s cadence) + immediate startup run
- **Responsibilities**: Turn tagging, embedding pass, wiki generation, graph extraction,
  failure classification (delegated to `sexton/` subsystem)
- **Does NOT**: Handle user requests, modify canonical store, run health checks

## Contracts (What This Module Promises to Consumers)

### Sexton State Contract (Consumed by adapter/api/routes/corpus.py and gui/pages/corpus.py)
- **Backfill state**: Read via `sexton._embedding_backfill_state`
  (computed by `_compute_embedding_backfill_state()`)
- **Valid states**: `not_configured`, `configured_idle`, `backfill_pending`,
  `backfill_running`, `partially_embedded`, `embedded`, `degraded`, `failed`,
  `rate_limited`
- **DO NOT** read `sexton_pass.state` — this attribute does not exist
- **DO NOT** invent new states without updating this contract AND
  `gui/status_types.py` AND `gui/components/corpus_summary.py`
- **State priority in `_compute_embedding_backfill_state()`**: rate_limited is
  checked BEFORE mock/fake provider detection. If rate_limited is true, the
  state is `rate_limited` regardless of provider type.

### Sexton Rate Limit Contract
- Attributes: `_rate_limited` (bool), `_rate_limited_until` (float timestamp),
  `_rate_limited_reason` (str)
- When OpenRouter returns HTTP 429: Sexton sets `_rate_limited = True` with
  60s initial backoff, aborts after 3 consecutive 429s
- Auto-expires: `_run_embedding_pass()` checks `time.time() > _rate_limited_until`
  before proceeding
- API endpoint `/corpus/embedding-progress` enriches response with
  `rate_limited` and `rate_limited_reason` fields

### Sexton Concurrency Contract
- `_cycle_lock = asyncio.Lock()` prevents concurrent cycle execution
- `_cycle_active: bool` flag for introspection
- `run_cycle()` returns `{"skipped": True, "reason": "already_running"}` if locked
- Startup run and scheduler MUST NOT overlap — `app.py` stores startup task as
  `_sexton_startup_task`; scheduler `await`s it before entering periodic loop

### Sexton Status Summary Contract
- `get_status_summary()` returns dict with: `backfill_state`, `rate_limited`,
  `rate_limited_reason`, `cycle_active`, `embedded_count`, `total_count`
- Consumed by: `adapter/api/routes/corpus.py` → `gui/pages/corpus.py`

## Data Flows (In / Out)

### In (What actors receive)
- Config from `config/aip.config.toml` sections: `[beast]`, `[sexton]`, `[vigil]`
- Model slots via `ModelSlotResolver` — actors never call models directly
- Storage via Protocol interfaces from `foundation/protocols/`

### Out (What actors produce)
- **Sexton → adapter/api/routes/corpus.py**: `_embedding_backfill_state`,
  `_rate_limited`, `_rate_limited_reason`, `_cycle_active`, embedding counts
- **Sexton → adapter/api/routes/actors.py**: `get_status_summary()` dict
- **Beast → adapter/api/routes/beast_scan.py**: scan results, domain registry
- **Vigil → adapter/api/routes/vigil_quality.py**: quality metrics, drift records

### Cross-Folder Data Flows
```
sexton.py (_embedding_backfill_state, _rate_limited, _rate_limited_reason)
  → adapter/api/routes/corpus.py (GET /corpus/embedding-progress)
    → gui/pages/corpus.py (embedding_progress dict)
      → gui/components/corpus_summary.py (status badge)
      → gui/components/corpus_actions.py (button state)

sexton.py (get_status_summary)
  → adapter/api/routes/actors.py (GET /actors/status)
    → gui/pages/dashboard.py (actor status display)
```

## Known Gotchas
- **Concurrent cycles**: Startup run and scheduler firing simultaneously causes
  duplicate model calls. Always guard with `asyncio.Lock` and coordinate in `app.py`.
- **429 rate limiting**: OpenRouter returns 429; without explicit detection Sexton
  goes to "degraded" state instead of "rate_limited". Check for "429" in
  `ConnectionError` message in the embedding error handler.
- **Mock provider detection**: `type(provider).__name__` containing "Mock" or "Fake"
  triggers "degraded" state. Rate-limited check MUST come BEFORE mock detection
  in `_compute_embedding_backfill_state()` or rate-limited state is masked.
- **`sexton_pass.state` never existed**: Old code read this phantom attribute.
  Always read `_embedding_backfill_state` instead.
- **Wiki artifact_type must be `beast_wiki`** (NOT `sexton_wiki`): Sexton writes
  wiki artifacts with `artifact_type: "beast_wiki"`. All consumers (wiki_channel.py,
  chat.py, /wiki/articles route) filter on `beast_wiki`. The old value `sexton_wiki`
  made wiki artifacts invisible to all downstream consumers. Verified by
  `tests/test_wiki_artifact_contract.py`.
- **Wiki generation requires tagged corpus turns**: `_run_wiki_generation` skips
  domains where `get_domain_stats()` returns `total_turns=0`. Turns must have
  `tagging_version > 0` to be counted. If tagging hasn't run or hasn't
  completed, wiki generation silently skips all domains.
- **Domain registry path is CWD-relative**: `load_registry("docs/beast_domain_registry_v1.md")`
  uses a relative path. If the app CWD is not the project root, the registry
  won't be found and wiki generation returns `{"skipped": "registry_not_found"}`.
- **`beast` slot vs Beast actor — NOT the same thing (current cycle)**:
  The `beast` slot is a TOML-configured model routing key consumed by
  `ModelSlotResolver` — it routes to whatever model is configured in
  `[models.beast]` regardless of caller. The Beast actor is a background
  scheduled process that runs `run_cycle()` for corpus health checks.
  They share a name but are different concepts:
  - The Beast actor's `run_cycle()` is UNCHANGED by the Multi-Model
    dropdown work — it still runs on its scheduler and does its
    corpus-health job.
  - The `beast` slot is BORROWED by the Multi-Cast Fusion pipeline
    (`adapter/api/routes/model_council.py::_pick_fusion_engine`) for
    the Judge+Synth synthesis stages when the user picks ≥2 models
    in the GUI's unified dropdown. The slot is used ONLY for synthesis,
    NOT as one of the panel models (the panel is built from the user's
    explicit OpenRouter ID selections via `selected_model_ids`).
  - If a future cycle wants a dedicated Judge model that's different
    from the Beast actor's maintenance model, add a new `[models.judge]`
    TOML slot — `ModelSlotResolver` handles it without code changes.
  Do NOT conflate the actor with the slot. The slot is a routing key;
  the actor is a process.

## Last Cycle
- **Multi-Model dropdown auto-routing (this cycle, no actor code change)**:
  The GUI's Multi-Model dropdown now sends only OpenRouter IDs
  (`selected_model_ids`) — NOT TOML slot names — for the panel. The
  `beast` slot is used ONLY for the Judge+Synth synthesis stages
  (via `_pick_fusion_engine`), not as a panel model. **The Beast
  actor itself is UNCHANGED** — `run_cycle()` still runs on its
  scheduler and does its corpus-health job. Only the slot-vs-actor
  distinction was reinforced in this file's Known Gotchas section.
  See `gui/AGENTS.md` (GUI side) and `src/aip/adapter/AGENTS.md`
  (backend contract: new `skip_default_slots` field on
  `ModelCouncilRequest`) for the full contract.
- **Commit 14d3a73**: Added `asyncio.Lock` concurrency guard to Sexton `run_cycle()`.
  Added rate limit detection (429 handling with backoff). Fixed `_compute_embedding_backfill_state()`
  to check rate_limited before mock/fake detection. Added `_rate_limited`, `_rate_limited_until`,
  `_rate_limited_reason` attributes. Coordinated startup/scheduler in `app.py`.
- **Wiki contract fix**: Changed `artifact_type` from `"sexton_wiki"` to `"beast_wiki"`
  in `_write_wiki_artifact()` and `_wiki_needs_generation()`. This was the #1 bug —
  wiki artifacts were written with a type no consumer reads. Also added `sexton:wiki:%`
  to the `/wiki/articles` SQL LIKE patterns in `adapter/api/routes/wiki.py`.

## Key Files
| File | Role |
|------|------|
| `beast.py` | Beast actor — corpus health, entity checks, context advisory |
| `vigil.py` | Vigil actor — canonical monitoring, model-slot re-evaluation |
| `sexton.py` | Sexton actor — background maintenance (ADR-011 vigil cycle) |
| `domain_registry.py` | Beast domain taxonomy (28 domains) |

## Local Contracts
- Actors communicate through foundation Protocols — never direct calls between actors.
- Actor schedule intervals in `config/aip.config.toml`: `[beast]` for Beast, `[sexton]` for Sexton (300s cadence per ADR-011).
- All actor operations must be idempotent — safe to run multiple times.
- Actor failures must not cascade to user-facing pipelines.

## Work Guidance
- Adding a Beast check: add to `beast.py`, register in the Beast scheduler loop,
  add a test in `tests/test_actors.py`.
- Adding a Sexton intervention rule: update `sexton/` classification taxonomy,
  add rule derivation logic, update this file's Contracts section.
- Never add cross-actor direct calls. Use Protocol interfaces.

## How to Test
```bash
uv run pytest tests/test_actors.py -v
uv run pytest tests/test_actors.py -k "beast"
uv run pytest tests/test_actors.py -k "vigil"
uv run pytest tests/test_operator_console_fixes.py -v  # regression tests for recent fixes
```


# ============================================================
