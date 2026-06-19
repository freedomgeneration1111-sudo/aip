# ADR-014: Phase 0 Extension Platform — ExtensionHost Lifecycle & Manifest v1

**Date:** 2026-06-18
**Status:** PROPOSED — build target
**DEFINER:** B. Moses Jorgensen
**Supersedes:** None (extends ADR-PHASE0 draft; corrects its §1 "already exists" framing)
**Verified against:** `feat/multi-corpus` @ `956f06f`

---

## Context

Phase 0 is AIP Brain becoming a platform. ARISTOTLE (the adaptive tutor) is the
first extension that proves the platform did. Every capability in this ADR is
designed against the union of what ARISTOTLE (a guided-session app), LOOM (a
document workspace), and CodeForge (a build console) each demand — using their
differences as the validation set. If the protocol bends toward any one of them,
it is wrong.

The prior draft (ADR-PHASE0) overstated what already exists. Three primitives
called "working extension points / precedent to follow" are **structurally
present but unwired** — not in `AipContainer`, never instantiated in `lifespan`:

- `PluginManager` / `PluginLoader` / `YamlPluginProvider` — REST/CLI surfaces
  return `503 Plugin infrastructure not available` in the default runtime.
- `AipMcpServer` — `start(transport=...)` is a stub; only in-process
  `call_tool()` works.
- `WorkflowRegistry` — not in the container; single hardcoded `workflows/` dir;
  silent `except Exception: continue` on YAML parse errors.

Only `register_custom_channel` (`orchestration/channels/registry.py:44`) is a
real, module-level registration point that's wired into production. **Treat
plugin / MCP / workflow as net-new for the manifest; the host must wire them.**

What *is* solid and wired: `CorpusRegistry`, `CorpusConnectionManager`, the
migration runner (`corpus_migration_runner.py`), session/corpus binding
(`session_corpus_binding.py`), retrieval scoping (`corpus_retrieval.py`), bridge
edges (`graph_store.py`), the audit CLI (`cli/audit.py`). The host stands on
these.

This ADR is the contract for the `ExtensionHost` that drives them.

---

## Decision

### 1. Settled decisions (this ADR assumes them)

