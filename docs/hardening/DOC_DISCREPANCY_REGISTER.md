# DOC_DISCREPANCY_REGISTER.md

**Frozen at:** commit `efeb887c799aa5cefa1add1f011b7b8cf99bd83b`
**Date:** 2026-06-10
**Hardening Cycle:** Chunk 1 — Continuity Audit and Baseline Freeze

This register captures every discrepancy found between documentation and actual code behavior.
A discrepancy is any place where a doc says something is true that the code does not support,
or where code does something the docs do not mention.

---

## Methodology

1. Read all documentation files in the repository
2. Cross-reference every factual claim against source code behavior
3. Cross-reference every code behavior against documentation coverage
4. Classify each discrepancy by severity

### Severity Classification

| Level | Meaning |
|---|---|
| **CRITICAL** | Doc claims a safety or governance property that code does not enforce |
| **HIGH** | Doc claims a feature is working that is actually broken, scaffold, or missing |
| **MEDIUM** | Doc is stale, inaccurate, or misleading but not safety-relevant |
| **LOW** | Doc is imprecise, uses outdated terminology, or has minor inconsistency |

---

## Discrepancy Register

### DDR-001 — README claims "Vector embeddings — Not built" but they ARE built

| Field | Value |
|---|---|
| File | `README.md` lines 26-30 |
| Claim | "Vector embeddings (vectors.db is empty — FTS5 only for now)" and "Vector embeddings | Not built | Phase 1.4" in the capabilities table |
| Reality | Vector embeddings ARE built and working. The vector store has 50 vectors. Hybrid retrieval with RRF fusion is operational. The `RetrievalOrchestrator` dispatches across FTS5, Vector, and Corpus channels. Coverage-aware gating falls back to FTS5-only when vector coverage < 10%, but the vector subsystem itself is fully implemented. |
| Severity | **HIGH** |
| Impact | Alpha testers may not attempt vector search or hybrid retrieval because the README tells them it does not exist. This directly undermines AIP-G-02 (no fake success) by inverting it — the system is *underclaiming* its capabilities, which is better than overclaiming but still creates a truth gap. |
| Resolution | Update README.md capabilities table: "Vector embeddings" should show "Working" with note "~1.8% coverage (50/2,766 turns embedded)" and "vectors.db is empty" should be removed or corrected |
| Assigned to | Later chunk (not fixed in Chunk 1 — Chunk 1 is audit-only) |

### DDR-002 — README claims "Knowledge graph — Not built" but it IS built

| Field | Value |
|---|---|
| File | `README.md` line 28 and capabilities table |
| Claim | "Knowledge graph (entity extraction + Cytoscape.js visualization)" listed under "planned but not yet built" |
| Reality | Knowledge graph is fully built and working. GraphStore uses aiosqlite with persistent connections. 36 nodes and 17 edges exist. Cytoscape.js visualization is live at `/graph-viz`. Graph API endpoints are at `/api/v1/graph/data`, `/neighbors`, `/stats`. PPR retrieval with `GraphRetriever` is implemented. Entity alias registry has 22 entries. STATUS.md correctly documents this as "COMPLETE". |
| Severity | **HIGH** |
| Impact | Same as DDR-001 — alpha testers are told a working feature does not exist. The STATUS.md is accurate but the README (the first document anyone reads) is misleading. |
| Resolution | Move "Knowledge graph" from "planned but not yet built" to "working" section in README, with accurate description |
| Assigned to | Later chunk |

### DDR-003 — README claims "DEFINER profile injection" is "Not built" but partial implementation exists

| Field | Value |
|---|---|
| File | `README.md` line 29 |
| Claim | "DEFINER profile injection in augmented chat" listed under "planned but not yet built" |
| Reality | `adapter/definer_profile.py` implements `DefinerProfile` that reads from `definer_profile_v1.md`. The Beast context advisory in augmented chat already injects domain overview context. The definer profile content IS loaded and available, though it may not be actively injected into every augmented chat prompt. STATUS.md does not mention this capability either way. |
| Severity | **MEDIUM** |
| Impact | Partial implementation exists but is not documented. Alpha testers cannot discover and use it. |
| Resolution | Investigate exact wiring state. If definer profile IS injected in augmented chat, update README. If not, document as "partial" rather than "not built". |
| Assigned to | Later chunk |

### DDR-004 — Build specs all reference Architecture Revision 5.2; STATUS.md says 6.4

