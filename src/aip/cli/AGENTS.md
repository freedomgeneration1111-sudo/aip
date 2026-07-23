# CLI Layer — Agent Navigation
> The `aip` command-line interface. Imports from adapter + foundation only.

## Purpose
The CLI layer provides operator-facing commands for corpus management,
ingestion, graph building, wiki generation, evaluation, and project
administration. All commands are synchronous (no event loop) — they
use `asyncio.run()` to invoke async store methods and exit.

## Architecture Constraints
- **Foundation + adapter imports only**: `from aip.foundation...` and
  `from aip.adapter...` are allowed. No `from aip.orchestration...` —
  orchestration logic is invoked through adapter pipelines or
  container-mediated access, never imported directly.
- **Sync entry, async body**: every command is a `click` callback that
  wraps an `async def _run_*()` in `asyncio.run()`. Stores are opened,
  used, and closed within a single `try/finally` block.
- **DB path resolution**: all commands use `aip.cli._db_path.get_default_db_path()`
  to resolve the database path (config → env → `db/state.db` fallback).
  No command hardcodes `"data/aip.db"` or `"db/state.db"`.

## Contracts (What This Module Promises to Consumers)

### `aip corpus ingest-code <path>` (QW11, 2026-07-23)
Ingests a Python source directory into the codeforge corpus.
- **Path**: defaults to `src/aip/` if not given (enables AIP-asks-AIP)
- **DB**: derives `db/codeforge.db` from the main db_path's directory
- **Stale detection**: skips turns whose `content_hash` matches (unless `--force`)
- **Skip rules**: `.pyi`, `test_*.py`, `*_test.py` (delegated to parser)
- **Schema**: calls `CorpusTurnStore.initialize()` to ensure tables exist
  before ingest. Idempotent — safe if the app server already created the db.
- **Output**: human-readable counts (files scanned/skipped/parsed, turns
  created/skipped_stale/superseded)

### `aip corpus ingest <path>` (Sprint 9)
Ingests conversation exports or documents into the definer corpus.
- **Source models**: claude, gpt, deepseek, glm, gemini, grok, document
- **Dedup**: content_hash-based; re-ingest increments doc_version

### `aip corpus graph --build-from-bridges`
Builds the seed knowledge graph from bridge tags + entity aliases.

### `aip corpus graph --extract --limit N`
Runs Beast LLM entity extraction on high-importance corpus turns.

## Data Flows (In / Out)

### In (What CLI receives)
- Click arguments + options from the operator
- Config from `config/aip.config.toml` (via `_db_path.get_default_db_path`)

### Out (What CLI produces)
- CorpusTurns written to per-corpus SQLite DBs (`db/state.db`, `db/codeforge.db`)
- Graph nodes/edges written to the definer graph (in `state.db`)
- Human-readable stdout (counts, status, errors to stderr)

## Known Gotchas
- **codeforge.db schema**: the CLI's `CorpusTurnStore.initialize()` creates
  the base `corpus_turns` table. The M001/M003/M004 migrations (which add
  `revision_parent_id`, `latest_ecs_state`, `artifact_turn_links` columns)
  are run by the CorpusRegistry when the app server starts. If you ingest
  via CLI before ever starting the server, the extra columns won't exist
  yet — but the ingest pipeline doesn't use them, so this is safe. The
  app server's registry will run migrations idempotently on next startup.
- **--force re-ingests everything**: `--force` bypasses stale detection
  and re-writes all turns. Old turns are superseded (not deleted). This
  is correct but can be slow on large codebases.

## Last Cycle
- **QW11 — Added `aip corpus ingest-code <path>` CLI command** (this cycle):
  new click subcommand in `cli/corpus.py` that calls
  `adapter/code_ingest_pipeline.ingest_python_directory()` to populate
  the codeforge corpus. Defaults to `src/aip/` if no path given (enables
  the AIP-asks-AIP self-referential use case). Derives `db/codeforge.db`
  from the main db_path. Calls `CorpusTurnStore.initialize()` to ensure
  schema before ingest. 7 tests in `tests/test_corpus_ingest_code_cli.py`
  pin the contract: help text, nonexistent path rejection, end-to-end
  ingest, stale detection, --force flag, skip rules. Created this
  `AGENTS.md` (the CLI folder previously lacked one). Closes half of
  ND5 from the tech-debt assessment (the CLI half; QW1 registered the
  corpus, QW11 populates it).

## Key Files / Subdirectories
| File | Role |
|------|------|
| `corpus.py` | `aip corpus` group: ingest, ingest-code (QW11), tag, wiki, graph, embed |
| `_db_path.py` | Shared DB path resolution (config → env → fallback) |
| `main.py` | `aip` entry point; registers all subcommand groups |
| `ask.py` | `aip ask` — single-shot query against the corpus |
| `init.py` | `aip init` — initialize a new AIP project |
| `status.py` | `aip status` — show corpus stats, actor status, config |

## Work Guidance
- New corpus-related CLI commands go in `corpus.py` under the `corpus` group.
- Always use `get_default_db_path()` — never hardcode `"db/state.db"`.
- Wrap async store calls in `asyncio.run()` with `try/finally` for cleanup.
- Test new commands with `tests/test_corpus_*_cli.py` using `CliRunner`.

## How to Test
```bash
# Run CLI tests
uv run python -m pytest tests/test_corpus_ingest_code_cli.py -v

# Smoke-test the command
uv run aip corpus ingest-code --help
uv run aip corpus ingest-code src/aip/ --db-path /tmp/test-state.db
```
