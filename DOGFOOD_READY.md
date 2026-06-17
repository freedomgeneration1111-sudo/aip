---
## ⚠️ Read This First — Alpha Test Release (2026-06-17)

AIP v0.1 is now in **alpha test release**. The dogfood guide below describes the foundational
ingest→ask→review→export loop. This still works. However AIP has grown significantly since
that guide was written, and there are important caveats for alpha testers:

**What works well:**
- FTS5 full-text search across your ingested corpus
- Ingest, tag, ask, review, approve, export pipeline
- Augmented chat with Beast context advisory
- Knowledge graph visualization at /graph-viz
- CLI evaluation tools (`aip eval retrieval`)
- Model Council — advisory multi-model comparison (Ask Workbench → Model Council button on any answer card)
- Crosslink System v1 — knowledge links between wiki articles, artifacts, turns, and other first-class objects with approve/reject workflow
- Retrieval Lab v1 — standalone retrieval testing without answer synthesis
- Maintenance Center v1 — actor status (Beast, Vigil, Sexton), maintenance job controls (backfill embeddings, rebuild graph/CODEX, retrieval eval, stale docs, contradictions), recent maintenance log, and problem panel
- **Multi-Model Fusion pipeline** (Phase 1-3 + 4.1, 2026-06-17) — Multi-Cast with ≥2 models triggers Beast Fusion (Judge-Beast → Synth-Beast). Per-model answer cards + a fusion synthesis card with structured Judge JSON (consensus, contradictions, partial_coverage, unique_insights, blind_spots). Models are NOT tied to actor slots — pick any 2+ from the unified dropdown.
- **Augmented Multi-Cast** (Phase 1 retrieval bridge) — when Augmented mode is ON + Multi-Cast ≥2 models, the panel sees the same corpus context as single-model augmented chat. Dogfood-confirmed: panel models correctly identify AIP as AI Poiesis.
- **Real-time provenance widget** (Phase 4.1) — every answer card with sources shows an inline provenance strip (source count + domain badges + collapsible detail list) so the DEFINER can trace provenance instantly without clicking a button.
- **Context Preparer visualizer** (Phase 4.1) — the Trace panel shows a 4-step fusion flow diagram (channel retrieval → RRF fusion → gating → final context) so the DEFINER can see which channel misfired when retrieval goes wrong.
- **Vigil consistency checker** (Phase 4.1) — Vigil's 5th evaluation pass detects cross-turn contradictions. Writes `vigil_consistency_score` + `vigil_consistency_contradictions` to turn metadata.
- **Per-model compression pass** (Phase 2) — opt-in via the "Compress" checkbox in the Ask page header. Summarizes each panelist's answer to 5-8 key claims before the Judge reads them (reduces context pressure on long panel outputs).
- **Dedicated [models.judge] slot** (Phase 3) — optional TOML slot for a dedicated Judge model. Uncomment in `config/aip.config.toml` to use a different model for judging vs the Beast actor's maintenance calls.

**Known limitations:**
- **Embedding coverage is low (~1.8%)** — Sexton will automatically embed turns when an
  embedding provider is configured and the server is running, but this requires sustained
  uptime. Use `aip corpus tag` and `aip embed` manually to accelerate the process.
  The embedding backfill state is reported at `/health` (field: `embedding_coverage.backfill_state`)
  and `/health/dogfood` (field: `embedding_backfill_state`). States are: not_configured,
  configured_idle, backfill_pending, backfill_running, partially_embedded, embedded, degraded,
  failed. Runtime model assignment via `PATCH /models/slots/embedding/model` propagates to
  Sexton and triggers re-embedding for turns with a different model.
- **Per-channel retrieval health visibility** (Chunk 5) — `/health` now includes a
  `retrieval_channel_health` section showing per-channel registration status, vector backend
  detail, and embedding provider status. `/health/dogfood` includes `channel_states` with
  per-channel state (available, unavailable, not_configured, degraded). Unregistered channels
  report `not_configured` rather than `failed`; channels returning 0 results report `empty`
  rather than `active`. The `lexical_only` flag in ask responses indicates when only FTS5
  contributed results.
