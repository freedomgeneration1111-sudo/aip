# CODE_DEBT_REGISTER.md

**Frozen at:** commit `efeb887c799aa5cefa1add1f011b7b8cf99bd83b`
**Date:** 2026-06-10
**Hardening Cycle:** Chunk 1 — Continuity Audit and Baseline Freeze

This register captures all code debt discovered during the baseline audit. It includes
pre-existing documented debt (DEBT-001 through DEBT-007) and newly discovered debt
from the grep scans and code review.

---

## Debt Classification

| Level | Meaning |
|---|---|
| **CRITICAL** | System is broken, unsafe, or violating governance invariants |
| **HIGH** | Significant gap that affects functionality, reliability, or maintainability |
| **MEDIUM** | Technical debt that should be addressed in normal maintenance |
| **LOW** | Cosmetic or minor issue that can be addressed opportunistically |

---

## Previously Documented Debt (from docs/TECH_DEBT.md)

### DEBT-001 — Graph Node Alias Cleanup (`aip_methodology → aip`)

| Field | Value |
|---|---|
| Status | Deferred |
| Severity | LOW |
| Filed | 2026-06-05 |
| Summary | Bridge tag `aip_methodology->theology_research` references orphan node `aip_methodology` that was renamed to `aip`. Only affects 5 bridge-tagged turns with 1 edge. |
| Trigger | After full corpus retag, re-run `aip corpus graph --build-from-bridges --force`. If `aip_methodology` nodes persist, implement merge command. |

### DEBT-002 — Full PPR Expansion in Augmented Chat

| Field | Value |
|---|---|
| Status | Deferred |
| Severity | MEDIUM |
| Filed | 2026-05-05 |
| Summary | HippoRAG PPR expansion in chat path deferred. Current implementation does 1-hop domain adjacency only. Full PPR with entity extraction requires NER or Beast LLM call, which would add latency. |
| Trigger | Phase 3: Wire entity extraction as background pre-fetch. If graph >500 nodes and extraction <200ms, promote to full PPR. |

### DEBT-003 — MCP Tool Dispatch (Real dispatch, not runtime-wired)

| Field | Value |
|---|---|
| Status | Active — non-live governance debt |
| Severity | **HIGH** (would be CRITICAL if MCP were wired) |
| Filed | 2026-06-04 (pre-existing) |
| Updated | 2026-06-10 (Chunk 1.5 triage corrected description) |
| Summary | MCP tool dispatch performs **real mutations** (ECS transitions, canonical writes, search via Protocols), NOT hardcoded fake responses as originally documented. However: (1) MCP server is **not wired into app.py runtime** — no transport, no API route triggers it. (2) When `container.autonomy_gate is None`, write/admin tools bypass the gate entirely (fail-open escape hatch at `server.py:213`). The original Chunk 1 description ("returns hardcoded True") was stale — it reflected pre-`0d63e58` code. |
| Classification | **Non-live governance debt** — the MCP server is only exercisable via direct `call_tool()` invocation from tests. The `autonomy_gate=None` fail-open is a latent vulnerability that must be hardened before MCP is wired into runtime. |
| Trigger | Phase 5 multi-user deployment, or earlier if MCP is wired into app.py. Must harden `autonomy_gate=None` fail-closed before wiring. |
| Chunk 2 action | Harden `server.py:213` to reject write/admin tools when `autonomy_gate is None`. Add test. |
| Chunk 2 status | **DONE** — fail-closed behavior implemented; 3 new tests pass; 7 existing tests updated |

### DEBT-004 — GraphStore Connection Churn

| Field | Value |
|---|---|
| Status | **RESOLVED** |
| Filed | 2026-06-06 |
| Summary | GraphStore was using per-call `sqlite3.connect()`. Resolved by Chunk 4 — converted to aiosqlite with persistent connection. |

### DEBT-005 — GraphStore Protocol Missing + Synchronous sqlite3

| Field | Value |
|---|---|
| Status | **RESOLVED** |
| Filed | 2026-06-06 |
| Summary | GraphStore had no Protocol in foundation/protocols and used sync sqlite3. Both resolved by Chunk 4 — Protocol added, aiosqlite conversion complete. |

### DEBT-006 — `actors/sexton.py` NOT WIRED into app.py (CRITICAL)

