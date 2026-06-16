# ============================================================

# Tests — Agent Navigation
> 1090+ tests. CI is blocking. Fixture discipline is law.

## Purpose
The test suite is the conformance gate for all AIP guarantees. Tests verify that
governance invariants hold, pipelines behave correctly, and stores maintain integrity.
CI runs on every push to main — failures block merge.

## Contracts (What This Module Promises to Consumers)

### Test Environment Contract
- Tests run in isolation — no shared state between test files
- `CI=true` environment variable disables all network calls
- No real model calls in tests: use `embedding.provider = "fake"` in test config
- Tests that require network access must be marked and skipped in CI
- Tests must be deterministic: no `time.sleep()`, no race conditions, no flaky ordering

### Fixture Contract
- **CI fixtures must be flagged**: Any fixture that injects synthetic data for
  testing purposes must include `ci_fixture=True`. This flag blocks promotion to
  production paths. Never remove this flag to make a test pass.
- **Fixtures are domain-scoped**: Put fixtures in the test file that owns their domain.
  Cross-domain fixtures go in a shared conftest.py with explicit documentation.

### Test Import Contract
- Tests SHOULD import and call the module under test directly
- If the test environment cannot import a module (e.g., NiceGUI, aiosqlite not
  available), that's a signal the **dependency graph needs attention**, not a
  workaround
- **Avoid `Path().read_text()` patterns**: Reading source files as text to test
  for patterns is fragile. Import the module and test its behavior instead.
- If you MUST read source (e.g., import not possible), document WHY and add
  a comment explaining the dependency gap

## Data Flows (In / Out)

### In (What tests read)
- Source code under test from `src/aip/`, `gui/`
- Config fixtures from `config/`
- Database fixtures (in-memory SQLite for isolation)
- Mock providers for model calls and embedding

### Out (What tests produce)
- Pass/fail results for CI gate
- Coverage reports
- Regression test documentation in test file docstrings

## Known Gotchas
- **Don't remove `ci_fixture=True` to make a test pass**: That's hiding a real
  issue. Find the actual cause.
- **MagicMock detected as "degraded"**: `type(MagicMock()).__name__` contains "Mock",
  which Sexton's `_compute_embedding_backfill_state()` detects as fake provider and
  returns "degraded". Use plain classes instead: `class RealProvider: pass`
- **Import boundary**: Some modules transitively import `aiosqlite` or `nicegui`,
  which may not be in the test environment. If a test can't import a module, prefer
  fixing the import chain over reading source as text.
- **Async tests**: Use `pytest-asyncio` and mark async tests with `@pytest.mark.asyncio`.
  Forgetting the mark causes the test to be skipped silently.

## Last Cycle
- **Commit 14d3a73**: Added 19 regression tests in `test_operator_console_fixes.py`:
  - `TestCorpusDialogHandlerOrder` (5 tests): definition order for handlers
  - `TestSextonConcurrencyGuard` (4 tests): cycle_lock, cycle_active, concurrent skip
  - `TestRateLimitHandling` (4 tests): rate limit attributes, 429 detection
  - `TestCorpusBackfillUIState` (6 tests): no sexton_pass.state, backfill_state reading

## Fixture Rules (Non-Negotiable)
- **CI fixtures must be flagged**: Any fixture with `ci_fixture=True` is blocked from
  production promotion paths. Never remove this flag to make a test pass.
- **No network calls in CI**: `CI=true` environment variable disables all network calls.
  Tests that require network access must be marked and skipped in CI.
- **No real model calls in tests**: Use `embedding.provider = "fake"` in test config.
  Tests that inadvertently call real models will fail in CI (no API keys).
- **Deterministic tests only**: No `time.sleep()`, no race conditions, no flaky
  ordering dependencies.

## Test File Map
| File | Domain |
|------|--------|
| `test_operator_console_fixes.py` | Regression tests for operator console bug fixes |
| `test_ingestion.py` | Ingestion pipeline — parse, chunk, embed, index |
| `test_ask.py` | Ask pipeline — retrieve, assemble, dispatch, persist |
| `test_review_export.py` | Review, approve, reject, export — ECS transitions |
| `test_actors.py` / `test_beast_actor.py` / `test_sexton.py` / `test_vigil.py` | Actor behavior |
| `test_workflow.py` / `test_workflow_engine.py` | YAML workflow engine |
| `test_ecs_graph.py` | ECS state machine — all valid and invalid transitions |
| `test_foundation.py` / `test_schemas_core.py` | Foundation layer — schemas, protocols, validation |
| `test_config.py` / `test_config_validation.py` | Config loading — all sections, env overrides |
| `test_api.py` / `test_api_chat.py` | FastAPI routers — endpoint contracts, auth gates |
| `test_cli.py` | CLI commands — integration paths |
| `test_stores.py` / various `test_*store*.py` | Storage adapters — CRUD, versioning, async safety |
| `test_model_slot_resolver.py` | Model slot dispatch and routing |
| `test_layering.py` / `test_import_boundary.py` | Architecture layer discipline |
| `test_definer_gate.py` | DEFINER sovereignty enforcement |
| `test_retrieval_orchestrator.py` | Multi-channel retrieval |
| `test_budget_system.py` | Budget tracking and thresholds |

## Work Guidance
- Adding a test: put it in the file that owns its domain. One domain per file.
- Adding a fixture: mark it with `ci_fixture=True` if it injects synthetic data.
- A test that passes by removing `ci_fixture=True` has fixed nothing — find the
  real cause.
- Test coverage target: new pipeline code requires tests for happy path,
  ECS transition, and at least one error/rejection path.
- **For bug fixes**: Add a regression test that would have caught the bug.
  Name it descriptively. Put it in the appropriate domain file or in
  `test_operator_console_fixes.py` if it crosses multiple domains.

## How to Run
```bash
uv run pytest                          # full suite
uv run pytest tests/test_ingestion.py  # single module
uv run pytest --cov=aip --cov-report=term-missing  # with coverage
CI=true uv run pytest                  # CI mode (no network, deterministic)
bash scripts/dogfood_smoke_test.sh    # dogfood smoke test
```


# ============================================================
