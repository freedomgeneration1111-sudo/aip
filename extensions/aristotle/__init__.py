"""Aristotle — Adaptive Tutor (Phase A dogfood).

The first extension built against the ADR-014 Phase 0 platform. This package
is discovered by ExtensionHost at startup when `extensions/` is on the
operator-owned extensions path (default: `extensions/` relative to CWD).

Phase A scope (ADR-ARISTOTLE §11):
  - Ingestor + curriculum map + prerequisite graph (placeholder — content
    ingestion comes when the textbook corpus has material)
  - student_profile + struggle_pattern (schema in M001_aristotle.sql)
  - TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine (placeholder —
    SOCRATES actor is the entry point; full state machine comes with
    workflow integration)
  - SM-2 via core VIGIL (reused, not re-implemented)
  - Bilingual (content_primary + content_alt + content_alt_lang schema)

This is the dogfood drop. The goal is to prove the platform contract, not
to ship the full tutor. Each gap ARISTOTLE surfaces in the platform is a
Phase 0 protocol gap to log (ADR-ARISTOTLE §9).
"""