| # | Decision | Rationale |
|---|---|---|
| Tenancy | **One install per learner** (pre-alpha). Multi-tenant is the deferred enterprise version. Progress store gets a stable PK now; the tenant dimension is added later without a rewrite. No multi-tenant code now. | Pre-alpha dogfood stage. No need to build enterprise. The tenant dimension is a forward-compatible schema concern, not a runtime concern. |
| Storage | **Corpora are per-subject** (`textbook`, later `field`). **Progress/mastery/struggle_pattern is a single-tenant relational store, not a corpus.** `MAX_CORPORA = 4` is a non-issue at this scale. | The per-student corpus explosion is the wrong shape for pre-alpha. ARISTOTLE's `struggle_pattern` is a single row update, not a fan-in delivery. |
| Progress store location | **Tables in the `definer` corpus** with `aristotle_*` naming convention (e.g. `aristotle_progress`, `aristotle_mastery`, `aristotle_struggle_pattern`). The definer corpus already carries extension-contributed tables (`review_queue_fanin`, `corpus_audit_log`, `review_fanin_outbox`); ARISTOTLE's tables follow the same pattern. | Avoids a new `CorpusConnectionManager` and migration runner for the progress store. Revisit at Phase B (teacher dashboard) when cross-student aggregation matters. |
| Sensitivity | Per-corpus `sensitive` flag (the generalized branham model). Unrelated to student data. | The §4 generalization is real; no `corpus_id == "branham"` branch remains. The flag is data, not code. |
| Manifest | **Hybrid** — YAML is the contract; one optional sandboxed `hooks.py::on_load(host)` is the escape hatch. | YAML gives declarative validation + JSON Schema generation. The escape hatch handles dynamic registration (e.g. conditional on a feature flag) without making the manifest Turing-complete. |
| Manifest v1 scope | `manifest_version, id, name, version, depends, corpora, actors, channels, workflows_dir, migrations, config`. **Defer `tools`, `expose_as_mcp` to v1.2** (MCP transport is a stub). **`gui` lands in v1.1** — the very next increment — because `register_page` is built as part of this host. | v1.0 ships the backend contract. v1.1 ships GUI mount. v1.2 ships MCP generalization. Each increment is independently useful; the manifest version is reserved now so v1.1 / v1.2 don't break v1.0 extensions. |
| Actors | Define an `Actor` Protocol (§5.2); **new** actors conform. **Do not** migrate Beast/Vigil/Sexton — adapt them at the boundary with a thin `Actor`-conforming wrapper. Startup-only registration; hot-load is a v2 problem. | God classes (1998 / 1808 / 2345 LOC) can't be migrated in pre-alpha. The Protocol is the contract new actors code against; the wrapper is the bridge for legacy. |
| Migration failure | SQLite has no transactional DDL — do not fake rollback. A failed migration marks the extension `DEGRADED` and surfaces it. | The runner already records `applied_migrations` per-statement; a half-applied migration is visible. Rollback would require per-migration down-scripts, which is out of scope. |
| Bilingual schema | `content_primary` + `content_alt` + `content_alt_lang` (ISO 639-1), not `content_urdu`. | Generalizes to any bilingual pair (English/Spanish, English/Urdu) without being unbounded JSON. ARISTOTLE sets `content_alt_lang='ur'`. |
| Branham rename | **Done in this ADR's first build step.** `corpus_retrieval.py:244` was the last runtime emitter of `BRANHAM_POLICY_TRIGGERED`; it now emits `RESTRICTED_CORPUS_ACCESS_DENIED`, matching `corpus_registry.py:324`. The `BranhamIsolationViolation = RestrictedCorpusAccessViolation` exception alias and the deprecated parameter aliases (`session_branham_allowlist`, `branham_policy_enabled`) are kept for one release cycle. The structured log key `branham_isolation_suppressed` is also kept (it's a log filter key, not an audit action). | The audit action is the user-facing surface that has to be clean. The exception alias and log key are operator-facing surfaces that are cheaper to keep than to migrate. |

### 2. Placement

New package `src/aip/adapter/extensions/` (adapter layer — it wires the
container, FastAPI, and GUI):

```
src/aip/adapter/extensions/
    __init__.py        # re-exports ExtensionHost, ExtensionState, ExtensionRegistry
    host.py            # ExtensionHost — lifecycle driver
    registry.py        # ExtensionRegistry — per-extension state + contributions
    manifest.py        # Manifest (pydantic v2 BaseModel) + validator
    state.py           # ExtensionState enum
    supervision.py     # _supervised_task helper (see §3.4)
    loaders/
        __init__.py
        migration_loader.py    # .sql files → Migration dataclasses (see §3.1)
        workflow_path.py       # multi-dir globbing adapter (see §3.2)
```

**Manifest validation library: pydantic v2 `BaseModel`.** Already a project
dependency (`pydantic>=2.0` in `pyproject.toml`). Gives structured validation
errors (which feed into the §7 health surface), JSON Schema generation for
documentation, and familiar types.

**Lifespan integration** — the 1910-line god-function gains **two** blocks, not
eight per extension. After `CorpusRegistry.startup()` and before actor
schedulers start:

```python
# In lifespan startup, after CorpusRegistry.startup() and before actor schedulers:
host = ExtensionHost(
    extensions_dir=Path(config.get("extensions", {}).get("dir", "extensions")),
    container=container,
    manifest_version_range=(1, 1),   # inclusive; v1.0 + v1.1
)
container.extensions = host
await host.start()   # discover -> validate -> migrate -> register -> ready
# ... existing actor schedulers start AFTER host.start() so extension
# actors are registered before the first Beast/Vigil/Sexton cycle.
```

In shutdown (before `CorpusRegistry` close):

```python
await host.stop()   # cancel extension actor schedulers, call on_unload, mark DISABLED
```

That's the entire lifespan change for extensions. Every other lifecycle concern
lives inside `ExtensionHost`.

