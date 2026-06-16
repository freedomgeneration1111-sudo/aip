# ============================================================

# Workflows — Agent Navigation
> YAML-driven workflow definitions. Schema changes require engine + doc updates.

## Purpose
Workflow definitions are YAML files that describe task graphs for the AIP workflow
engine. Each workflow is a declarative specification of steps, dependencies, and
conditions that the engine executes.

## Contracts (What This Module Promises to Consumers)

### Workflow Schema Contract (Consumed by orchestration/workflow/)
- Schema defined in `orchestration/workflow/schema.py`
- YAML files must conform to this schema or the loader rejects them
- Adding a new node type requires: schema update + engine support + test
- Changing the schema version requires updating all existing workflow YAML files

### Workflow File Contract
- Each `.yaml` file in this directory is a valid workflow definition
- Filenames must be lowercase with underscores: `corpus_maintenance_v1.yaml`
- Version suffix (`_v1`, `_v2`) indicates iteration, not schema version

## Data Flows (In / Out)

### In
- Schema definition from `orchestration/workflow/schema.py`
- Config parameters from `config/aip.config.toml`

### Out
- `orchestration/workflow/engine.py` loads and executes these definitions
- `adapter/api/routes/` exposes workflow status via API

## Known Gotchas
- **Schema version mismatch**: If the YAML uses features not in the current schema,
  the loader will reject it at runtime. Test new YAML against the current schema.
- **Circular dependencies**: The engine detects cycles, but it's better to avoid
  them in the YAML. Check dependencies before adding them.

## Last Cycle
- No changes. Workflow layer was stable during operator console debugging.

## Key Files
| File | Role |
|------|------|
| `corpus_maintenance_v1.yaml` | Corpus health check and maintenance workflow |
| `incremental_update_v1.yaml` | Incremental knowledge update workflow |
| `synthesis_session_v1.yaml` | Synthesis session workflow |
| `adversarial_redteam_v1.yaml` | Adversarial evaluation workflow |

## Work Guidance
- Adding a workflow: create YAML file, validate against schema, add test
- Modifying schema: update `orchestration/workflow/schema.py` first, then update
  all YAML files, then update this AGENTS.md

## How to Test
```bash
uv run pytest tests/test_workflow_yaml_valid.py
uv run pytest tests/test_workflow_engine.py
uv run pytest tests/test_workflow.py
```


# ============================================================
