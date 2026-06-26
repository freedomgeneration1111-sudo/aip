# ADR-008: Semantic Session Context Architecture

**Date:** 2026-06-05
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

---

## Context

AIP's augmented chat currently retrieves context via FTS5 keyword search
against corpus_turns. This is recency-blind and keyword-dependent. The
system has no conversational memory — each turn is independent. Clicking
away loses the thread. There is no Thread Log.

The DEFINER works across radically different domains in a single day:
theology, NBCM physics, bonded labor policy, educational administration,
AI methodology. A context system that assumes topical continuity fails
this use pattern. A lawyer works across cases; a doctor across patients;
a researcher across simultaneous investigations. AIP must orient by
relevance, not recency.

The current implementation passes no history to the synthesis model.
This is correct but creates a different problem: "expand on that" has no
referent unless the DEFINER re-states context explicitly every turn.

## Decision

Replace recency-based history with **relevance-based context assembly**.
The synthesis model never receives full conversation history. Before each
synthesis call in AUGMENTED mode, a Context Assembly step builds the
optimal system prompt from:

1. **DEFINER profile** (always, ~600 tokens)
2. **Wiki overview** for detected domain (if approved article exists)
3. **Graph neighbor domains** (if graph is built — Phase 2.2)
4. **Semantically relevant corpus turns** (top 8, FTS5+vector RRF,
   min_importance=0.3, any age — recency is not a filter)
5. **Recent session turns** (last 1-2 turns from current session,
   stored in app state, for "expand on that" immediate coherence)

The synthesis model always works with the **best available evidence**,
not just the most recent.

---

## Real-Time Turn Processing Loop

After each synthesis response is returned and displayed:

```
Response shown to DEFINER
    ↓
Background (non-blocking, Beast-handled):
    ↓  Tag this turn (domain, importance, bridges, entities)
    ↓  Embed this turn (vectors.db)
    ↓  Detect new entity connections → update graph edges (Phase 2.2)
    ↓  Update Thread Log row with completed tag
```

Turn becomes searchable within ~30 seconds of response delivery.
DEFINER sees the answer immediately; Beast catches up asynchronously.
The tagging loop is identical for CHAT mode turns and AUGMENTED mode
turns — all turns enter the corpus.

---

## Thread Log

Every turn stored in a persistent, infinite, searchable Thread Log.
Filtered by domain tag, not by session boundary.

**There are no sessions.** There is one continuous corpus of thought.

The DEFINER's mental model: everything said in this system is potentially
in scope for any future query. The context assembler finds it if it's
relevant. Domain filters in the Thread Log let the DEFINER navigate their
own thinking by topic without destroying the connections between topics.

Thread Log behaviors:
- Every turn has: timestamp, detected domain (after tagging), source model,
  importance score, truncated preview
- Filter buttons reduce view to single domain or ALL
- Search box runs FTS5 against user_message and assistant_message
- Clicking a past turn opens it in a reference panel (read-only)
- The reference panel turn surfaces automatically in future context
  assembly if semantically relevant — no explicit "resume" needed
- No session compaction needed — the corpus_turns table IS the Thread Log

---

## Domain Generalization

The same architecture serves:
- **Doctor**: turns tagged by case ID, symptom cluster, treatment domain
- **Lawyer**: turns tagged by case ID, legal doctrine, jurisdiction
- **Researcher**: turns tagged by hypothesis, experimental context, paper
- **DEFINER**: turns tagged by the 28-domain Beast registry

No special configuration needed per user type — Beast's domain registry
drives the orientation. A lawyer using AIP installs a legal domain
registry; a doctor installs a clinical domain registry.

---

## Implementation Notes

**Context Assembly** runs as a pre-synthesis step in
`src/aip/orchestration/context_advisory.py`. Fast model acceptable
(Sexton slot or a lightweight call). Target latency: <500ms so it
feels instant to the DEFINER.

**Session continuity** (last 1-2 turns) stored in NiceGUI app state
as a Python list. Not persisted to database. Cleared on page refresh
or explicit "New topic" action. This is by design — the corpus_turns
table is the permanent record; app state is only for immediate
conversational coherence.

**Embedding pipeline** (Phase 1.4) is required for vector similarity
in context assembly. Until embeddings are built, FTS5 keyword matching
is the fallback. The architecture is the same; the retrieval quality
improves when vectors are added.

---

## Alternatives Considered

**Full history (Option A)** — rejected. Cost grows unboundedly as
conversation lengthens. Model attention dilutes across long histories.
Fails cross-domain work where old turns are irrelevant noise. This is
how claude.ai works but AIP's use pattern is different.

**Last N turns by recency (Option B)** — rejected. Recency is not
relevance. A turn from 6 months ago about NBCM relational time may be
more relevant to a current NBCM query than the last 5 turns which were
about theology. The whole point of the corpus is that nothing is lost
to recency.

**Explicit session resume (Option C, naive hybrid)** — rejected as
primary mechanism. Placing the burden on the DEFINER to explicitly
resume sessions is unnecessary friction. The corpus assembler should
find relevant prior turns automatically.

---

## Consequences

- Context assembly adds ~500ms latency per augmented turn. Acceptable.
- Session continuity (last 1-2 turns) is lost on page refresh. The
  DEFINER must re-establish context if they refresh mid-conversation.
  This is a known tradeoff, noted in STATUS.md.
- Thread Log UI is a significant new component. See Phase 4.1 in ROADMAP.
- Embedding pipeline (Phase 1.4) is required for full semantic retrieval.
  Until then, FTS5 fallback degrades quality but does not break behavior.
- Real-time tagging loop assumes Beast can handle per-turn tagging within
  ~30 seconds. If Beast is overloaded, tags are delayed but not lost.

## Related

- ADR-003: Beast Context Advisory (current augmented chat mechanism,
  this ADR extends it)
- ADR-006: Beast Wiki Architecture (wiki overview injection)
- ADR-007: Knowledge Graph Architecture (graph neighbor injection)
- ADR-009: Cohort Synthesis (turns from cohort responses enter same loop)
- ADR-015: Professional Agent Fleet — introduces `trajectory` as a new
  corpus type (Layer 3, trajectory memory). Platform-level (not
  per-extension). Trust-tiered: raw_trajectory_events → candidate_tips
  → approved_tips → domain_tips → agent_tips. Read: all agents via
  state-conditioned retrieval. Write: CURATOR actor only. See ADR-015
  §5.1 for the trajectory corpus spec.

  **NOTE:** The multi-corpus architecture is governed by ADR-004
  (multi-corpus-architecture). The trajectory corpus type will be
  registered in the corpus type registry when ADR-015 Layer 3 is
  implemented (Phase 3C).
- ROADMAP.md: Phase 3 (Semantic Context Assembler), Phase 4.1 (Thread Log)
- src/aip/orchestration/context_advisory.py
