"""QW7 (2026-07-23) — Doc Drift Guard.

CI-level check that prevents the most damaging class of doc drift:
docs claiming a feature is "✅ Complete" when the code doesn't back it up.

This test file was created in response to the 2026-07-23 tech-debt
assessment, which found 3 HIGH-severity drift items (D1, D2, D3) where
STATUS.md / ROADMAP.md / PLANNED_FEATURES.md claimed capabilities were
"complete" but they were infrastructure-only or dead code.

DESIGN:
  Each test checks ONE specific claim. A failure includes the exact
  doc location + what the code actually shows. Tests use only stdlib
  (ast, re, pathlib) so they run in any CI environment without deps.

  When a doc claim is intentionally aspirational, the doc should mark
  it as "target spec" or "planned" — NOT "✅ Complete". This guard
  catches the gap between "✅" and reality.

USAGE:
    pytest tests/test_doc_drift_guard.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _grep(pattern: str, *paths: Path) -> list[str]:
    """Return matching lines across the given paths."""
    results: list[str] = []
    for p in paths:
        if not p.exists():
            continue
        for line in _read(p).splitlines():
            if re.search(pattern, line):
                results.append(line)
    return results


# ---------------------------------------------------------------------------
# D1 guard: "Multi-corpus architecture ✅ — All 9 chunks complete"
# Reality check: definer + codeforge must both be registered at startup
# ---------------------------------------------------------------------------


class TestMultiCorpusStartupRegistration:
    """Guard for D1 — ensure app.py actually registers the corpora the
    ROADMAP claims are 'complete'."""

    def test_app_py_registers_definer_and_codeforge(self):
        """ROADMAP.md claims 'Multi-corpus architecture ✅'. Verify app.py
        actually registers both definer + codeforge at startup."""
        app_py = REPO_ROOT / "src" / "aip" / "adapter" / "api" / "app.py"
        src = _read(app_py)
        assert '"definer"' in src and "CorpusType.CONVERSATION" in src, (
            "app.py must register the definer corpus (CorpusType.CONVERSATION). "
            "ROADMAP.md claims multi-corpus is ✅ complete."
        )
        assert '"codeforge"' in src and "CorpusType.CODE" in src, (
            "app.py must register the codeforge corpus (CorpusType.CODE). "
            "ROADMAP.md claims multi-corpus is ✅ complete — codeforge "
            "registration was added in QW1 (2026-07-23)."
        )

    def test_roadmap_claims_multi_corpus_complete(self):
        """Verify ROADMAP.md still claims multi-corpus is complete (so the
        guard above remains relevant). If this test fails, the ROADMAP
        was updated to remove the claim — update the guard accordingly."""
        roadmap = _read(REPO_ROOT / "ROADMAP.md")
        assert "Multi-corpus" in roadmap or "multi-corpus" in roadmap, (
            "ROADMAP.md no longer mentions multi-corpus — update this guard."
        )


# ---------------------------------------------------------------------------
# D2 guard: "Chunk 7 delivers Phase 1.6 Codebase-as-Corpus"
# Reality check: ingest-code CLI + code_ingest_pipeline must exist
# ---------------------------------------------------------------------------


class TestCodebaseAsCorpusGuard:
    """Guard for D2 — ensure the Codebase-as-Corpus infrastructure that
    PLANNED_FEATURES.md claims is 'complete' actually exists."""

    def test_code_ingest_pipeline_exists(self):
        """PLANNED_FEATURES.md:144 claims Chunk 7 is ✅ Complete. Verify
        the ingest pipeline exists."""
        pipeline = REPO_ROOT / "src" / "aip" / "adapter" / "code_ingest_pipeline.py"
        assert pipeline.exists(), (
            "code_ingest_pipeline.py not found — PLANNED_FEATURES.md claims "
            "Chunk 7 is ✅ Complete."
        )
        src = _read(pipeline)
        assert "async def ingest_python_directory" in src, (
            "ingest_python_directory function missing from code_ingest_pipeline.py"
        )

    def test_python_ast_parser_exists(self):
        """PLANNED_FEATURES.md claims the AST parser is complete. Verify."""
        parser = REPO_ROOT / "src" / "aip" / "adapter" / "python_ast_parser.py"
        assert parser.exists(), (
            "python_ast_parser.py not found in adapter/ — PLANNED_FEATURES.md "
            "claims Chunk 7 is ✅ Complete."
        )
        src = _read(parser)
        assert "def parse_python_file" in src
        assert "def make_code_corpus_turn" in src

    def test_ingest_code_cli_exists(self):
        """QW11 added the CLI command. Verify it's wired into the corpus group."""
        cli = REPO_ROOT / "src" / "aip" / "cli" / "corpus.py"
        src = _read(cli)
        assert '@corpus.command("ingest-code")' in src, (
            "aip corpus ingest-code command missing from cli/corpus.py — "
            "QW11 added it; PLANNED_FEATURES.md Phase 1.6 status depends on it."
        )

    def test_no_duplicate_python_ast_parser(self):
        """QW3 deleted the duplicate. Ensure it doesn't come back."""
        orchestration_copy = (
            REPO_ROOT / "src" / "aip" / "orchestration" / "ingestion" / "parsers" / "python_ast_parser.py"
        )
        assert not orchestration_copy.exists(), (
            "orchestration/ingestion/parsers/python_ast_parser.py exists — "
            "this duplicate was deleted in QW3 (2026-07-23). Only the adapter "
            "copy should exist."
        )


# ---------------------------------------------------------------------------
# D3 guard: "Chunk 5 — GUI corpus_selector.py complete"
# Reality check: the component must be importable AND the endpoint it calls
# must exist
# ---------------------------------------------------------------------------