### 3. ExtensionState

```python
class ExtensionState(str, Enum):
    DISCOVERED = "DISCOVERED"   # manifest found, not yet parsed
    VALIDATED  = "VALIDATED"    # manifest + config schema valid
    MIGRATING  = "MIGRATING"    # running contributed migrations
    REGISTERED = "REGISTERED"   # corpora/channels/actors/workflows registered
    MOUNTED    = "MOUNTED"      # GUI mounted (v1.1); fully live
    DEGRADED   = "DEGRADED"     # partially up; one contribution failed, host intact
    DISABLED   = "DISABLED"     # operator-disabled OR host.stop() called; not serving
    FAILED     = "FAILED"       # could not reach REGISTERED; isolated, host intact
```

Terminal-ish: `FAILED` / `DISABLED` do not serve. `DEGRADED` serves what it can.
The health surface (§7) exposes state + the per-contribution failure reason.

### 4. Lifecycle stages (host-owned)

Each stage is per-extension and sandbox-wrapped (reuse the `_sandbox_wrap`
pattern from `plugins.py`); a raise transitions that extension to
`DEGRADED` / `FAILED` and **never** propagates to the host. Stage order is
fixed by data dependency:

| Stage | Does | Contribution | On failure | State |
|---|---|---|---|---|
| 0 discover | find `extension.yaml` under operator-owned `extensions/` dir | — | skip, log | `DISCOVERED` |
| 1 validate | parse manifest (pydantic); `manifest_version` in host range; load + validate `config.schema`; check `ext_id` uniqueness; check `enabled` field | `config` | `FAILED` | `VALIDATED` or `FAILED` |
| 2 migrate | run contributed migrations via `corpus_migration_runner` (after `MigrationLoader` reads `.sql` files) | `migrations` | `DEGRADED` (no rollback) | `MIGRATING` → `DEGRADED` |
| 3 register | `CorpusRegistry.startup` adds corpora (namespaced `{ext_id}:{role}`); `register_custom_channel` (host-owned list); `WorkflowRegistry.add_path(ext_workflows_dir)`; actor factories registered from `on_load` (advisory list in manifest warns at stage 5 if not registered) | `corpora, channels, actors, workflows_dir` | `DEGRADED` per-item | `REGISTERED` or `DEGRADED` |
| 4 mount (v1.1) | `register_gui_page` injects nav + pages | `gui` | `DEGRADED` (backend stays up) | `MOUNTED` or `DEGRADED` |
| 5 ready | run `hooks.py::on_load(host)` if present (sandboxed); warn on declared-but-not-registered actors/channels; mark `MOUNTED` (v1.1) or `REGISTERED` (v1.0) | hook | `DEGRADED` | `MOUNTED` / `REGISTERED` or `DEGRADED` |

#### 4.1 FAILED vs DEGRADED boundary (pinned)

- **Stage 1 FAILED:** manifest unparseable, schema invalid, `manifest_version`
  out of range, `config.schema` class missing or not a `BaseSettings` /
  `dataclass` subclass, required env var unset, `ext_id` collision with another
  extension or with `"definer"`.
- **Stage 2 DEGRADED:** migration SQL syntax error, migration apply raises,
  migration checksum mismatch (extension changed a migration that already ran).
- **Stage 3 DEGRADED per-item:** one actor factory raises, one channel register
  raises, one workflow YAML malformed — other items still register.
- **Stage 4 DEGRADED:** GUI page builder raises on mount — backend stays up,
  nav entry suppressed.
- **Stage 5 DEGRADED:** `hooks.py::on_load` raises — extension is `REGISTERED`
  but its dynamic contributions (if any) didn't land.

#### 4.2 Shutdown stages (host-owned, reverse order)

`host.stop()` runs on lifespan shutdown:

1. **Cancel** every actor scheduler task created by `register_actor` (via
   `_supervised_task` tracking — see §3.4).
2. **Call** `hooks.py::on_unload(host)` if present (sandboxed; failures logged,
   never propagated).
