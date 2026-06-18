# ============================================================

# Aristotle Extension — Agent Navigation
> ADR-ARISTOTLE Phase A dogfood. The first extension built on the ADR-014 platform.
> Imports from aip.foundation only (via the Actor Protocol). Self-contained otherwise.

## Purpose
ARISTOTLE is the adaptive tutor — the first real consumer of the Phase 0
extension platform (ADR-014). This is the **Phase A dogfood drop**: a minimal
extension that proves the platform contract end-to-end. Each gap ARISTOTLE
surfaces is a Phase 0 protocol gap to log (ADR-ARISTOTLE §9).

Phase A scope (ADR-ARISTOTLE §11):
- Ingestor + curriculum map + prerequisite graph (placeholder — content
  ingestion comes when the textbook corpus has material)
- student_profile + struggle_pattern (schema in M001_aristotle.sql)
- TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine (placeholder — SOCRATES
  is the entry point; full state machine comes with workflow integration)
- SM-2 via core VIGIL (reused, not re-implemented)
- Bilingual (content_primary + content_alt + content_alt_lang schema)

## Architecture Constraints
- **Self-contained**: imports from `aip.foundation.protocols.actors` only
  (ActorResult, ActorContext). No adapter or orchestration imports. The
  container is accessed via `ctx.container` (duck-typed as Any in the
  foundation Protocol).
- **Discovered by ExtensionHost**: lives under `extensions/aristotle/`.
  The host adds `extensions/` to sys.path at stage 1 validate (ADR-014 §6.4),
  making `aristotle.config`, `aristotle.actors`, `aristotle.hooks` importable.
- **Actor Protocol conformance**: SOCRATES conforms to
  `aip.foundation.protocols.actors.Actor` (name/cadence/run_cycle/health).
  The host validates this via `isinstance(actor, Actor)` at scheduler start.