- **Ask Workbench upgrade** (UI Cycle 4) — The Ask page now shows retrieval health per answer
  via a status strip (retrieval healthy, degraded, lexical only, no sources, direct model only,
  trace unavailable). Sources are inspectable in a detail drawer (title/path, snippet, score,
  channel). Retrieval trace is inspectable in a detail drawer when available (shows "Trace
  unavailable" honestly when no trace exists). Save-as-artifact is available via the action bar
  — it creates a GENERATED artifact requiring DEFINER review before approval (no auto-approve).
  Link Wiki buttons are now enabled via Wiki/CODEX Home v1 (UI Cycle 7).
  **Model Council is now available** (UI Cycle 6) — click the "Model Council" button on any answer card
  to run an advisory multi-model comparison across all configured text-generation slots.
  Model Council reports are ADVISORY ONLY — they never auto-approve, auto-export, or mutate
  system state. If fewer than 2 text-gen slots are configured, the comparison returns
  `insufficient_models` honestly. Reports can optionally be saved as GENERATED artifacts
  requiring DEFINER review before approval.
- **Ask Workbench API sovereignty verified** (UI Cycle 4.1) — 42 focused tests confirm:
  save-as-artifact always creates GENERATED artifacts (never APPROVED/EXPORTED);
  retrieval trace endpoint returns honest not_found when no trace exists (never fakes data);
  ask/chat WebSocket responses include trace_available, lexical_only, vector_contributed, direct_model;
  answer card shows correct status for all states; import boundaries verified for all new components.
  Zero blockers for Beast Counsel development.
- **Beast Counsel Panel v1** (UI Cycle 5) — The Ask page now includes a Beast Counsel panel accessible via the "Beast Counsel" button on each answer card. Beast provides an advisory second perspective on each turn with five modes: Continuity, Critique, Strategy, Librarian, and Risk. Beast commentary is ADVISORY ONLY — suggested actions always require DEFINER approval. Commentary is persisted as GENERATED artifacts requiring review before approval (no auto-approve). When no model provider is configured, Beast honestly reports "not wired" rather than faking commentary. The panel also shows "unavailable" when the artifact store is not configured, and "error" when generation fails.
- **Model Council** (UI Cycle 6) — The Ask page now includes a Model Council comparison tool accessible via the "Model Council" button on each answer card. Model Council runs your prompt across all configured text-generation model slots and produces an advisory comparison report showing side-by-side responses, consensus/disagreement highlights, and recommendations. Reports are ADVISORY ONLY — they require DEFINER approval before any action is taken. If fewer than 2 text-gen slots are configured, the comparison returns `insufficient_models` honestly. If one model fails, the remaining models produce a partial/degraded report rather than a total failure. The embedding slot is excluded from text generation comparison. Reports can optionally be saved as GENERATED artifacts.
- **MCP dispatch is built but not runtime-wired** — no MCP operations are reachable via API/CLI today
- **No review queue web UI** — use `aip review list/approve/reject` via CLI
- **Wiki/CODEX Home v1** (UI Cycle 7 + 7.1 hardening) — The Operator Console now includes a full Wiki/CODEX Home at `/wiki` with article listing (with search), single article viewing with full WikiArticle schema, article creation (DEFINER action, state=GENERATED — never auto-approved), article editing (DEFINER action, no ECS state change), backlinks, contradiction detection, stale article detection, and enhanced wiki statistics. All endpoints return honest empty/unavailable states when CODEX tables don't exist — no fake article content. CREATE always sets state to GENERATED; EDIT creates a new version but does NOT change ECS state; no secret exposure in wiki responses. Cycle 7.1 hardens the storage path: wiki create/edit now routes through `container.artifact_store` + `container.ecs_store` when available (shared connection pool, validated ECS transitions, event provenance), with an explicitly isolated `sqlite_compat` fallback. Every response includes a `storage_backend` indicator. Article IDs are stable (`wiki:{domain}:{title_slug}:{timestamp}`) and crosslink-safe (now used by Crosslink System v1 in UI Cycle 8).
- **Crosslink System v1** (UI Cycle 8) — The Operator Console now includes a full Crosslink System for creating knowledge links between first-class objects (wiki articles, artifacts, turns, sources, conversations, domains, entities, canonicals, graph nodes, projects). Links are directional with 12 relation types (supports, contradicts, derives_from, related_to, references, prerequisite_of, supersedes, elaborates, summarizes, context_for, answer_to, question_about). Links default to `suggested` status with `approved_by_definer=false` — no auto-approve. Creating a link never mutates linked objects, approves artifacts, or triggers exports. The wiki article view sidebar includes a Link Panel showing backlinks and forward links with status badges and approve/reject/delete actions. The Answer card "Link Wiki" button is now wired (no longer "not yet implemented") and opens a link creation dialog. 6 API client methods and 6 TypedDicts support the frontend. Backend uses `KnowledgeLinkStore` (aiosqlite adapter with dedicated `knowledge_links` table in state.db). Every response includes a `storage_backend` indicator (`"knowledge_link_store"` or `"unavailable"`). 56 tests in `tests/test_crosslink_system_cycle8.py`; all existing tests still pass.
- **Operator Console shell is now the default GUI** — Start with `python -m gui.app` (port 8080). The `./scripts/start.sh` script launches the Operator Console by default. Dashboard shows honest system health. Ask page is now the Ask Workbench with answer inspection, source detail, trace detail, and save-as-artifact. Wiki page is now a full CODEX Home with article CRUD, backlinks, contradictions, and stale detection (UI Cycle 7). Corpus page is now a full Corpus Workbench with document listing, detail view, ingest action, embedding backfill, retry failed embeds, and problems panel (UI Cycle 10). Retrieval page is now a full Retrieval Lab with query panel, channel toggles, health cards, per-channel results, and ranked context (UI Cycle 11). Maintenance page is now a full Maintenance Center with actor status table, maintenance job controls (backfill, graph rebuild, CODEX rebuild, retrieval eval, stale docs check, contradiction check), recent maintenance log panel, and problem panel (UI Cycle 12). Placeholder pages for Settings show "Not yet implemented" honestly. The old `python -m gui.shell` is frozen (no new features). The old `python -m gui.main` is preserved until Ask Workbench is proven.

- **Retrieval Lab provides diagnostic-only retrieval testing** — The `POST /retrieval/test` endpoint runs retrieval without synthesizing an answer. It is intended for diagnostic and debugging use. The `context_budget` parameter is not yet wired; results may differ from the context budget applied during normal Ask augmented retrieval.

The current recommended first-run sequence is:

### Step 0 — Initialize
```bash
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
cd AIP_Brain
uv sync
uv run aip init
```

### Step 1 — Ingest your Claude conversations
Export your conversations from claude.ai (Settings → Export data).
Unzip and ingest:
```bash
uv run aip corpus ingest /path/to/conversations.json \
  --source-model claude \
  --source-account claude_export_$(date +%Y_%m)
```

### Step 2 — Tag your corpus with Sexton
```bash
uv run aip corpus tag --limit 500
# For a large corpus this takes time — Sexton calls LLM for each batch of 8 turns
# Run with --retag after updating docs/beast_domain_registry_v1.md
```

### Step 3 — Check corpus status
```bash
uv run aip status
# Shows: total turns, tagged/untagged, domain distribution
```

### Step 4 — Start the server and use augmented chat
```bash
./scripts/start.sh
# Opens backend on http://127.0.0.1:8000
# Opens Operator Console on http://127.0.0.1:8080
# Switch to AUGMENTED mode in Ask Workbench for Beast-context-enhanced responses
```

Note: `uv run aip serve` does not exist. Always use `./scripts/start.sh` to
start both the FastAPI backend and the NiceGUI Operator Console together.

### Step 5 — Review Sexton's domain proposals
Any domains Sexton couldn't classify appear in the review queue:
```bash
uv run aip review list
```

The original dogfood guide continues below for the foundational
ask→review→approve→export pipeline.

---

# DOGFOOD_READY.md — AIP First-Run Guide

This document provides the exact commands needed for a fresh user to go from
clean checkout to a working knowledge base with review and export — without
reading source code or guessing database paths.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended package manager)
- Optional: Ollama (for local model inference)

