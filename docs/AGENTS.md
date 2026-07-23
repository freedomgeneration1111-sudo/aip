# ============================================================

# Docs — Agent Navigation
> Internal documentation hierarchy. Architecture specs, ADRs, guides.

## Purpose
The docs directory contains all internal documentation: architecture specifications,
Architecture Decision Records (ADRs), developer guides, deployment guides, and
operational documentation. This is NOT user-facing documentation — it is for
developers and operators working on AIP itself.

## Contracts (What This Module Promises to Consumers)

### ADR Contract (Consumed by all developers and agents)
- Architecture Decision Records live in `docs/decisions/`
- ADR numbering: `ADR-NNN-title.md` (zero-padded, sequential)
- ADRs are immutable once published — corrections go in a new ADR
- Template: `docs/decisions/ADR-000-template.md`

### Spec Contract (Consumed by implementation agents)
- Build specs in `docs/internal/specs/` define what to build
- Specs are versioned: `AIP_0_1_PhaseN_BuildSpec_RevM.M.md`
- Implementation should follow the spec; deviations need ADR justification

### API Reference Contract
- `docs/API_REFERENCE.md` documents all API endpoints
- Must be updated when routes are added or changed
- Consumers: GUI developers, external integrators, agents writing API tests

## Data Flows (In / Out)

### In
- Architecture decisions from development discussions
- Build specifications from product requirements
- API surface from `adapter/api/routes/`

### Out
- **AGENTS.md files** in code folders reference docs for deeper context
- **Developers** read docs before making architectural changes
- **Agents** read docs for orientation (per Coding Cycle Protocol in root AGENTS.md)

## Known Gotchas
- **Docs can drift from code**: If code changes without updating docs, the docs
  become misleading. Always update relevant docs when changing architecture.
- **ADRs are historical records**: Don't delete ADRs. If a decision is superseded,
  mark it as superseded by a new ADR.
- **Spec revision numbers must increment**: Never reuse a spec revision number.

## Last Cycle
- **QW12 — Added `docs/ADDING_A_CORPUS.md` operator guide** (this cycle):
  new onboarding doc covering: picking a corpus_id + CorpusType, registering
  at startup (app.py lifespan), TOML config (planned), ingesting content
  (CLI commands for code/document/conversation), the sensitive flag + 4-layer
  defense, MAX_CORPORA budget arithmetic, extension-contributed corpora
  (manifest pattern), migration patterns, common pitfalls, and cross-
  references. All cross-referenced files verified to exist. Closes the
  onboarding gap (R8 from tech-debt assessment — reduces bus-factor risk).
- **QW15 + QW4 — ROADMAP.md fleet phases marked spec-only; STATUS.md header refreshed** (prior cycle):
  - ROADMAP.md: Fleet Phases section header changed from "when accepted" to
    "ACCEPTED, spec only"; added prominent banner listing exactly which
    primitives don't exist (AgentRun, CapabilityGate, FleetCoordinator,
    DispatchPlan, fleet_cost_ledger, start_policy, parallel_safe, trajectory
    corpus, CURATOR); all 7 phases marked "🔲 Planned (zero code)".
  - STATUS.md: "Last Updated" bumped from 2026-06-17 to 2026-07-23. Project
    Mode line expanded to mention ADR-014 Extension Platform + ADR-008
    Multi-Corpus + ARISTOTLE extraction. Added test count (4,384 collected)
    and cross-references to ROADMAP/TECH_DEBT. Closes D8 from the tech-debt
    assessment (STATUS.md was 6 days stale).
- **QW5 — UI_CONVENTIONS.md marked as target spec** (prior cycle): added a
  prominent banner at the top of `docs/UI_CONVENTIONS.md` clarifying that
  the document describes the **target** UI shell, not the current state.
  Itemized which parts are implemented (left sidebar, extension nav via
  KNOWN_EXTENSIONS polling, conditional right rail) vs not (+ menu, 8 of 9
  extensions). The Right Sidebar reference table now has a Status column
  marking ARISTOTLE as SHIPPED and the other 8 as "spec only." Doc drift
  items D6/D7 from the 2026-07-23 tech-debt assessment.

## Key Subdirectories
| Path | Role |
|------|------|
| `decisions/` | Architecture Decision Records (15 ADRs) |
| `internal/specs/` | Build specifications per phase |
| `internal/` | Internal design docs (ask, ingestion, review_export) |
| `ui/` | UI design references, mockups, style system |
| `hardening/` | Hardening audit docs, discrepancy registers |
| `evals/` | Evaluation criteria and golden test definitions |
| `UI_CONVENTIONS.md` | Target spec for the three-panel shell (marked target spec 2026-07-23) |
| `ADDING_A_CORPUS.md` | Operator guide for adding a new corpus (QW12, 2026-07-23) |

## Key ADRs
| ADR | Title | Impact |
|-----|-------|--------|
| ADR-001 | Turn-level corpus ingestion | Corpus data model |
| ADR-002 | Beast domain registry | 28-domain taxonomy |
| ADR-003 | Beast context advisory | Actor → user advisory path |
| ADR-004 | Multi-corpus architecture | Corpus isolation model |
| ADR-005 | AIP HALL model | Architecture overview |
| ADR-006 | Wiki architecture | Wiki storage and retrieval |
| ADR-007 | Knowledge graph architecture | Graph store design |
| ADR-008 | Semantic session context | Session management |
| ADR-009 | Cohort synthesis | Batch synthesis design |
| ADR-010 | Browser extension ingest | Extension data flow |
| ADR-011 | Actor role boundaries | Beast/Vigil/Sexton separation |
| ADR-012 | Single-writer sufficiency | Write concurrency model |
| ADR-013 | Retrieval quality validation closure | Quality gate design |
| ADR-014 | Phase 0 extension platform — ExtensionHost lifecycle & manifest v1 | Extension contract; ARISTOTLE is the first consumer |

## Work Guidance
- Adding an ADR: copy `ADR-000-template.md`, fill in sections, commit
- Updating a spec: increment the revision number, update the file, add changelog
- API changes: update `API_REFERENCE.md` alongside the route code

## How to Test
```bash
# Docs don't have automated tests, but verify:
# 1. ADR filenames follow ADR-NNN-title.md pattern
# 2. Spec revision numbers are unique
# 3. API_REFERENCE matches actual routes
ls docs/decisions/ADR-*.md | sort
```


# ============================================================
