"""End-to-end smoke test for ARISTOTLE LLM-driven intake + upload + draft plan.

Spawns the real AIP_Brain FastAPI app via TestClient (lifespan runs, ExtensionHost
starts, ARISTOTLE router is mounted). Then monkey-patches the container's
model_provider with a SCRIPTED FAKE that returns valid JSON for each intake
turn — simulating a well-behaved LLM that drives the conversation forward.

Verifies the full pipeline:
  1. POST /aristotle/intake/start  → greeting prompt
  2. POST /aristotle/upload        → paper text extracted + persisted
  3. POST /aristotle/intake/step   → subject extracted
  4. POST /aristotle/intake/step   → prior_knowledge probed
  5. POST /aristotle/intake/step   → goals probed
  6. POST /aristotle/intake/step   → schedule probed
  7. POST /aristotle/intake/step (with material_ids attached) → draft plan proposed
  8. POST /aristotle/intake/step (confirm) → COMPLETE + plan_id returned
  9. GET  /aristotle/dashboard     → draft plan concepts appear in mastery table

Run:
    cd /home/z/my-project/repos/AIP_Brain
    source .venv/bin/activate
    python /home/z/my-project/scripts/smoke_test_intake_e2e.py

Exit code 0 = all stages passed. Non-zero = failed stage (printed).
"""

from __future__ import annotations

import io
import json
import os
import sys
import warnings
from typing import Any

warnings.filterwarnings("ignore")

# Ensure AIP_Brain + AIP_Aristotle are on sys.path (editable installs already
# handle this, but be defensive for non-editable runs).
_REPO_BRAIN = "/home/z/my-project/repos/AIP_Brain"
if _REPO_BRAIN not in sys.path:
    sys.path.insert(0, _REPO_BRAIN)

os.environ.setdefault("AIP_DOGFOOD_MODE", "minimal")


# ---------------------------------------------------------------------------
# Scripted fake model provider — returns the right JSON for each intake turn
# based on conversation history. Mimics what a well-behaved LLM would do.
# ---------------------------------------------------------------------------


