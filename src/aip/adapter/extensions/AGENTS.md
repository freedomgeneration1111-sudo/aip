# ============================================================

# Adapter / Extensions — Agent Navigation
> ADR-014 Phase 0 Extension Platform. ExtensionHost lifecycle + manifest v1.
> Imports from foundation only. Never from orchestration directly.

## Purpose
The extensions package implements the extension platform contract from
ADR-014. It discovers, validates, migrates, registers, and (in v1.1) mounts
extension packages that declare a `extension.yaml` manifest. One
`ExtensionHost` instance lives on `AipContainer.extensions` and is the
**only** way the lifespan learns about extensions.

## Architecture Constraints
- **Foundation imports only**: `from aip.foundation...` and `from aip.adapter...`
  (sibling adapter modules). Zero orchestration imports — extension actors
  that need orchestration behavior get it through the container's Protocol-injected
  actors (Beast/Vigil/Sexton), not by importing orchestration directly.
- **Host-owned registries, not module globals**: the prior `register_custom_channel`
  used a module-level `_custom_channels` list. This package replaces that pattern
  with host-owned `ExtensionRegistry` instances. The host's `register_actor` /
  `register_channel` / `register_workflow` / `register_page` mutate the host's
  registry, not module state.
- **Sandbox per stage**: every lifecycle stage (discover/validate/migrate/register/
  ready) is wrapped per-extension. A raise transitions that extension to
  DEGRADED or FAILED and never propagates to the host. A broken extension
  never takes down the shell.
- **No hot-load** (v1): extensions are registered at startup only. Hot-load
  is a v2 concern.

## Contracts (What This Module Promises to Consumers)

### ExtensionHost API (consumed by lifespan, on_load hooks, health routes)

```python
class ExtensionHost:
    def __init__(*, extensions_dir: Path, container: Any,
                 manifest_version_range: tuple[int, int] = (1, 1)) -> None

    # Lifecycle (called from lifespan)
    async def start(self) -> None      # stages 0–3 + 5 (v1.0); 0–5 (v1.1)
    async def stop(self) -> None       # §4.2 shutdown stages

    # Registration (called from on_load; also used by host for built-ins)
    def register_actor(name: str, factory: Callable[[], Any], *,
                       cadence: float = 0.0) -> None
    def register_channel(name: str, register_fn: Callable) -> None
    def register_workflow(path: str) -> None
    def register_page(route: str, title: str, icon: str,
                      builder_fn: Callable, *, order: int = 50) -> None  # v1.1

    # Access (read-only; for use inside on_load)
    @property
    def container(self) -> Any
    @property
    def config(self) -> BaseSettings       # the calling extension's own config
    @property
    def manifest(self) -> Manifest         # the calling extension's own manifest

    # State (read-only)
    def state(ext_id: str) -> ExtensionState
    def failures(ext_id: str) -> list[Failure]
    def registered_actors() -> list[str]
    def nav_items() -> list[NavItem]       # v1.1
    def health() -> list[dict]             # ADR-014 §7
    def is_running() -> bool
```

### ExtensionState (8 states, terminal-ish semantics)

`DISCOVERED` → `VALIDATED` → `MIGRATING` → `REGISTERED` → `MOUNTED` (v1.1)
                                                                    ↓
                                          `DEGRADED` / `FAILED` / `DISABLED`

- `FAILED` / `DISABLED` do not serve.
- `DEGRADED` serves what it can.
- `REGISTERED` is the v1.0 terminal state (backend live, no GUI mount).
- `MOUNTED` is the v1.1 terminal state (GUI mounted, fully live).

### Manifest v1 schema (pydantic v2 BaseModel)

Top-level fields: `manifest_version, id, name, version, depends, enabled,
contributes, config`.

`contributes` sub-block: `corpora, actors, channels, workflows_dir,
migrations, gui` (gui is v1.1).

Constraints enforced by pydantic:
- `id` must not contain `:` (used for `{ext_id}:{role}` corpus namespacing).
- `id` must not be `"definer"` (reserved core anchor corpus).
- `corpus.type` must be one of `{conversation, code, document, book}`.
- `corpus.role` must not contain `:`.
- Extra fields are forbidden (`extra="forbid"`).

`config.schema` is a Python path `"pkg.module:Class"` loaded at stage 1.
Must be a `dataclass`, `pydantic.BaseModel`, or
`pydantic_settings.BaseSettings` subclass.

### Corpus ID namespacing (ADR-014 §6.2)

Contributed corpora are registered as `{ext_id}:{role}`. Core corpora
(`"definer"`) stay flat. Examples: `aristotle:textbook`, `loom:book`,
`codeforge:code`.