## 1. Install

```bash
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
cd AIP_Brain
uv sync
```

## 2. Initialize

```bash
uv run aip init
```

This creates:
- `config/aip.config.toml` — configuration file with `db_path = "db/state.db"`
- `db/state.db` — main database (artifacts, projects, events, ECS state, canonicals)
- `db/lexical.db` — FTS5 full-text search index
- `db/trace.db` — trace events and routing outcomes

**All CLI commands use the same databases** initialized by `aip init`.
You do NOT need to pass `--db-path` manually.

Verify with:
```bash
uv run aip status
```

## 3. Create a Project

```bash
uv run aip project create --name aip_loom --domain aip_loom
```

This creates a project named `aip_loom` with domain `aip_loom`.
The domain is the tag used for indexing and retrieval.

List projects:
```bash
uv run aip project list
```

## 4. Ingest Conversations

```bash
# Using --domain (matches the project's domain)
uv run aip ingest directory examples/sample_threads --domain aip_loom

# Using --project (resolves the domain automatically from the project)
uv run aip ingest directory examples/sample_threads --project aip_loom

# Single file
uv run aip ingest file examples/sample_threads/aip_loom_decisions.md --project aip_loom
```

Supported formats (auto-detected):
- ChatGPT JSON export (`conversations.json`)
- Markdown transcripts (`**Role**: content`)
- Plain text (`Role: content`)

