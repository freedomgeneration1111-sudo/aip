# AIP Brain — Local-First Sovereign Knowledge Engine

[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](LICENSE)

> **No artifact may bypass DEFINER gates (§1.7).**

AIP Brain is a local-first knowledge engine that manages the lifecycle of
knowledge artifacts — from ingestion through synthesis, evaluation, review,
and canonical promotion. It is also an **extension platform**: other
applications (like [ARISTOTLE](https://github.com/freedomgeneration1111-sudo/AIP_Aristotle),
the adaptive tutor) mount as pip-installable packages and are discovered
automatically at startup.

**What makes it different:**
- **DEFINER sovereignty** — every artifact promotion requires explicit human approval. The AI proposes; the human decides. No bypass.
- **Source-grounded answers** — every generated answer includes provenance back to ingested sources. No fabrication.
- **Honest evaluation** — CI fixtures are flagged and blocked from production. Default scores are 0.0 on failure. No silent passes.
- **Extension platform** — extensions mount via a declared manifest (ADR-014), discovered through Python entry points. The platform never imports an extension by name — the boundary is machine-enforced.
- **Web source acquisition (ADR-017)** — AIP can ground answers on current web sources via Tavily search, with SSRF defense, prompt-injection boundaries, and explicit corpus promotion. Web content never enters the corpus without DEFINER approval.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager) — `pip install uv`
- An OpenAI-compatible API key (OpenRouter, OpenAI, etc.)

### Install + Run

```bash
# 1. Clone
git clone -b feat/multi-corpus https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
cd AIP_Brain

# 2. Install dependencies
uv sync

# 3. Configure
cp .env.example .env
# Edit .env — add your API key:
#   AIP_OPENAI_API_KEY=sk-or-v1-...
cp config/aip.config.toml.example config/aip.config.toml

# 4. Initialize the database
uv run aip init

# 5. (Optional) Bootstrap with self-knowledge seed corpus
bash examples/seed_corpus/seed_bootstrap.sh

# 6. Start the system
./start.sh
```

- **Backend API**: http://localhost:8000
- **Operator Console (GUI)**: http://localhost:8080

### Install an Extension

Extensions are separate pip packages. The platform discovers them
automatically via the `aip.extensions` entry-point group.

```bash
# Install ARISTOTLE (the adaptive tutor)
pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git

# Verify discovery
python -c "
from importlib.metadata import entry_points
print('extensions:', [e.name for e in entry_points(group='aip.extensions')])
"
# Expected: extensions: ['aristotle']
```

After installing, restart the server. The extension's actors, API routes,
and GUI pages mount automatically.

### Development Setup (Editable Install)

```bash
git clone -b feat/multi-corpus https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
git clone https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git
cd AIP_Brain && pip install -e .
cd ../AIP_Aristotle && pip install -e .
```

Editable install means changes to either repo are picked up immediately.

---

## What AIP Brain Does

### Knowledge Artifact Lifecycle

AIP manages artifacts through an ECS (Evolution, Curation, Status) state
machine:

```
SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED
                                      ↘ ARCHIVED (terminal)
```

- **Ingest** conversation exports (Claude, ChatGPT, markdown, plaintext, PDF)
- **Ask** source-grounded questions with hybrid FTS5 + vector retrieval
- **Review** generated artifacts with DEFINER gates (approve, reject, revise)
- **Export** approved artifacts to markdown with provenance

### Extension Platform (ADR-014)

AIP Brain is a platform. Extensions contribute:
- **Corpora** — isolated knowledge containers (per-subject textbooks, code repos, etc.)
- **Actors** — background schedulers conforming to the foundation `Actor` Protocol
- **API routes** — FastAPI routers passed to the host via `register_api_router()`
- **GUI pages** — NiceGUI pages discovered via `aip.extension_gui` entry points
- **Workflows** — YAML-defined pipelines executed by the L5 Workflow Engine
- **Migrations** — SQL schema changes applied to the extension's corpus

The host discovers extensions via `importlib.metadata.entry_points(group="aip.extensions")`.
Extensions pass their API router + GUI page builders as objects — the platform
**never imports an extension by name**. The import boundary is machine-enforced
by `tests/test_extension_import_boundary.py` (scans both `src/aip/` and `gui/`).