| Field | Value |
|---|---|
| Status | Active |
| Severity | **CRITICAL** |
| Filed | 2026-06-06 |
| Summary | ADR-011 built a full-maintenance Sexton actor (`actors/sexton.py`, 1,341 lines, 5 operations: tagging, embedding, wiki, graph, classification). But `app.py` was never updated to wire it. The old failure-classifier-only Sexton (`sexton/sexton.py`, ~220 lines) remains active. As a result: automatic tagging, embedding, wiki generation, and graph extraction are NOT running. |
| Impact | (1) 2,716 turns unembedded (~98.2%), limiting hybrid retrieval quality. (2) No automatic wiki generation. (3) No automatic graph extraction. (4) No automatic corpus tagging. Only failure classification runs every 300s. |
| Remediation | In app.py: import `actors/sexton.Sexton` instead of `sexton/sexton.Sexton`, pass full store set, change `run_classification_cycle()` to `run_cycle()`, update interval, fix docstring reference. |

### DEBT-007 — CLI Commands Using Blocking sqlite3.connect()

| Field | Value |
|---|---|
| Status | Active — low priority |
| Severity | MEDIUM (for admin.py) / LOW (for CLI) |
| Filed | 2026-06-10 |
| Summary | CLI commands use sync sqlite3 (acceptable — no event loop). But `admin.py:308` also uses blocking `sqlite3.connect()` inside the async FastAPI event loop. Read-only with short query duration, but architecturally inconsistent with the aiosqlite migration. |
| Trigger | Next time admin.py routes are modified, convert to store layer. |

---

## Newly Discovered Debt (from Chunk 1 Audit)

### CDR-001 — Massive Sprint-Number Commentary in Runtime Code

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | Grep scan: 300+ Sprint-number references in `src/` |
| Summary | Source code contains hundreds of Sprint-number comments that narrate implementation history rather than durable design. The worst offenders are: `alerting.py` (~200 Sprint references across 9,133 lines), `alert_history_store.py` (~80 Sprint references across 3,159 lines), `read_pool.py` (~20 Sprint references). These comments create maintenance burden because: (1) they reference Sprint numbers that have no meaning outside the original development timeline, (2) they document when something was added rather than why it exists, (3) they create visual noise that makes it harder to find meaningful comments. |
| Examples | `# Sprint 5.30: Schema migration v1 -> v2`, `# Sprint 5.38: Alert throttling & circuit breaker`, `# Sprint 5.63: Connection pool for high-concurrency WS delivery` |
| Resolution | Replace Sprint-number comments with comments explaining durable design constraints. Comments that explain WHY a feature exists or what constraint it satisfies are valuable. Comments that say "Sprint X added this" are AI fingerprints that should be removed. |

### CDR-002 — Step-Number Scaffold Comments

| Field | Value |
|---|---|
| Severity | LOW |
| Discovery | Grep scan: 22 `Step \d` references in `src/` |
| Summary | Several files use "Step 1", "Step 2", etc. comments to narrate sequential logic. While some of these are genuinely helpful for understanding multi-step processes, others are AI scaffold that narrates implementation rather than design. |
| Files | `cli/eval.py` (5 steps), `beast_scan.py` (3 steps), `definer_gate.py` (4 steps), `context_reset.py` (6 steps), `entity_extractor.py` (1 step), `vigil.py` (7 steps + 7 retrieval steps), `l4/reset.py` (1 step) |
| Resolution | Replace "Step N:" with descriptive comments that explain what the step does and why. Example: replace `# Step 1: Check structural validation` with `# Validate artifact structure before proceeding to evaluation`. |

