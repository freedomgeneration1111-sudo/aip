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

### Model Slot Resolver Contract
- `model_slot_resolver.py` resolves per-slot provider routing
- `_call_openai_compatible()` detects HTTP 429 before `raise_for_status()`
- On 429: returns error dict with `retry_after` from `Retry-After` header
- Logs `model_call_rate_limited` event
- **Consumers must check for rate limit response**, not just generic failure

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