### Extension migration storage (ADR-014 §9)

Extension `.sql` migrations are recorded in a SEPARATE
`extension_applied_migrations` table (keyed by `(ext_id, name)`), NOT in
the core `applied_migrations` table. This is critical: the core
`CorpusMigrationRunner`'s fingerprint check on `applied_migrations` would
otherwise see "unknown migrations applied" and raise
`CorpusMigrationError`. The two namespaces are cleanly separated.

### Health surface (ADR-014 §7)

`container.extensions.health()` returns:
```python
[
    {
        "id": "aristotle",            # manifest id (rec.manifest.id)
        "version": "0.1.0",
        "state": "MOUNTED",           # ExtensionState value
        "failures": [
            {"stage": "migrate", "contribution": "migrations", "reason": "..."}
        ]
    }
]
```

## Data Flows (In / Out)

### In
- `extension.yaml` manifests from the operator-owned `extensions/` directory.
- `hooks.py::on_load(host)` / `on_unload(host)` from each extension directory.
- `.sql` migration files from each extension's `migrations/` directory.
- The `AipContainer` (passed at construction) — the host reads
  `container.corpus_registry` to register contributed corpora.

### Out
- `container.extensions = host` — the host is stored on the container.
- Contributed corpora registered with `CorpusRegistry.register()` under
  `{ext_id}:{role}` ids.
- Actor scheduler tasks (one per registered actor, via `supervised_task`).
- Health snapshot for the `/health/extensions` route and the teacher dashboard.

### Cross-folder flows
- `extensions/host.py` → `adapter/corpus_registry.py`: `register(corpus_id,
  corpus_type, db_path, sensitive=...)` and `get_stores(corpus_id)`.
- `extensions/loaders/migration_loader.py` → `adapter/corpus_stores.py`:
  `stores.connection_manager.write_conn` (property) for executing migration SQL.
- `extensions/host.py` → `foundation/corpus_types.py`: `CorpusType` enum
  for corpus type validation.

## Known Gotchas

- **Records are keyed by directory name, not manifest id.** The directory
  name is the unique physical key (two directories can declare the same
  manifest id — one survives, one FAILED). `host.state(ext_id)` looks up by
  directory name in the common case where dir name == manifest id. The
  manifest id is the logical identity (checked for collisions at validate).
- **Extension migrations use a separate table.** Do NOT pass extension
  migrations to the core `CorpusMigrationRunner.run_migrations` — its
  fingerprint check would see "unknown migrations applied" and raise
  `CorpusMigrationError`. Use `apply_extension_migrations()` from
  `loaders/migration_loader.py` which writes to
  `extension_applied_migrations`.
- **`host.config` / `host.manifest` only work inside on_load.** They read
  `_current_ext_id`, which is set by the host's on_load context manager.
  Calling them outside on_load raises `RuntimeError`.
- **`register_actor` is sync; the scheduler task is async.** `on_load` is
  called synchronously; it calls `host.register_actor(...)` which records
  the factory. The host starts the async scheduler task at the END of
  `start()`, after all extensions' on_load hooks have run.
- **`cadence=0` means manual-only.** The actor runs one cycle on start,
  then waits forever on the cancel event. This is the ARISTOTLE shape
  (tutoring state machine driven by user turns, not by a timer).
- **GUI mount is v1.1.** `test_mounts_extension_gui_pages` is `xfail(strict=True)`
  until `register_gui_page` + stage 4 mount land. Do not remove the xfail
  marker without implementing stage 4.
- **`config.schema` import is arbitrary code execution.** Loading
  `aristotle.config:AristotleSettings` imports the extension's config module.
  Only install extensions from trusted sources. The host validates the
  imported class is a dataclass/BaseModel/BaseSettings subclass.
- **WorkflowRegistry is host-owned (ADR-014 §5.4).** The host is constructed
  with a `workflow_registry` param (passed by lifespan). At stage 3 register,
  `_register_one` calls `workflow_registry.add_path(workflows_path)` for each
  extension's `workflows_dir`. If the param is None (tests, or pre-wiring),
  workflows are recorded on the extension record but not discoverable via
  `WorkflowRegistry.list_templates()`.
- **WorkflowRegistry no longer silently swallows parse failures.** ADR-014
  replaced `except Exception: continue` with a logged WARNING that includes
  the file path and exception. A malformed contributed workflow is now
  debuggable instead of invisible.