### CDR-003 — `except Exception: pass` — Silent Exception Swallowing

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Discovery | Grep scan: 21 `except Exception: pass` patterns in `src/` |
| Summary | Twenty-one instances of `except Exception: pass` silently swallow all exceptions, making failures invisible. This directly conflicts with AIP-G-02 (No Fake Success) and the "No Silent Degradation" hardening rule. |
| Distribution | `app.py` (5), `alerting.py` (5), `dependencies.py` (2), `vigil/sqlite_vigil_store.py` (3), `vigil/vigil_quality_store.py` (3), `alert_history_store.py` (3), `auth/session_store.py` (1 at line 61), `vector/sqlite_vss_store.py` (2), `entity/sqlite_entity_store.py` (2), `event_store_queryable.py` (2), `canonical/sqlite_canonical_store.py` (2), `artifact_store_versioned.py` (2), `graph_store.py` (2), `codex/codex_store.py` (2), `config_watcher.py` (1), `cli/eval.py` (2), `cli/history.py` (1), `cli/corpus.py` (2) |
| Resolution | Replace `pass` with either: (1) `logger.warning(...)` for non-critical failures, (2) `await self._reset_conn()` for connection-reset patterns (many already do this), (3) explicit error state return for critical paths. Some instances are acceptable (e.g., WebSocket send failures where the client may have disconnected), but all should be documented with a comment explaining WHY the exception is swallowed. |

### CDR-004 — Broad `except Exception:` Without pass (Total: ~170 instances)

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | Grep scan: ~170 `except Exception:` patterns in `src/` |
| Summary | Beyond the 21 `except Exception: pass` cases, approximately 150 more instances catch `except Exception:` with various handling (logging, returning defaults, resetting connections). While many are reasonable (e.g., health check failures that degrade gracefully), the breadth of the pattern is concerning because: (1) it catches `KeyboardInterrupt`, `SystemExit`, and `CancelledError` unless explicitly re-raised, (2) it makes debugging difficult because exception types are lost, (3) it can mask programming errors as "expected" failures. |
| Worst offenders | `health.py` (~30 instances), `codex/codex_store.py` (~25 instances), `graph_store.py` (~18 instances), `alerting.py` (~20 instances), `sessions.py` (~15 instances), `corpus.py` (CLI, ~10 instances), `admin.py` (~10 instances) |
| Resolution | Opportunistically narrow exception types where the specific failure mode is known. For connection-reset patterns, catch `sqlite3.OperationalError` or `aiosqlite.Error` instead. For health check failures, catch `OSError`/`ConnectionError` instead. Add explicit `except (KeyboardInterrupt, SystemExit): raise` where appropriate. |

### CDR-005 — `return []` Potentially Hiding Failures

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | Grep scan: ~30 `return []` patterns in `src/` |
| Summary | Empty-list returns in exception handlers can silently hide failures, making it appear that a query returned no results when it actually crashed. The "No Silent Degradation" rule requires that failures be distinguishable from empty results. |
| Distribution | `alert_history_store.py` (13), `auth/session_store.py` (1), `auto_tuning_policy.py` (3), `health.py` (1) |
| Resolution | Where `return []` follows an exception, either: (1) log the failure, (2) return a degraded status instead of an empty list, or (3) raise the exception. For query methods where "no results" is a valid outcome, the empty list is correct — but the code path that produced it should be distinguishable in logs. |

### CDR-006 — Layer Violation: Orchestration Imports Adapter (21+ function-local imports)

| Field | Value |
|---|---|
| Severity | **HIGH** (governance finding) |
| Discovery | Grep scan + AIP_GOVERNANCE.md finding |
| Updated | 2026-06-11 (Chunk 6: partial fix — route violations eliminated) |
| Summary | The orchestration layer imports concrete adapter implementations through function-local imports in 11 files. This tensions with the declared layer dependency direction (orchestration should not import adapter) and is documented as an AIP-G-06/07 open finding in the governance contract. The function-local pattern prevents import-time coupling but does not prevent runtime coupling. Chunk 6 eliminated all adapter→orchestration route violations and the foundation→orchestration backward-compat shim. |
| Files | `ask_pipeline.py` (10 imports), `review_export_pipeline.py` (5 imports), `artifact_lifecycle.py` (4 imports), `ingestion/corpus_ingest_pipeline.py` (2 imports), `ingestion/pipeline.py` (5 imports), `embed_providers.py` (7 imports including importlib), `codex/librarian.py` (5 imports), `channels/graph_channel.py` (1 import), `actors/vigil.py` (2 imports), `actors/beast.py` (2 imports), `actors/sexton.py` (3 imports), `model_provider_proxy.py` (1 importlib import) |
| Resolution | Relocate concrete wiring to composition root (app.py or dependencies.py) so orchestration sees only Protocols. All violations are tracked in the governance suite's `acknowledged_import_violations` list (38 entries, reconciled in Chunk 6). New unacknowledged violations will cause the import-boundary test to fail. |
| Chunk 6 status | **PARTIAL** — route violations fixed; orchestration→adapter violations remain acknowledged debt |

