# AIP Brain — Global UI Conventions
*Status: ACCEPTED | Date: 2026-06-20*
*Applies to: Brain core + all extensions*

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
Extension-specific context panel. Collapses when not in an active
extension session. Each extension declares its right-panel content
via manifest. Reference map:

| Extension     | Right sidebar content                    |
|---------------|------------------------------------------|
| Aristotle     | Mastery state, concept progress          |
| CodeForge     | Terminal emulator, file tree             |
| Loom          | Document sections, chunk navigator       |
| Praxis        | Selected task detail, resource view      |
| Herald        | Pending approvals, notification stream   |
| Chronicle     | Session timeline, linked decisions       |
| Company Brain | WhatsApp preview, confidence scores      |
| Agent Studio  | Agent status panel, execution trace      |
| Federation    | Node details, sync status                |

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