| Field | Value |
|---|---|
| Files | `docs/internal/specs/AIP_0_1_Phase1_BuildSpec_Rev1.3.md` through `AIP_0_1_Phase9_BuildSpec_Rev1.0.md` (9 files) |
| Claim | All 9 build specs declare `Architecture Revision: 5.2` |
| Reality | STATUS.md declares `Architecture Revision: 6.4`. The architecture has been through at least 2 revisions since these specs were frozen. Major changes include ADR-011 (actor role boundaries), ADR-012 (single-writer sufficiency), ADR-013 (retrieval quality validation), and the full aiosqlite migration. |
| Severity | **MEDIUM** |
| Impact | Build specs are historical documents that guided construction; they are not expected to track current state. However, the mismatch could confuse future developers who reference specs alongside STATUS.md. |
| Resolution | Add a note to each build spec indicating it is a historical document frozen at Rev 5.2 and that the current architecture revision is tracked in STATUS.md. Alternatively, create a single ARCHITECTURE_REVISION_HISTORY.md that maps spec versions to current state. |
| Assigned to | Later chunk |

### DDR-005 — DOGFOOD_READY.md Step numbering uses "Step 0-6" but refers to alpha steps

| Field | Value |
|---|---|
| File | `DOGFOOD_READY.md` |
| Claim | The alpha test release header uses "Step 0" through "Step 5" for the first-run sequence, while the original dogfood guide below uses "Step 1" through "Step 9". Two overlapping step numbering systems exist. |
| Reality | The alpha section (Steps 0-5) and the original section (Steps 1-9) overlap in content but diverge in order and detail. For example, the alpha section puts "Ingest your Claude conversations" at Step 1, while the original puts "Install" at Step 1. The original still references `aip ingest file/directory` while the alpha section uses `aip corpus ingest`. |
| Severity | **LOW** |
| Impact | Confusing for a first-time user who reads both sections. Not safety-relevant. |
| Resolution | Merge into a single step sequence or clearly separate with "Quick Start (Alpha)" vs "Full Guide". |
| Assigned to | Later chunk |

### DDR-006 — STATUS.md claims "Scaffolding ~5-8% overall" but alerting.py alone is 9,133 lines of largely Sprint-annotated code

| Field | Value |
|---|---|
| File | `STATUS.md` line 46 |
| Claim | "Scaffolding: ~5-8% overall (MCP dispatch, adaptive router, ScriptNode sandbox)" |
| Reality | While the three named surfaces (MCP, router, ScriptNode) are indeed scaffold at the stated percentages, the overall scaffolding percentage is higher if Sprint-number commentary and placeholder logic are counted. The `alerting.py` file at 9,133 lines contains extensive Sprint-number comments that narrate implementation history rather than durable design. The `alert_history_store.py` at 3,159 lines has similar patterns. These are not scaffold in the sense of "fake behavior", but they contain code that was built incrementally across 30+ Sprints without consolidation, leading to accumulated technical debt that inflates the effective "not production-hardened" percentage. |
| Severity | **MEDIUM** |
| Impact | The 5-8% figure may give a false sense of codebase maturity. The actual "needs hardening" percentage is higher when Sprint-annotated, unconsolidated code is included. |
| Resolution | Either (a) update STATUS.md to clarify that 5-8% refers to explicitly scaffold surfaces only, and add a separate metric for "Sprint-annotated code requiring consolidation", or (b) revise the percentage upward. |
| Assigned to | Later chunk |

### DDR-007 — STATUS.md says tests are "1002+ passing" but does not document the 2 pre-existing failures

| Field | Value |
|---|---|
| File | `STATUS.md` line 43 |
| Claim | "Tests: 1002+ passing, 23 skipped (sqlite_vss extension + pre-existing governance), 2 pre-existing failures" |
| Reality | The document DOES mention "2 pre-existing failures" but does not identify which tests they are or what causes them. The implementation_status.md identifies them as `test_model_slot_resolver.py` (4 tests fail due to env var pollution) and `test_sqlite_vss_graceful_skip.py` (fails due to global state pollution), but STATUS.md itself does not name them. |
| Severity | **MEDIUM** |
| Impact | Per AIP-G-02 and AIP-G-11, honest disclosure of gaps is required. Naming the failing tests would allow any developer to reproduce and fix them. |
| Resolution | Add a "Pre-existing Test Failures" subsection to STATUS.md naming the two test files and their root cause (env var pollution). |
| Assigned to | Later chunk |

