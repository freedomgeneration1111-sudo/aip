# AIP Brain Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-20
# Process: Update this document after each significant build session or architectural decision.
# Release: 0.1.0-alpha (extension platform + ARISTOTLE dogfood-ready)

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

**Test count:** 60 passed, 1 skipped (platform suite — 6 files: extension lifecycle,
import boundary, actor protocol, extended workflows, workflow engine wiring,
model slot resolver). 0 warnings.

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

---

## GUI Phase — Next Sprint (no blockers)

Ordered by dependency:

1. **pypdf fix** (DEBT-012) — one line, do first, unblocks OCR.
   NOTE: Already done (commit 48aea1a). The file uses `from pypdf import PdfReader`.
   Listed for completeness — skip to #2.
2. **ADR-014 A1 implementation** — KNOWN_EXTENSIONS in TOML +
   ui.timer/ui.refreshable in left sidebar
3. **Three-panel shell restructure** — left/right drawers wired
4. **+ menu** — Upload PDF, Image, Voice, Settings
5. **Extension mode shift** — header accent + mode label
6. **ARISTOTLE stats page** (/aristotle/stats)
7. **ARISTOTLE learning map** (/aristotle/map)
8. **ARISTOTLE settings page** (/aristotle/settings)
9. **ARISTOTLE right panel** — mastery state + concept progress
10. **OCR path** via pytesseract (depends on #1 — already done)
11. **Voice mode toggle** via + menu (Web Speech API, zero-dep)
12. **Teacher dashboard** (/aristotle/teacher — Komal's interface)

See `docs/UI_CONVENTIONS.md` for the governing UI conventions document.
See `PLANNED_FEATURES.md` → "GUI Phase — Brain Core Shell Features" for the feature spec.

---

## Blocked

- HERALD Phase C: Brain web/feed layer (ADR-014 §3.4) not built
- Web-search material sourcing: same block as HERALD
- test_extension_lifecycle regression: discover_installed_packages
  fixture fix needed before GUI work touches ExtensionHost
  (NOTE: tests currently pass — 11 passed, 0 warnings. This item
  may refer to a prior issue that's already resolved. Verify before
  scheduling.)

---

## Deferred (conscious decisions, not forgotten)

- Self-registration protocol (ADR-014 Amendment A1 — deferred until third-party extensions)
- Desktop shell migration (NiceGUI → PyWebView → Tauri)
- Loom as extension
- CodeForge as extension
- Agent Studio, Company Brain, Federation, Praxis, Chronicle, Astra (not yet specced)
- Third-party extension support
- Multi-tenant / enterprise features
- Per-exception HTTP handlers (nice-to-have)
- MCP tools (ADR-014 step 7, v1.2 — not needed for ARISTOTLE)

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

---

## Ongoing / Evergreen

- Keep `PLANNED_FEATURES.md` current (move items from Near-Term to Already Built when shipped)
- Keep `STATUS.md` current after each build session
- Keep `TECH_DEBT.md` current (file new debt, mark resolved debt)
- Write ADRs for each significant architectural decision
- Log every platform-reach as a Phase 0 protocol gap