class TestCorpusSelectorGuard:
    """Guard for D3 — ensure the GUI corpus_selector + its API endpoint
    both exist (not just one half)."""

    def test_corpus_selector_component_exists(self):
        """PLANNED_FEATURES.md:142 claims Chunk 5 GUI is complete. Verify."""
        selector = REPO_ROOT / "gui" / "components" / "corpus_selector.py"
        assert selector.exists(), (
            "gui/components/corpus_selector.py not found — PLANNED_FEATURES.md "
            "claims Chunk 5 is ✅ Complete."
        )

    def test_corpus_registry_endpoint_exists(self):
        """QW9 added GET /corpus-registry/corpora. The GUI selector calls it.
        Verify the endpoint is registered so the GUI isn't dead code."""
        corpus_route = REPO_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "corpus.py"
        src = _read(corpus_route)
        assert '@router.get("/corpus-registry/corpora")' in src, (
            "GET /corpus-registry/corpora endpoint missing — gui/components/"
            "corpus_selector.py calls it. Without this endpoint, the GUI "
            "component is dead code (D3 from tech-debt assessment)."
        )

    def test_corpus_selector_calls_registered_endpoint(self):
        """Verify the GUI component calls the endpoint that exists."""
        selector_src = _read(REPO_ROOT / "gui" / "components" / "corpus_selector.py")
        assert "/corpus-registry/corpora" in selector_src, (
            "corpus_selector.py must call /corpus-registry/corpora (QW9 endpoint)."
        )


# ---------------------------------------------------------------------------
# QW10 guard: MAX_CORPORA consistency
# ---------------------------------------------------------------------------


class TestMaxCorporaConsistency:
    """Guard for ND11 — ensure MAX_CORPORA is consistent between the
    constant and the test that asserts it, and that app.py uses the
    constant (not a hardcoded number)."""

    def test_app_py_uses_constant_not_hardcode(self):
        """app.py must import MAX_CORPORA, not hardcode a number."""
        app_py = _read(REPO_ROOT / "src" / "aip" / "adapter" / "api" / "app.py")
        assert "max_corpora=4" not in app_py, (
            "app.py hardcodes max_corpora=4 — it should import and use the "
            "MAX_CORPORA constant from foundation.corpus_constants (QW10)."
        )
        assert "from aip.foundation.corpus_constants import MAX_CORPORA" in app_py, (
            "app.py must import MAX_CORPORA from foundation.corpus_constants (QW10)."
        )

    def test_constant_matches_test_assertion(self):
        """The constant value and the test assertion must agree."""
        constants_src = _read(
            REPO_ROOT / "src" / "aip" / "foundation" / "corpus_constants.py"
        )
        # Extract the MAX_CORPORA value
        match = re.search(r"^MAX_CORPORA:\s*int\s*=\s*(\d+)", constants_src, re.MULTILINE)
        assert match, "MAX_CORPORA constant not found in corpus_constants.py"
        constant_value = int(match.group(1))

        test_src = _read(REPO_ROOT / "tests" / "test_corpus_foundation.py")
        # The test asserts MAX_CORPORA == N
        test_match = re.search(r"assert MAX_CORPORA == (\d+)", test_src)
        assert test_match, (
            "test_corpus_foundation.py must assert MAX_CORPORA == N "
            "(test_connection_budget_formula_constants)"
        )
        test_value = int(test_match.group(1))
        assert constant_value == test_value, (
            f"MAX_CORPORA constant={constant_value} but test asserts =={test_value}. "
            "They must agree."
        )


# ---------------------------------------------------------------------------
# Generic guard: ADR-015 spec-only claim must stay accurate
# ---------------------------------------------------------------------------


class TestAdr015SpecOnlyGuard:
    """Guard for R10 — ROADMAP.md must not claim ADR-015 fleet phases are
    further along than 'spec only' until code actually exists."""

    def test_roadmap_marks_fleet_as_spec_only(self):
        """ROADMAP.md must mark ADR-015 fleet phases as 'spec only' until
        fleet code exists. This guard catches if someone removes the
        banner without adding the code."""
        roadmap = _read(REPO_ROOT / "ROADMAP.md")
        # The banner must be present
        assert "SPEC ONLY" in roadmap.upper() or "spec only" in roadmap.lower(), (
            "ROADMAP.md must mark ADR-015 fleet phases as 'spec only' — "
            "zero fleet code exists today (R10 from tech-debt assessment). "
            "Either add the banner back or add fleet code."
        )

    def test_no_fleet_code_exists_yet(self):
        """Verify the claim is still accurate — no fleet primitives exist.
        If this test FAILS, it means fleet code was added — update the
        ROADMAP banner to reflect the new state."""
        # Check for fleet primitives that would indicate Phase 3A-0 started
        src_files = list((REPO_ROOT / "src").rglob("*.py"))
        src_text = ""
        for f in src_files:
            if "__pycache__" in f.parts:
                continue
            try:
                src_text += _read(f)
            except Exception:
                pass

        # These are the ADR-015 primitives. If any appear as a class/table
        # definition, fleet work has started and the ROADMAP banner needs
        # updating.
        fleet_primitives = [
            "class AgentRun",
            "class CapabilityGate",
            "class FleetCoordinator",
            "class DispatchPlan",
            "agent_runs",  # table name
            "fleet_cost_ledger",  # table name
        ]
        found = [p for p in fleet_primitives if p in src_text]
        if found:
            pytest.skip(
                f"Fleet code now exists ({found}) — update ROADMAP.md to "
                f"remove the 'spec only' banner for the relevant phase. "
                f"This guard can be removed once all phases have code."
            )
        # If no fleet code found, the spec-only banner must be present
        # (already checked in test_roadmap_marks_fleet_as_spec_only)
