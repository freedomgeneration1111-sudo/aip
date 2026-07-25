# AIP Brain — Release Notes v1.0.0

**Release Date:** 2026-07-24
**Branch:** `feat/multi-corpus`
**Architecture Revision:** 6.4
**Test Count:** 4,384+ tests collected, 237 focused suite tests passing

---

## Overview

AIP Brain v1.0.0 is the first production-ready release. It ships four
interconnected capabilities that were the focus of the 2026-07-23 tech-debt
assessment and roadmap:

1. **Multiple corpus databases** — fully operational multi-corpus architecture
2. **User corpus selection** — per-session multi-corpus retrieval from the UI
3. **AIP self-knowledge** — AIP ingests its own codebase + docs as a searchable
   corpus with a dependency graph, kept current via auto-ingest
4. **Extension platform** — extensions can dynamically register corpora, actors,
   and API surfaces with safe lifecycle management

---

## What's New

### Multi-Corpus Architecture (ADR-008)

- **CorpusRegistry** with per-corpus SQLite databases, fingerprinted migrations,
  two-phase delete, cross-corpus review fan-in, and bridge-edge reconciliation
- **MAX_CORPORA raised to 8** (was 4) — accommodates the fleet vision
- **`[corpora.{id}]` TOML config section** — operators can add corpora via
  config file without editing source code
- **`GET /corpus-registry/corpora` endpoint** — lists registered corpora with
  type, sensitive flag, deletion state
- **Per-corpus lexical + graph stores** — CorpusStoreFactory builds
  SqliteFts5LexicalStore + GraphStore for each corpus (vector_store deferred
  to Phase β+)

### Codebase-as-Corpus (Phase 1.6)

- **Codeforge corpus registered at startup** — AIP's own Python source is
  searchable as a corpus alongside conversation turns
- **`aip corpus ingest-code` CLI** — ingests a Python directory via AST parser
  (functions, classes, module-level registration calls; skip .pyi/test_*)
- **Auto-ingest background task** — the server lifespan spawns a
  `codeforge-ingest-scheduler` that ingests `src/aip/` on startup and re-ingests
  every 60s (configurable via `[codeforge]` section in TOML)
- **`aip corpus watch-code` CLI** — polling-based file watcher for non-server
  contexts (CI, external repos)
- **Code dependency graph** — `build_code_graph()` creates FUNCTION/CLASS nodes
  + `imports`/`calls` edges in the per-corpus GraphStore, enabling "what calls
  X?" queries
- **Importance fix** — code turns get `importance=1.0` (was 0.0, which was
  below the retrieval `min_importance=0.3` filter — code turns were invisible)

### Extension Platform (ADR-014)

- **ExtensionHost lifecycle** — discover → validate → migrate → register →
  ready → mount, with per-stage sandbox isolation
- **Actor Protocol** — `@runtime_checkable` Protocol with `ActorContext`,
  `ActorResult`, `start_policy` (DEBT-020 fix — manual_only actors skip
  startup cycle, safe for write-capable agents)
- **`register_corpus_provider`** — dynamic corpus registration from `on_load`
  (extensions can register corpora based on runtime conditions, not just
  manifest-static)
- **`/health/extensions` endpoint** — per-extension state + failures + nav_items
- **Import boundary enforcement** — AST-based test ensures extensions import
  only from `aip.foundation.protocols` + `aip.adapter.extensions`

### Wiki → User Manual Evolution

- **`manual_chapter` artifact type** — wiki route classifies `manual:*` IDs
- **`prerequisite_of` crosslink relation** — enables chapter ordering
- **`aip export manual <domain>` CLI** — compiles all APPROVED wiki articles
  in a domain into a structured markdown manual with TOC + chapters
- **WIKI_ARTICLE graph nodes** — wiki articles become first-class graph
  entities on creation (best-effort upsert, never fails wiki creation)

### Retrieval Pipeline

- **Augmented context helper** — shared `assemble_augmented_context()` extracted
  from chat.py, used by both WebSocket chat + Multi-Cast model council