### CDR-007 — `admin.py:308` Blocking sqlite3 in Async Path

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | Already documented as DEBT-007 |
| Summary | The admin route at `src/aip/adapter/api/routes/admin.py:308` uses a blocking `sqlite3.connect()` inside the async FastAPI event loop. All other adapter-layer SQLite stores have been migrated to aiosqlite. This is the last remaining async-path blocking call. |
| Resolution | Convert to use existing store methods (entity_store, event_store, etc.) or add a dedicated admin query method. |

### CDR-008 — `cli/session.py` — Two TODOs for Unwired APIs

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | Grep scan: 2 TODOs in session.py |
| Summary | `cli/session.py` has two TODOs: (1) line 39: "Wire through SessionManager API once available", (2) line 76: "Not yet implemented — requires SessionManager API wiring". The `resume` command is non-functional. |
| Resolution | Wire CLI session commands to the SessionManager API when it becomes available. |

### CDR-009 — `cli/config.py` and `cli/project.py` — AutonomyGate Write Approval TODOs

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | Grep scan: 2 TODOs (config.py:137, project.py:80) |
| Summary | Both CLI commands have TODOs for wiring through AutonomyGate for admin-level write approval. Currently, CLI writes bypass the autonomy gate. |
| Resolution | Wire CLI write paths through AutonomyGate, consistent with the API surface. |

### CDR-010 — `_AlwaysApproveDialogNode` removed; `_ReviewGateNode` defaults to AUTO_APPROVE_STUB

| Field | Value |
|---|---|
| Severity | **MEDIUM** (non-live workflow/governance debt) |
| Original classification | **CRITICAL** (governance violation) — now superseded |
| Discovery | From implementation_status.md (line 97) |
| Updated | 2026-06-10 (Chunk 1.5 triage corrected classification) |
| Summary | `_AlwaysApproveDialogNode` was **removed** in commit `f66e00b`. Its replacement (`_ReviewGateNode`) exists in `workflow_01.py` but `workflow_01` itself is **not wired into app.py, CLI, or any runtime path**. The residual risk is that `_ReviewGateNode` defaults to `AUTO_APPROVE_STUB` mode (lines 262 and 518), which auto-approves when validation + eval both pass with real data. This could bypass DEFINER review if workflow_01 is ever wired into production without changing the default to MANUAL. |
| Classification | **Non-live workflow/governance debt** — the workflow is only exercisable via `examples/run_workflow_01.py` or tests. The AUTO_APPROVE_STUB default is a latent risk that should be changed before workflow_01 is wired. |
| Resolution | (1) Change `_ReviewGateNode` default from `AUTO_APPROVE_STUB` to `MANUAL` or make it config-driven. (2) Update `implementation_status.md:97` to remove stale "DANGEROUS: `_AlwaysApproveDialogNode`" reference. (3) Update this register to reflect resolved state of the original CDR-010 finding. |
| Chunk 2 action | Change default mode; add test; update implementation_status.md |
| Chunk 2 status | **DONE** — default changed to MANUAL; tests pass; implementation_status.md updated |

### CDR-011 — ECS State Lost on Process Restart

| Field | Value |
|---|---|
| Severity | HIGH |
| Discovery | From implementation_status.md (line 129, 481) |
| Summary | `ecs_store_guardrailed.py` uses an in-memory cache with no persistence. `current_state()` never queries the underlying store. All ECS state is lost on restart. However, `PersistentEcsStore` exists and is wired into the application — the guardrailed store may be a secondary wrapper. |
| Resolution | Verify that `PersistentEcsStore` is the primary ECS store in the running application. If `EcsStoreGuardrailed` is still used for any critical path, add persistence. |

### CDR-012 — Old Sexton Event Store Write Signature Mismatch

| Field | Value |
|---|---|
| Severity | HIGH |
| Discovery | From implementation_status.md (line 478-479) |
| Summary | `sexton/sexton.py` (the old, currently-wired Sexton) writes events with wrong signatures — passes dicts instead of kwargs to `write_event()`. This will crash at runtime with a real EventStore. |
| Resolution | Fix event write call signatures in the old Sexton. This is a latent bug that will manifest when the EventStore is fully functional. |

