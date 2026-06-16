# ============================================================

# Config — Agent Navigation
> TOML configuration. Profile-based. Key names are a contract.

## Purpose
`aip.config.toml` is the single source of configuration truth. Deployment profiles
(laptop, production) select different backend combinations. Environment variables
override specific values for CI and cloud deployment.

## Contracts (What This Module Promises to Consumers)

### Key Name Contract (CRITICAL — #1 Config Bug Class)
Config key names in `aip.config.toml` must **exactly match** the Python attribute
names used in `ModelSlotResolver` and all config loader classes. A key mismatch is
a known blocker class — always verify both sides when adding or renaming a config key.

**Verification rule**: For every `[section]` key in TOML, there must be a matching
Python attribute in the owning class. Check the Section Map below.

### Section Ownership Contract
Each section has one owner in Python code. Changes to a section must be verified
against the owner module:

| Section | Owner | Key Variables |
|---------|-------|---------------|
| `[database]` | All stores | `db_path` (default: `db/state.db`) |
| `[vector_backend]` | `adapter/vector/` | provider, host, port |
| `[embedding]` | `adapter/embedding/` | provider ("openai_compatible" for real, in-memory mock for CI) |
| `[auth]` | `adapter/auth/` | auth_enabled, session_timeout, bcrypt_rounds |
| `[deployment]` | Config loader | profile (laptop/production), vector_backend, model_provider |
| `[budget]` | `adapter/budget/` | token limits, warning thresholds |
| `[beast]` | `orchestration/actors/beast.py` | health_check_interval, corpus_reindex_interval, entity_maintenance_interval |
| `[review]` | `orchestration/review_export_pipeline.py` | faithfulness_threshold, coherence_threshold, definer_approval |
| `[sexton]` | `orchestration/actors/sexton.py` | classification_batch_size, classification_interval_seconds, embed_delay_seconds |
| `[models]` | `adapter/model_slot_resolver.py` | Per-slot provider/model config: synthesis, evaluation, sexton, embedding, beast |
| `[chat]` | `orchestration/ask_pipeline.py` | system_prompt_path, max_context_turns, auto_summarize_at |
| `[mcp]` | `adapter/mcp/` | enabled, transport, max_concurrent_tools |
| `[performance]` | Various | profiling_enabled, max_memory_mb, retrieval_timeout, batch_embed_size |
| `[rate_limit]` | `adapter/middleware/rate_limiter.py` | enabled, requests_per_minute, burst_size, model_budget_protection |
| `[alerting]` | `adapter/alerting.py` | enabled, webhook_url, alert_on_quality_degradation |
| `[canonical_pipeline]` | `orchestration/canonical_pipeline.py` | faithfulness/coherence thresholds, require_vigil_health_check |
| `[trajectory]` | `orchestration/trajectory/` | loop_detection_window, anxiety_threshold, failure_streak_threshold |
| `[vigil]` | `orchestration/actors/vigil.py` | canonical_health_check_interval, stale_threshold_days |

### Environment Override Contract
```
AIP_DB_PATH                → overrides [database].db_path
AIP_SYNTHESIS_BASE_URL     → synthesis model API endpoint
AIP_SYNTHESIS_MODEL        → synthesis model name string
AIP_SYNTHESIS_API_KEY      → synthesis model API key (never in TOML)
AIP_OLLAMA_BASE_URL        → Ollama endpoint (default: http://localhost:11434)
AIP_<SLOT>_BASE_URL        → per-slot provider URL override
AIP_<SLOT>_API_KEY        → per-slot API key override (SLOT = SYNTHESIS, EVALUATION, SEXTON, EMBEDDING, BEAST)
AIP_OPENAI_API_KEY        → global API key fallback for all slots
CI=true                    → CI mode: deterministic fixtures, no network calls
```

### No Secrets in Config
API keys go in environment variables, **never** in TOML files. The example config
(`aip.config.toml.example`) must not contain real keys.

## Data Flows (In / Out)

### In (What config receives)
- `aip.config.toml` file (primary source)
- Environment variable overrides
- Hot reload via `adapter/config_watcher.py`

### Out (What config provides)
- **All Python modules** read config through `config/loader.py`
- **ModelSlotResolver** reads `[models]` section for per-slot provider routing
- **Actors** read their section for schedule intervals
- **Pipelines** read thresholds and parameters

### Cross-Folder Data Flows
```
config/aip.config.toml ([models] section)
  → adapter/model_slot_resolver.py (slot provider/model resolution)
    → orchestration/actors/sexton.py (which provider to call for embedding)
    → gui/pages/settings.py (model slot display via API)

config/aip.config.toml ([sexton] section)
  → orchestration/actors/sexton.py (classification_batch_size, embed_delay_seconds)

config/aip.config.toml ([embedding] section)
  → adapter/embedding/factory.py (provider selection: openai_compatible, ollama, fake)
```

## Known Gotchas
- **Key name mismatches are silent**: A typo in a TOML key doesn't raise an error —
  the Python code just gets `None` or the default. Always verify both sides.
- **Partial renames cause blockers**: If you rename a key in TOML but not in the
  Python class (or vice versa), the system silently misconfigures. Update both
  atomically in the same commit.
- **Profile changes affect all sections**: Switching from laptop to production
  profile changes vector backend, model provider, and embedding provider
  simultaneously. Test both profiles after any deployment section change.
- **Environment variables take precedence**: Even if TOML is correct, a stale
  env var can override it. Check `os.environ` when config seems wrong.

## Last Cycle
- **Commit 14d3a73**: No config changes. Config was stable during the operator
  console debugging cycle. Sexton's `[sexton]` section was already correct.

## Work Guidance
- Adding a config key: add to TOML, add to the Python config class, update this
  file's Section Map, add a test that validates the key loads correctly.
- Renaming a key: update TOML AND every Python reference atomically. This is how
  config key mismatches happen — partial renames.
- Profile changes: test both laptop and production profiles after any deployment
  section change.

## How to Test
```bash
uv run aip status  # validates config loads cleanly
uv run pytest tests/test_config.py
uv run pytest tests/test_config_validation.py
```


# ============================================================
