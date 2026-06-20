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

## Immediate Next — GUI Phase (no blockers)

Ordered by dependency:

  [Brain] 1. ADR-014 A1 implementation: Extension UI sidebar visibility
             (ui.timer + ui.refreshable + KNOWN_EXTENSIONS config in
             config/aip.config.toml [extensions] section)
  [Brain] 2. Three-panel shell restructure (left drawer + right drawer + main chat)
  [Brain] 3. + menu (Upload PDF, Upload Image, Voice mode, Chat settings,
             extension items below divider)
  [Brain] 4. Extension mode shift (header accent, mode label, sidebar open/close)
  [Arst]  5. ARISTOTLE stats page (/aristotle/stats — mastery, misconceptions, patterns)
  [Arst]  6. ARISTOTLE learning map (/aristotle/map — concept graph, progress)
  [Arst]  7. ARISTOTLE settings page (/aristotle/settings — preferences)
  [Arst]  8. Right panel: mastery state + concept progress (collapsible)
  [Arst]  9. OCR path via pytesseract (pypdf fix already done — DEBT-012 resolved)
  [Arst] 10. Voice mode toggle (Web Speech API — contributed via Brain core + menu)
  [Arst] 11. Teacher dashboard (Komal's interface — revised per UI_CONVENTIONS.md)

**Note:** The pypdf import fix listed as step 9 in the user's prompt is already
done (DEBT-012 resolved at commit 48aea1a). OCR is NOT blocked by a pypdf fix —
it is only blocked on the OCR implementation itself (pytesseract pipeline).

See `docs/UI_CONVENTIONS.md` for the governing UI conventions document.
See `PLANNED_FEATURES.md` → "GUI Phase — Core Shell Features" for the feature spec.

---

## Blocked (do not schedule until unblocked)

- **HERALD Phase C (ARISTOTLE):** blocked on Brain web/feed layer (ADR-014 §3.4 — not started)
- **Web-search material sourcing:** same block as HERALD
- **MCP transport (stdio/SSE):** deferred — not on current roadmap

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

---

## Ongoing / Evergreen

- Keep `PLANNED_FEATURES.md` current (move items from Near-Term to Already Built when shipped)
- Keep `STATUS.md` current after each build session
- Keep `TECH_DEBT.md` current (file new debt, mark resolved debt)
- Write ADRs for each significant architectural decision
- Log every platform-reach as a Phase 0 protocol gap