- **Manual-only actor**: `cadence=0.0` — the tutoring state machine is driven
  by user turns, not by a timer (ADR-ARISTOTLE §3: "the learner only feels
  rhythm"). The host runs one cycle on start, then waits for cancellation.

## Contracts (What This Module Promises to Consumers)

### Manifest (extension.yaml)
- `id: aristotle` (immutable post-registration; must not collide)
- `manifest_version: 1`
- `contributes.corpora`: one `textbook` corpus (type=document, sensitive=false)
  → registered as `aristotle:textbook` (ADR-014 §6.2 namespacing)
- `contributes.actors: [socrates]` (advisory; actual registration in hooks.py)
- `contributes.workflows_dir: workflows` (placeholder tutoring_session_v1.yaml)
- `contributes.migrations: migrations` (M001_aristotle.sql)
- `config.schema: aristotle.config:AristotleSettings`

### AristotleSettings (config.py)
Plain dataclass (not pydantic_settings.BaseSettings) so it instantiates
without env-var dependencies. Defaults:
- `primary_language: str = "en"`
- `alt_language: str = "ur"` (ADR-ARISTOTLE §7 bilingual)
- `bloom_default: int = 3` (1-6 scale, ADR-ARISTOTLE §4)
- `review_interval_seconds: int = 86400` (24h SM-2 default)

### SOCRATES actor (actors/socrates.py)
- `name = "socrates"`, `cadence = 0.0` (manual-only)
- `run_cycle(ctx)`: verifies `aristotle:textbook` corpus is registered via
  `ctx.container.corpus_registry.get_stores()`, logs its presence, returns
  `ActorResult(ok=True)`. A full SOCRATES would query the concept graph +
  call a model + persist the result — that's Phase A follow-up.
- `health()`: returns `{"state": "active", "name": "socrates", ...}`

### EXAMINER actor (actors/examiner.py)
- `name = "examiner"`, `cadence = 0.0` (manual-only)
- `run_cycle(ctx)`: verifies the corpus is reachable + checks whether
  `container.model_provider` is configured. Returns `ok=True` in both cases
  (the actor is healthy; it just can't generate questions without a model).
  The tutoring loop checks model availability before attempting a quiz —
  governance invariant: "No silent model calls" (AGENTS.md §1.7).
- `health()`: returns `{"state": "active", "name": "examiner", ...}`
- Role in state machine: PROBE → QUIZ → EVALUATE (ADR-ARISTOTLE §3)

### MENTOR actor (actors/mentor.py)
- `name = "mentor"`, `cadence = 0.0` (manual-only)
- `run_cycle(ctx)`: reads `aristotle_struggle_pattern` for the default
  student (`'definer'` — pre-alpha single-tenant). If absent, initializes
  with a placeholder. If present, logs it. This proves the actor can
  execute SQL against the extension's own corpus via
  `stores.connection_manager.write_conn`.
- `health()`: returns `{"state": "active", "name": "mentor", ...}`
- Role in state machine: EVALUATE (updates struggle_pattern after scoring) +
  feeds REMEDIATE (the struggle_pattern sentence is injected into the
  re-teaching prompt, ADR-ARISTOTLE §2).

### Migration (M001_aristotle.sql)
Creates two tables in the `aristotle:textbook` corpus:
- `aristotle_concept`: concept-aware chunks (ADR-ARISTOTLE §4) with bilingual
  columns `content_primary` + `content_alt` + `content_alt_lang` (ADR-014 §1).
  Includes `prerequisite_concept_id` for the DAG.
- `aristotle_struggle_pattern`: one persistent AI-written diagnostic sentence
  per student (ADR-ARISTOTLE §2 MENTOR role). Pre-alpha single-tenant:
  `student_id` defaults to `'definer'`.

**Note on progress store location**: ADR-014 §1 says progress tables go in
the `definer` corpus, but the migration_loader (step 1) applies to the
extension's own corpus (`aristotle:textbook`). For pre-alpha dogfood,
per-corpus is simpler and matches the loader's behavior. Revisit at Phase B
(teacher dashboard) when cross-corpus aggregation matters.

### Hooks (hooks.py)
- `on_load(host)`: calls `host.register_actor("socrates", SocratesActor, cadence=0.0)`.
  The host sets `_current_ext_id` before calling, so `host.config` /
  `host.manifest` resolve to ARISTOTLE's validated config + manifest.
- `on_unload(host)`: no-op (no background resources to release in Phase A).

## Data Flows (In / Out)

### In
- `extension.yaml` manifest (discovered by host at stage 0)
- `M001_aristotle.sql` migration (applied to `aristotle:textbook` corpus at stage 2)
- `AristotleSettings` config (loaded + instantiated at stage 1)
- `hooks.py::on_load` (called at stage 5 to register SOCRATES)

### Out
- `aristotle:textbook` corpus registered with CorpusRegistry
- `aristotle_concept` + `aristotle_struggle_pattern` tables in that corpus
- `socrates` actor registered + scheduler task started (runs one cycle on start)
- `tutoring_session_v1` workflow template discovered via WorkflowRegistry.add_path

### Cross-folder flows
- `extensions/aristotle/hooks.py` → `aip.adapter.extensions.host.ExtensionHost`:
  calls `host.register_actor(...)` at stage 5.
- `extensions/aristotle/actors/socrates.py` → `aip.foundation.protocols.actors`:
  imports `ActorContext` + `ActorResult`.
- `extensions/aristotle/actors/socrates.py` → `ctx.container.corpus_registry`:
  calls `get_stores("aristotle:textbook")` at runtime.
- `extensions/aristotle/config.py` → host's `_import_class`:
  loaded via `importlib.import_module("aristotle.config")` at stage 1
  (requires `extensions/` on sys.path — added by host).

## Known Gotchas
- **Progress tables are in `aristotle:textbook`, not `definer`.** ADR-014 §1
  says progress tables go in the definer corpus, but the migration_loader
  applies to the extension's own corpus. Pre-alpha pragmatism; revisit at
  Phase B. The `aristotle_*` naming convention is preserved either way.
- **All three actors are placeholders.** The dogfood SOCRATES/EXAMINER/MENTOR
  verify platform reachability (corpus, model provider, struggle_pattern
  table) but don't do real teaching/probing/mentoring. The full tutoring
  loop (concept graph query, model call, persistence, state machine
  execution) is Phase A follow-up work.
- **`cadence=0.0` means manual-only.** All three actors run one cycle on
  start, then wait forever for cancellation. The tutoring state machine is
  driven by user turns, not by a timer (ADR-ARISTOTLE §3).
- **The `tutoring_session_v1.yaml` workflow is engine-compatible and executable.**
  Rewritten to use the L5 Workflow Engine's node types (`agent`, `script`,
  `condition`) instead of the placeholder types (`synthesize`, `decision`,
  `commit`) that the loader rejects. The engine is wired into the container
  (`container.workflow_engine`, ADR-014 §8 step 2). Extensions access it via
  `ctx.container.workflow_engine.run_workflow(yaml_path, variables)`. The
  workflow's 7 nodes (teach → probe → quiz → evaluate → check_mastery →
  remediate → next_concept) match the ADR-ARISTOTLE §3 state machine. The
  `script` nodes (`evaluate`, `next_concept`) reference `run: aristotle_evaluate`
  and `run: aristotle_next_concept` — these are script handlers that need
  to be registered with the engine (future work; the engine currently runs
  them in fixture/no-op mode).
