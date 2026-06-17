"""Tests for the Judge prompt coverage-gradient fix.

The dogfood run (2026-06-17) revealed that single-model points were
landing in ``partial_coverage`` when they belong in ``unique_insights``.
The Judge prompt said ``partial_coverage`` = "topic only some models
covered" but did NOT explicitly state the boundary: single-model points
go in ``unique_insights``, NOT ``partial_coverage``. ``partial_coverage``
is for 2..N-1 models.

This test verifies the fix: the Judge prompt now explicitly states
- ``partial_coverage[]`` is for points covered by 2 to N-1 models
- a point covered by only ONE model goes in ``unique_insights[]``, NOT
  ``partial_coverage[]``
"""

from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODEL_COUNCIL_PY = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "model_council.py"


def _read_model_council_source() -> str:
    return _MODEL_COUNCIL_PY.read_text(encoding="utf-8")


class TestJudgePromptCoverageGradientFix:
    """The Judge prompt explicitly states the coverage-gradient boundary
    so single-model points go in unique_insights, NOT partial_coverage."""

    def test_partial_coverage_rule_mentions_2_to_n_minus_1(self):
        """The partial_coverage rule explicitly says '2 to N-1 models'."""
        source = _read_model_council_source()
        # Find the Rules section in the Judge prompt
        rules_idx = source.find('"Rules:\\n"')
        assert rules_idx != -1, "Rules section not found in Judge prompt"
        rules_section = source[rules_idx:rules_idx + 2000]
        assert "2 to N-1" in rules_section or "2 to N-1 models" in rules_section, (
            "Coverage-gradient fix: partial_coverage rule must explicitly "
            "say '2 to N-1 models' so the Judge doesn't put single-model "
            "points in partial_coverage."
        )

    def test_partial_coverage_rule_says_single_model_goes_in_unique_insights(self):
        """The partial_coverage rule explicitly says a point covered by
        only ONE model goes in unique_insights, NOT partial_coverage."""
        source = _read_model_council_source()
        rules_idx = source.find('"Rules:\\n"')
        rules_section = source[rules_idx:rules_idx + 2000]
        assert "only ONE model" in rules_section, (
            "Coverage-gradient fix: the partial_coverage rule must "
            "explicitly state that a point covered by only ONE model "
            "goes in unique_insights, NOT partial_coverage."
        )
        assert "unique_insights[]" in rules_section
        assert "NOT partial_coverage[]" in rules_section

    def test_unique_insights_rule_says_single_model_is_unique(self):
        """The unique_insights rule explicitly says a point raised by
        only ONE model is a unique insight (NOT partial coverage)."""
        source = _read_model_council_source()
        rules_idx = source.find('"Rules:\\n"')
        rules_section = source[rules_idx:rules_idx + 2500]
        # The unique_insights rule must cross-reference the boundary
        assert "only ONE model is a" in rules_section, (
            "Coverage-gradient fix: the unique_insights rule must "
            "explicitly state that a point raised by only ONE model "
            "is a unique insight."
        )
        assert "NOT partial coverage" in rules_section, (
            "Coverage-gradient fix: the unique_insights rule must "
            "cross-reference 'NOT partial coverage' so the Judge "
            "understands the boundary from both directions."
        )

    def test_judge_prompt_does_not_say_some_models_without_qualification(self):
        """The old language 'topic only some models covered' is gone —
        replaced with the explicit '2 to N-1 models' boundary."""
        source = _read_model_council_source()
        rules_idx = source.find('"Rules:\\n"')
        rules_section = source[rules_idx:rules_idx + 2500]
        # The old vague language should NOT appear in the Rules section
        # (it's been replaced with the explicit boundary)
        # Note: the JSON schema example may still say "only some models"
        # but the RULES section must use the explicit boundary.
        assert "topic only some models covered" not in rules_section, (
            "Coverage-gradient fix: the old vague language 'topic only "
            "some models covered' must NOT appear in the Rules section — "
            "replaced with the explicit '2 to N-1 models' boundary."
        )


class TestPlannedFeaturesFileExists:
    """PLANNED_FEATURES.md exists at the repo root — the single source
    of truth for 'what's built, what's planned, what's deferred.'"""

    def test_planned_features_file_exists(self):
        """PLANNED_FEATURES.md exists at the repo root."""
        assert (_REPO_ROOT / "PLANNED_FEATURES.md").exists(), (
            "PLANNED_FEATURES.md must exist at the repo root — single "
            "source of truth for built/planned/deferred features."
        )

    def test_planned_features_has_already_built_section(self):
        """The file has an 'Already Built' section."""
        source = (_REPO_ROOT / "PLANNED_FEATURES.md").read_text(encoding="utf-8")
        assert "Already Built" in source, (
            "PLANNED_FEATURES.md must have an 'Already Built' section "
            "so agents can check if their recommendation is already "
            "implemented before suggesting it."
        )

    def test_planned_features_has_near_term_section(self):
        """The file has a 'Near-Term' section."""
        source = (_REPO_ROOT / "PLANNED_FEATURES.md").read_text(encoding="utf-8")
        assert "Near-Term" in source

    def test_planned_features_has_long_term_section(self):
        """The file has a 'Long-Term' section."""
        source = (_REPO_ROOT / "PLANNED_FEATURES.md").read_text(encoding="utf-8")
        assert "Long-Term" in source

    def test_planned_features_mentions_debt_006_resolution(self):
        """The file mentions that DEBT-006 is RESOLVED — so no future
        agent repeats the 'fix DEBT-006' recommendation."""
        source = (_REPO_ROOT / "PLANNED_FEATURES.md").read_text(encoding="utf-8")
        assert "DEBT-006" in source, (
            "PLANNED_FEATURES.md must mention DEBT-006 so no future "
            "agent repeats the stale 'fix DEBT-006' recommendation."
        )
        assert "Resolved" in source or "RESOLVED" in source, (
            "PLANNED_FEATURES.md must state DEBT-006 is resolved."
        )

    def test_planned_features_mentions_codebase_as_corpus(self):
        """The file mentions the codebase-as-corpus long-term plan."""
        source = (_REPO_ROOT / "PLANNED_FEATURES.md").read_text(encoding="utf-8")
        assert "Codebase-as-Corpus" in source or "codebase" in source.lower(), (
            "PLANNED_FEATURES.md must mention the codebase-as-corpus "
            "long-term plan (Phase 1.6)."
        )
