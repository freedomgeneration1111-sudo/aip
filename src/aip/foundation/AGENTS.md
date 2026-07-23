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
The 10 Protocol interfaces in `protocols/` are the ONLY legal seam between
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
| `CorpusRegistryProtocol` | `protocols/corpus_registry.py` | Multi-corpus store access (ADR-008) |
| (reserved) | `protocols/__init__.py` | Protocol registry |

**Adding a new Protocol**: Define here first, then implement in adapter,
then consume in orchestration. Never add a concrete implementation in foundation.

### ECS State Machine Contract (Consumed by ALL pipelines)
- `ecs_graph.py` is the GOLD STANDARD for all lifecycle transitions
- ECS transitions: SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED
- **ARCHIVED** is a second terminal state (ADR-008 Rev 3.1 §5.1):
  - Reachable from GENERATED, REVIEWED, APPROVED (NOT from SPECIFIED)
  - Semantic: content withdrawn from retrieval while remaining on disk for
    revision-history traversal (e.g., old manuscript chapter draft)
  - SUPERSEDED = canonical artifact made obsolete by a conceptual replacement
  - Both ARCHIVED and SUPERSEDED are terminal — no exits from either
- `TERMINAL_STATES` frozenset: `{"ARCHIVED", "SUPERSEDED"}`
- `RETRIEVAL_EXCLUDED_STATES` (in `corpus_types.py`): `{"ARCHIVED", "SUPERSEDED"}` —
  turns whose latest ECS state is here are hidden from default retrieval
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
- **ARCHIVED vs SUPERSEDED** (ADR-008 Rev 3.1): both are terminal, but they have
  different semantics. ARCHIVED = content withdrawn from retrieval (revision
  history preserved, turn row stays on disk). SUPERSEDED = canonical artifact
  made obsolete by a conceptual replacement. Book revisions use ARCHIVED, not
  SUPERSEDED. Don't conflate them.
- **ECS states are strings, not Enum**: the codebase uses plain strings throughout
  (`validate_transition(from_state: str, to_state: str)`). Do NOT introduce an
  EcsState enum — it would force a rewrite of `ecs_store_persistent.py:188` and
  `artifact_lifecycle.py:180`. The `CorpusType` and `CorpusDeletionState` enums
  in `corpus_types.py` ARE enums (they're new, no backward-compat constraint).

## Last Cycle
- **QW10 — Raised MAX_CORPORA from 4 to 8** (this cycle): the conservative
  cap on registered corpora in `corpus_constants.py` was 4 — enough for
  definer + ARISTOTLE + 2 future extensions, but the ADR-015 fleet vision
  names 6+ domain extensions (HERALD, LOOM, CodeForge, Praxis, Chronicle,
  Oracle). Raising to 8 accommodates definer + ARISTOTLE + 6 future
  extensions while leaving 12 connections of headroom under the
  36-connection corpus budget (8 × 3 = 24 ≤ 36; theoretical max is 12).
  Also fixed `app.py:481` to import and use `MAX_CORPORA` constant instead
  of hardcoding `4` (latent bug — if the constant changed, app.py wouldn't
  pick it up). Updated `test_corpus_foundation.py::test_connection_budget_formula_constants`
  to assert `MAX_CORPORA == 8` with explicit headroom checks. Closes ND11
  from the 2026-07-23 tech-debt assessment.
- **ADR-014 §5.2 — Actor Protocol** (prior cycle): Added `Actor` Protocol +
  `ActorContext` + `ActorResult` dataclasses to `protocols/actors.py` (was
  `VigilStore` only). The Protocol is `@runtime_checkable` so the
  ExtensionHost validates actor conformance at scheduler start via
  `isinstance(actor, Actor)`. Extension-contributed actors (ARISTOTLE's
  SOCRATES/EXAMINER/MENTOR, future LOOM/CodeForge actors) conform to this
  Protocol. Core actors (Beast/Vigil/Sexton) do NOT conform — they keep
  their existing 12-param constructors and hand-wired schedulers (ADR-014
  §1: "Do not migrate — adapt at the boundary with a thin wrapper" is
  future work). All fields on `ActorContext` (`container`, `config`,
  `logger`, `cancel_event`) are typed as `Any` to preserve the foundation
  → adapter/orchestration import boundary — the Protocol promises shape,
  not concrete types. 11 contract tests in `tests/test_actor_protocol.py`
  pin the Protocol shape (conforming actor passes isinstance; 4 non-
  conforming variants fail; runtime_checkable flag; dataclass fields;
  barrel re-export; demo actor conformance). Re-exported from
  `foundation.protocols` barrel.
- **ADR-008 Multi-Corpus Chunk 1** (prior cycle): Added ARCHIVED terminal state to
  `ecs_graph.py` (second terminal alongside SUPERSEDED). Added 4 new foundation
  files for the multi-corpus architecture: `corpus_types.py` (CorpusType,
  CorpusDeletionState enums, RETRIEVAL_EXCLUDED_STATES, MIGRATIONS_FOR_CORPUS_TYPE),
  `corpus_exceptions.py` (7 corpus-layer exceptions), `corpus_constants.py`
  (connection budget + Sexton batch constants), `protocols/corpus_registry.py`
  (CorpusRegistryProtocol + ReviewItem dataclass). All backward-compatible —
  existing 15 consumers of `ecs_graph.py` unaffected. 43 new tests in
  `tests/test_corpus_foundation.py`. See ADR-008 Rev 3.1 Amendment §A0–A16.
- **Commit 14d3a73**: No changes to foundation layer. This layer was stable
  during the operator console debugging cycle.

## Ownership
This layer is owned by no runtime — it is consumed by both orchestration and adapter.
Changes here have system-wide blast radius. Review all Protocol consumers before editing.

## Key Files
| File | Role |
|------|------|
| `ecs_graph.py` | Declarative ECS state machine — gold standard for all lifecycle transitions. Now includes ARCHIVED terminal state (ADR-008 Rev 3.1) |
| `corpus_types.py` | ADR-008: CorpusType, CorpusDeletionState enums, RETRIEVAL_EXCLUDED_STATES, MIGRATIONS_FOR_CORPUS_TYPE |
| `corpus_exceptions.py` | ADR-008: 7 corpus-layer exceptions (CorpusError base + 6 subclasses) |
| `corpus_constants.py` | ADR-008: connection budget constants (MAX_CONNECTIONS, MAX_CORPORA, pool sizes) + Sexton batch constants |
| `protocols/` | 10 Protocol interfaces for dependency injection — the adapter/orchestration seam (now includes CorpusRegistryProtocol + Actor Protocol) |
| `protocols/corpus_registry.py` | ADR-008: CorpusRegistryProtocol + ReviewItem dataclass — multi-corpus store access interface |
| `protocols/actors.py` | ADR-011: VigilStore Protocol (Vigil storage). ADR-014 §5.2: Actor Protocol + ActorContext + ActorResult — extension-contributed actors conform; Beast/Vigil/Sexton NOT migrated |
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
uv run pytest tests/test_corpus_foundation.py  # ADR-008 multi-corpus types
uv run pytest tests/test_validation.py
uv run ruff check src/aip/foundation/
```


# ============================================================