The output will show the domain the content was indexed into.

## 5. Ask a Question

### Without a Model Configured

```bash
uv run aip ask "What have we decided about artifact storage?" \
  --project aip_loom \
  --source all \
  --show-context
```

If no model provider is configured, this returns `NEEDS_CONFIGURATION`
but **still shows the retrieved sources**. This proves that ingestion
and retrieval are working correctly.

### With a Model Configured

Set environment variables for your model provider:

```bash
# Option A: OpenAI-compatible API
export AIP_SYNTHESIS_BASE_URL="https://api.openai.com/v1"
export AIP_SYNTHESIS_MODEL="gpt-4o"
export AIP_SYNTHESIS_API_KEY="sk-..."

# Option B: Local Ollama
# Make sure Ollama is running: ollama serve
export AIP_SYNTHESIS_BASE_URL="http://127.0.0.1:11434/v1"
export AIP_SYNTHESIS_MODEL="llama3"
```

Then ask with `--save-artifact` to save the generated answer:

```bash
uv run aip ask "What have we decided about artifact storage?" \
  --project aip_loom \
  --source all \
  --show-context \
  --save-artifact
```

The output will include:
- The generated answer
- Source citations
- The artifact ID (e.g., `ask:abc123...`)
- Instructions to review the artifact

## 6. Model Configuration

### Environment Variables

| Variable | Purpose |
|----------|---------| 
| `AIP_SYNTHESIS_BASE_URL` | API endpoint for the synthesis slot |
| `AIP_SYNTHESIS_MODEL` | Model name for synthesis |
| `AIP_SYNTHESIS_API_KEY` | API key (if required) |
| `AIP_EVALUATION_BASE_URL` | API endpoint for the evaluation slot |
| `AIP_SEXTON_BASE_URL` | API endpoint for the sexton slot |
| `AIP_OLLAMA_BASE_URL` | Ollama endpoint (default: `http://localhost:11434`) |

### Config File

You can also configure model providers in `config/aip.config.toml`:

```toml
[synthesis]
base_url = "https://api.openai.com/v1"
model = "gpt-4o"

[evaluation]
base_url = "http://127.0.0.1:11434/v1"
model = "llama3"
```

## 7. Review and Approve

```bash
# List generated artifacts pending review
uv run aip review list --project aip_loom

# Show artifact content and details
uv run aip review show <artifact_id>

# Show source/provenance links
uv run aip review sources <artifact_id>

# Approve the artifact (GENERATED → REVIEWED → APPROVED)
uv run aip review approve <artifact_id>

# Reject the artifact (preserves it for re-generation)
uv run aip review reject <artifact_id> --note "Reason for rejection"

# Mark as needing revision (stays in current state, records instruction)
uv run aip review needs-revision <artifact_id> --note "Add more detail about X"
```

**Lifecycle states:**
- `GENERATED` → `REVIEWED` → `APPROVED` (full approval path)
- `GENERATED` → `REJECTED` (preserved, can be re-generated)
- `APPROVED` → `SUPERSEDED` (replaced by newer version)

No auto-approve path exists. All approvals require explicit DEFINER action.

## 8. Export