### DDR-008 — README "Current Capabilities" table says "MCP tool server | Scaffold | Returns structured NOT_IMPLEMENTED"

| Field | Value |
|---|---|
| File | `README.md` capabilities table |
| Claim | MCP tool server is scaffold that "Returns structured NOT_IMPLEMENTED" |
| Reality (Chunk 1, frozen) | MCP server dispatch returned hardcoded success responses. `aip_search` returned `{"results": []}`, `aip_artifact_approve` returned `{"approved": True, "canonical": True}`. |
| Reality (Chunk 1.5, corrected) | **MCP dispatch now performs REAL mutations.** `aip_artifact_approve` calls `ecs_store.transition(REVIEWED→APPROVED)` and `canonical_store.write_canonical()` — it is NOT returning hardcoded fake approval. `aip_search` dispatches to real lexical + vector search via Protocols. However: (1) MCP server is **not wired into app.py runtime** — it is only exercisable via direct `call_tool()` invocation. (2) When `container.autonomy_gate is None`, write/admin tools bypass the gate entirely (fail-open). (3) README claim of "Returns structured NOT_IMPLEMENTED" was stale even in Chunk 1 — the code returned fake success then, and now does real work. |
| Severity | **HIGH** (non-live governance debt; would be CRITICAL if MCP were wired) |
| Classification | **Non-live governance debt** — MCP server is not reachable at runtime. The `autonomy_gate=None` escape hatch is a latent vulnerability that must be hardened before MCP is wired. |
| Impact | The README claim is stale in two directions: (1) it underclaims by saying NOT_IMPLEMENTED when real dispatch exists, and (2) the `autonomy_gate=None` fail-open means that if MCP IS wired without explicit gate provision, write/admin tools will bypass DEFINER sovereignty. The code is not a runtime risk today because MCP is not wired. |
| Resolution | (1) Update README to describe MCP as "built but not runtime-wired" with real dispatch and `autonomy_gate=None` fail-open risk. (2) Harden `server.py` to fail-closed when `autonomy_gate is None` for write/admin tools. (3) Update STATUS.md scaffolding table. |
| Assigned to | Chunk 2 |
| Chunk 2 status | **DONE** — MCP fail-closed hardened; README updated; STATUS.md updated |

### DDR-009 — DOGFOOD_READY.md references "aip corpus ingest" but original guide references "aip ingest file/directory"

| Field | Value |
|---|---|
| File | `DOGFOOD_READY.md` |
| Claim | Alpha section uses `aip corpus ingest`, original section uses `aip ingest file/directory` |
| Reality | Both commands exist in the CLI. `aip corpus ingest` handles Claude JSON exports. `aip ingest file/directory` handles individual file formats. The alpha section does not explain the distinction or when to use which command. |
| Severity | **LOW** |
| Impact | Confusing for alpha testers who might try the wrong command for their data format. |
| Resolution | Add a brief explanation of which command to use for which data source. |
| Assigned to | Later chunk |

### DDR-010 — TECH_DEBT.md (top-level) does not contain DEBT-001 through DEBT-007

| Field | Value |
|---|---|
| Files | `TECH_DEBT.md` (project root) vs `docs/TECH_DEBT.md` |
| Claim | The project has two TECH_DEBT.md files. The root-level `TECH_DEBT.md` contains only test isolation debt notes (DEBT-002, DEBT-003 style notes). The `docs/TECH_DEBT.md` contains the full DEBT-001 through DEBT-007 register. |
| Reality | The root `TECH_DEBT.md` is a leftover fragment. The authoritative register is in `docs/TECH_DEBT.md`. This split is confusing. |
| Severity | **MEDIUM** |
| Impact | A developer looking at the root TECH_DEBT.md would not find the actual debt register and might assume DEBT-001 through DEBT-007 do not exist. |
| Resolution | Merge root TECH_DEBT.md content into docs/TECH_DEBT.md, or delete root TECH_DEBT.md and add a redirect reference. |
| Assigned to | Later chunk |

### DDR-011 — README references "ADR-001 through ADR-007" but there are 14 ADRs (ADR-000 through ADR-013)

