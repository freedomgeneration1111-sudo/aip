# ADR-011: Actor Role Boundaries — Beast, Sexton, Vigil

**Date:** 2026-06-06
**Status:** ACCEPTED — supersedes current implementation
**DEFINER:** B. Moses Jorgensen
**Supersedes:** Implicit role assignments in Phase 1-3 implementation

---

## Context

The three background actors (Beast, Sexton, Vigil) were implemented with
overlapping and incorrectly assigned responsibilities. Specifically:

- Beast was assigned corpus turn tagging — a maintenance function
- Sexton was given only failure classification — a fraction of its intended scope
- Vigil became a separate quality evaluation actor rather than the name
  for the Sexton's recurring maintenance cycle

This drift accumulated across many build sessions without a canonical
role definition document. The result: Beast is doing Sexton's job,
Sexton is doing a fraction of its job, and Vigil's identity is confused.

The April 2026 architecture session established clear role boundaries
that were not transferred into ADR form before implementation began.
This ADR captures those boundaries as the authoritative specification.

---

## Decision

### BEAST — Active Synthesis Support Actor

Beast is the **active intelligence layer**. It operates in the foreground
of the DEFINER's work sessions, not as a background scheduler.

**Responsibilities:**
- Context advisory assembly for augmented chat (retrieving relevant turns,
  building the system prompt stack, injecting domain wiki overview)
- Real-time domain detection on incoming queries
- Proposing domain bridge connections when noticed during active sessions
- Responding to DEFINER requests for corpus analysis during a session
- Wiki article drafting when explicitly triggered by DEFINER or threshold

**What Beast does NOT do:**
- Scheduled background tagging passes (→ Sexton)
- Scheduled wiki generation cycles (→ Sexton)
- Scheduled graph building (→ Sexton)
- Scheduled embedding passes (→ Sexton)
- Failure classification (→ Sexton)
- Quality evaluation of outputs (→ Vigil)

**Model slot:** Frontier or near-frontier. Beast needs reasoning capability
because it assembles context under time pressure and makes relevance
judgments. Current: `meta-llama/llama-4-maverick` or equivalent.

**Cadence:** On-demand, not scheduled. Beast fires when the DEFINER
sends a message in augmented mode. It may also run a brief heartbeat
cycle (60s) to check for urgent conditions, but does no heavy work
in that heartbeat.

---

### SEXTON — Background Maintenance Actor (The Vigil Cycle)

Sexton is the **maintenance agent**. Named for the church sexton who
maintains the building — keeps it clean, current, and ready for when
the operator arrives. The Sexton doesn't preach. The Sexton prepares.

**Vigil** is the name for the Sexton's recurring maintenance cycle.
"The Sexton ran its vigil at 03:30." There is no separate Vigil actor —
Vigil is what the Sexton does on a schedule.

**Sexton Operational Vocabulary:**
- **Scan** — Check corpus_turns for untagged or stale-tagged turns
- **Tag** — Run Beast domain tagging on untagged turns (batch, max 200/cycle)
- **Compile** — Generate or update Beast wiki articles for domains that
  exceed the token threshold (200k new words since last wiki)
- **Classify** — Assign domain coordinates, update importance scores,
  detect bridge connections between domains
- **Embed** — Run embedding pass on unembedded turns (Phase 1.4)
- **Graph** — Extract new entity relationships, update graph edges
  from bridge-tagged turns
- **Lint** — Check consistency across wiki articles and corpus
- **Decay** — Demote stale importance scores over time
- **Observe** — Write maintenance cycle log as a structured artifact

**What Sexton does NOT do:**
- Respond to DEFINER queries (→ Beast)
- Evaluate synthesis quality (→ Vigil)
- Make autonomous decisions beyond its defined operations
- Auto-approve any artifact (DEFINER gate always applies)

**Model slot:** Mid-tier free model. Sexton's operations are structured
and don't require frontier reasoning — they are batch classification,
summarization, and extraction tasks. Current: `google/gemma-4-31b-it:free`
or equivalent free model. This keeps maintenance cost at zero.

**Cadence:** Scheduled vigil cycle every 300 seconds (5 minutes).
Each cycle runs a subset of operations based on what needs doing:
1. Scan for untagged turns → Tag if found (max 200)
2. Check embedding status → Embed if needed (max 50)
3. Check wiki freshness → Compile if threshold exceeded (max 3 domains)
4. Check graph staleness → Extract new edges if bridge-tagged turns exist
5. Classify any domain proposals pending review
6. Write cycle observation artifact

---

### VIGIL — Quality Evaluation Actor