- **Multi-corpus retrieval** — `gather_corpus_results()` fans out to active
  corpora with graceful degrade on restricted-corpus denial
- **Corpus selector in Ask page** — collapsible "Corpus Selection" panel with
  checkboxes per corpus; writes `active_corpus_ids` to session metadata via
  PATCH /sessions/{id}

### Documentation & Governance

- **Doc drift guard suite** — 13 CI-level tests that verify doc claims against
  code reality (prevents "✅ Complete" when code doesn't back it up)
- **`docs/ADDING_A_CORPUS.md`** — operator guide for adding corpora
- **`docs/UI_CONVENTIONS.md`** — marked as target spec (not all features
  implemented)
- **ROADMAP.md** — ADR-015 fleet phases marked "spec only — zero code today"
- **STATUS.md** — refreshed with current test count, ARISTOTLE note,
  extension platform status

---

## Debt Items Resolved

| ID | Title | Resolution |
|----|-------|------------|
| DEBT-011 | Branham deprecated aliases | Removed all 6 backward-compat sites |
| DEBT-020 | cadence=0 startup hazard | Added `start_policy` field |
| DEBT-022 | AdaptiveRouter dead code docs | Updated 6 stale doc locations |
| DEBT-023 | trajectory/ naming collision | Renamed to l4_regulation/ |
| DEBT-025 | MAX_CORPORA budget | Raised from 4 to 8 |
| DEBT-026 | CorpusStoreFactory lexical/graph slots | Built per-corpus |
| ND1 | Corpus selector dead code | Wired into Ask page + endpoint added |
| ND3 | lexical/vector/graph slots None | lexical + graph built (vector deferred) |
| ND4 | Duplicate python_ast_parser.py | Deleted orchestration copy |
| ND5 | No codeforge corpus registered | Registered at startup |
| ND6 | No file watcher for code corpus | Auto-ingest in lifespan + CLI watcher |
| ND9 | register_corpus_provider missing | Dynamic hook added |
| ND10 | No E2E extension→corpus→retrieval test | Acceptance tests added |
| ND11 | MAX_CORPORA=4 budget pressure | Raised to 8 |

---

## Known Limitations

- **vector_store per corpus** — deferred to Phase β+ (needs embedding provider
  injection, currently container-level not per-corpus)
- **AdaptiveRouter** — fully implemented but never called (dead code); wiring
  deferred to Phase 3C (CURATOR)
- **ADR-015 fleet** — entirely spec; zero fleet code exists (AgentRun,
  CapabilityGate, FleetCoordinator, DispatchPlan all unbuilt)
- **ARISTOTLE** — the reference extension lives in a separate repo
  (`AIP_Aristotle`); not visible to CI
- **Code graph edges** — `imports` and `calls` edges are built; `tests` and
  `implements` edges are future work
- **Cross-graph edges** — conversation turn → function reference edges are
  future work
- **Wiki manual ordering** — currently by `created_at`; topological sort by
  `prerequisite_of` edges is future work

---

## Upgrade Instructions

1. Pull the latest code: `git pull origin feat/multi-corpus`
2. Sync dependencies: `uv sync`
3. Force re-ingest the codeforge corpus (picks up the importance=1.0 fix):
   ```bash
   uv run aip corpus ingest-code src/aip/ --force
   ```
4. Restart the server — the auto-ingest task will keep the codeforge corpus
   current going forward
5. (Optional) Add corpora via TOML config:
   ```toml
   [corpora.my_research]
   type = "document"
   sensitive = true
   access_note = "Restricted research corpus"
   ```

---

## Test Summary

- **4,384** tests collected (full suite)
- **237** focused suite tests passing (corpus + extensions + wiki + graph +
  drift guard + acceptance)
- **0** failures, **0** errors
- **13** doc drift guard tests (CI-level doc-vs-code consistency)
- **19** acceptance tests (AC-01 through AC-10 + multi-corpus E2E)