3. **Mark** every extension `DISABLED`. (Corpora stay open — `CorpusRegistry`
   owns their lifecycle and closes them in its own shutdown.)

### 5. The host's public API + registration functions

#### 5.1 ExtensionHost public API

```python
class ExtensionHost:
    def __init__(
        self,
        *,
        extensions_dir: Path,
        container: AipContainer,
        manifest_version_range: tuple[int, int] = (1, 1),
    ) -> None: ...

    # Lifecycle (called from lifespan)
    async def start(self) -> None: ...      # stages 0–3 + 5 (v1.0); 0–5 (v1.1)
    async def stop(self) -> None: ...       # §4.2 shutdown stages

    # Registration (called from on_load; also used by host for built-ins)
    def register_actor(
        self, name: str, factory: Callable[[], Actor], *,
        cadence: float | None = None,
    ) -> None: ...
    def register_channel(
        self, name: str, register_fn: ChannelRegisterFn,
    ) -> None: ...       # host-owned; replaces the module-level _custom_channels list
    def register_workflow(self, path: str) -> None: ...    # single-file runtime registration
    def register_mcp_tool(self, ...) -> None: ...          # v1.2
    def register_page(
        self, route: str, title: str, icon: str,
        builder_fn: Callable, *, order: int = 50,
    ) -> None: ...       # v1.1; conflict = build error

    # Access (read-only; for use inside on_load)
    @property
    def container(self) -> AipContainer: ...
    @property
    def config(self) -> BaseSettings: ...    # the calling extension's own validated config
    @property
    def manifest(self) -> Manifest: ...      # the calling extension's own manifest

    # State (read-only)
    def state(self, ext_id: str) -> ExtensionState: ...
    def failures(self, ext_id: str) -> list[Failure]: ...
    def registered_actors(self) -> list[str]: ...
    def nav_items(self) -> list[NavItem]: ...   # v1.1
    def health(self) -> list[dict]: ...
    def is_running(self) -> bool: ...
```

The host tracks which extension is currently executing `on_load` (a context
manager) so `self.config` and `self.manifest` resolve to the right extension.

#### 5.2 Actor Protocol (new actors conform)

```python
class Actor(Protocol):
    name: str
    cadence: float   # seconds between cycles; 0 = manual only

    async def run_cycle(self, ctx: ActorContext) -> ActorResult: ...
    def health(self) -> dict: ...

@dataclass
class ActorContext:
    container: AipContainer
    config: BaseSettings         # the extension's own validated config
    logger: structlog.BoundLogger
    cancel_event: asyncio.Event  # set by host.stop()

@dataclass
class ActorResult:
    ok: bool
    error: str | None = None
    next_run_at: float | None = None   # override cadence for next cycle (epoch)
```

The host's `ActorScheduler` runs one `asyncio.Task` per registered actor, gated
on `CorpusRegistry.migration_ready` (existing pattern from §A5). Each task is
created via `_supervised_task` (§3.4) so exceptions are logged and the task is
trackable for `stop()`.