Vigil is the **quality assurance actor**. It evaluates synthesis outputs,
monitors for quality degradation, and proposes profile amendments when
it notices systematic patterns.

**Responsibilities:**
- Evaluate augmented chat responses for source citation quality
- Flag responses that make claims not supported by retrieved sources
- Propose DEFINER profile amendments when synthesis patterns drift
- Monitor Beast wiki articles for factual consistency
- Generate quality evaluation reports as reviewable artifacts

**What Vigil does NOT do:**
- Background maintenance (→ Sexton)
- Context assembly (→ Beast)
- Any write operations to corpus_turns or artifacts without DEFINER review

**Model slot:** Evaluation-capable model. Needs to read a response and
its sources and judge whether the response faithfully represents the
sources. Current: `openai/gpt-oss-20b:free` or equivalent.

**Cadence:** Hourly. Vigil evaluates the last N synthesis responses
produced since its previous cycle. It does not evaluate in real-time
(that would add latency to every augmented response).

---

## Refactor Required

The current implementation must be updated to match these boundaries:

### 1. Beast refactor
- Remove: scheduled corpus tagging from Beast's run_cycle()
- Remove: scheduled wiki generation from Beast's run_cycle()  
- Keep: context advisory assembly (_build_context_advisory)
- Keep: heartbeat / health check
- Add: on-demand wiki draft trigger (called by CLI or DEFINER action,
  not by scheduler)

### 2. Sexton refactor
- Add: corpus tagging (_run_turn_tagging, max 200/cycle)
- Add: wiki compilation (_run_wiki_generation, max 3 domains/cycle)
- Add: graph extraction (_run_graph_extraction)
- Add: embedding pass (_run_embedding_pass, max 50/cycle)
- Keep: failure classification
- Keep: stale vector detection
- Rename internal: "vigil cycle" → the Sexton's scheduled run

### 3. Vigil refactor
- Clarify: Vigil is a separate actor (not just a cycle name) but its
  responsibilities are quality evaluation, not maintenance
- Remove: any maintenance operations currently in Vigil
- Add: synthesis quality evaluation (reads last N ask_response artifacts)
- Add: profile amendment proposals

### 4. Config / naming
- Remove "vigil_cycle" language from Sexton's scheduler logs
- Sexton logs should say "sexton_cycle_start/complete"
- Vigil logs should say "vigil_eval_start/complete"
- STATUS tab should reflect the correct role labels

---

## Alternatives Considered

**Keep current assignment (Beast does tagging)** — rejected. Beast is
a frontier model. Running 200 tagging operations per cycle on a frontier
model is expensive and architecturally confused. Tagging is a Sexton
function. Beast should only fire on-demand during active synthesis.

**Merge Sexton and Vigil into one actor** — rejected. Quality evaluation
(Vigil) and background maintenance (Sexton) are different cognitive modes.
Mixing them into one actor creates confusion about which model to assign
(frontier for quality evaluation vs. free mid-tier for maintenance) and
makes the scheduler logic more complex.

**Remove the scheduling entirely, CLI-only** — rejected for maintenance
tasks. The corpus grows every time the DEFINER uses the system. Tagging
and embedding need to run automatically or the corpus degrades. CLI-only
is appropriate for explicit DEFINER operations (bulk retag, force wiki
regeneration) but not for routine maintenance.

---

## Consequences

- Sexton becomes the most active background actor — this is correct
- Beast becomes quieter (heartbeat only) until the DEFINER sends a query
- The "beast: no LLM configured, heartbeat only" log message becomes
  expected behavior between sessions, not an error condition
- Sexton requires a model slot capable of classification and summarization
  but NOT frontier reasoning — free models are sufficient
- Wiki generation cost moves from Beast (expensive) to Sexton (free)
- The STATUS tab actor display should show Beast as "idle between sessions"
  not as an always-active maintenance worker

## Related

- ADR-003: Beast Context Advisory
- ADR-008: Semantic Session Context
- ADR-015: Professional Agent Fleet — adds CURATOR as a 4th platform
  actor (trajectory memory curator, every 6h cycle). CURATOR's role
  boundaries are defined in ADR-015 §5.4. This ADR (ADR-011) covers
  Beast/Sexton/Vigil only; CURATOR is NOT a replacement for any of
  them — it is a new concern (trajectory memory curation).
- ROADMAP.md: Phase 3 (Actor refactor) + Fleet Phases (ADR-015)
- src/aip/orchestration/actors/beast.py
- src/aip/orchestration/actors/sexton.py
- src/aip/orchestration/actors/vigil.py (to be properly implemented)
- src/aip/orchestration/actors/curator.py (to be built — ADR-015 §5.4, Phase 3C)