```bash
# Export an approved artifact to markdown
uv run aip export artifact <artifact_id> \
  --format markdown \
  --out ./exports/output.md

# Export all approved artifacts for a project
uv run aip export project aip_loom \
  --format markdown \
  --out ./exports/aip_loom.md

# Force export of unapproved artifact (not recommended)
uv run aip export artifact <artifact_id> \
  --format markdown \
  --out ./exports/draft.md \
  --force
```

> ⚠️ **Warning:** Force export bypasses the DEFINER approval gate. This should only be used for debugging or local review, never in a shared or production context.

Exported markdown includes:
- YAML frontmatter (artifact_id, project, lifecycle_state, timestamps, model info)
- Generated content
- Provenance footer (source IDs, types, and export timestamp)

**Export rules:**
- APPROVED artifacts export without `--force`
- GENERATED/REVIEWED artifacts require `--force`
- REJECTED artifacts require `--force` (not recommended)

## 9. Automated Smoke Test

```bash
bash scripts/dogfood_smoke_test.sh
```

This runs the full dogfood loop automatically on a clean datastore,
including init, project creation, ingestion, ask, review, approve, and export.

## 10. Demo Corpus

AIP_Brain includes a small prebuilt demo corpus under `demo/aip_demo`.
The demo contains 60 curated Q&A turns about AIP_Brain, 24 prebuilt wiki articles,
tags, entities, and graph nodes. It is intended for smoke testing and alpha
demonstration only — it is not your live dogfood corpus.

```bash
# Build the demo DB (from project root)
python scripts/demo/build_aip_demo_db.py --reset

# Verify the demo DB
python scripts/demo/verify_aip_demo_db.py

# Run AIP against the demo DB
export AIP_DB_PATH=demo/aip_demo/db/state.db
uv run aip status
uv run aip corpus verify
```

To generate embeddings for the demo DB after configuring an embedding provider:

```bash
uv run aip corpus embed --limit 100 --db-path demo/aip_demo/db/state.db
```

See `demo/aip_demo/README.md` for full details.

## Troubleshooting

### Project Not Found

```
Error: Project 'my_project' not found
```

**Fix:** Create the project first:
```bash
uv run aip project create --name my_project --domain my_project
```

### No Project Memory

```
AIP: NO PROJECT MEMORY
No relevant sources found in project 'aip_loom'
```

**Fix:** Ingest conversations into the project's domain:
```bash
uv run aip ingest directory <path> --project aip_loom
# or: aip ingest directory <path> --domain aip_loom
```

### No Model Provider Configured

```
AIP: NEEDS CONFIGURATION
No model provider is configured.
```

**Fix:** Set model environment variables or configure in `config/aip.config.toml`.
The retrieved sources are still shown so you can verify ingestion worked.

See [Model Configuration](#6-model-configuration) for details.

### Review List Empty

```
No artifacts found for project 'aip_loom'.
```

**Fix:** Generate an artifact first:
```bash
uv run aip ask "<question>" --project aip_loom --save-artifact
```

### Export Refused (Artifact Unapproved)

```
Error (UNREVIEWED_ARTIFACT): Artifact is in GENERATED state
```

**Fix:** Approve the artifact first:
```bash
uv run aip review approve <artifact_id>
```

Or use `--force` to export an unapproved artifact (not recommended for production).

### Database Path Mismatch

If you see data in `aip status` but not in `aip ask`/`aip review`:

1. Check that `config/aip.config.toml` has `db_path = "db/state.db"`
2. Re-run `aip init` to ensure the config is correct
3. Verify with `aip status` that it shows "Main DB: db/state.db"

All CLI commands now use the same default database path. If you previously
used `data/aip.db`, you can migrate by copying it to `db/state.db`:
```bash
cp data/aip.db db/state.db
```

### Ingest --domain vs --project

Both work:
- `--domain aip_loom` indexes content directly into the `aip_loom` domain
- `--project aip_loom` looks up the project's domain and indexes into it

If both are specified, `--project` takes precedence and resolves the domain
from the project store. If the project has domain `aip_loom` and you pass
`--domain other`, a warning is shown and the project's domain is used.

### Server Won't Start / `aip serve` Not Found

`uv run aip serve` does not exist. Use:
```bash
./scripts/start.sh
```

This starts both the FastAPI backend (port 8000) and the NiceGUI Operator Console (port 8080).
