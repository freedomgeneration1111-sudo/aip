# ============================================================

# Foundation Layer — Agent Navigation
> Pure domain. Zero external I/O. Zero imports from orchestration or adapter.

## Purpose
The foundation layer defines the canonical types, validation rules, Protocol interfaces,
and the ECS state machine that all other layers depend on. It is the only source of
truth for domain contracts. If something should be true everywhere, it is defined here.

## Architecture Constraints
- **Zero imports from orchestration or adapter** — enforced by layer discipline.
  A linter violation here breaks the entire architectural guarantee.
- **No side effects** — no file I/O, no network calls, no database calls in this layer.
- **Schemas are dataclasses only** — no business logic in schema classes.
  Validation logic belongs in `validation.py`.
- **Protocols are the only seam** — `foundation/protocols/` defines interfaces.
  New capabilities go through a new Protocol first.

## Contracts (What This Module Promises to Consumers)

### Protocol Interface Contract (Consumed by orchestration AND adapter)
The 9 Protocol interfaces in `protocols/` are the ONLY legal seam between
orchestration and adapter. Any cross-layer communication MUST go through one of these:

| Protocol | File | Consumers |
|----------|------|-----------|
| `StorageProvider` | `protocols/storage.py` | All stores, pipelines |
| `ModelProvider` | `protocols/model.py` | Model dispatch, ask pipeline |
| `RetrievalProvider` | `protocols/retrieval.py` | Retrieval orchestrator |
| `ActorProvider` | `protocols/actors.py` | Actor scheduling |
| `AuthProvider` | `protocols/auth.py` | Auth middleware |
| `BudgetProvider` | `protocols/budget.py` | Budget tracking |
| `KnowledgeProvider` | `protocols/knowledge.py` | Knowledge store |
| `PluginProvider` | `protocols/plugin.py` | Plugin system |
| (reserved) | `protocols/__init__.py` | Protocol registry |

**Adding a new Protocol**: Define here first, then implement in adapter,
then consume in orchestration. Never add a concrete implementation in foundation.

### ECS State Machine Contract (Consumed by ALL pipelines)
- `ecs_graph.py` is the GOLD STANDARD for all lifecycle transitions
- ECS transitions: SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED
- **No reverse transitions. No skip transitions.** These are governance invariants.
- Pipelines MUST NOT implement their own transition logic — call `ecs_graph.py` only
- Changing transitions requires updating `tests/test_ecs_graph.py` first

### Schema Contracts (Consumed by all layers)
- 14 domain schemas in `schemas/`
- All schemas are `@dataclass` — no Pydantic, no business logic
- Validation rules live in `validation.py`, not in schema classes
- Adding a field: update dataclass + validation.py + all test constructors

## Data Flows (In / Out)

### In (What foundation receives)
- Nothing — foundation has zero external dependencies
- It defines types that other layers import

### Out (What foundation provides)
- **Protocol interfaces** → orchestration (consumes) + adapter (implements)
- **ECS graph** → all pipelines (transition logic)
- **Schemas** → all layers (data types)
- **Validation** → orchestration (pre-pipeline validation)
- **Source types** → ingestion pipeline
- **FTS sanitization** → lexical search

### Cross-Folder Data Flows
```
foundation/protocols/ (9 Protocol interfaces)
  → adapter/ (implements concrete classes)
  → orchestration/ (consumes through Protocol injection)

foundation/ecs_graph.py (state machine)
  → orchestration/review_export_pipeline.py (APPROVED transition)
  → orchestration/artifact_lifecycle.py (lifecycle transitions)
  → adapter/ecs_store_persistent.py (persistent state)

foundation/schemas/ (14 domain schemas)
  → adapter/api/routes/ (request/response types)
  → orchestration/ (pipeline data types)
  → adapter/stores/ (storage types)
```

## Known Gotchas
- **Never add a concrete implementation here**: Protocols only. If you add a class
  with actual logic in foundation, it violates the "pure domain" constraint.
- **Protocol changes have system-wide blast radius**: Adding a method to a Protocol
  requires updating ALL implementations in adapter. Check the consumer map above.
- **ECS graph is authoritative**: If a pipeline has transition logic that disagrees
  with `ecs_graph.py`, the pipeline is wrong. Always defer to the graph.
- **Schema field additions require test updates**: Every test that constructs a
  schema will need the new field. Add it with a default value if possible.

## Last Cycle
- **Commit 14d3a73**: No changes to foundation layer. This layer was stable
  during the operator console debugging cycle.

## Ownership
This layer is owned by no runtime — it is consumed by both orchestration and adapter.
Changes here have system-wide blast radius. Review all Protocol consumers before editing.

## Key Files
| File | Role |
|------|------|
| `ecs_graph.py` | Declarative ECS state machine — gold standard for all lifecycle transitions |
| `protocols/` | 9 Protocol interfaces for dependency injection — the adapter/orchestration seam |
| `schemas/` | Dataclass definitions: ingestion, ask, review, export, artifact, etc. (14 schemas) |
| `validation.py` | Structural validation rules — used by orchestration before any pipeline step |
| `source_types.py` | Source type definitions for ingestion pipeline |
| `sanitize_fts.py` | FTS5 query sanitization — prevents injection in search queries |

## Work Guidance
- Adding a new domain capability: define the Protocol interface here first,
  then implement in adapter, then consume in orchestration.
- Adding a schema field: update the dataclass in `schemas/`, update `validation.py`
  if the field has constraints, update any tests that construct the schema.
- Modifying ECS transitions: edit `ecs_graph.py` only. Verify all transition paths
  in `tests/test_ecs_graph.py` before committing.
- Never add a concrete implementation here — Protocols only.

## How to Test
```bash
uv run pytest tests/test_foundation.py
uv run pytest tests/test_ecs_graph.py
uv run pytest tests/test_validation.py
uv run ruff check src/aip/foundation/
```


# ============================================================