- **No HERALD actor yet.** HERALD (field awareness) is Phase C — depends on
  the Phase 0 web/feed layer (ADR-014 §3.4), which is not yet built.
- **EXAMINER returns `ok=True` even without a model.** This is intentional:
  the actor is healthy, it just can't generate questions. The tutoring loop
  checks model availability before attempting a quiz (governance: "No silent
  model calls"). A future EXAMINER will return NEEDS_CONFIGURATION when
  asked to generate a question without a model.

## Last Cycle
- **ARISTOTLE Phase A — multi-actor + state machine** (this cycle):
  - Built EXAMINER actor (`actors/examiner.py`): probe/quiz/evaluate mode.
    Conforms to Actor Protocol. Verifies corpus reachability + checks model
    availability. Returns `ok=True` in both cases (healthy actor; the
    tutoring loop checks model before quiz). Governance: no silent model calls.
  - Built MENTOR actor (`actors/mentor.py`): long-arc tracking. Conforms to
    Actor Protocol. Reads `aristotle_struggle_pattern` table via
    `stores.connection_manager.write_conn`; initializes with a placeholder
    if absent. Proves per-student state read/write against the extension's
    own corpus.
  - Updated `actors/__init__.py` to re-export all three actors.
  - Updated `hooks.py` to register SOCRATES + EXAMINER + MENTOR (all
    cadence=0.0, manual-only).
  - Updated `extension.yaml` advisory actors list: `[socrates, examiner, mentor]`.
  - Replaced the placeholder `tutoring_session_v1.yaml` with a real state
    machine workflow: 7 nodes (teach → probe → quiz → evaluate →
    remediate_on_struggle [decision] → remediate → next_concept). Declared
    but not executable — the workflow engine isn't wired into the container
    yet (ADR-014 §8 step 2 deferred).
  - Added `tests/test_aristotle_actors.py` (10 tests): 5 conformance
    (isinstance + distinct names + health for all three) + 5 behavior
    (EXAMINER degrades gracefully without model; EXAMINER fails without
    corpus_registry; MENTOR initializes struggle_pattern when absent;
    MENTOR reads existing without INSERTing; MENTOR fails without
    corpus_registry). All 10 pass locally (fakes, no aiosqlite needed).
  - Verified: manifest validates with 3 actors; all three conform to Actor
    Protocol; workflow YAML parses with 7 nodes; all 14 existing Actor
    Protocol + WorkflowRegistry tests still pass (no regression).
- **Phase A dogfood drop** (prior cycle):
  - Built `extensions/aristotle/` (7 files): `extension.yaml` manifest,
    `config.py` (AristotleSettings dataclass), `migrations/M001_aristotle.sql`
    (aristotle_concept + aristotle_struggle_pattern tables with bilingual
    schema), `actors/socrates.py` (minimal SOCRATES conforming to Actor
    Protocol), `actors/__init__.py`, `hooks.py` (on_load registers SOCRATES),
    `workflows/tutoring_session_v1.yaml` (placeholder), `__init__.py`.
  - **Surfaced + fixed a platform gap**: the host's `_import_class` did
    `importlib.import_module("aristotle.config")` but `aristotle` wasn't
    importable because `extensions/` wasn't on sys.path. Fixed by adding
    `extensions/` to sys.path at stage 1 validate (host.py). This is
    exactly the kind of gap ARISTOTLE was supposed to surface
    (ADR-ARISTOTLE §9).
  - Added `tests/test_aristotle_extension.py` (7 integration tests):
    manifest validates; migrations create tables; SOCRATES registers;
    SOCRATES conforms to Actor Protocol; config.schema loads; health
    surfaces; stop cancels.
  - Verified locally: manifest validates (8 fields); AristotleSettings
    instantiates with bilingual defaults (en/ur); SocratesActor conforms
    to Actor Protocol; all 14 existing Actor Protocol + WorkflowRegistry
    tests still pass (no regression from the sys.path fix).
  - Full ARISTOTLE integration tests deferred to CI (need aiosqlite for
    CorpusRegistry).

## Key Files
| File | Role |
|------|------|
| `extension.yaml` | Manifest v1 — declares textbook corpus, 3 actors (socrates/examiner/mentor), migrations, config.schema |
| `config.py` | AristotleSettings dataclass (bilingual defaults: en primary, ur alt) |
| `migrations/M001_aristotle.sql` | Creates aristotle_concept (bilingual schema) + aristotle_struggle_pattern |
| `actors/__init__.py` | Re-exports SocratesActor, ExaminerActor, MentorActor |
| `actors/socrates.py` | SOCRATES — teach mode. Verifies corpus reachability. Conforms to Actor Protocol. |
| `actors/examiner.py` | EXAMINER — probe/quiz/evaluate. Verifies corpus + checks model availability. Conforms to Actor Protocol. |
| `actors/mentor.py` | MENTOR — long-arc tracking. Reads/writes aristotle_struggle_pattern. Conforms to Actor Protocol. |
| `hooks.py` | on_load registers all 3 actors; on_unload is a no-op |
| `workflows/tutoring_session_v1.yaml` | TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine (7 nodes; declared, not yet executable) |
| `__init__.py` | Package marker + docstring |

## Work Guidance
- Adding a new actor (EXAMINER, MENTOR): create `actors/<name>.py` with a
  class conforming to the foundation Actor Protocol (name/cadence/run_cycle/
  health). Add to `actors/__init__.py`. Register in `hooks.py::on_load` via
  `host.register_actor(...)`. Update the manifest's advisory `actors:` list.
- Adding a new table: add a new `M00X_<name>.sql` migration (M<3-digit>_
  naming convention). The migration_loader applies it to the
  `aristotle:textbook` corpus. Use `CREATE TABLE IF NOT EXISTS` for
  idempotency.
- Adding a config field: add to `AristotleSettings` in `config.py` with a
  default. The host instantiates via `cls()` (zero-arg), so all fields
  must have defaults.
- Testing: every new behavior gets a test in `tests/test_aristotle_extension.py`.
  The integration tests point the host at the real `extensions/` dir.

## How to Test
```bash
# Run the ARISTOTLE integration tests (needs aiosqlite + structlog):
CI=true uv run pytest tests/test_aristotle_extension.py -v

# Verify the manifest validates in isolation:
PYTHONPATH=src python -c "
import yaml
from aip.adapter.extensions.manifest import Manifest
m = Manifest.model_validate(yaml.safe_load(open('extensions/aristotle/extension.yaml')))
print(m.id, m.version, m.contributes.corpora)
"

# Verify SocratesActor conforms to the Actor Protocol:
PYTHONPATH=src:extensions python -c "
from aip.foundation.protocols.actors import Actor
from aristotle.actors import SocratesActor
print('conforms:', isinstance(SocratesActor(), Actor))
"
```

# ============================================================