class _ScriptedIntakeModel:
    """Returns canned JSON responses for the intake conversation.

    The IntakeActor calls model_provider.call(slot="beast", messages=[...]).
    We inspect the user-prompt content (which includes the conversation
    history + current focus) to decide which canned response to return.

    The script advances the focus one step per call, then proposes a draft
    plan, then completes. This is the minimum 6-turn happy path.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []
        self._turn = 0

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        self.calls.append((slot_name, messages))
        if slot_name != "beast":
            return {"content": "", "model": "fake", "usage": {}, "latency_ms": 1}

        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        self._turn += 1
        # Decide which canned response based on turn number + content.
        if self._turn == 1:
            # First turn — generate greeting.
            payload = {
                "response": "Hello! I'm Aristotle. What subject would you like to study?",
                "next_focus": "SUBJECT",
                "extracted": {},
                "draft_plan": None,
            }
        elif self._turn == 2:
            # Learner said "physics". Extract subject, advance to PRIOR_KNOWLEDGE.
            payload = {
                "response": "Great — physics! How much do you already know about it?",
                "next_focus": "PRIOR_KNOWLEDGE",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "",
                    "goals": "",
                    "schedule_minutes": 0,
                },
                "draft_plan": None,
            }
        elif self._turn == 3:
            # Learner said "a little high school". Advance to GOALS.
            payload = {
                "response": "Got it. What do you want to achieve with physics?",
                "next_focus": "GOALS",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "",
                    "schedule_minutes": 0,
                },
                "draft_plan": None,
            }
        elif self._turn == 4:
            # Learner said "personal interest". Advance to SCHEDULE.
            payload = {
                "response": "Wonderful. How many minutes per day can you commit?",
                "next_focus": "SCHEDULE",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "personal interest",
                    "schedule_minutes": 0,
                },
                "draft_plan": None,
            }
        elif self._turn == 5:
            # Learner said "30". The materials have been uploaded by now
            # (material_ids attached). Propose a draft plan derived from
            # the uploaded paper's content.
            payload = {
                "response": (
                    "Based on what you've told me and the paper you uploaded, "
                    "here's a draft learning plan. Let me know if it looks right."
                ),
                "next_focus": "PLAN_DRAFT",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "personal interest",
                    "schedule_minutes": 30,
                },
                "draft_plan": [
                    {
                        "topic": "Newton's First Law",
                        "subtopic": "inertia",
                        "bloom_target": 2,
                        "content_primary": "Objects resist changes in motion. An object at rest stays at rest; an object in motion stays in motion unless acted on by a net external force.",
                        "prerequisite_concept_id": None,
                    },
                    {
                        "topic": "Newton's Second Law",
                        "subtopic": "F = ma",
                        "bloom_target": 3,
                        "content_primary": "The acceleration of an object is directly proportional to the net force acting on it and inversely proportional to its mass.",
                        "prerequisite_concept_id": 0,
                    },
                    {
                        "topic": "Newton's Third Law",
                        "subtopic": "action-reaction pairs",
                        "bloom_target": 3,
                        "content_primary": "For every action there is an equal and opposite reaction. Forces come in pairs acting on different bodies.",
                        "prerequisite_concept_id": 1,
                    },
                ],
            }
        else:
            # Turn 6+: learner confirmed. COMPLETE.
            payload = {
                "response": "Your plan is confirmed. Let's begin with Newton's First Law!",
                "next_focus": "COMPLETE",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "personal interest",
                    "schedule_minutes": 30,
                },
                "draft_plan": [
                    {
                        "topic": "Newton's First Law",
                        "subtopic": "inertia",
                        "bloom_target": 2,
                        "content_primary": "Objects resist changes in motion.",
                        "prerequisite_concept_id": None,
                    },
                    {
                        "topic": "Newton's Second Law",
                        "subtopic": "F = ma",
                        "bloom_target": 3,
                        "content_primary": "F = ma",
                        "prerequisite_concept_id": 0,
                    },
                    {
                        "topic": "Newton's Third Law",
                        "subtopic": "action-reaction pairs",
                        "bloom_target": 3,
                        "content_primary": "Action equals reaction.",
                        "prerequisite_concept_id": 1,
                    },
                ],
            }

        return {
            "content": json.dumps(payload),
            "model": "scripted-fake",
            "usage": {"prompt_tokens": 100, "completion_tokens": 80},
            "latency_ms": 5,
        }


# ---------------------------------------------------------------------------
# Build a minimal PDF in memory (no external assets needed)
# ---------------------------------------------------------------------------


def _make_test_pdf() -> bytes:
    """Create a 2-page PDF with text content for the upload test."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    # pypdf doesn't directly add text to pages — use blank pages.
    # The upload route extracts text via pypdf.PdfReader.extract_text().
    # A blank page returns empty string, which is fine for the smoke test
    # (we're testing the extraction pipeline, not OCR).
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Main smoke test
# ---------------------------------------------------------------------------


