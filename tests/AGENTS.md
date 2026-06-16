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
- **Phase 1 Fix A/B/C regression tests (this cycle)**: Added 6 new
  tests to `test_model_council_fusion.py` (total now 28) covering the
  three fixes from this cycle:
  - `TestFusionPerCallTimeouts::test_hung_panel_model_does_not_block_gather`
    — patches `_PANEL_CALL_TIMEOUT_S` down to 0.3s, hangs the
    `evaluation` slot forever, asserts the gather completes, the
    hung slot is recorded as `status="failed"` with `"timed out"` in
    the error message, the fast slots still complete, and the Fusion
    pipeline still runs.
  - `TestFusionPerCallTimeouts::test_judge_timeout_yields_failed_synthesis_empty_judge_analysis`
    — patches `_JUDGE_CALL_TIMEOUT_S` down to 0.3s, hangs the
    Judge-Beast call forever, asserts `synthesis_status="failed"`,
    `fusion_answer=""`, `judge_analysis={}`.
  - `TestFusionPerCallTimeouts::test_synth_timeout_preserves_judge_analysis`
    — patches `_SYNTH_CALL_TIMEOUT_S` down to 0.3s, hangs the
    Synth-Beast call forever, asserts `synthesis_status="failed"`,
    `fusion_answer=""`, but `judge_analysis` is still populated
    (Judge succeeded earlier).
  - `TestFusionJudgePromptContract::test_judge_prompt_contains_model_label_contract`
    — source-string contract check that the Judge system prompt in
    `model_council.py` contains the MODEL LABEL CONTRACT block, the
    "EXACT <LABEL>" instruction, the "Do NOT invent your own labels"
    prohibition, and the `anthropic/claude-3-opus` concrete example.
  - `TestFusionGuiRendersJudgeAnalysis::test_ask_page_reads_judge_analysis`
    — AST/string contract check that `ask.py` reads
    `result.get("judge_analysis"` and defines
    `_format_judge_analysis_markdown`.
  - `TestFusionGuiRendersJudgeAnalysis::test_panel_renders_judge_analysis`
    — AST/string contract check that `model_council_panel.py` reads
    `data.get("judge_analysis"` and defines `_render_judge_analysis`.
  All 28 fusion tests pass. All 141 council + import/layering tests
  pass (was 135 before this cycle).
- **Phase 1 Fusion tests (prior cycle)**: Added `test_model_council_fusion.py`
  with 22 tests covering the new two-stage Fusion pipeline (Judge-Beast →
  Synth-Beast). Tests assert:
  - Schema additions: `fusion_answer` and `judge_analysis` fields exist
    with correct defaults; all legacy fields still present.
  - Two-stage Beast call: the `beast` slot is called once as a panelist
    + twice for Fusion (Judge with JUDGE system prompt, then Synth with
    SYNTHESIZER system prompt), verified by filtering on system prompt
    content.
  - `fusion_answer` populated from Synth-Beast output.
  - `judge_analysis` populated from Judge-Beast JSON output.
  - Legacy fields derived from new `analysis.*` schema (consensus,
    contradictions, partial_coverage, unique_insights, blind_spots).
  - Backward compat: Judge returning old top-level schema
    (convergence/disagreements/etc. as top-level keys) still populates
    legacy fields, judge_analysis, and produces a fusion_answer.
  - Asymmetric information contract: Synth-Beast's user prompt does NOT
    contain raw panel output strings (only the Judge JSON).
  - Failure paths: Judge failure → synthesis_status="failed",
    fusion_answer empty. Synth failure → synthesis_status="failed" but
    judge_analysis still populated. Single successful model →
    synthesis_status="unavailable".
  - Guarantees preserved: advisory_only=True, requires_DEFINER_approval=True,
    no secrets in response, save_as_artifact produces GENERATED only
    (never APPROVED).
  - GUI consumer contract: AST check that `ask.py` reads
    `result["fusion_answer"]` and `model_council_panel.py` reads
    `data["fusion_answer"]`.
  All 22 new tests pass. All 97 existing council tests
  (test_model_council_cycle6.py, test_model_council_cycle6_1.py,
  test_model_council_library_ids.py) continue to pass unchanged —
  backward compat is verified.
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
| `test_model_council_fusion.py` | Phase 1 Fusion pipeline — Judge+Synth two-stage Beast synthesis + per-call timeouts + Judge label contract + GUI judge_analysis rendering (28 tests) |
| `test_model_council_cycle6.py` | UI Cycle 6 — Model Council schema, multi-model exec, partial failure, save-as-artifact |
| `test_model_council_cycle6_1.py` | UI Cycle 6.1 — selected_model_slots honoring, embedding exclusion, text-generation-slots endpoint |
| `test_model_council_library_ids.py` | Library model-ID bridge — selected_model_ids, PerModelResult.source, combined insufficient gate |
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