### Background Actors

Three core actors run on schedulers:
- **Beast** — context advisory, domain detection, corpus health
- **Vigil** — canonical monitoring, quality evaluation (faithfulness, coherence, drift)
- **Sexton** — tagging, embeddings, wiki generation, graph extraction

Extensions register additional actors via `host.register_actor()` in their
`hooks.py::on_load()`.

### Multi-Corpus Architecture (ADR-008)

The `CorpusRegistry` manages multiple isolated knowledge containers:
- **definer** (conversation corpus) — the default, always present
- **aristotle:textbook** (document corpus) — ARISTOTLE's concept-chunked content
- Future: code corpora, field-news corpora, etc.

Each corpus has its own SQLite database, connection pool, and migration
history. Sessions bind to active corpora via `active_corpus_ids`.

---

## CLI Usage

```bash
# System
aip init                              # Initialize databases and config
aip status                            # Show system status and store health

# Ingestion
aip ingest file <path> --project X    # Import a conversation file
aip ingest directory <path>           # Import all files in a directory
aip corpus ingest <path>              # Import into the turn-level corpus

# Ask
aip ask "<question>" --project X      # Ask a source-grounded question
aip ask "<question>" --show-context   # Show retrieved context

# Review
aip review list --project X           # List artifacts pending review
aip review approve <artifact_id>      # Approve (DEFINER gate)
aip review reject <artifact_id>       # Reject

# Export
aip export artifact <id> --format markdown --out ./out.md

# Corpus
aip corpus tag --limit N              # Beast domain tagging
aip corpus graph --build-from-bridges # Build knowledge graph
aip audit log                         # View corpus audit log

# Extension health
curl http://localhost:8000/health/extensions
```

---

## Architecture

```
src/aip/
├── foundation/              # Pure types — no I/O, no imports from above
│   ├── ecs_graph.py         # Declarative ECS state machine
│   ├── protocols/           # Protocol interfaces (Actor, ModelProvider, etc.)
│   ├── schemas/             # Dataclass definitions (14 schema modules)
│   └── corpus_types.py      # CorpusType, CorpusDeletionState, migration registry
│
├── orchestration/           # Business logic — imports from foundation only
│   ├── ask_pipeline.py      # Retrieve → assemble → dispatch → persist
│   ├── actors/              # Beast, Vigil, Sexton (background schedulers)
│   ├── workflow/            # L5 YAML workflow engine (agent/script/condition nodes)
│   ├── ingestion/           # Parse → persist → chunk → index pipeline
│   └── workflow_registry.py # Discovers YAML workflows (add_path for extensions)
│
├── adapter/                 # External interfaces — imports from foundation only
│   ├── api/                 # FastAPI app + 30 route modules
│   │   ├── app.py           # Lifespan: CorpusRegistry → ExtensionHost → actors
│   │   └── routes/          # /health, /ask, /corpus, /models, /aristotle/*, etc.
│   ├── cli/                 # Click-based CLI (17 subcommands)
│   ├── corpus_registry.py   # ADR-008: multi-corpus manager
│   ├── extensions/          # ADR-014: ExtensionHost lifecycle
│   │   ├── host.py          # discover → validate → migrate → register → mount → ready
│   │   ├── manifest.py      # Pydantic v2 manifest validator
│   │   └── loaders/         # .sql migration loader (separate from core runner)
│   ├── lexical/             # FTS5 full-text search
│   ├── vector/              # pgvector / sqlite-vss / in-memory
│   └── ...                  # auth, budget, vigil, embedding, autonomy, graph
│
gui/                         # NiceGUI operator console (separate package)
├── app.py                   # Entry point — dynamic extension GUI discovery
├── components/layout.py     # Three-region layout (top bar + left nav + content)
├── pages/                   # Dashboard, Ask, Models, Corpus, Graph, Wiki, etc.
└── api_client.py            # HTTP client to the backend
```

