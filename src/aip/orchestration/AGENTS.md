# ============================================================

# Orchestration Layer — Agent Navigation
> Business logic. Imports from foundation only. No I/O except through Protocols.

## Purpose
Orchestration implements the AIP knowledge lifecycle: ingest → ask → review → export.
It contains all pipeline logic, actor scheduling, failure classification, and
YAML-driven workflow execution. This is where AIP's intelligence lives.

## Architecture Constraints
- **Foundation imports only** — `from aip.foundation...` is the only allowed import
  source. No `from aip.adapter...` ever.
- **No direct storage calls** — storage is accessed through Protocol interfaces
  injected at runtime. Orchestration never calls `aiosqlite` directly.
- **DEFINER gates enforced here** — the review_export_pipeline.py must enforce
  §1.7 before any APPROVED transition. No shortcut paths.
- **Evaluation is honest** — evaluation scores default to 0.0 on failure.
  No silent pass-through. CI fixtures must not reach production promotion paths.

## Contracts (What This Module Promises to Consumers)

### Pipeline Contracts (Consumed by adapter/api/routes/)

#### Ask Pipeline
- Entry: `ask_pipeline.py` → processes query through retrieval → assembly → model dispatch → persist
- Returns: answer dict with provenance, evaluation scores, artifact ID
- Error: returns NEEDS_CONFIGURATION if model slot not configured (never empty result)

#### Ingestion Pipeline
- Entry: `ingestion/pipeline.py` → parse → chunk → embed → index
- Returns: ingestion result dict with turn IDs, chunk count, embed status
- Error: surfaces parser errors, never silently drops content

#### Review/Export Pipeline
- Entry: `review_export_pipeline.py` → review → evaluate → DEFINER gate → approve/reject
- Returns: review result dict with evaluation scores, DEFINER decision
- **CRITICAL**: No APPROVED transition without DEFINER approval (§1.7)

#### Canonical Pipeline
- Entry: `canonical_pipeline.py` → faithfulness/coherence thresholds → DEFINER gate
- Returns: canonical result dict
- Requires: `vigil_health_check` if configured

### Actor Contracts (see actors/AGENTS.md for full detail)
- Beast, Vigil, Sexton are non-overlapping — Beast owns corpus health,
  Vigil owns canonical monitoring, Sexton owns background maintenance
  (tagging, embedding, wiki, graph, failure classification per ADR-011).
- Do not cross-wire their responsibilities.

### Workflow Engine Contract
- YAML schema defined in `workflow/schema.py`
- Engine executes task graphs defined in `workflows/*.yaml`
- Changing the schema requires updating both the engine and `workflows/AGENTS.md`

## Data Flows (In / Out)

### In (What orchestration receives)
- Protocol interfaces from `foundation/protocols/` — injected at runtime
- Config from `config/aip.config.toml` through adapter/config layer
- Model dispatch via `ModelSlotResolver` (accessed through Protocol)

### Out (What orchestration produces)
- **Actors → adapter/api/routes/**: status summaries, scan results, quality metrics
- **Pipelines → adapter/api/routes/**: query results, ingestion results, review decisions
- **Workflow → adapter/api/routes/**: workflow instance status

### Cross-Folder Data Flows
```
orchestration/actors/sexton.py (_embedding_backfill_state, _rate_limited)
  → adapter/api/routes/corpus.py (GET /corpus/embedding-progress)
    → gui/pages/corpus.py

orchestration/ask_pipeline.py (answer with provenance)
  → adapter/api/routes/ask.py (POST /ask)
    → gui/pages/ask.py

orchestration/review_export_pipeline.py (review decision)
  → adapter/api/routes/review.py
    → gui/pages/artifacts.py
```

## Known Gotchas
- **No direct adapter imports**: If you write `from aip.adapter import ...` in
  orchestration, you've violated the layer discipline. Use Protocols.
- **ECS transitions live in ecs_graph.py only**: Never implement transition logic
  in pipeline code. Call `ecs_graph.py` transition methods only.
- **Evaluation scores default to 0.0**: Never change a failed evaluation to pass
  silently. Honest evaluation is a governance invariant.
- **Actor cross-wiring**: Beast does NOT do embedding. Sexton does NOT do health
  checks. Vigil does NOT do tagging. Mixing these breaks the ADR-011 contract.

## Last Cycle
- **Commit 14d3a73**: Sexton actor received concurrency guard (`asyncio.Lock`),
  rate limit detection (429 handling), and fixed state machine priority
  (rate_limited before mock/fake detection). See `actors/AGENTS.md` for full detail.

## Ownership
Pipelines are owned by their domain (ingestion, ask, review/export). Actors are
independent scheduled processes. Workflow engine is standalone.

## Key Files / Subdirectories
| Path | Role |
|------|------|
| `ingestion/` | Parse → persist → chunk → embed → index pipeline |
| `ask_pipeline.py` | Retrieve → assemble → model dispatch → persist pipeline |
| `review_export_pipeline.py` | Review, approve, reject, needs-revision, export — DEFINER gated |
| `canonical_pipeline.py` | Faithfulness/coherence thresholds → DEFINER gate |
| `actors/` | Beast, Vigil, Sexton — see `actors/AGENTS.md` |
| `sexton/` | Failure classification subsystem — delegated to by `actors/sexton.py` |
| `channels/` | 7 retrieval channel implementations — vector, FTS5, corpus, graph, wiki, procedural |
| `workflow/` | YAML-driven workflow engine — task graph execution |
| `nodes/` | Evaluation nodes — faithfulness, domain_coherence, adversarial_eval, synthesis, commit |
| `l4/` | Level 4 trajectory — loop detector, anxiety detector, failure streak, regulator, reset |
| `trajectory/` | Context reset, session regulation |
| `codex/` | Librarian — structured knowledge compilation |
| `retrieval_orchestrator.py` | Multi-channel retrieval with weighting |
| `channel_selector.py` | Channel selection logic |
| `smart_context_packer.py` | Context window optimization |
| `entity_extractor.py` | Named entity extraction |
| `graph_retrieval.py` | Knowledge graph retrieval |

## Work Guidance
- Editing a pipeline: read the pipeline file top-to-bottom before making changes.
  Each pipeline has a clear entry function — find it, trace the data flow,
  make the minimal change, verify the ECS transitions are unchanged.
- Adding an actor behavior: add to the correct actor file (Beast XOR Vigil XOR Sexton).
  Cross-actor logic goes through foundation Protocols, not direct calls.
  Update `actors/AGENTS.md` Contracts section.
- Workflow engine changes: YAML schema is defined in `workflow/schema.py`.
  Changing the schema requires updating both the engine and `workflows/AGENTS.md`.
- ECS transitions: never implement a transition in pipeline code.
  Call `ecs_graph.py` transition methods only.

## How to Test
```bash
uv run pytest tests/test_ingestion.py
uv run pytest tests/test_ask.py
uv run pytest tests/test_review_export.py
uv run pytest tests/test_actors.py
uv run pytest tests/test_workflow.py
uv run pytest tests/test_operator_console_fixes.py  # regression for recent fixes
```


# ============================================================