**Beast/Vigil/Sexton are NOT migrated.** Their existing schedulers in `lifespan`
continue to run as-is. When an extension needs to call a core actor (e.g.
ARISTOTLE's MENTOR queries Vigil for SM-2 due items), it does so through the
container: `host.container.vigil.due_for(student_id)`. If the core actor's API
doesn't expose what the extension needs, that's a Phase 0 protocol gap — log it
per ADR-ARISTOTLE §9.

#### 5.3 Manifest `actors` / `channels` are advisory

The manifest's `actors: [socrates, examiner, mentor]` and `channels: [...]`
lists are **documentation, not registration**. Actual registration happens
programmatically from `hooks.py::on_load(host)`:

```python
# aristotle/hooks.py
def on_load(host: ExtensionHost) -> None:
    host.register_actor("socrates", lambda: Socrates(host.config), cadence=0)
    host.register_actor("examiner", lambda: Examiner(host.config), cadence=0)
    host.register_actor("mentor", lambda: Mentor(host.config), cadence=300)
    host.register_channel("curriculum_channel", register_curriculum_channel)
```

At stage 5, the host warns if a manifest-declared actor/channel wasn't
registered by `on_load`. The warning is logged and surfaced in the health
output (§7) but doesn't transition the extension to `DEGRADED`.

#### 5.4 WorkflowRegistry multi-directory globbing

The existing `WorkflowRegistry.__init__(workflows_dir)` globs a single dir.
The host adds a new method:

```python
class WorkflowRegistry:
    def __init__(self, workflows_dir: str | Path = "workflows") -> None: ...
    def add_path(self, dir: Path) -> None:
        """Add another directory to the glob set. Re-globs immediately."""
        ...
```

The host calls `workflow_registry.add_path(ext_dir / workflows_dir)` at stage 3
for each extension. `register_workflow(path)` from `on_load` is for **single-file
runtime registration** — an extension that generates a workflow at runtime
(e.g. a teacher-authored tutoring session) calls it directly. Two APIs, two
purposes.

The `except Exception: continue` in `WorkflowRegistry.__init__` (and `add_path`)
is replaced with a logged warning that includes the file path and exception.
Silent YAML parse failures are no longer silent.

### 6. Manifest v1 (minimal)

```yaml
manifest_version: 1
id: aristotle
name: "Aristotle — Adaptive Tutor"
version: 0.1.0
depends: []                      # reserved: list of extension ids; not enforced in v1
enabled: true                    # default; operator can set false to disable
contributes:
  corpora:
    - { role: textbook, type: document, sensitive: false }   # per subject
  actors:   [socrates, examiner, mentor]   # advisory; registered from on_load
  channels: [curriculum_channel]           # advisory
  workflows_dir: workflows                 # relative to the extension directory
  migrations: migrations                   # relative to the extension directory
config:
  schema: aristotle.config:AristotleSettings   # typed; required-secret env vars validated at stage 1
# v1.1 adds: gui: { nav: { label, icon, order }, pages: aristotle.gui:register_pages }
# v1.2 adds: tools, expose_as_mcp
```

#### 6.1 Field semantics

- **`manifest_version`** (int, required): the manifest schema version. Host
  checks against `manifest_version_range`. Out-of-range → `FAILED` at stage 1
  with a `manifest_version`-tagged failure reason.
- **`id`** (str, required): the extension id. **Immutable post-registration.**
  Must not contain `:` (used for corpus_id namespacing). Must not collide with
  another extension's id or with `"definer"`.
- **`name`** (str, required): human-readable name.
- **`version`** (str, required): semver. Used for compatibility checks against
  other extensions and for upgrade migrations.
- **`depends`** (list[str], optional, default `[]`): reserved for v1.1+.
  Validated as a list of extension ids in v1; not enforced (no dependency
  resolution yet). Reserved now so v1.1+ can add it without a manifest version
  bump.
- **`enabled`** (bool, optional, default `true`): manifest-declared default.
  Operator can override via host config (deferred to v1.1 — for v1.0,
  manifest-only).
- **`contributes`** (dict, required): the contribution declarations.
- **`config`** (dict, optional): the extension's typed settings.

#### 6.2 Corpus ID namespacing convention

Contributed corpora are registered as `{ext_id}:{role}`. Core corpora
(`"definer"`) stay flat. Examples:

- ARISTOTLE: `aristotle:textbook`, `aristotle:field`
- LOOM (future): `loom:book`
- CodeForge (future): `codeforge:code`

Both `ext_id` and `role` must not contain `:`. The convention is enforced at
stage 1 (validate). The session binding's `active_corpus_ids` carries these
namespaced ids verbatim.

#### 6.3 Path resolution

`workflows_dir` and `migrations` are **relative to the extension directory**
discovered at stage 0. For an extension at `extensions/aristotle/`,
`workflows_dir: workflows` resolves to `extensions/aristotle/workflows/`.

Pip-installed extensions (resolved via `importlib.resources`) are a v2 concern.

#### 6.4 `config.schema` trust and sandboxing

`config.schema: aristotle.config:AristotleSettings` is a Python path string.
Loading it requires `importlib.import_module("aristotle.config")` then
`getattr(..., "AristotleSettings")`. This is arbitrary code execution at
validation time.

For operator-installed extensions (same trust level as the extension code
itself), this is acceptable. The host:

1. Runs the import inside the same sandbox wrapper used for `on_load`. If the
   import raises, transitions to `FAILED` at stage 1 with a `config`-tagged
   failure.
2. Validates the imported class is a `pydantic_settings.BaseSettings` or
   `dataclasses.dataclass` subclass. Arbitrary classes are rejected.
3. Documents in the operator guide: "loading `config.schema` imports the
   extension's config module. Only install extensions from trusted sources."

### 7. Health surface

`container.extensions.health() -> list[dict]` returns:

```python
[
    {
        "id": "aristotle",
        "version": "0.1.0",
        "state": "MOUNTED",        # or REGISTERED / DEGRADED / FAILED / DISABLED
        "failures": [
            {
                "stage": "migrate",
                "contribution": "migrations",
                "reason": "sqlite3.OperationalError: near \"this\": syntax error"
            }
        ]
    }
]
```

This backs the operator/teacher "extension health" tab and the FastAPI
`/health/extensions` route.

**Gating semantics:**
- ARISTOTLE's **backend session** is gated on `REGISTERED` (stages 0–3 + 5
  complete). The tutoring state machine can run; API/CLI surfaces are live.
