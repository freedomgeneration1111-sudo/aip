# UI Conventions — AIP Brain + Extensions

**Date:** 2026-06-20
**Status:** ACCEPTED — governing document for all UI work
**Scope:** Brain core shell + all first-party extensions

---

## Left Sidebar

- Core links always present: Home, Search, Corpus
- Extension nav items appear ONLY when backend health check passes
  (ADR-014 Amendment A1 — known-list polling, 5s interval)
- Each extension contributes ≤4 nav links
- No extension name appears until its backend is running

## Right Sidebar

Extension-specific context panel. Collapses when not in extension
session. Each extension declares its right-panel content:

| Extension     | Right sidebar content                    |
|---------------|------------------------------------------|
| Aristotle     | Mastery state, concept progress          |
| CodeForge     | Terminal emulator, file tree             |
| Loom          | Document sections, chunk navigator       |
| Praxis        | Selected task detail, resource view      |
| Herald        | Pending approvals, notification stream   |
| Chronicle     | Session timeline, linked decisions       |
| Company Brain | WhatsApp preview, confidence scores      |

## The Chat Bar

The main chat input (with + menu) is the universal interaction
surface. It is NEVER removed — only migrated.

- **Chat-primary extensions** (Aristotle, Chronicle, Herald, Astra):
  Chat bar is the main view.
- **Non-chat-primary extensions** (Praxis, Loom, CodeForge):
  Primary view is Gantt / document editor / build console.
  Chat bar migrates to a collapsible bottom panel or sidebar strip.

## The + Menu (Brain Core Feature)

Small + button adjacent to chat input. Opens context menu:

  Upload PDF
  Upload Image
  Voice mode
  Chat settings
  --- (divider)
  [extension-registered items below]

Extensions register additional items via manifest.
Brain core owns the menu; extensions contribute below the divider.

## Extension Mode Shift

When an extension session is active:
- Subtle header accent color change
- Mode label appears (e.g., "ARISTOTLE - Tutoring")
- Left sidebar shows extension nav items
- Right sidebar opens with extension context panel

On session end: returns to Brain default. No full UI repaint.

## ARISTOTLE-Specific Note

The INTAKE interview happens in the main Brain chat — NOT a
separate /intake page. Chat IS the intake surface. ARISTOTLE
registers only three pages:

  Stats (mastery, misconception log, struggle patterns)
  Learning Map (concept graph, progress visualization)
  Settings (ARISTOTLE preferences)

These appear in the left sidebar only when ARISTOTLE is running.