### CDR-013 — health.py Embedding Status Always Returns "healthy"

| Field | Value |
|---|---|
| Severity | HIGH |
| Discovery | From implementation_status.md (line 419-420, 371-372) |
| Summary | `health.py` always returns `{"status": "healthy"}` for embedding status with model name "nomic-embed-text:v1.5" without checking if Ollama is running. This violates AIP-G-02 (No Fake Success). |
| Resolution | Implement real embedding health check that probes the configured embedding provider. |

### CDR-014 — Adaptive Router Is Not Adaptive

| Field | Value |
|---|---|
| Severity | HIGH |
| Discovery | From implementation_status.md (line 422-424) and code inspection |
| Summary | `orchestration/router.py` is labeled "adaptive" but `update_weights()` is fully implemented but never called (dead code — 60 lines of recency-weighted exponential decay logic). `recommend_exploration_weight()` uses hardcoded `count=5`. Exploration/exploitation is `random.random() < 0.10`. Budget enforcement IS real. Loop 5 is dormant not for lack of implementation but for lack of invocation. |
| Resolution | Either implement real adaptation (using routing_outcomes from trace_store) or rename to `StaticRouter` and remove the "adaptive" label to prevent confusion. |

### CDR-015 — InMemoryBudgetStore.check_limit() Always Returns True

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | From implementation_status.md (line 153) |
| Summary | `InMemoryBudgetStore.check_limit()` always returns True, meaning budget limits are never actually enforced when using the in-memory store. The SQLite BudgetStore does enforce limits, but if the in-memory store is used in any path, budget enforcement is bypassed. |
| Resolution | Fix `check_limit()` to actually check accumulated spend against limits. Verify that production paths always use the SQLite store. |

### CDR-016 — Vigil on_model_slot_change Does Not Trigger Re-evaluation

| Field | Value |
|---|---|
| Severity | MEDIUM |
| Discovery | From implementation_status.md (line 262-263) |
| Summary | `vigil.py::on_model_slot_change()` only writes a trace event — it does not trigger re-evaluation of canonicals that were generated with the previous model. The code exists to mark canonicals for re-evaluation but the full re-evaluation loop is not wired. |
| Resolution | Wire the re-evaluation trigger: when model slot changes, queue affected canonicals for re-evaluation in the next Vigil cycle. |

---

## Pass/Fail Table — Known Audit Issues

This table catalogs all known issues from the codebase audits (Claude initial assessment, Grok
review, hardening scans) and their current pass/fail status against the hardening rules.