| Field | Value |
|---|---|
| File | `README.md` line 235 |
| Claim | "`docs/decisions/` — Architecture Decision Records (ADR-001 through ADR-007)" |
| Reality | There are 14 ADR files: ADR-000 (template), ADR-001 through ADR-013. Key missing references include ADR-008 (semantic session context), ADR-009 (cohort synthesis), ADR-010 (browser extension ingest), ADR-011 (actor role boundaries — critical for DEBT-006), ADR-012 (single-writer sufficiency), ADR-013 (retrieval quality validation closure). |
| Severity | **LOW** |
| Impact | Developers reading the README will not know about the more recent and architecturally significant ADRs. |
| Resolution | Update README to reference "ADR-001 through ADR-013" |
| Assigned to | Later chunk |

### DDR-012 — STATUS.md "Actor Status" says Sexton "❌ NOT WIRED — DEBT-006" but table also says "Built" for code state

| Field | Value |
|---|---|
| File | `STATUS.md` Actor Status table |
| Claim | The table correctly shows the dichotomy: code state is "Built" but wired status is "NOT WIRED". |
| Reality | This is actually ACCURATE and truthful. The new Sexton IS built (1,341 lines of real code) but IS NOT wired. STATUS.md is telling the truth here. |
| Severity | N/A — NOT A DISCREPANCY (documented for completeness) |
| Resolution | None needed |
| Assigned to | N/A |

### DDR-013 — `corpus_registry.py::delete_corpus` docstring calls Phase 2 a "Stub" but it is implemented

| Field | Value |
|---|---|
| File | `src/aip/adapter/corpus_registry.py` lines 353-354 (docstring of `delete_corpus`) |
| Claim | "Phase 2: delete_bridge_edges(corpus_id) in definer graph. (Stub — implemented in Chunk 6 when bridge edges exist.)" |
| Reality | The bridge-edge cleanup IS implemented in Chunk 6 — `corpus_registry.py:378-396` calls `GraphStore.delete_bridge_edges(corpus_id)` for real, with best-effort error handling and logging. The docstring was never updated when Chunk 6 landed. |
| Severity | **LOW** |
| Impact | Anyone reading the registry source would believe the bridge-edge cleanup is unimplemented and might try to "fix" it, duplicating work or introducing bugs. No runtime impact. |
| Resolution | **RESOLVED 2026-07-23** — docstring updated to describe the real implementation. Discovered in the 2026-07-23 tech-debt assessment (item D5). |

---

## Summary Table

| ID | Severity | File | Claim vs Reality | Resolution Scope |
|---|---|---|---|---|
| DDR-001 | HIGH | README.md | Vector embeddings marked "Not built" but working | Doc + code verification |
| DDR-002 | HIGH | README.md | Knowledge graph marked "Not built" but working | Doc update |
| DDR-003 | MEDIUM | README.md | DEFINER profile "Not built" but partial | Investigation + doc |
| DDR-004 | MEDIUM | Build specs (9 files) | Architecture Revision 5.2 vs 6.4 | Add historical note |
| DDR-005 | LOW | DOGFOOD_READY.md | Dual step numbering systems | Merge/simplify |
| DDR-006 | MEDIUM | STATUS.md | Scaffolding 5-8% understates debt | Clarify metric scope |
| DDR-007 | MEDIUM | STATUS.md | Test failures not named | Add failure names |
| DDR-008 | **HIGH** (non-live) | README.md | MCP: real dispatch, not wired, autonomy_gate=None fail-open | Hardening + doc fix (Chunk 2 IN PROGRESS) |
| DDR-009 | LOW | DOGFOOD_READY.md | Ingest command confusion | Add explanation |
| DDR-010 | MEDIUM | Root TECH_DEBT.md | Split debt register | Merge or delete |
| DDR-011 | LOW | README.md | ADR count is 7 not 13+ | Update count |
| DDR-012 | N/A | STATUS.md | NOT a discrepancy | None |
| DDR-013 | LOW (RESOLVED) | corpus_registry.py | delete_corpus Phase 2 docstring said "Stub" but was implemented | Docstring fix (resolved 2026-07-23) |

**Critical:** 0 (DDR-008 reclassified to HIGH — non-live debt)
**High:** 3 (DDR-001, DDR-002, DDR-008)
**Medium:** 5 (DDR-003, DDR-004, DDR-006, DDR-007, DDR-010)
**Low:** 3 (DDR-005, DDR-009, DDR-011) + 1 RESOLVED (DDR-013)