- **The host adds `extensions/` to sys.path at stage 1 validate (ADR-014 §6.4).**
  This is required so the extension's Python modules (`aristotle.config`,
  `aristotle.actors.socrates`, `aristotle.hooks` sibling imports) are
  importable via `importlib.import_module`. Without it, `config.schema`
  loading fails with `ModuleNotFoundError`. The extensions/ dir (PARENT of
  the extension dir) is added, so the extension's package name becomes a
  top-level import. **Risk**: extension package names could collide with
  installed packages (e.g. naming an extension `click` or `yaml`). The
  operator owns the extensions dir and is responsible for avoiding
  collisions. Pip-installed extensions (importlib.resources) are a v2 concern.

## Last Cycle
- **ARISTOTLE Phase A dogfood drop** (this cycle):
  - Built `extensions/aristotle/` (7 files) — the first real extension on
    the platform. Manifest v1 with one `textbook` corpus, `socrates` actor,
    `M001_aristotle.sql` migration (aristotle_concept + aristotle_struggle_pattern
    with bilingual schema), `AristotleSettings` dataclass (en/ur defaults),
    `SocratesActor` conforming to the foundation Actor Protocol, `hooks.py`
    registering SOCRATES at stage 5, placeholder `tutoring_session_v1.yaml`
    workflow. See `extensions/aristotle/AGENTS.md` for the full contract.
  - **Surfaced + fixed a platform gap**: the host's `_import_class` did
    `importlib.import_module("aristotle.config")` but `aristotle` wasn't
    importable because `extensions/` wasn't on sys.path. Fixed by adding
    `extensions/` to sys.path at stage 1 validate (`host.py` — new "host
    adds extensions/ to sys.path" Known Gotcha above). This is exactly the
    kind of gap ARISTOTLE was supposed to surface (ADR-ARISTOTLE §9).
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
- **ADR-014 step 3 — Actor Protocol formalization** (prior cycle):
  - Replaced the local `_ActorContext` dataclass in `host.py` with the
    foundation `ActorContext` (ADR-014 §5.2). The host now imports
    `Actor`, `ActorContext`, `ActorResult` from
    `aip.foundation.protocols.actors`.
  - The scheduler's `_actor_scheduler_loop` now validates actor conformance
    via `isinstance(actor, Actor)` at start. A non-conforming actor is
    logged as `actor_not_conforming` and the scheduler exits — the actor
    name stays registered (so `registered_actors()` lists it) but no
    cycles run.
  - Added `_run_one_cycle()` helper that handles `ActorResult`: logs
    non-ok results (`actor_cycle_not_ok`), honors `next_run_at` override
    for the next cycle only (back-off / speed-up).
  - The `ActorContext.logger` is a stdlib `LoggerAdapter` bound with ext +
    actor names (foundation types it as `Any` — works with both stdlib
    logging and structlog).
  - Updated `tests/test_extension_lifecycle.py`'s `_DemoActor` to return
    `ActorResult(ok=True)` instead of a bare dict — the demo actor now
    conforms to the Protocol and is a correct example for extension authors.
  - Added `tests/test_actor_protocol.py` (11 contract tests): conforming
    actor passes `isinstance`; 4 non-conforming variants (missing name /
    cadence / run_cycle / health) fail; `runtime_checkable` flag;
    `ActorContext` + `ActorResult` dataclass fields; barrel re-export;
    demo actor conformance belt-and-suspenders check.
  - Verified: all 11 Actor Protocol tests pass; all 3 WorkflowRegistry
    tests still pass; layer discipline tests pass (foundation doesn't
    import from adapter/orchestration); host imports cleanly with the
    new foundation import.
- **ADR-014 step 2 — Lifespan wiring + WorkflowRegistry.add_path** (prior cycle):
  - Wired `ExtensionHost` into `app.py::lifespan`: `container.extensions =
    host` + `await host.start()` after CorpusRegistry (before actor
    schedulers), `await host.stop()` in shutdown. The host block is
    sandboxed — a failure logs a warning and continues (host stays None,
    degraded mode).
  - Added `extensions` and `workflow_registry` fields to `AipContainer`.
  - Constructed `WorkflowRegistry` in lifespan with the default `workflows/`
    dir (backward compat), stored on `container.workflow_registry`, passed
    to `ExtensionHost(workflow_registry=...)`.
  - Added `WorkflowRegistry.add_path(dir)` (ADR-014 §5.4): re-globs a
    per-extension workflows dir and merges templates into the registry.
    Tracks per-template source dirs so `load_workflow()` resolves paths
    correctly (absolute for extension templates, relative for default).
  - Replaced `except Exception: continue` in `_load_templates` with a
    logged WARNING — malformed YAMLs are now debuggable, not silent.
  - Wired `host._register_one` to call `workflow_registry.add_path()` for
    each extension's `workflows_dir`. If add_path raises (it shouldn't —
    it's sandboxed internally), records a workflow-tagged failure without
    failing the whole register stage.
  - Verified: all 3 existing `test_extended_workflows.py` tests pass
    (backward compat preserved); 6 new WorkflowRegistry behavior tests
    pass (default discovery, add_path, load_workflow for extension
    templates, malformed YAML logged + skipped, missing dir no-op,
    absolute path resolution); `ExtensionHost` accepts `workflow_registry`
    param (defaults None for backward compat with tests); app.py imports
    well-formed (no circular imports).
- **ADR-014 step 1 — ExtensionHost skeleton + TDD contract GREEN** (prior cycle):
  - Built `src/aip/adapter/extensions/` package: `state.py` (ExtensionState
    enum + Failure dataclass), `supervision.py` (supervised_task helper),
    `manifest.py` (pydantic v2 Manifest model with v1 schema),
    `registry.py` (ExtensionRecord + ExtensionRegistry + ActorRegistration
    + NavItem), `host.py` (ExtensionHost lifecycle driver),
    `loaders/migration_loader.py` (.sql files → LoadedMigration → applied
    via separate `extension_applied_migrations` table).
  - Stages 0–3 + 5 implemented: discover (keyed by directory name), validate
    (pydantic + manifest_version range + id collision + config.schema load),
    migrate (CorpusRegistry.register + apply_extension_migrations), register
    (channels + workflows recorded), ready (on_load hook with context
    manager for host.config/manifest).
  - Stage 4 (GUI mount) is v1.1 — `test_mounts_extension_gui_pages` marked
    `xfail(strict=True)`.
  - Shutdown: `host.stop()` cancels actor scheduler tasks, calls on_unload
    hooks (sandboxed), marks every extension DISABLED.
  - Fixed test bug: `test_two_extensions_with_same_id_fails_cleanly` had a
    dict-comprehension logic error (two records with the same manifest id
    collapse to one dict key). Rewrote the assertion to iterate records
    directly and check that one is VALIDATED and the other is FAILED.
  - Verified: all 8 files pass `ast.parse`; Manifest model passes 8
    validation cases (valid, colon-in-id, id=definer, invalid corpus type,
    extra field, config.schema alias, gui block, path helpers);
    ExtensionHost imports cleanly with all required API surface;
    discover+validate flow smoke-tested (VALIDATED / FAILED /
    DISABLED all transition correctly).

## Key Files
| File | Role |
|------|------|
| `__init__.py` | Re-exports ExtensionHost, ExtensionState, Manifest, etc. |
| `host.py` | ExtensionHost — lifecycle driver (discover/validate/migrate/register/ready/stop) |
| `state.py` | ExtensionState enum (8 states) + Failure dataclass |
| `manifest.py` | Pydantic v2 Manifest model (v1 schema) + Contributes/CorpusContribution/GuiContribution/ConfigBlock |
| `registry.py` | ExtensionRegistry (host-owned) + ExtensionRecord + ActorRegistration + NavItem |
| `supervision.py` | `supervised_task(name, coro)` — named, supervised asyncio.create_task |
| `loaders/migration_loader.py` | `load_migrations_dir()` + `apply_extension_migrations()` (separate `extension_applied_migrations` table) |
| `loaders/__init__.py` | Re-exports loader API |

## Work Guidance
- Adding a new lifecycle stage: add it to `ExtensionHost._migrate_register_ready_one`
  (or a new stage method), wrap it in try/except that records a `Failure` and
  transitions to `DEGRADED`. Never let a stage raise out of the per-extension
  sandbox.
- Adding a new manifest field: add it to `Manifest` (or the relevant sub-model)
  in `manifest.py`. Pydantic validates it automatically. If it's a new
  contribution type, also add the registration function to `ExtensionHost`
  and the tracking list to `ExtensionRecord`.
- Adding a new ExtensionState: add it to the enum in `state.py`, update the
  terminal-ish semantics in the docstring, and ensure `health_snapshot()`
  in `registry.py` doesn't need changes (it just reads `.value`).
- Testing: every new behavior gets a test in `tests/test_extension_lifecycle.py`.
  The test file IS the contract — do not loosen a test to make it pass.

## How to Test
```bash
# Run the lifecycle contract tests (should be GREEN for v1.0 stages, xfail for v1.1 GUI):
CI=true uv run pytest tests/test_extension_lifecycle.py -v

# Verify the manifest model in isolation:
PYTHONPATH=src python -c "
import yaml
from aip.adapter.extensions.manifest import Manifest
m = Manifest.model_validate(yaml.safe_load(open('path/to/extension.yaml')))
print(m.id, m.version, m.contributes.corpora)
"
```

# ============================================================
