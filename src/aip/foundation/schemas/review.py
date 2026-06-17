"""Review, gate, and canonical promotion types.

Review gate verdicts, review context assembly, review queue entries,
Vigil configuration, and canonical promotion pipeline configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .base import FailureTypeCode


@dataclass
class ReviewVerdict:
    """Outcome of a review gate on a generated artifact.

    REVIEWED state follows GENERATED.
    DEFINER sovereignty for APPROVED state.
    failure_types use Appendix E taxonomy codes.
    """

    artifact_id: str
    verdict: Literal["APPROVED", "REJECTED", "NEEDS_REVISION", "PENDING"]
    reviewer: str  # "automated" | "definer"
    failure_types: list[FailureTypeCode] = field(default_factory=list)
    detail: str | None = None
    confidence: float = 1.0


@dataclass
class ReviewContext:
    """Assembled context for review decision.

    Contains everything a reviewer needs: the artifact content,
    its version history, recent trace events, and prior verdicts.
    """

    artifact_id: str
    artifact_content: str
    artifact_version: int
    trace_events: list[dict] = field(default_factory=list)
    prior_verdicts: list[ReviewVerdict] = field(default_factory=list)


@dataclass
class ReviewQueueEntry:
    """A single entry in the review queue surface.

    Review Queue surface.
    ECS transitions REVIEWED→APPROVED or REVIEWED→FAILED.
    Canonical promotion requires DEFINER approval.
    """

    artifact_id: str
    artifact_version: int = 1
    ecs_state: str = "GENERATED"
    domain: str = ""
    project_id: str = ""
    review_type: str = "definer"  # definer / adversarial
    evaluation_scores: list[dict] = field(default_factory=list)
    created_at: str = ""  # REQUIRED — ISO 8601


@dataclass
class VigilConfig:
    """Configuration for the Vigil actor (compiled knowledge maintenance).

    Vigil — last missing orchestration actor.
    Monitors canonical corpus health and triggers re-evaluation on model slot changes.
    Per INTERFACES: canonical_health_check_interval_seconds, stale_threshold_days,
    re_evaluate_on_slot_change, max_re_evaluate_batch_size, entity_consistency_check.

    Sprint 5.23 additions:
    - llm_faithfulness_enabled: Toggle for LLM-powered faithfulness checking.
      When True, Vigil uses the "evaluation" model slot to check whether
      responses accurately reflect their sources. When False (or when the
      evaluation slot is unavailable), falls back to pure-Python heuristic
      checks (citation rate, numeric grounding, hedging detection).
    - llm_faithfulness_model_slot: The model slot to use for LLM faithfulness
      evaluation. Defaults to "evaluation" as specified in ADR-011 Phase 3.3.
    - llm_faithfulness_sample_size: Max turns to evaluate per cycle via LLM.
      Keeps LLM cost bounded — only the most recently flagged or borderline
      turns are sent for LLM evaluation.
    """

    canonical_health_check_interval_seconds: int = 3600
    stale_threshold_days: int = 30
    re_evaluate_on_slot_change: bool = True
    max_re_evaluate_batch_size: int = 50
    entity_consistency_check: bool = True
    # LLM-powered faithfulness evaluation (Phase 3.3 of ADR-011 Vigil roadmap)
    # Graduated to default-on in Sprint 5.24 after validation in Sprint 5.23.
    # Graceful fallback: when the evaluation model slot is unavailable or
    # returns errors, Vigil falls back to pure-Python heuristic checks
    # (citation rate, numeric grounding, hedging detection) without degradation.
    llm_faithfulness_enabled: bool = True  # Default-on since Sprint 5.24
    llm_faithfulness_model_slot: str = "evaluation"
    llm_faithfulness_sample_size: int = 10  # Max turns per cycle for LLM eval
    # Sprint 6.4: Retrieval quality sampling — Vigil periodically samples
    # retrieval results via golden queries and flags precision@5 degradation.
    # Light-weight: only a few queries per sample, runs every N cycles.
    retrieval_quality_sampling_enabled: bool = True
    retrieval_quality_sample_size: int = 5  # Number of golden queries to sample
    retrieval_quality_threshold: float = 0.3  # precision@5 threshold below which alerts fire
    retrieval_quality_sample_interval_cycles: int = 6  # Only run every N cycles
    # Phase 4.1: Cross-turn consistency checking — Vigil's 5th evaluation
    # pass. Detects contradictions between a new response and earlier
    # responses in the same session. Uses the evaluation model slot
    # (same as faithfulness). Runs on a bounded sample per cycle to
    # keep LLM cost bounded. Default-on (same as faithfulness).
    consistency_check_enabled: bool = True
    consistency_check_model_slot: str = "evaluation"
    consistency_check_sample_size: int = 5  # Max turns per cycle for consistency check
    consistency_check_lookback_turns: int = 10  # How many prior turns to compare against


@dataclass
class CanonicalPromotionConfig:
    """Configuration for the canonical promotion pipeline.

    Drives REVIEWED→APPROVED→CANONICAL lifecycle with multi-stage verification.
    Per INTERFACES: faithfulness_threshold, domain_coherence_threshold,
    model_gen_assumption.
    """

    faithfulness_threshold: float = 0.85
    domain_coherence_threshold: float = 0.80
    model_gen_assumption: str | None = None
    auto_promote_on_approval: bool = False  # requires explicit DEFINER gate in 9.2
    require_vigil_health_check: bool = True
    indexing_enabled: bool = True  # LexicalStore + VectorStore sync
    require_faithfulness_check: bool = True
    require_domain_coherence: bool = True
    require_definer_approval: bool = True
    auto_reindex_on_promotion: bool = True


__all__ = [
    "ReviewVerdict",
    "ReviewContext",
    "ReviewQueueEntry",
    "VigilConfig",
    "CanonicalPromotionConfig",
]