| # | Issue | Source | Severity | Status | Classification |
|---|---|---|---|---|---|
| 1 | DEBT-006: Sexton not wired | TECH_DEBT.md | CRITICAL | **FAIL** | Active debt — highest priority |
| 2 | DDR-008/DEBT-003: MCP real dispatch, not wired, autonomy_gate=None | Doc discrepancy + code debt | HIGH (non-live) | **FAIL** (latent fail-open) | Chunk 2: harden fail-closed |
| 3 | CDR-010: _AlwaysApproveDialogNode removed; AUTO_APPROVE_STUB default | impl_status.md | MEDIUM (non-live) | **PARTIAL** (class removed, default risk remains) | Chunk 2: change default |
| 4 | CDR-003: 21 `except Exception: pass` | Grep scan | HIGH | **FAIL** | Silent degradation |
| 5 | CDR-006: Layer violations (21 imports) | Governance finding | HIGH | **FAIL** | Acknowledged debt |
| 6 | CDR-012: Sexton event write signature mismatch | impl_status.md | HIGH | **FAIL** | Latent runtime crash |
| 7 | CDR-013: Embedding health always "healthy" | impl_status.md | HIGH | **FAIL** | Fake success (AIP-G-02) |
| 8 | CDR-014: Router not adaptive | impl_status.md | HIGH | **FAIL** | Honestly disclosed scaffold |
| 9 | CDR-011: ECS state lost on restart | impl_status.md | HIGH | **PARTIAL** | PersistentEcsStore exists |
| 10 | DEBT-003: MCP tool dispatch (real, not wired, fail-open) | TECH_DEBT.md | HIGH (non-live) | **FAIL** (latent fail-open) | Chunk 2: harden |
| 11 | DDR-001: README "vector not built" | Doc audit | HIGH | **FAIL** | Doc stale |
| 12 | DDR-002: README "knowledge graph not built" | Doc audit | HIGH | **FAIL** | Doc stale |
| 13 | CDR-001: 300+ Sprint-number comments | Grep scan | MEDIUM | **FAIL** | AI fingerprints |
| 14 | CDR-002: 22 Step-number comments | Grep scan | LOW | **FAIL** | AI fingerprints |
| 15 | CDR-004: ~170 broad `except Exception:` | Grep scan | MEDIUM | **FAIL** | Error handling debt |
| 16 | CDR-005: `return []` hiding failures | Grep scan | MEDIUM | **FAIL** | Silent degradation |
| 17 | CDR-007: admin.py blocking sqlite3 | DEBT-007 | MEDIUM | **FAIL** | Async-path violation |
| 18 | CDR-008: CLI session TODOs | Grep scan | MEDIUM | **FAIL** | Unwired commands |
| 19 | CDR-009: CLI AutonomyGate TODOs | Grep scan | MEDIUM | **FAIL** | Governance gap |
| 20 | CDR-015: InMemoryBudgetStore bypass | impl_status.md | MEDIUM | **FAIL** | Budget enforcement gap |
| 21 | CDR-016: Vigil re-evaluation not triggered | impl_status.md | MEDIUM | **FAIL** | Incomplete wiring |
| 22 | DEBT-001: Graph node alias | TECH_DEBT.md | LOW | **DEFERRED** | Minimal blast radius |
| 23 | DEBT-002: PPR expansion in chat | TECH_DEBT.md | MEDIUM | **DEFERRED** | Latency constraint |
| 24 | DEBT-004: GraphStore connection churn | TECH_DEBT.md | N/A | **RESOLVED** | Fixed in Chunk 4 |
| 25 | DEBT-005: GraphStore Protocol + sync | TECH_DEBT.md | N/A | **RESOLVED** | Fixed in Chunk 4 |
| 26 | DEBT-007: CLI blocking sqlite3 | TECH_DEBT.md | MEDIUM | **ACCEPTED** | CLI is sync context |
| 27 | DDR-003: DEFINER profile "Not built" | Doc audit | MEDIUM | **INVESTIGATE** | Partial implementation |
| 28 | DDR-004: Build spec arch rev 5.2 | Doc audit | MEDIUM | **ACCEPTED** | Historical docs |
| 29 | DDR-005: Dual step numbering | Doc audit | LOW | **DEFERRED** | Cosmetic |
| 30 | DDR-006: Scaffolding % may understate | Doc audit | MEDIUM | **INVESTIGATE** | Metric definition |
| 31 | DDR-007: Test failures not named | Doc audit | MEDIUM | **FAIL** | Incomplete disclosure |
| 32 | DDR-009: Ingest command confusion | Doc audit | LOW | **DEFERRED** | Cosmetic |
| 33 | DDR-010: Split TECH_DEBT.md | Doc audit | MEDIUM | **FAIL** | Confusing structure |
| 34 | DDR-011: ADR count in README | Doc audit | LOW | **FAIL** | Stale reference |

**Summary:**
- **CRITICAL FAIL:** 1 item (DEBT-006 Sexton not wired)
- **HIGH FAIL:** 8 items (MCP fail-open, silent exceptions, layer violations, fake health, stale docs)
- **MEDIUM FAIL:** 11 items (broad exceptions, return [], TODOs, doc splits, test disclosure, AUTO_APPROVE_STUB default)
- **LOW FAIL:** 4 items (step comments, command confusion, ADR count, dual numbering)
- **DEFERRED:** 3 items (graph alias, PPR, step numbering)
- **RESOLVED:** 2 items (DEBT-004, DEBT-005)
- **ACCEPTED:** 2 items (CLI blocking sqlite3, build spec versions)
- **INVESTIGATE:** 2 items (definer profile, scaffolding %)

**Chunk 2 reclassifications:**
- DDR-008 / DEBT-003: Reclassified from CRITICAL to HIGH (non-live) — MCP performs real mutations but is not runtime-wired
- CDR-010: Reclassified from CRITICAL to MEDIUM (non-live) — `_AlwaysApproveDialogNode` removed; residual AUTO_APPROVE_STUB default risk
