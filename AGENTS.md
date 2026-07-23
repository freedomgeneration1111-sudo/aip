# ============================================================

# AIP Brain — Agent Navigation Root
> AI Poiesis (AIP) v0.1 — Local-first Sovereign Knowledge Engine
> Architecture Revision 6.4 | Status: Alpha dogfood-ready

## Purpose
AIP manages the lifecycle of knowledge artifacts from ingestion through synthesis,
evaluation, review, and canonical promotion. All operations are DEFINER-gated,
source-grounded, and async-safe.

## Governance Invariants — DEFINER Law (Non-Negotiable)
These apply everywhere in the codebase. Violation = blocker.

- **§1.7 — No bypass**: No UI, workflow, Beast cadence, MCP call, or queued task
  may promote an artifact without explicit DEFINER approval. This is absolute.
- **ECS lifecycle is unidirectional**: SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED.
  No reverse transitions. No skip transitions. **ARCHIVED** is a second terminal state
  (ADR-008 Rev 3.1): reachable from GENERATED/REVIEWED/APPROVED, means content withdrawn
  from retrieval while remaining on disk for revision history. Both ARCHIVED and
  SUPERSEDED are terminal — no exits from either.
- **No silent model calls**: A model call that cannot dispatch must return
  NEEDS_CONFIGURATION, not a placeholder string or empty result.
- **Async-safe storage**: All SQLite access uses `aiosqlite`. No `sqlite3.connect()`
  inside any async method, anywhere. This is a hard rule.
- **CI fixture isolation**: Any fixture with `ci_fixture=True` is blocked from
  production promotion paths. Never remove this flag to make a test pass.
- **Source-grounded answers only**: Every generated answer must include provenance
  back to ingested sources. No fabricated information in any artifact.
- **No admin endpoint without auth gate**: All `/admin/*` FastAPI routes require
  auth validation. Never add an unprotected admin endpoint.

## Layer Discipline — Check Every Import
This is the #1 source of architectural violations. Before adding any import:

```
foundation   → imports NOTHING from orchestration or adapter
orchestration → imports from foundation ONLY
adapter      → imports from foundation ONLY
adapter      → NEVER imports from orchestration directly
gui          → imports from adapter API ONLY (never orchestration or foundation directly)
```

If you find yourself writing `from aip.orchestration import ...` inside
`src/aip/adapter/`, stop. Redesign using a Protocol from `foundation/protocols/`.

## Configuration Entry Points
- Primary config: `config/aip.config.toml`
- Env overrides: `AIP_DB_PATH`, `AIP_SYNTHESIS_BASE_URL`, `AIP_SYNTHESIS_MODEL`,
  `AIP_SYNTHESIS_API_KEY`, `AIP_EVALUATION_API_KEY`, `AIP_SEXTON_API_KEY`,
  `AIP_EMBEDDING_API_KEY`, `AIP_BEAST_API_KEY`, `AIP_OLLAMA_BASE_URL`, `CI=true`
- Single unified DB: `db/state.db` — initialized by `aip init`.
  All CLI commands share this path. No `--db-path` flags needed in normal flow.
- Config key contract: Key names in `aip.config.toml` MUST match
  the Python attribute names used in `ModelSlotResolver` and config loaders exactly.
  A mismatch here is a known blocker class — verify both sides on any config change.

## Brand System (GUI and docs)
- Background: dark field `#0d1117`
- Accent 1: slate-teal `#4A9B8E`
- Accent 2: amber `#D4A843`
- Text: cream `#F5F0E8`
- Display font: Fraunces | Editorial: Newsreader | UI: Inter | Code: IBM Plex Mono

## Docs Framework Rules (for all agents working in this repo)
1. Read the full doc chain root→target before touching any code.
2. Make the minimal edit. No scope expansion without explicit instruction.
3. After any edit, update this file and all parent AGENTS.md on the path.
4. Create or update the leaf AGENTS.md for the folder you edited.
5. If a shared convention changes, update root AGENTS.md first.
6. Sibling folders are invisible unless explicitly linked. Do not assume context
   from adjacent folders you haven't read.
7. **Read the status-tracking docs before recommending changes.** The root
   docs (`ROADMAP.md`, `TECH_DEBT.md`, `STATUS.md`, `PLANNED_FEATURES.md`)
   are the single source of truth for what's built, what's planned, and
   what's deferred. Recommending a change that's already implemented — or
   claiming something is "blocked" when the debt item is resolved — is a
   known failure mode. `PLANNED_FEATURES.md` is the canonical tracker;
   `TECH_DEBT.md` has the resolution status; `ROADMAP.md` has the phase
   plan; `STATUS.md` has the current operational state.

## ============================================================
## CODING CYCLE PROTOCOL (Mandatory — Every Agent, Every Cycle)
## ============================================================

Every coding cycle — whether new feature, bug fix, or refactor — follows this
sequence. There are no exceptions.

