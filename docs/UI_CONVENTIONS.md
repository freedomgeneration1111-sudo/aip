# AIP Brain — Global UI Conventions
*Status: ACCEPTED (target spec) | Date: 2026-06-20 | Last updated: 2026-07-23*
*Applies to: Brain core + all extensions*

> **⚠️ TARGET SPEC — NOT ALL FEATURES IMPLEMENTED**
>
> This document describes the **target UI shell** for AIP Brain. It is the
> architectural contract that new GUI work should converge toward, not a
> description of the current state. As of 2026-07-23:
>
> - **Three-panel shell**: partially implemented. Left sidebar + main panel
>   are wired. Right sidebar (`build_right_rail` in `gui/components/layout.py`)
>   renders conditionally — only when an extension session is active
>   (`_active_extension` is truthy). It is NOT a global always-on panel.
> - **Extension nav items**: implemented via ADR-014 Amendment A1 known-list
>   health polling (5s interval, `KNOWN_EXTENSIONS` in `config/aip.config.toml`).
> - **+ menu**: NOT implemented. The chat input has no adjacent + button today.
> - **Extension mode shift**: partially implemented (header accent + mode label
>   via `_active_extension`).
> - **Extensions shipped**: only **ARISTOTLE** exists (in a separate repo,
>   `AIP_Aristotle`). The other 8 extensions listed in the Right Sidebar
>   reference map below (CodeForge, Loom, Praxis, Herald, Chronicle,
>   Company Brain, Agent Studio, Federation) are **spec only — zero code**.
>   They are listed here as design targets so future contributors know what
>   the right-sidebar content should be when each extension is built.
>
> Treat this doc as a north star, not a status report. For current state, see
> `STATUS.md` and `ROADMAP.md`. Discovered as doc-drift items D6/D7 in the
> 2026-07-23 tech-debt assessment.

## The Three-Panel Shell

Every AIP view uses the same frame:

```
+----------+--------------------------+-------------+
| LEFT     |  MAIN                    | RIGHT       |
| SIDEBAR  |  (chat / primary view)   | SIDEBAR     |
|          |                          | (collapses) |
| [core]   |                          |             |
| Home     |                          | extension   |
| Search   |  [chat bar][+][mic][>]   | context     |
| Corpus   |                          | panel       |
|          |                          |             |
| [exts]   |                          |             |
+----------+--------------------------+-------------+
```

## Left Sidebar
- Core links always present: Home, Search, Corpus
- Extension nav items appear ONLY when backend health check passes
  (ADR-014 Amendment A1 — known-list polling, 5s interval)
- KNOWN_EXTENSIONS defined in config/aip.config.toml under
  existing [extensions] section
- Each extension contributes <=4 nav links
- No extension name or icon renders until its backend is alive

## Right Sidebar
Extension-specific context panel. Renders conditionally — only when an
extension session is active (`_active_extension` is truthy in
`gui/components/layout.py`). Each extension declares its right-panel
content via manifest. Reference map:

| Extension     | Status      | Right sidebar content                    |
|---------------|-------------|------------------------------------------|
| Aristotle     | **SHIPPED** (separate repo `AIP_Aristotle`) | Mastery state, concept progress          |
| CodeForge     | spec only   | Terminal emulator, file tree             |
| Loom          | spec only   | Document sections, chunk navigator       |
| Praxis        | spec only   | Selected task detail, resource view      |
| Herald        | spec only   | Pending approvals, notification stream   |
| Chronicle     | spec only   | Session timeline, linked decisions       |
| Company Brain | spec only   | WhatsApp preview, confidence scores      |
| Agent Studio  | spec only   | Agent status panel, execution trace      |
| Federation    | spec only   | Node details, sync status                |

"spec only" means the extension is named in ADR-015 or this doc but has
zero Python code, no manifest, and no entry point in this repo or any
known sibling repo as of 2026-07-23.

## The Chat Bar
The main chat input is the universal interaction surface.
It is NEVER removed — only migrated.

Chat-primary extensions (Aristotle, Chronicle, Herald, Astra):
  Chat bar is the main view. Right sidebar is context panel.

Non-chat-primary extensions (Praxis, Loom, CodeForge):
  Primary view is Gantt / document editor / build console.
  Chat bar migrates to a collapsible bottom panel or sidebar strip.
  It remains accessible at all times.

## The + Menu (Brain Core Feature)
Small + button adjacent to chat input. Opens context menu:

  Upload PDF
  Upload Image
  Voice mode
  Chat settings
  --- (divider)
  [extension-registered items below divider]

Extensions register items below the divider via manifest.
Brain core owns the menu structure and items above the divider.

## Extension Mode Shift
When an extension session is active:
  - Subtle header accent color change
  - Mode label appears (e.g., "ARISTOTLE - Tutoring")
  - Left sidebar shows extension nav items
  - Right sidebar opens with extension context panel
On session end: Brain default restored. No full UI repaint.

## ARISTOTLE-Specific Convention
INTAKE interview runs in the main Brain chat — NOT a separate
/intake page. Chat IS the intake surface.

ARISTOTLE registers exactly three pages:
  /aristotle/stats    — mastery, misconception log, patterns
  /aristotle/map      — concept graph, progress visualization
  /aristotle/settings — ARISTOTLE preferences

These appear in the left sidebar only when ARISTOTLE backend is
running. Right panel shows mastery + concept progress during
active sessions; collapses otherwise.

## Convention Applies To All Extensions
Every extension built against this platform inherits this shell.
Deviations require an explicit ADR amendment documenting why.
