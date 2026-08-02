# AIP Brain Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-07-30
# Process: Update this document after each significant build session or architectural decision.
# Release: 1.0.0 (multi-corpus + extension platform + codebase-as-corpus + wiki→manual + web source acquisition)

---

## How to Read This Document

Status indicators:
- ✅ COMPLETE — built, tested, in use
- ⏳ IN PROGRESS — actively being built
- 🔲 PLANNED — decided, not yet started
- 💡 PROPOSED — under consideration, not yet decided
- ❌ DEFERRED — decided to defer, reason noted

Architecture decisions are recorded in `docs/decisions/`. When a decision changes
the roadmap, update both documents.

---

## Current State (verified, not reconstructed)

**Test count:** ~4,870+ tests (4,384 pre-ADR-017 + ~490 web source acquisition tests). All passing. 0 warnings.

**What is built and passing:**

| Feature | Status | Notes |
|---------|--------|-------|
| Multi-corpus architecture (ADR-008) | ✅ | All 9 chunks complete. CorpusRegistry, migration runner, retrieval scoping, graph bridge edges, code corpus ingest. |
| Extension platform (ADR-014 steps 0–6) | ✅ | ExtensionHost lifecycle, entry-point discovery, Actor Protocol, WorkflowEngine, `/health/extensions`, GUI mount (stage 4), import boundary test. |
| ARISTOTLE integration | ✅ | Entry-point discovery, router mount in lifespan (DEBT-014), CLI URL fix (DEBT-009). |
| ActorResult.data field | ✅ | Added `data: Any = None` to ActorResult (DEFINER decision ADR-002 §16 #4). |
| Model slot resolver CI fixture | ✅ | Evaluation slot returns JSON with diagnosis field. ARISTOTLE-DEBT-010 resolved. |
| ExtensionHost test fixture teardown | ✅ | DEBT-013 resolved — 0 warnings in platform suite. |
| pypdf import fix | ✅ | DEBT-012 resolved — `from pypdf import PdfReader` (not PyPDF2). |
| Fusion pipeline | ✅ | Retrieval bridge, Judge/Synth split, per-model compression, provenance widget. |
| ADR-014 Amendment A1 | ✅ | Extension UI visibility via known-list health polling (docs-only — implementation is in the GUI phase below). |
| Actor `start_policy` | ✅ | `scheduled` and `manual_only` modes built and tested (DEBT-020 resolved). |
| **Web Source Acquisition (ADR-017)** | **✅** | **D2.0–D2.5 delivered (2026-07-30). Tavily search, bounded HTTP fetcher with SSRF defense, HTML/PDF extractors, prompt-injection boundary, explicit corpus promotion, evaluation suite. 5 API routes + Ask web_grounding toggle. ~490 tests.** |
| **Multi-Cast retrieval telemetry** | **✅** | **ModelCouncilResponse carries retrieval_attempted, context_assembled, active_corpus_ids, source_count, augmented_sources, retrieval_warnings. GUI renders sources + warnings on per-model cards.** |
| **Corpus selection persistence** | **✅** | **GuiState.active_corpus_ids survives reset_session(); ensure_session() re-applies to replacement sessions. FTS5 sanitize fix for file paths with '/'.** |
| Remote ingress/messaging (ADR-018) | 💡 | PROPOSED — Telegram long-polling adapter, transport-neutral envelope. |
| Evaluation Runs (ADR-016) | 💡 | PROPOSED — Ringer-class function, task-specific model qualification. WS-6 validators are standalone (not yet integrated). |
| Full AgentRun/CapabilityGate/Fleet Coordinator | 🔲 | Still not implemented as a complete fleet runtime. |

---

## Dogfood Phase D2 — Web Source Acquisition (ADR-017) — ✅ COMPLETE

All six delivery slices shipped to `feat/multi-corpus` (2026-07-30):

| Slice | Deliverable | Status |
|-------|------------|--------|
| D2.0 (WS-1) | Schemas, protocols, fake provider, SSRF policy | ✅ |
| D2.1 (WS-2) | Bounded HTTP fetcher, HTML/PDF extractors, provenance | ✅ |
| D2.2 (WS-3) | Tavily provider + API routes + health + lifespan wiring | ✅ |
| D2.3 (WS-4) | Ask web_grounding toggle + WebSourceContextBlock + sources kind discriminator | ✅ |
| D2.4 (WS-5) | Explicit source promotion + dedup by content_hash | ✅ |
| D2.5 (WS-6) | Web-grounding Evaluation Suite (5 validators, 16 cases) | ✅ |

See `docs/decisions/ADR-017-web-source-acquisition.md` for the full delivery summary.

---

## Dogfood Phase D0 — Truth Baseline — ✅ COMPLETE

Baseline established. Issue #3 (process hang) has a minimal lifecycle contract
(BackgroundTaskRegistry) in place; the full W5 (AlertManager threading.Timer
removal, CI timeout-workaround removal) is tracked in TECH_DEBT.md.

---

## Blocked

- HERALD Phase C: Brain web/feed layer — **no longer blocked on web search**
  (ADR-017 D2 is delivered). HERALD can now consume the web source acquisition
  platform. Still blocked on full Fleet Coordinator (ADR-015 Phase 3A-0).
- Evaluation Runs (ADR-016): WS-6 validators are standalone; full integration
  with EvaluationRun/Candidate/Scorecard infrastructure requires W9.

---

## Deferred (conscious decisions, not forgotten)

- Self-registration protocol (ADR-014 Amendment A1 — deferred until third-party extensions)
- Desktop shell migration (NiceGUI → PyWebView → Tauri)
- Loom as extension → see ADR-015 (Phase 3B+, fleet extension)
- CodeForge as extension → see ADR-015 (Phase 3B+, parallel_safe: false)
- Praxis, Chronicle → see ADR-015 (fleet extensions, Phase 3B+)
- Agent Studio, Company Brain, Federation, Astra → not yet specced
- Third-party extension support
- Multi-tenant / enterprise features
- Per-exception HTTP handlers (nice-to-have)
- MCP tools (ADR-014 step 7, v1.2 — not needed for ARISTOTLE)

---

## Fleet Phases (ADR-015 — ACCEPTED, spec only)

> **⚠️ SPEC ONLY — ZERO FLEET CODE TODAY (as of 2026-07-23)**
>
> ADR-015 was accepted on 2026-06-20 as the architectural contract for the
> professional agent fleet. **None of the primitives below exist as code yet:**
> no `AgentRun` table, no `CapabilityGate`, no `FleetCoordinator`, no
> `DispatchPlan`, no `fleet_cost_ledger`, no `start_policy` manifest field,
> no `parallel_safe` manifest field, no trajectory corpus, no CURATOR actor.
> The only extension on the platform is ARISTOTLE (in a separate repo) —
> none of HERALD, LOOM, CodeForge, Praxis, Chronicle, Oracle, Studio have
> any Python code, manifest, or entry point.
>
> The phases below are the **target sequence** for when fleet work begins.
> Phase 3A-0 is the prerequisite for any write-capable extension actor
> (DEBT-020 — cadence=0 startup hazard — must be fixed first).

| Phase | Trigger | Work | Status |
|-------|---------|------|--------|
| **3A-0** | Before 2nd extension | AgentRun table + schema. `start_policy` manifest field. Fail-closed CapabilityGate. Fix cadence=0 startup (DEBT-020). MCP scaffold wiring. | 🔲 Planned (zero code) |
| **3A-1** | 3A-0 complete | HERALD as first domain extension. Read-mostly (no write tools). Validates manifest discipline + corpus isolation + actor registration at fleet scale. | 🔲 Planned (zero code) |
| **3A-2** | HERALD stable | Dry-run mode. Tiered auto-approve config. Fleet Coordinator prototype (intent classification + DispatchPlan + cost estimation). | 🔲 Planned (zero code) |
| **3B** | 2 domain agents live | Full Fleet Coordinator. Fleet Synthesizer as separate agent. Cost ledger. Daily dashboard. Budget hard stop. | 🔲 Planned (zero code) |
| **3C** | 10+ completed dispatches | Trajectory corpus with temporal bounds. CURATOR v1. Forgetting policy. State-conditioned retrieval. Close Loop 5 (DEBT-022 — wire AdaptiveRouter + update_weights call site). | 🔲 Planned (zero code) |
| **3D** | Trajectory memory stable | Full MCP/tool integration behind CapabilityGate. Workspace sandboxing. autonomy_gate closure (DEBT-021). | 🔲 Planned (zero code) |
| **4** | Fleet stable at 5+ agents | PublicAgentCard exporter. A2A Task mapping. External federation readiness. | 🔲 Planned (zero code) |

Architectural contract: `docs/decisions/ADR-015-professional-agent-fleet.md`

---

## Version History

| Date | Change | Author |
|------|--------|--------|
| 2026-06-04 | Phase 1 corpus work reflected | Claude + Moses |
| 2026-06-10 | Sprint 6.4 completion; maintenance mode | Claude + Moses |
| 2026-06-10 | Alpha test release; documentation refresh | Claude + Moses |
| 2026-06-17 | Phase 6: Fusion pipeline complete; Phase 1.6 proposed; DEBT-006 reference fixed | Super Z |
| 2026-06-18 | ADR-008 Multi-Corpus Chunks 1–9 complete (all chunks). 43+55+29+12+21+30+17+24+19 tests across the sequence. Phase 1.5 marked COMPLETE. | GLM (Coding Agent) |
| 2026-06-18 | **Phase 0 Extension Platform (ADR-014) complete.** ExtensionHost lifecycle, entry-point discovery, Actor Protocol, WorkflowEngine wired, `/health/extensions` endpoint, import boundary test. ARISTOTLE extracted to separate repo. Chunk 3 wiring verified LIVE. | Super Z (main) |
| 2026-06-19 | DEBT-013 (coroutine warning) resolved — platform test suite at 0 warnings. DEBT-014 (extension router mount) resolved. DEBT-009 (CLI URL) resolved. ActorResult.data field added (DEFINER decision ADR-002 §16 #4). Model slot resolver CI fixture extended with diagnosis field. | Super Z (main) |
| 2026-06-20 | ADR-014 Amendment A1 accepted (extension UI visibility via known-list health polling). UI_CONVENTIONS.md created. GUI Phase section added to PLANNED_FEATURES.md. Roadmap rewritten to reflect current state + GUI phase as immediate next. | Super Z (main) |
| 2026-06-20 | ADR-014 status updated to ACCEPTED. UI_CONVENTIONS.md expanded with ASCII shell diagram, full extension right-panel reference map, chat bar migration rules, + menu spec. PLANNED_FEATURES GUI section updated with 6 items. ROADMAP updated with 12-item GUI sprint plan + blocked/deferred sections. | Claude + Moses |
| 2026-07-23 | QW15 — Fleet Phases section: header updated from "when accepted" to "ACCEPTED, spec only"; added prominent "SPEC ONLY — ZERO FLEET CODE TODAY" banner listing exactly which primitives don't exist (AgentRun, CapabilityGate, FleetCoordinator, DispatchPlan, fleet_cost_ledger, start_policy, parallel_safe, trajectory corpus, CURATOR); all 7 phases marked "🔲 Planned (zero code)" instead of just "🔲 Planned". Closes the gap between ADR-015 ambition and current reality (R10 from tech-debt assessment). | Super Z (assessment agent) |

---

## Ongoing / Evergreen

- Keep `PLANNED_FEATURES.md` current (move items from Near-Term to Already Built when shipped)
- Keep `STATUS.md` current after each build session
- Keep `TECH_DEBT.md` current (file new debt, mark resolved debt)
- Write ADRs for each significant architectural decision
- Log every platform-reach as a Phase 0 protocol gap
