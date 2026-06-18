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
- **SOCRATES is a placeholder.** The dogfood SOCRATES only verifies the
  corpus is reachable. The full tutoring loop (query concept graph, call
  model, persist result) is Phase A follow-up work.
- **`cadence=0.0` means manual-only.** The host runs one cycle on start,
  then waits forever for cancellation. The tutoring state machine is
  driven by user turns, not by a timer (ADR-ARISTOTLE §3).
- **The `tutoring_session_v1.yaml` workflow is a placeholder.** It declares
  frontmatter only (no `nodes:`). The workflow engine will discover it via
  `WorkflowRegistry.add_path` but it can't be executed yet. The full state
  machine (TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE) is Phase A follow-up.
- **No EXAMINER/MENTOR actors yet.** Phase A ships SOCRATES only. EXAMINER
  (probe/quiz/evaluate) + MENTOR (struggle_pattern tracking) are Phase A
  follow-ups. HERALD (field awareness) is Phase C.

## Last Cycle
- **Phase A dogfood drop** (this cycle):
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
| `extension.yaml` | Manifest v1 — declares textbook corpus, socrates actor, migrations, config.schema |
| `config.py` | AristotleSettings dataclass (bilingual defaults: en primary, ur alt) |
| `migrations/M001_aristotle.sql` | Creates aristotle_concept (bilingual schema) + aristotle_struggle_pattern |
| `actors/__init__.py` | Re-exports SocratesActor |
| `actors/socrates.py` | Minimal SOCRATES actor — conforms to Actor Protocol, verifies corpus reachability |
| `hooks.py` | on_load registers SOCRATES; on_unload is a no-op |
| `workflows/tutoring_session_v1.yaml` | Placeholder workflow (frontmatter only; full state machine is Phase A follow-up) |
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