### 1. Orient (Read Phase)
- Read root AGENTS.md (you are here)
- Read the root status-tracking docs before recommending or planning any change:
  - `PLANNED_FEATURES.md` — canonical tracker: what's Already Built / Near-Term / Long-Term
  - `TECH_DEBT.md` — debt items with resolution status (don't recommend fixing a resolved debt)
  - `ROADMAP.md` — phase plan (Phase 0-5 + long-term)
  - `STATUS.md` — current operational state
- Read AGENTS.md for every folder you will **MODIFY**
- Read AGENTS.md for every folder that **CONSUMES** what you will produce
  (the bug is always in the gap between producer and consumer)
- If a consumer folder lacks AGENTS.md, **create one before coding**

### 2. Contract Check (Before Writing Code)
- Identify the data flow: what leaves this module, what enters it
- Verify **attribute names match** between producer and consumer
- If adding a new state machine or API field: write the contract
  into AGENTS.md **BEFORE** writing the code (contract-first, not afterthought)
- If the change crosses a layer boundary: verify the Protocol interface
  in `foundation/protocols/` covers it. If not, extend the Protocol first.

### 3. Code (Minimal Change Discipline)
- **One concern per change**. Resist fixing 5 things at once unless
  they are the SAME root cause.
- Every import: verify it respects layer discipline
- Every async handler: **definition before reference** (define functions
  BEFORE the `ui.button(on_click=...)` or equivalent that references them)
- Every state transition: verify it's in the ECS graph, not ad-hoc
- Every error path: **surface, don't swallow**. No silent failures.
- Every cross-module data reference: verify the attribute name exists
  on the producer (do NOT assume — check the producer's AGENTS.md Contracts)

### 4. Verify (Test + Smoke)
- Write regression tests for the specific bug or new behavior
- Tests must test **behavior**, not source text (no `Path().read_text()`
  when you can import and call)
- If the test env can't import the module, that's a signal the
  dependency graph needs attention, not a workaround
- Run `bash scripts/dogfood_smoke_test.sh` for end-to-end verification

### 5. Document (Update Phase)
- Update AGENTS.md for **every folder you modified**
- Update AGENTS.md for **every consumer folder** whose data flow changed
- Add any bug you fixed as a **"Known Gotcha"** in the relevant AGENTS.md
- Update the **"Last Cycle"** section with what changed and why
- If you created a new contract (state machine, API field, config key),
  it MUST appear in the AGENTS.md of **both producer and consumer**
- Update `PLANNED_FEATURES.md` if you shipped a feature (move it from
  Near-Term/Long-Term to Already Built) or deferred one (move to Long-Term
  with the reason). This keeps the canonical tracker current so no future
  agent gives advice that's already obsolete.
- Commit AGENTS.md changes alongside code changes, never separately

## ============================================================
## AGENTS.md SECTION TEMPLATE (Required for Every Folder)
## ============================================================

Every AGENTS.md must include these sections. Existing content maps into
the appropriate section; do NOT duplicate.

1. **Purpose** — What this folder is for
2. **Architecture Constraints** — Layer rules, import boundaries
3. **Contracts** — What this module PROMISES to consumers
   - Attribute names, API response fields, state machine values
   - Mismatches here are the #1 bug class
4. **Data Flows (In / Out)** — What enters, what leaves, attribute names
   - Cross-folder flows: `producer → consumer` with specific fields
5. **Known Gotchas** — Every bug that happened here, one line each
6. **Last Cycle** — What changed most recently and why
7. **Key Files** — File → role mapping
8. **Work Guidance** — How to edit safely
9. **How to Test** — Commands to verify

## Child Docs Index

| Subsystem | AGENTS.md Path | One-line description |
|-----------|----------------|----------------------|
| Foundation | `src/aip/foundation/AGENTS.md` | Pure types, schemas, protocols, ECS graph — no I/O |
| Orchestration | `src/aip/orchestration/AGENTS.md` | Business logic, pipelines, actors, workflow engine |
| Adapter | `src/aip/adapter/AGENTS.md` | API, all storage backends, external interfaces |
| CLI | `src/aip/cli/AGENTS.md` | `aip` command-line interface (corpus, ask, init, status) |
| GUI | `gui/AGENTS.md` | NiceGUI Operator Console — ACTIVE DEBUGGING ZONE |
| Config | `config/AGENTS.md` | TOML config schema, deployment profiles, env contract |
| Tests | `tests/AGENTS.md` | 1090+ test suite — fixture rules, CI discipline |
| Workflows | `workflows/AGENTS.md` | YAML workflow definitions for the workflow engine |
| Prompts | `prompts/AGENTS.md` | Actor prompt templates (Beast, Vigil, Sexton) |
| Scripts | `scripts/AGENTS.md` | Utility scripts, smoke test, deploy helpers |
| Docs | `docs/AGENTS.md` | Internal documentation hierarchy |

## Root Status-Tracking Docs (read before recommending changes)

| Doc | Path | Role |
|-----|------|------|
| Planned Features | `PLANNED_FEATURES.md` | Canonical tracker: Already Built / Near-Term / Long-Term |
| Tech Debt | `TECH_DEBT.md` | Debt items with resolution status |
| Roadmap | `ROADMAP.md` | Phase plan (Phase 0-5 + fleet phases per ADR-015) |
| Status | `STATUS.md` | Current operational state |
| Dogfood Ready | `DOGFOOD_READY.md` | Dogfood readiness criteria + status |

**Fleet architecture:** `docs/decisions/ADR-015-professional-agent-fleet.md`
(when accepted) is the architectural contract for all fleet work. Fleet
invariants (AgentRun required, fail-closed gate, no autonomous
cross-domain delegation, trajectory untrusted until approved) are
governance invariants that compose with — do not relax — the existing
invariants above.


# ============================================================