**Layer discipline** (machine-enforced by `tests/test_import_boundary.py`):
- `foundation` → stdlib only
- `orchestration` → `foundation` only
- `adapter` → `foundation` only (composition root wires orchestration via importlib)
- `gui` → API client only (never imports adapter/orchestration directly)
- Extensions → `aip.foundation.protocols.*` + `aip.adapter.extensions` only

---

## Configuration

Primary config: `config/aip.config.toml`

Key sections:
- `[database]` — `db_path` (default: `db/state.db`)
- `[models]` — per-slot model configuration (synthesis, evaluation, beast, sexton, embedding)
- `[embedding]` — provider ("fake" for CI, "ollama" or "openai_compatible" for real)
- `[extensions]` — `dir` (default: `extensions/`), `manifest_version_range`
- `[workflows]` — `dir` (default: `workflows/`)

Environment variable overrides:
- `AIP_DB_PATH` — database path
- `AIP_OPENAI_API_KEY` — OpenRouter/OpenAI API key
- `AIP_BACKEND_URL` — backend URL (for GUI client, default: `http://127.0.0.1:8000`)
- `AIP_GUI_PORT` — GUI port (default: `8080`)
- `CI=true` — CI mode (deterministic fixtures, no network calls)

---

## Running Tests

```bash
# Full suite (4374 tests collected, 0 errors)
uv run pytest

# Extension platform tests
uv run pytest tests/test_extension_lifecycle.py tests/test_extension_import_boundary.py tests/test_actor_protocol.py

# With coverage
uv run pytest --cov=aip --cov-report=term-missing

# Dogfood smoke test
bash scripts/dogfood_smoke_test.sh
```

---

## Extension Development

To build a new extension on the AIP Brain platform:

1. Create a Python package with a `pyproject.toml` declaring an `aip.extensions` entry point
2. Write an `extension.yaml` manifest (manifest v1, pydantic-validated)
3. Write a `hooks.py` with `on_load(host)` that registers actors, pages, and API routers
4. Write SQL migrations (M001_, M002_, etc. — applied to the extension's corpus)
5. (Optional) Write a `gui.py` with `@ui.page` decorators, declare an `aip.extension_gui` entry point

See [AIP_Aristotle](https://github.com/freedomgeneration1111-sudo/AIP_Aristotle) as the reference implementation.

Key contracts:
- Extensions import from `aip.foundation.protocols.*` + `aip.adapter.extensions` only
- The platform never imports an extension by name (boundary-enforced)
- Actors conform to `aip.foundation.protocols.actors.Actor` (name/cadence/run_cycle/health)
- API routers are passed via `host.register_api_router(router)` — not imported by the platform
- GUI pages are discovered via `aip.extension_gui` entry points — not imported by name

See [ADR-014](docs/decisions/ADR-014-phase0-extension-host.md) for the full spec.

---

## Documentation

- [`DOGFOOD_READY.md`](DOGFOOD_READY.md) — First-run dogfood guide
- [`STATUS.md`](STATUS.md) — Current operational state
- [`ROADMAP.md`](ROADMAP.md) — Phase plan (Phase 0–6 + extension platform)
- [`PLANNED_FEATURES.md`](PLANNED_FEATURES.md) — Canonical feature tracker
- [`TECH_DEBT.md`](TECH_DEBT.md) — Debt register with resolution status
- [`AIP_GOVERNANCE.md`](AIP_GOVERNANCE.md) — Binding invariants (AIP-G-01 through AIP-G-11)
- [`docs/decisions/`](docs/decisions/) — Architecture Decision Records (ADR-000 through ADR-014)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Architecture overview
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — Configuration reference

---

## Governance

This component conforms to the [AIP Governance Contract](AIP_GOVERNANCE.md)
(invariants AIP-G-01 through AIP-G-11). Conformance is checked by
`tests/test_governance_conformance.py`.

Core invariants:
- **AIP-G-01**: No artifact reaches a terminal state without explicit DEFINER action
- **AIP-G-02**: No fake success — unimplemented features return structured errors
- **AIP-G-03**: All artifacts carry provenance
- **AIP-G-04**: The governance contract is hosted in one location and linked, not copied

---

## License

BUSL-1.1 — see [LICENSE](LICENSE). Changes to Apache License 2.0 on 2030-06-10.