- ARISTOTLE's **GUI learning view** is gated on `MOUNTED` (v1.1, stage 4
  complete). In v1.0, ARISTOTLE is API/CLI-testable; the GUI learning view
  lands at v1.1.
- `DEGRADED` falls back to an explicit "tutor unavailable" message, never a 500.

Pair with per-exception HTTP handlers
(`RestrictedCorpusAccessViolation` → 403, `CorpusNotFound` → 404,
`CorpusMigrationError` → 503, `DeletionStateError` → 409) so the GUI can tell
a sensitivity gate from a dead database. (The HTTP handler work is a separate
PR — it doesn't block this ADR.)

### 8. Build order (this unit)

**Step 0 (DONE in this commit):** Finish the branham audit-action rename.
`corpus_retrieval.py:244` was the last runtime emitter; it now emits
`RESTRICTED_CORPUS_ACCESS_DENIED`. The stale comment in
`corpus_store_factory.py:325` is updated to match. The exception alias and
deprecated parameter aliases are kept for one release cycle.

1. `ExtensionState`, `ExtensionRegistry`, `ExtensionHost` skeleton +
   `_supervised_task` helper + the failing `test_extension_lifecycle.py`
   (§contract). The test is RED by design — it fails to collect (ImportError)
   until the host exists, then each test pins one behavior.

2. Wire `PluginManager`, `WorkflowRegistry`, `McpToolRegistry` as host-owned
   services on `container.extensions`. This includes:
   - `McpToolRegistry` (net-new) — replaces the hardcoded `TOOLS` list in
     `adapter/mcp/server.py:48`. The 8 built-in tools are migrated onto it;
     `AipMcpServer.TOOLS` becomes `registry.list_tools()`. Extensions don't
     contribute tools until v1.2, but the registry exists in v1.0 so v1.2 is
     additive.
   - `WorkflowRegistry.add_path(dir)` — multi-dir globbing adapter (net-new
     method on the existing class).
   - `PluginManager` instantiation in `lifespan` (one-line wiring; today it's
     dead code in the default runtime).
   - Replace the module-level `_custom_channels` list in
     `orchestration/channels/registry.py` with a host-owned registry instance.

3. `register_actor` / `register_workflow` + the `Actor` Protocol (new actors
   only). The `ActorScheduler` runs one `_supervised_task` per actor.

4. **`MigrationLoader`** (net-new) — reads `.sql` files from the extension's
   `migrations/` dir, constructs `Migration(name, sql, verify=())` dataclasses,
   passes the dict to the existing `CorpusMigrationRunner`. `verify=()` is
   acceptable for pre-alpha; the host logs the migration name + checksum on
   success.

5. Stages 0–3 (discover → register) green against the test. Stage 5 (ready)
   also green; stage 4 (mount) is `xfail` until v1.1.

6. Manifest v1 validator (pydantic v2 `BaseModel`) + cross-stage coherence
   checks (e.g. a `corpora` entry referencing a table no migration creates is
   a build-time error, not a pilot 500).

7. (v1.1) `register_gui_page` + stage 4 mount; `gui/components/layout.py`
   reads nav from the registry instead of the hardcoded `_NAV_ITEMS` list.
   Layout's `_NAV_ITEMS` becomes `host.nav_items()` called at render time.

After 1–6, manifest v1 is glue over wired primitives, and ARISTOTLE Phase A can
start against a real contract — its first hand-wired page swapped for a mounted
one at v1.1.

### 9. Net-new work the ADR explicitly calls out

(Consolidated for visibility — these are the items not in the prior draft's
build order.)

| Item | Where | Why |
|---|---|---|
| `MigrationLoader` | `adapter/extensions/loaders/migration_loader.py` | The existing `CorpusMigrationRunner` takes `dict[str, Migration]` Python dataclasses; the manifest declares a directory of `.sql` files. The loader bridges them. |
| `WorkflowRegistry.add_path(dir)` | New method on existing `orchestration/workflow_registry.py:WorkflowRegistry` | Per-extension workflow dirs without copying files. |
| `McpToolRegistry` | New `adapter/mcp/tool_registry.py` (or similar) | Replaces the hardcoded `TOOLS` list. Must exist in v1.0 so v1.2 is additive. |
| `_supervised_task(name, coro)` | `adapter/extensions/supervision.py` | Every actor scheduler needs exception logging + cancellation tracking. ~15 lines. |
| pydantic v2 `BaseModel` for manifest | `adapter/extensions/manifest.py` | Validation + JSON Schema generation. Already a project dependency. |
| Per-exception HTTP handlers | `adapter/api/app.py` (separate PR) | `RestrictedCorpusAccessViolation` → 403, etc. Doesn't block this ADR. |

### 10. Longevity hedges (won't bite in dogfood; pinned now so they don't bite at Phase B/C)

- **Progress store location** (§1): `aristotle_*` tables in the definer corpus.
  Revisit at Phase B (teacher dashboard) when cross-student aggregation matters.
- **`Actor` Protocol shape** (§5.2): pinned before ARISTOTLE codes SOCRATES
  against it. ARISTOTLE's actors conform; Beast/Vigil/Sexton get thin wrappers.
- **`depends: []`** (§6.1): reserved field, validated as a list of extension
  ids in v1. Not enforced (no dependency resolution yet). Reserved so v1.1+
  can add it without a manifest version bump.
- **Audit action namespace convention**: extension-contributed audit actions
  are prefixed `{EXT_ID Upper}_...` (e.g. `ARISTOTLE_STRUGGLE_PATTERN_UPDATED`,
  `ARISTOTLE_CONCEPT_MASTERED`). The host doesn't enforce it — it's a
  convention ARISTOTLE follows so the audit CLI can filter by extension. The
  existing core audit actions (`CORPUS_REGISTERED`, `RESTRICTED_CORPUS_ACCESS_DENIED`,
  etc.) stay unprefixed.

---

## Alternatives Considered

**Python-only manifest (no YAML)** — rejected because declarative validation,
JSON Schema generation, and lintability all favor YAML. The escape hatch
(`hooks.py::on_load`) covers the dynamic cases.

**Manifest-declared factory paths** (e.g.
`actors: [{name: socrates, factory: aristotle.actors.socrates:factory}]`) —
rejected because the manifest would then need to know the factory's signature,
dependencies, and cadence. `on_load` has access to the container and can close
over dependencies; the manifest's `actors:` list is advisory documentation.

**Migrate Beast/Vigil/Sexton to the `Actor` Protocol** — rejected because the
three god-classes (6,151 LOC total) can't be migrated in pre-alpha without
destabilizing the working dogfood. The Protocol is the contract new actors
code against; thin wrappers bridge the legacy actors at the boundary.

**Per-student corpus for progress/mastery** — rejected because it forces
`MAX_CORPORA` to grow with the student count, couples the sensitivity model to
per-row granularity, and complicates backup/restore. A single-tenant
relational store in the definer corpus is the right pre-alpha shape.

**`content_urdu` column** — rejected because it doesn't generalize.
`content_primary` + `content_alt` + `content_alt_lang` (ISO 639-1) handles any
bilingual pair without schema changes.

**Roll back migrations on failure** — rejected because SQLite has no
transactional DDL. Per-migration down-scripts are out of scope for pre-alpha.
A half-applied migration is visible in `applied_migrations` and surfaces as
`DEGRADED` with the SQL error in the failure reason.

## Consequences

**What gets easier:**
- Adding a new extension (ARISTOTLE, then LOOM, then CodeForge) is one package
  + one manifest + one `hooks.py` — zero core edits.
- The lifespan god-function stops growing: two blocks for `host.start()` /
  `host.stop()`, not eight try/excepts per extension.
- Extension failure isolation: a broken ARISTOTLE doesn't take down the host
  or LOOM.
- The teacher dashboard has a real health surface to render ("tutor
  unavailable" vs 500).

**What gets harder:**
- Two new concepts to learn: `ExtensionHost` and the manifest. Onboarding cost
  for the next contributor.
- The host is a new failure surface. If `host.start()` itself raises (not a
  per-extension raise — a host-internal bug), the lifespan catches it. The
  host's own startup must be defensive.
- The `Actor` Protocol is a constraint on new actors. If ARISTOTLE codes
  against the wrong shape, the Protocol becomes a constraint instead of a
  contract. Mitigation: the Protocol is sketched in §5.2 and reviewed before
  ARISTOTLE Phase A starts.

**Dependencies introduced:**
- pydantic v2 (already present).
- No new runtime dependencies.

**Maintenance burden:**
- The manifest schema is now versioned. v1 → v1.1 → v1.2 each require a
  validator update and a host `manifest_version_range` bump.
- The `ExtensionHost` public API is the de-facto extension ABI. Breaking
  changes require a major version bump.

**Upgrade path if this decision is wrong:**
- If the manifest v1 shape is wrong, v2 is a new `manifest_version` with a
  migration path (host supports both ranges during transition).
- If the `Actor` Protocol is wrong, new actors can be migrated; legacy actors
  were never migrated so they're unaffected.
- If the host-owned registry pattern is wrong, the module-level
  `register_custom_channel` still exists as a fallback — the host can be
  unwound without losing the extension point.

## Related

- **ADR-PHASE0** (draft, superseded by this ADR's §0 correction): the original
  extension platform framing.
- **ADR-ARISTOTLE** (draft): the first extension. Reference implementation of
  this contract.
- **ADR-008** (multi-corpus architecture, Rev 3.1): the foundation the host
  stands on — `CorpusRegistry`, `CorpusConnectionManager`, migration runner,
  session/corpus binding, retrieval scoping, bridge edges, audit CLI.
- **ADR-011** (actor role boundaries): Beast/Vigil/Sexton separation. This
  ADR's `Actor` Protocol is the external-registration counterpart.
- Source files most affected:
  - `src/aip/adapter/api/app.py` — lifespan gains two blocks (§2).
  - `src/aip/adapter/api/dependencies.py` — `container.extensions` field.
  - `src/aip/adapter/extensions/` — new package (§2).
  - `src/aip/adapter/corpus_retrieval.py:244` — branham rename (DONE).
  - `src/aip/adapter/corpus_store_factory.py:325` — stale comment (DONE).
  - `src/aip/orchestration/workflow_registry.py` — `add_path(dir)` method.
  - `src/aip/adapter/mcp/server.py` — `TOOLS` list → `McpToolRegistry`.
  - `src/aip/orchestration/channels/registry.py` — `_custom_channels` → host-owned.
  - `tests/test_extension_lifecycle.py` — TDD contract (RED by design).
