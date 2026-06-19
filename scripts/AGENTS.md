# ============================================================

# Scripts — Agent Navigation
> Utility scripts, smoke tests, deployment helpers. Not production code.

## Purpose
Scripts in this directory are operational tools: smoke tests, deployment helpers,
database utilities, and demo builders. They are NOT imported by the application —
they are run directly by operators or CI.

## Contracts (What This Module Promises to Consumers)

### Smoke Test Contract
- `dogfood_smoke_test.sh` verifies the full API surface that the GUI depends on
- Must pass after any code change before considering the cycle complete
- Exit code 0 = all checks pass, non-zero = failure with diagnostic output

### Demo Contract
- `demo/build_aip_demo_db.py` builds a self-contained demo database
- `demo/verify_aip_demo_db.py` validates the demo database integrity
- Demo databases are NOT production databases — they contain synthetic data

## Data Flows (In / Out)

### In
- Application API endpoints (smoke test calls them)
- Config from `config/aip.config.toml`
- Source data from `examples/` for demo building

### Out
- Exit codes for CI/CD pipelines
- Demo databases for demonstrations
- Diagnostic output for operators

## Known Gotchas
- **Scripts assume running server**: `dogfood_smoke_test.sh` requires the backend
  to be running. Start it first with `bash start.sh` or `python -m gui.app`.
- **Demo scripts create databases**: They will overwrite existing demo databases.
  Never point them at production `db/state.db`.
- **Python scripts need venv**: Run via `uv run python scripts/...` or ensure
  the venv is activated.

## Last Cycle
- No changes. Scripts were stable during operator console debugging.

## Key Files
| File | Role |
|------|------|
| `dogfood_smoke_test.sh` | End-to-end smoke test for dogfood readiness |
| `dogfood_seed_corpus.sh` | Seed the corpus with initial test data |
| `start.sh` | Start the AIP backend server |
| `retrieval_weight_tuning.py` | Tune retrieval channel weights |
| `watch_import.sh` | Watch for import boundary violations |
| `demo/build_aip_demo_db.py` | Build self-contained demo database |
| `demo/verify_aip_demo_db.py` | Validate demo database integrity |
| `ingest_claude.py` | Ingest Claude conversation exports |

## Work Guidance
- Adding a script: make it executable, add a shebang line, document it here
- Modifying smoke test: ensure it still covers the full GUI API surface
- Never import scripts from application code — they are operational tools only

## How to Test
```bash
bash scripts/dogfood_smoke_test.sh
uv run python scripts/demo/verify_aip_demo_db.py
```


# ============================================================