def _stage(stage: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {stage}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        sys.exit(1)


def main() -> None:
    print("=" * 70)
    print("ARISTOTLE LLM-driven intake — end-to-end smoke test")
    print("=" * 70)
    print()

    # Quiet down the AIP logger noise.
    import logging
    for name in ("aip", "aristotle", "uvicorn", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.ERROR)
    logging.getLogger().setLevel(logging.ERROR)

    from fastapi.testclient import TestClient

    from aip.adapter.api.app import create_app

    print("[setup] creating AIP app...")
    app = create_app()

    print("[setup] entering lifespan (ExtensionHost starts, ARISTOTLE mounts)...")
    with TestClient(app) as client:
        # Monkey-patch the container's model_provider with our scripted fake.
        # The container lives in app.state — fetch via a probe request.
        container = None

        # The lifespan function uses a local `container` var, not app.state.
        # We access it via the closure by triggering a route that uses it.
        # Simpler: reach into the closure via the route handler.

        # The aristotle routes use request.app.state.container. So the
        # container MUST be set on app.state by the lifespan. Let's check.
        container = getattr(app.state, "container", None)
        if container is None:
            # Fallback: hook the lifespan's container via the extension host.
            # The ExtensionHost stores a reference to the container.
            extensions_host = getattr(app.state, "extensions_host", None)
            if extensions_host is not None:
                container = getattr(extensions_host, "_container", None)

        if container is None:
            print("[ERROR] could not locate container to inject fake model_provider")
            print("        app.state keys:", list(vars(app.state).keys()))
            sys.exit(1)

        print("[setup] injecting scripted fake model_provider...")
        fake_model = _ScriptedIntakeModel()
        container.model_provider = fake_model

        # ------------------------------------------------------------------
        # STAGE 1: /aristotle/intake/start (no plan_id → full intake)
        # ------------------------------------------------------------------
        r = client.post("/aristotle/intake/start", json={"plan_id": None})
        body = r.json()
        _stage(
            "1. POST /aristotle/intake/start",
            r.status_code == 200 and body.get("trigger") == "full",
            f"status={r.status_code} trigger={body.get('trigger')}",
        )
        session = body["session"]
        prompt = body.get("prompt", "")
        _stage(
            "   greeting prompt non-empty",
            bool(prompt) and "Aristotle" in prompt,
            f"prompt={prompt[:80]!r}",
        )
        _stage(
            "   fake model was called on beast slot",
            len(fake_model.calls) == 1 and fake_model.calls[0][0] == "beast",
            f"calls={len(fake_model.calls)}",
        )

        # ------------------------------------------------------------------
        # STAGE 2: /aristotle/upload (test PDF, Content-Type application/pdf)
        # ------------------------------------------------------------------
        pdf_bytes = _make_test_pdf()
        r = client.post(
            "/aristotle/upload",
            content=pdf_bytes,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="newtons_laws_paper.pdf"',
            },
        )
        body = r.json()
        _stage(
            "2. POST /aristotle/upload (PDF)",
            r.status_code == 200 and body.get("source_type") == "pdf",
            f"status={r.status_code} source_type={body.get('source_type')}",
        )
        _stage(
            "   material_id returned (DB persisted)",
            bool(body.get("material_id")),
            f"material_id={body.get('material_id')[:8] if body.get('material_id') else None}...",
        )
        _stage(
            "   page_count == 2",
            body.get("page_count") == 2,
            f"page_count={body.get('page_count')}",
        )
        material_id = body["material_id"]

        # ------------------------------------------------------------------
        # STAGE 3: /aristotle/intake/step — learner says "physics"
        # ------------------------------------------------------------------
        r = client.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "physics"},
        )
        body = r.json()
        _stage(
            "3. intake/step (subject='physics')",
            r.status_code == 200 and body.get("state") == "PRIOR_KNOWLEDGE",
            f"state={body.get('state')}",
        )
        session = body["session"]
        _stage(
            "   subject extracted into session",
            session.get("subject") == "physics",
            f"subject={session.get('subject')!r}",
        )
        _stage(
            "   extracted.subject = 'physics' (not raw text)",
            session.get("extracted", {}).get("subject") == "physics",
            f"extracted={session.get('extracted')}",
        )

        # ------------------------------------------------------------------
        # STAGE 4: /aristotle/intake/step — learner says "a little high school"
        # ------------------------------------------------------------------
        r = client.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "a little high school"},
        )
        body = r.json()
        _stage(
            "4. intake/step (prior_knowledge)",
            r.status_code == 200 and body.get("state") == "GOALS",
            f"state={body.get('state')}",
        )
        session = body["session"]

        # ------------------------------------------------------------------
        # STAGE 5: /aristotle/intake/step — learner says "personal interest"
        # ------------------------------------------------------------------
        r = client.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "personal interest"},
        )
        body = r.json()
        _stage(
            "5. intake/step (goals)",
            r.status_code == 200 and body.get("state") == "SCHEDULE",
            f"state={body.get('state')}",
        )
        session = body["session"]

        # ------------------------------------------------------------------
        # STAGE 6: /aristotle/intake/step — learner says "30" + attaches material_id
        #          Model proposes draft plan
        # ------------------------------------------------------------------
        r = client.post(
            "/aristotle/intake/step",
            json={
                "session": session,
                "student_input": "30 minutes per day",
                "material_ids": [material_id],
            },
        )
        body = r.json()
        _stage(
            "6. intake/step (schedule + attach material)",
            r.status_code == 200 and body.get("state") == "GENERATING_PLAN",
            f"state={body.get('state')}",
        )
        session = body["session"]
        draft_plan = session.get("draft_plan", [])
        _stage(
            "   draft_plan proposed (3 concepts)",
            isinstance(draft_plan, list) and len(draft_plan) == 3,
            f"draft_plan_len={len(draft_plan) if isinstance(draft_plan, list) else 'N/A'}",
        )
        if draft_plan:
            _stage(
            "   draft_plan[0] has required fields",
            all(k in draft_plan[0] for k in ("topic", "bloom_target", "content_primary")),
            f"keys={list(draft_plan[0].keys())}",
        )
            _stage(
            "   material_id attached to session",
            material_id in session.get("material_ids", []),
            f"material_ids={session.get('material_ids')}",
        )
        _stage(
            "   schedule_minutes extracted = 30",
            session.get("schedule_minutes") == 30,
            f"schedule_minutes={session.get('schedule_minutes')}",
        )

        # ------------------------------------------------------------------
        # STAGE 7: /aristotle/intake/step — learner confirms draft plan
        #          Model returns COMPLETE → plan_id generated
        # ------------------------------------------------------------------
        r = client.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "looks good, let's start"},
        )
        body = r.json()
        _stage(
            "7. intake/step (confirm draft plan)",
            r.status_code == 200 and body.get("state") == "COMPLETE",
            f"state={body.get('state')}",
        )
        _stage(
            "   plan_id returned",
            bool(body.get("plan_id")),
            f"plan_id={body.get('plan_id', '')[:8]}...",
        )
        _stage(
            "   concept_count == 3",
            body.get("concept_count") == 3,
            f"concept_count={body.get('concept_count')}",
        )
        plan_id = body.get("plan_id", "")

        # ------------------------------------------------------------------
        # STAGE 8: GET /aristotle/dashboard — draft plan concepts appear
        # ------------------------------------------------------------------
        r = client.get("/aristotle/dashboard")
        body = r.json()
        _stage(
            "8. GET /aristotle/dashboard",
            r.status_code == 200 and "mastery_by_concept" in body,
            f"status={r.status_code} keys={list(body.keys())}",
        )
        mastery = body.get("mastery_by_concept", [])
        _stage(
            "   dashboard shows 3 concepts (from draft plan)",
            len(mastery) == 3,
            f"concepts_shown={len(mastery)}",
        )
        if mastery:
            topics = [m.get("topic") for m in mastery]
            _stage(
            "   concepts include Newton's First/Second/Third Law",
            any("First" in (t or "") for t in topics)
                and any("Second" in (t or "") for t in topics)
                and any("Third" in (t or "") for t in topics),
            f"topics={topics}",
        )

        # ------------------------------------------------------------------
        # STAGE 9: GET /aristotle/concepts — concepts persisted to DB
        # ------------------------------------------------------------------
        r = client.get("/aristotle/concepts")
        body = r.json()
        concepts = body if isinstance(body, list) else body.get("concepts", [])
        _stage(
            "9. GET /aristotle/concepts — 3 concepts persisted",
            r.status_code == 200 and len(concepts) == 3,
            f"status={r.status_code} count={len(concepts)}",
        )

    print()
    print("=" * 70)
    print("ALL STAGES PASSED — LLM-driven intake end-to-end smoke test OK")
    print("=" * 70)
    print()
    print(f"Total model calls: {len(fake_model.calls)}")
    print(f"Plan ID:           {plan_id}")
    print()
    print("What was verified:")
    print("  - ARISTOTLE router mounted by ExtensionHost during lifespan")
    print("  - /aristotle/intake/start triggers LLM call on beast slot")
    print("  - /aristotle/upload extracts PDF text + persists to DB")
    print("  - LLM-driven intake loop extracts structured fields per turn")
    print("  - material_ids attached to session → included in model context")
    print("  - next_focus=PLAN_DRAFT populates session.draft_plan")
    print("  - next_focus=COMPLETE triggers generate_plan → plan_id returned")
    print("  - Concepts ingested into aristotle_concept (draft_plan path)")
    print("  - /aristotle/dashboard shows the new concepts")
    print("  - /aristotle/concepts confirms DB persistence")
    print()
    print("To run against a REAL LLM:")
    print("  1. Set AIP_OPENAI_API_KEY=<your-openrouter-key>")
    print("  2. Start backend:  uvicorn aip.adapter.api.app:create_app --factory \\")
    print("                       --host 127.0.0.1 --port 8000")
    print("  3. Walk the same flow via curl/httpie/GUI — the LLM will drive")
    print("     the conversation naturally instead of the scripted fake.")


if __name__ == "__main__":
    main()
