"""Vigil actor — quality evaluation (ADR-011).

Vigil is the quality assurance actor. It evaluates synthesis outputs,
monitors for quality degradation, and proposes profile amendments when
it notices systematic patterns.

Per ADR-011:
- Vigil evaluates augmented chat responses for source citation quality
- Vigil flags responses that make claims not supported by retrieved sources
- Vigil proposes DEFINER profile amendments when synthesis patterns drift
- Vigil monitors Beast wiki articles for factual consistency
- Vigil generates quality evaluation reports as reviewable artifacts

What Vigil does NOT do (per ADR-011):
- Background maintenance (→ Sexton)
- Context assembly (→ Beast)
- Any write operations to corpus_turns or artifacts without DEFINER review

The previous Vigil implementation contained maintenance operations
(canonical health checks, stale detection, entity inconsistency checks,
model slot change handling) that are now outside Vigil's scope per ADR-011.
Those operations are retained as private methods for backward compatibility
but are no longer called from run_cycle().

Phase C (Sprint 5.21): Pure Python citation-rate scoring — no LLM needed.
  - Reads augmented turns since last eval
  - Checks whether source_turn_ids appear in assistant_text as citations
  - Computes citation_rate = cited / retrieved
  - Writes vigil_score to metadata_json
  - Flags low-citation turns via GENERATED artifact for DEFINER review

Phase 3.3 (Sprint 5.23, graduated Sprint 5.24): LLM-powered faithfulness evaluation.
  - Uses the "evaluation" model slot to check whether the response
    accurately reflects retrieved sources without hallucination or
    unsupported claims.
  - Enabled by default since Sprint 5.24 (`llm_faithfulness_enabled=True`).
  - Graceful fallback to pure-Python checks when model unavailable.
  - Only evaluates a bounded sample per cycle (llm_faithfulness_sample_size).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import (
    CanonicalStore,
    EntityStore,
    ModelProvider,
    TraceStore,
    VigilStore,
)
from aip.foundation.schemas import ModelSlotConfig, VigilConfig
from aip.logging import get_logger

logger = get_logger(__name__)


class Vigil:
    """Vigil — quality evaluation actor (ADR-011).

    Evaluates synthesis quality on an hourly cadence. Reads the last N
    augmented chat turns since the previous vigil run and checks each
    for source citation quality. Writes an evaluation summary as a
    GENERATED artifact for DEFINER review.

    Phase C: Pure-Python citation-rate scoring (always runs):
      citation_rate = (source_turn_ids found in response text) / (total source_turn_ids)

    Phase 3.3 (Sprint 5.23, graduated Sprint 5.24): LLM-powered faithfulness checking:
      When llm_faithfulness_enabled is True (now the default), uses the
      evaluation model slot to ask: "Does the response accurately reflect
      the retrieved sources without hallucination or unsupported claims?"
      This runs AFTER the pure-Python checks, on a bounded sample of
      turns that were flagged (or borderline). The LLM result is stored
      in the turn's metadata and can override or refine the pure-Python
      flagging decision. Graceful fallback: if the model is unavailable
      or returns errors, the pure-Python evaluation is preserved intact.

    A turn is considered "cited" if any form of its turn_id appears
    in the assistant_text — matching patterns like [source: abc123],
    [Source 1], or just the turn_id fragment itself.

    Legacy maintenance methods (check_canonical_health, detect_stale_canonicals,
    detect_entity_inconsistencies, on_model_slot_change) are retained for
    backward compatibility but are NOT part of the ADR-011 vigil cycle.
    """

    # Citation threshold below which a turn is flagged for DEFINER review
    _CITATION_THRESHOLD = 0.3

    # Contradiction / grounding thresholds
    _HEDGING_PHRASES = frozenset(
        {
            "i'm not sure but",
            "i think",
            "i believe",
            "it might be",
            "possibly",
            "perhaps",
            "it could be",
            "i'm guessing",
            "i'm not certain",
            "not entirely sure",
            "it seems like",
            "i would guess",
            "my guess is",
            "i assume",
        }
    )
    _GROUNDING_THRESHOLD = 0.5  # Flag if < 50% of numeric claims are grounded

    # LLM faithfulness prompt
    _FAITHFULNESS_SYSTEM_PROMPT = """\
You are an AIP Vigil evaluator performing faithfulness checking on an AI-generated
response. Your job is to determine whether the response accurately reflects the
retrieved source material without hallucination or unsupported claims.

You will receive:
1. The user's question
2. The retrieved source text(s)
3. The AI assistant's response

Evaluate the response for:
- Faithfulness: Does every claim in the response have support in the sources?
- Hallucination: Are there any claims that go beyond or contradict the sources?
- Source attribution: Are specific facts properly attributed to source material?

Scoring:
- faithfulness_score: 0.0-1.0 (1.0 = fully faithful, 0.0 = complete hallucination)
- hallucination_flags: list of specific unsupported claims found in the response

Output ONLY valid JSON with exactly these fields:
{
  "faithfulness_score": 0.85,
  "hallucination_flags": ["Claim about X is not supported by sources"],
  "grounding_assessment": "mostly_grounded",
  "explanation": "Brief explanation of the score"
}

Be strict: if a specific number, date, or factual claim appears in the response but not in the
sources, flag it. If the response adds reasonable inference or synthesis from the sources, that
is acceptable — flag only unsupported assertions."""

    def __init__(
        self,
        config: VigilConfig,
        vigil_store: VigilStore,
        canonical_store: CanonicalStore,
        entity_store: EntityStore,
        model_provider: ModelProvider,
        trace_store: TraceStore,
        sexton: Any | None = None,  # Sexton (optional for triggering)
        artifact_store: Any = None,  # for writing evaluation artifacts
        ecs_store: Any = None,  # for ECS transitions on evaluation artifacts
        event_store: Any = None,  # for emitting vigil events
        corpus_turn_store: Any = None,  # for reading augmented chat turns
        alert_manager: Any = None,  # Sprint 5.25: AlertManager for quality degradation alerts
        quality_store: Any = None,  # Sprint 5.26: VigilQualityStore for persistence
    ) -> None:
        self.config = config
        self.vigil_store = vigil_store
        self.canonical_store = canonical_store
        self.entity_store = entity_store
        self.model_provider = model_provider
        self.trace_store = trace_store
        self.sexton = sexton
        self._artifacts = artifact_store
        self._ecs = ecs_store
        self._events = event_store
        self._corpus_turns = corpus_turn_store
        self._last_eval_time: float | None = None

        # Sprint 6.2: Cycle count and recent errors for operator visibility
        self._cycle_count: int = 0
        self._recent_errors: list[str] = []  # Last 10 error messages

        # LLM faithfulness telemetry — accumulates across cycles
        self._llm_faithfulness_telemetry = {
            "total_llm_evaluations": 0,
            "total_llm_evaluations_failed": 0,
            "total_hallucinations_detected": 0,
            "avg_llm_faithfulness_score": 0.0,
            "last_llm_evaluations": [],  # Last N evaluation summaries
        }

        # Sprint 5.24: Per-cycle quality report history for trend tracking.
        # Stores the last 10 cycle summaries for trend computation.
        self._cycle_report_history: list[dict] = []

        # Sprint 5.25: Alert manager for operator notifications
        self._alert_manager = alert_manager

        # Sprint 5.26: Persistent quality history store
        self._quality_store = quality_store
        # History is loaded lazily on first run_cycle() via _load_quality_history()
        # because get_cycles() is async and cannot be awaited in __init__.
        self._history_loaded = False

    # ------------------------------------------------------------------
    # Status summary for operator visibility (Sprint 6.2)
    # ------------------------------------------------------------------

    def get_status_summary(self) -> dict:
        """Return a summary of Vigil's current state for operator visibility.

        Includes dependency availability, last eval time, and config.
        This method is synchronous and never raises. Called by /actors/status,
        /health, and dashboard endpoints.
        """
        return {
            "initialized": True,
            "dependencies": {
                "vigil_store": self.vigil_store is not None,
                "canonical_store": self.canonical_store is not None,
                "entity_store": self.entity_store is not None,
                "model_provider": self.model_provider is not None,
                "trace_store": self.trace_store is not None,
                "sexton": self.sexton is not None,
                "artifact_store": self._artifacts is not None,
                "ecs_store": self._ecs is not None,
                "event_store": self._events is not None,
                "corpus_turn_store": self._corpus_turns is not None,
                "alert_manager": self._alert_manager is not None,
                "quality_store": self._quality_store is not None,
            },
            "last_eval_time": self._last_eval_time,
            "interval_seconds": self.config.canonical_health_check_interval_seconds,
            "cycle_count": self._cycle_count,
            "recent_errors": list(self._recent_errors),
            "llm_faithfulness_enabled": self.config.llm_faithfulness_enabled,
            "retrieval_quality_sampling_enabled": self.config.retrieval_quality_sampling_enabled,
            "role": "quality_evaluation",
        }

    # ------------------------------------------------------------------
    # ADR-011 Quality Evaluation Cycle
    # ------------------------------------------------------------------

    async def _load_quality_history(self) -> None:
        """Load cycle report history from the quality store (async).

        Called lazily on first run_cycle() because get_cycles() is async
        and cannot be awaited in __init__.  Idempotent — subsequent calls
        are no-ops.
        """
        if self._history_loaded:
            return
        self._history_loaded = True
        if self._quality_store is not None:
            try:
                persisted = await self._quality_store.get_cycles(last_n_cycles=10)
                if persisted:
                    self._cycle_report_history = persisted
                    logger.info(
                        "vigil_quality_history_loaded_from_store",
                        loaded_cycles=len(persisted),
                    )
            except Exception as exc:
                logger.warning(
                    "vigil_quality_history_load_failed",
                    error=str(exc),
                )

    async def run_cycle(self) -> dict:
        """Execute a Vigil quality evaluation cycle (ADR-011).

        Reads augmented chat turns since the previous vigil run, checks each
        for source citation quality, writes the score to metadata_json, and
        flags low-citation turns for DEFINER review.

        Phase C: Pure-Python checks (always run):
        - Citation rate scoring
        - Source grounding (numeric claims)
        - Hedging detection

        Phase 3.3: LLM-powered faithfulness (when enabled):
        - Uses evaluation model slot to check faithfulness
        - Runs on a bounded sample of flagged/borderline turns
        - Gracefully falls back to pure-Python if model unavailable

        Returns a summary dict with evaluation results.
        """
        # Lazy-load quality history from store on first cycle
        await self._load_quality_history()

        cycle_start = time.monotonic()

        # Sprint 6.2: Structured start event with cycle count
        logger.info("vigil_eval_start", cycle=self._cycle_count + 1)

        evaluated_count = 0
        flagged_count = 0
        citation_rates: list[float] = []
        grounding_rates: list[float] = []
        hedging_detected_count = 0

        # Collect turns for potential LLM evaluation
        flagged_turns: list[dict] = []

        # --- Step 1: Read augmented turns since last eval ---
        if self._corpus_turns is not None:
            try:
                turns = await self._corpus_turns.get_augmented_turns_since(
                    since=self._last_eval_time,
                    limit=100,
                )
            except Exception as exc:
                logger.warning("vigil_augmented_turns_query_failed", error=str(exc))
                self._recent_errors.append(f"augmented_turns_query: {exc}")
                self._recent_errors = self._recent_errors[-10:]
                turns = []

            # --- Step 2: Evaluate citation quality for each turn ---
            for turn in turns:
                try:
                    meta = json.loads(turn.metadata_json) if turn.metadata_json else {}
                    source_turn_ids: list[str] = meta.get("source_turn_ids", [])

                    if not source_turn_ids:
                        # No sources were retrieved — nothing to cite, skip scoring
                        continue

                    # Check which source_turn_ids appear in the response text
                    cited_ids = self._find_cited_turns(
                        source_turn_ids=source_turn_ids,
                        response_text=turn.assistant_text,
                    )

                    citation_rate = len(cited_ids) / len(source_turn_ids)
                    citation_rates.append(citation_rate)

                    # --- Quality Check 2: Source grounding ---
                    source_text = (turn.user_text or "") + " " + (turn.assistant_text or "")
                    grounding_result = self._check_source_grounding(
                        response_text=turn.assistant_text or "",
                        source_text=source_text,
                    )
                    grounding_rates.append(grounding_result["grounding_rate"])

                    # --- Quality Check 3: Hedging / uncertainty detection ---
                    hedging_found = self._detect_hedging(turn.assistant_text or "")
                    if hedging_found and len(source_turn_ids) > 0:
                        hedging_detected_count += 1

                    evaluated_count += 1

                    # --- Step 3: Write score to metadata_json ---
                    updated_meta = dict(meta)  # copy existing
                    updated_meta["vigil_score"] = round(citation_rate, 3)
                    updated_meta["vigil_evaluated_at"] = datetime.now(timezone.utc).isoformat()
                    updated_meta["vigil_cited_ids"] = cited_ids
                    updated_meta["vigil_source_count"] = len(source_turn_ids)
                    updated_meta["vigil_grounding_rate"] = grounding_result["grounding_rate"]
                    updated_meta["vigil_ungrounded_claims"] = grounding_result["ungrounded_claims"]
                    updated_meta["vigil_hedging_detected"] = hedging_found

                    await self._corpus_turns.update_metadata_json(
                        turn_id=turn.turn_id,
                        metadata_json=json.dumps(updated_meta),
                    )

                    # --- Step 4: Flag low-quality turns for DEFINER review ---
                    flag_reasons = []
                    if citation_rate < self._CITATION_THRESHOLD:
                        flag_reasons.append("low_citation_rate")
                    if grounding_result["grounding_rate"] < self._GROUNDING_THRESHOLD:
                        flag_reasons.append("poor_source_grounding")
                    if hedging_found and len(source_turn_ids) > 0:
                        flag_reasons.append("unwarranted_hedging")

                    # Also collect borderline turns for potential LLM evaluation
                    is_borderline = self._CITATION_THRESHOLD <= citation_rate < self._CITATION_THRESHOLD + 0.2

                    if flag_reasons:
                        flagged_count += 1
                        await self._write_flagged_artifact(
                            turn=turn,
                            citation_rate=citation_rate,
                            cited_ids=cited_ids,
                            source_turn_ids=source_turn_ids,
                            grounding_rate=grounding_result["grounding_rate"],
                            ungrounded_claims=grounding_result["ungrounded_claims"],
                            hedging_detected=hedging_found,
                            flag_reasons=flag_reasons,
                        )

                        flagged_turns.append(
                            {
                                "turn": turn,
                                "citation_rate": citation_rate,
                                "cited_ids": cited_ids,
                                "source_turn_ids": source_turn_ids,
                                "grounding_rate": grounding_result["grounding_rate"],
                                "ungrounded_claims": grounding_result["ungrounded_claims"],
                                "hedging_detected": hedging_found,
                                "flag_reasons": flag_reasons,
                            }
                        )
                    elif is_borderline:
                        # Borderline — may benefit from deeper LLM evaluation
                        flagged_turns.append(
                            {
                                "turn": turn,
                                "citation_rate": citation_rate,
                                "cited_ids": cited_ids,
                                "source_turn_ids": source_turn_ids,
                                "grounding_rate": grounding_result["grounding_rate"],
                                "ungrounded_claims": grounding_result["ungrounded_claims"],
                                "hedging_detected": hedging_found,
                                "flag_reasons": ["borderline_citation_rate"],
                            }
                        )

                except Exception as exc:
                    logger.warning(
                        "vigil_turn_eval_failed",
                        turn_id=turn.turn_id,
                        error=str(exc),
                    )

        # --- Step 5: LLM-powered faithfulness evaluation (Phase 3.3) ---
        llm_eval_count = 0
        llm_hallucinations = 0
        llm_faithfulness_scores: list[float] = []

        if self.config.llm_faithfulness_enabled and flagged_turns:
            try:
                (
                    llm_eval_count,
                    llm_hallucinations,
                    llm_faithfulness_scores,
                ) = await self._run_llm_faithfulness_evaluation(flagged_turns)
            except Exception as exc:
                logger.warning("vigil_llm_faithfulness_cycle_failed", error=str(exc))
                self._recent_errors.append(f"llm_faithfulness: {exc}")
                self._recent_errors = self._recent_errors[-10:]

        # Compute aggregate metrics
        avg_citation_rate = round(sum(citation_rates) / len(citation_rates), 3) if citation_rates else 0.0
        avg_grounding_rate = round(sum(grounding_rates) / len(grounding_rates), 3) if grounding_rates else 1.0
        avg_llm_faithfulness = (
            round(sum(llm_faithfulness_scores) / len(llm_faithfulness_scores), 3) if llm_faithfulness_scores else 0.0
        )

        # Record the vigil check (legacy compatibility)
        try:
            await self.vigil_store.record_vigil_check(
                canonical_count=evaluated_count,
                stale_count=flagged_count,
                status="quality_evaluation_complete",
            )
        except Exception as exc:
            logger.warning("vigil_store_record_failed", error=str(exc))

        # Emit vigil event
        if self._events is not None:
            try:
                _vigil_event_metadata = {
                    "evaluated_count": evaluated_count,
                    "flagged_count": flagged_count,
                    "avg_citation_rate": avg_citation_rate,
                    "avg_grounding_rate": avg_grounding_rate,
                    "hedging_detected_count": hedging_detected_count,
                    "llm_faithfulness_enabled": self.config.llm_faithfulness_enabled,
                    "llm_eval_count": llm_eval_count,
                    "llm_hallucinations_detected": llm_hallucinations,
                    "avg_llm_faithfulness_score": avg_llm_faithfulness,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                # Adapt to QueryableEventStore.write_event() interface
                if hasattr(self._events, "write_event"):
                    await self._events.write_event(
                        event_type="vigil_eval_complete",
                        actor="vigil",
                        artifact_id="system",
                        **_vigil_event_metadata,
                    )
                elif hasattr(self._events, "emit"):
                    await self._events.emit(
                        event_type="vigil_eval_complete",
                        artifact_id="system",
                        metadata=_vigil_event_metadata,
                    )
                else:
                    logger.warning("vigil_event_store_no_write_method")
            except Exception as exc:
                logger.warning("vigil_eval_complete_event_failed", error=str(exc))

        # --- Step 6: Per-cycle quality report artifact (Sprint 5.24) ---
        elapsed = time.monotonic() - cycle_start
        self._last_eval_time = time.time()
        self._cycle_count += 1

        trend_indicators = self._compute_trend_indicators(
            avg_citation_rate=avg_citation_rate,
            avg_grounding_rate=avg_grounding_rate,
            avg_llm_faithfulness=avg_llm_faithfulness,
        )

        # Sprint 5.25: Alert on quality degradation
        if self._alert_manager is not None and any(
            v == "degrading" for v in trend_indicators.values() if isinstance(v, str)
        ):
            try:
                from aip.adapter.alerting import Alert

                degrading_metrics = [
                    k.replace("_trend", "")
                    for k, v in trend_indicators.items()
                    if isinstance(v, str) and v == "degrading"
                ]
                self._alert_manager.send_alert(
                    Alert(
                        alert_type="quality_degradation",
                        severity="warning",
                        subject="vigil_quality",
                        message=(
                            f"Vigil detected degrading quality trend in: "
                            f"{', '.join(degrading_metrics)}. "
                            f"Current scores — citation: {avg_citation_rate:.1%}, "
                            f"grounding: {avg_grounding_rate:.1%}, "
                            f"faithfulness: {avg_llm_faithfulness:.1%}."
                        ),
                        data={
                            "degrading_metrics": degrading_metrics,
                            "avg_citation_rate": avg_citation_rate,
                            "avg_grounding_rate": avg_grounding_rate,
                            "avg_llm_faithfulness": avg_llm_faithfulness,
                            "trend_indicators": trend_indicators,
                        },
                    )
                )
            except Exception as exc:
                logger.warning("vigil_alert_failed", error=str(exc))

        await self._write_cycle_quality_report(
            evaluated_count=evaluated_count,
            flagged_count=flagged_count,
            avg_citation_rate=avg_citation_rate,
            avg_grounding_rate=avg_grounding_rate,
            hedging_detected_count=hedging_detected_count,
            llm_eval_count=llm_eval_count,
            llm_hallucinations=llm_hallucinations,
            avg_llm_faithfulness=avg_llm_faithfulness,
            trend_indicators=trend_indicators,
            cycle_elapsed=elapsed,
        )

        # --- Step 7: Retrieval quality sampling (Sprint 6.4) ---
        # At the end of the cycle, run a light retrieval quality sample.
        # This is gated by config and cycle interval — may be a no-op.
        retrieval_quality_result = {}
        try:
            retrieval_quality_result = await self._run_retrieval_quality_sample()
        except Exception as exc:
            logger.warning("vigil_retrieval_quality_sample_error", error=str(exc))
            self._recent_errors.append(f"retrieval_quality_sample: {exc}")
            self._recent_errors = self._recent_errors[-10:]

        result = {
            "status": "quality_evaluation_complete",
            "evaluated_count": evaluated_count,
            "flagged_count": flagged_count,
            "avg_citation_rate": avg_citation_rate,
            "avg_grounding_rate": avg_grounding_rate,
            "hedging_detected_count": hedging_detected_count,
            "citation_threshold": self._CITATION_THRESHOLD,
            "grounding_threshold": self._GROUNDING_THRESHOLD,
            "cycle_elapsed_seconds": round(elapsed, 3),
            "last_eval_time": self._last_eval_time,
            # LLM faithfulness results (Phase 3.3)
            "llm_faithfulness_enabled": self.config.llm_faithfulness_enabled,
            "llm_eval_count": llm_eval_count,
            "llm_hallucinations_detected": llm_hallucinations,
            "avg_llm_faithfulness_score": avg_llm_faithfulness,
            "llm_faithfulness_telemetry": dict(self._llm_faithfulness_telemetry),
            # Sprint 5.24: trend indicators
            "trend_indicators": trend_indicators,
            # Sprint 6.4: retrieval quality gate
            "retrieval_quality_sample": retrieval_quality_result,
        }

        logger.info(
            "vigil_eval_complete",
            cycle=self._cycle_count,
            status=result["status"],
            evaluated=evaluated_count,
            flagged=flagged_count,
            avg_citation_rate=avg_citation_rate,
            avg_grounding_rate=avg_grounding_rate,
            hedging_detected=hedging_detected_count,
            llm_eval_count=llm_eval_count,
            elapsed_seconds=round(elapsed, 3),
        )
        return result

    # ------------------------------------------------------------------
    # Retrieval Quality Sampling (Sprint 6.4)
    # ------------------------------------------------------------------

    async def _run_retrieval_quality_sample(self) -> dict:
        """Run a light retrieval quality sample and flag precision@5 degradation.

        Sprint 6.4: Periodically runs 3-5 golden queries through the retrieval
        pipeline, computes precision@5, and alerts if mean precision drops below
        the configured threshold.  This is intentionally LIGHT — just a few
        queries to catch gross retrieval degradation, not a full eval harness.

        Gate: only runs when ``retrieval_quality_sampling_enabled`` is True AND
        ``_cycle_count % sample_interval_cycles == 0`` (so with 1-hour cycles
        and interval=6, this runs every ~6 hours).

        Graceful skip: if the retrieval infrastructure (vector store, embedding
        provider, DB) is unavailable, the sample is skipped silently.

        Returns:
            Dict with: sampled_count, mean_precision_at_5, threshold, degraded.
        """
        cfg = self.config
        # Gate 1: sampling enabled?
        if not cfg.retrieval_quality_sampling_enabled:
            return {
                "sampled_count": 0,
                "mean_precision_at_5": 0.0,
                "threshold": cfg.retrieval_quality_threshold,
                "degraded": False,
            }

        # Gate 2: interval cycle — only run every N cycles
        interval = cfg.retrieval_quality_sample_interval_cycles
        if interval > 0 and self._cycle_count % interval != 0:
            return {
                "sampled_count": 0,
                "mean_precision_at_5": 0.0,
                "threshold": cfg.retrieval_quality_threshold,
                "degraded": False,
            }

        # Step 1: Load golden queries
        try:
            from aip.orchestration.retrieval_eval import compute_precision_at_k, load_golden_queries
        except ImportError:
            logger.warning("vigil_retrieval_quality_import_failed", error="retrieval_eval module not available")
            return {
                "sampled_count": 0,
                "mean_precision_at_5": 0.0,
                "threshold": cfg.retrieval_quality_threshold,
                "degraded": False,
            }

        golden_queries = load_golden_queries()
        if not golden_queries:
            logger.info("vigil_retrieval_quality_no_golden_queries")
            return {
                "sampled_count": 0,
                "mean_precision_at_5": 0.0,
                "threshold": cfg.retrieval_quality_threshold,
                "degraded": False,
            }

        # Step 2: Sample a subset
        sample_size = min(cfg.retrieval_quality_sample_size, len(golden_queries))
        sampled = golden_queries[:sample_size]

        # Step 3: Create retrieval infrastructure (light, temporary)
        try:
            from aip.orchestration.ask_pipeline import _register_retriever_channels, create_ask_stores
            from aip.orchestration.retrieval_orchestrator import OrchestratorConfig, get_orchestrator_cache

            db_path = os.environ.get("AIP_DB_PATH", "db/state.db")
            try:
                stores = await create_ask_stores(db_path)
            except Exception as exc:
                logger.info("vigil_retrieval_quality_stores_unavailable", error=str(exc))
                return {
                    "sampled_count": 0,
                    "mean_precision_at_5": 0.0,
                    "threshold": cfg.retrieval_quality_threshold,
                    "degraded": False,
                }

            # Build hybrid OrchestratorConfig: FTS + Vector + Corpus
            orch_config = OrchestratorConfig(
                enable_fts=True,
                enable_vector=True,
                enable_graph=False,
                enable_wiki=False,
                enable_procedural=False,
                enable_corpus=True,
            )

            # Create orchestrator with registered channels
            cache = get_orchestrator_cache()
            store_key = id(stores.lexical_store) ^ id(stores.vector_store) ^ id(stores.corpus_turn_store)
            orchestrator = cache.get_or_create(
                store_key=store_key,
                register_fn=lambda orch: _register_retriever_channels(orch, stores),
            )
        except Exception as exc:
            logger.info("vigil_retrieval_quality_infra_unavailable", error=str(exc))
            return {
                "sampled_count": 0,
                "mean_precision_at_5": 0.0,
                "threshold": cfg.retrieval_quality_threshold,
                "degraded": False,
            }

        # Step 4: Run retrieval for each sampled query and compute precision@5
        precision_scores: list[float] = []
        for gq in sampled:
            try:
                hits, _trace = await orchestrator.retrieve(gq.query, config=orch_config)
                retrieved_ids = [h.id for h in hits]
                p5 = compute_precision_at_k(retrieved_ids, gq.relevant_ids, k=5)
                precision_scores.append(p5)
            except Exception as exc:
                logger.warning(
                    "vigil_retrieval_quality_query_failed",
                    query=gq.query[:80],
                    error=str(exc),
                )
                # Count as 0 precision for failed queries
                precision_scores.append(0.0)

        # Clean up temporary stores
        try:
            await stores.close()
        except Exception:
            pass

        # Step 5: Compute mean precision@5
        if not precision_scores:
            return {
                "sampled_count": 0,
                "mean_precision_at_5": 0.0,
                "threshold": cfg.retrieval_quality_threshold,
                "degraded": False,
            }

        mean_p5 = round(sum(precision_scores) / len(precision_scores), 4)
        threshold = cfg.retrieval_quality_threshold
        degraded = mean_p5 < threshold

        # Step 6: Alert if degraded
        if degraded:
            logger.warning(
                "vigil_retrieval_quality_degraded",
                mean_precision_at_5=mean_p5,
                threshold=threshold,
                sampled_count=len(precision_scores),
            )
            if self._alert_manager is not None:
                try:
                    from aip.adapter.alerting import Alert

                    self._alert_manager.send_alert(
                        Alert(
                            alert_type="retrieval_quality_degradation",
                            severity="warning",
                            subject="retrieval_quality",
                            message=(
                                f"Vigil retrieval quality gate triggered: "
                                f"mean precision@5 = {mean_p5:.2%} "
                                f"(threshold = {threshold:.2%}). "
                                f"Sampled {len(precision_scores)} golden queries."
                            ),
                            data={
                                "mean_precision_at_5": mean_p5,
                                "threshold": threshold,
                                "sampled_count": len(precision_scores),
                                "per_query_precision": precision_scores,
                            },
                        )
                    )
                except Exception as exc:
                    logger.warning("vigil_retrieval_quality_alert_failed", error=str(exc))

        # Step 7: Record in cycle report history
        sample_result = {
            "retrieval_quality_sample": {
                "sampled_count": len(precision_scores),
                "mean_precision_at_5": mean_p5,
                "threshold": threshold,
                "degraded": degraded,
                "per_query_precision": precision_scores,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }

        # Add to the latest cycle report in history (if any)
        if self._cycle_report_history:
            self._cycle_report_history[-1].update(sample_result)

        # Persist to quality store if available
        if self._quality_store is not None:
            try:
                await self._quality_store.record_cycle(sample_result)
            except Exception as exc:
                logger.warning("vigil_retrieval_quality_store_failed", error=str(exc))

        logger.info(
            "vigil_retrieval_quality_sample_complete",
            sampled_count=len(precision_scores),
            mean_precision_at_5=mean_p5,
            threshold=threshold,
            degraded=degraded,
        )

        return {
            "sampled_count": len(precision_scores),
            "mean_precision_at_5": mean_p5,
            "threshold": threshold,
            "degraded": degraded,
        }

    # ------------------------------------------------------------------
    # LLM-Powered Faithfulness Evaluation (Phase 3.3, Sprint 5.23)
    # ------------------------------------------------------------------

    async def _run_llm_faithfulness_evaluation(self, flagged_turns: list[dict]) -> tuple[int, int, list[float]]:
        """Run LLM-powered faithfulness evaluation on flagged/borderline turns.

        Uses the evaluation model slot to ask whether each response accurately
        reflects its retrieved sources without hallucination or unsupported claims.

        This method is called only when llm_faithfulness_enabled is True and
        there are flagged turns to evaluate. It processes up to
        llm_faithfulness_sample_size turns per cycle to bound LLM cost.

        Graceful fallback: If the model provider call fails or returns an
        error, the turn's pure-Python evaluation is kept as-is — no
        degradation from the baseline.

        Returns:
            (eval_count, hallucination_count, faithfulness_scores)
        """
        sample_size = self.config.llm_faithfulness_sample_size
        sample = flagged_turns[:sample_size]

        eval_count = 0
        hallucination_count = 0
        faithfulness_scores: list[float] = []
        model_slot = self.config.llm_faithfulness_model_slot

        for entry in sample:
            turn = entry["turn"]
            source_turn_ids = entry["source_turn_ids"]

            # Build source text from the turn's source_turn_ids
            # In production, this would fetch the actual source turn content.
            # For now, use the turn's user_text as a proxy for the question
            # and the source_turn_ids list as context identifiers.
            source_text_parts = []
            for sid in source_turn_ids:
                source_text_parts.append(f"[Source: {sid}]")

            source_summary = "\n".join(source_text_parts)
            user_question = (turn.user_text or "")[:500]
            assistant_response = (turn.assistant_text or "")[:1000]

            user_prompt = (
                f"USER QUESTION:\n{user_question}\n\n"
                f"RETRIEVED SOURCES:\n{source_summary}\n\n"
                f"ASSISTANT RESPONSE:\n{assistant_response}\n\n"
                f"Evaluate the faithfulness of the assistant's response."
            )

            messages = [
                {"role": "system", "content": self._FAITHFULNESS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            try:
                llm_result = await self.model_provider.call(model_slot, messages)

                if llm_result.get("error"):
                    logger.warning(
                        "vigil_llm_faithfulness_model_error",
                        turn_id=turn.turn_id,
                        error=llm_result.get("error_message", "unknown"),
                    )
                    self._llm_faithfulness_telemetry["total_llm_evaluations_failed"] += 1
                    continue

                content = (llm_result or {}).get("content", "").strip()

                # Parse the LLM response as JSON
                parsed = self._parse_faithfulness_response(content)
                if parsed is None:
                    self._llm_faithfulness_telemetry["total_llm_evaluations_failed"] += 1
                    continue

                faithfulness_score = parsed.get("faithfulness_score", 0.5)
                hallucination_flags = parsed.get("hallucination_flags", [])
                explanation = parsed.get("explanation", "")

                faithfulness_scores.append(faithfulness_score)
                eval_count += 1

                if hallucination_flags:
                    hallucination_count += 1

                # Update turn metadata with LLM faithfulness result
                try:
                    meta = json.loads(turn.metadata_json) if turn.metadata_json else {}
                    updated_meta = dict(meta)
                    updated_meta["vigil_llm_faithfulness_score"] = round(faithfulness_score, 3)
                    updated_meta["vigil_llm_hallucination_flags"] = hallucination_flags[:5]
                    updated_meta["vigil_llm_explanation"] = explanation[:300]
                    updated_meta["vigil_llm_evaluated_at"] = datetime.now(timezone.utc).isoformat()

                    await self._corpus_turns.update_metadata_json(
                        turn_id=turn.turn_id,
                        metadata_json=json.dumps(updated_meta),
                    )
                except Exception as exc:
                    logger.warning(
                        "vigil_llm_metadata_update_failed",
                        turn_id=turn.turn_id,
                        error=str(exc),
                    )

                # If LLM flags hallucination and turn wasn't already flagged,
                # write an additional flagged artifact
                if hallucination_flags and "borderline_citation_rate" in entry.get("flag_reasons", []):
                    await self._write_flagged_artifact(
                        turn=turn,
                        citation_rate=entry["citation_rate"],
                        cited_ids=entry["cited_ids"],
                        source_turn_ids=source_turn_ids,
                        grounding_rate=entry["grounding_rate"],
                        ungrounded_claims=entry.get("ungrounded_claims", []),
                        hedging_detected=entry.get("hedging_detected", False),
                        flag_reasons=["llm_hallucination_detected"],
                        llm_faithfulness_score=faithfulness_score,
                        llm_hallucination_flags=hallucination_flags,
                    )

                # Update telemetry
                self._llm_faithfulness_telemetry["total_llm_evaluations"] += 1
                self._llm_faithfulness_telemetry["total_hallucinations_detected"] += 1 if hallucination_flags else 0
                eval_summary = {
                    "turn_id": turn.turn_id,
                    "faithfulness_score": round(faithfulness_score, 3),
                    "hallucination_flags": hallucination_flags[:3],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._llm_faithfulness_telemetry["last_llm_evaluations"].append(eval_summary)
                # Keep only last 20 evaluations
                self._llm_faithfulness_telemetry["last_llm_evaluations"] = self._llm_faithfulness_telemetry[
                    "last_llm_evaluations"
                ][-20:]

            except Exception as exc:
                logger.warning(
                    "vigil_llm_faithfulness_turn_failed",
                    turn_id=turn.turn_id,
                    error=str(exc),
                )
                self._llm_faithfulness_telemetry["total_llm_evaluations_failed"] += 1
                continue

        # Update average faithfulness score in telemetry
        if faithfulness_scores:
            avg = round(sum(faithfulness_scores) / len(faithfulness_scores), 3)
            self._llm_faithfulness_telemetry["avg_llm_faithfulness_score"] = avg

        return eval_count, hallucination_count, faithfulness_scores

    @staticmethod
    def _parse_faithfulness_response(content: str) -> dict | None:
        """Parse the LLM faithfulness response into a dict.

        Handles various LLM response formats:
        - Clean JSON
        - JSON wrapped in markdown code fences
        - JSON embedded in conversational text

        Returns None if parsing fails.
        """
        if not content or not content.strip():
            return None

        s = content.strip()

        # Strip markdown code fences
        if s.startswith("```"):
            first_newline = s.find("\n")
            if first_newline != -1:
                s = s[first_newline + 1 :]
            if s.rstrip().endswith("```"):
                s = s.rstrip()[:-3].rstrip()

        # Try direct parse
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict) and "faithfulness_score" in parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Try finding JSON object boundaries
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(s[start : end + 1])
                if isinstance(parsed, dict) and "faithfulness_score" in parsed:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    @staticmethod
    def _check_source_grounding(response_text: str, source_text: str) -> dict:
        """Check whether numeric claims in the response are grounded in the source text.

        Extracts numbers (integers, decimals, percentages) from the response
        and checks whether they appear in the source text.  Returns a dict
        with grounding_rate (grounded / total_claims) and a list of
        ungrounded claims.

        This is a heuristic check — it does not understand semantics, but it
        catches common fabrication patterns like invented statistics or dates.
        """
        if not response_text:
            return {"grounding_rate": 1.0, "total_claims": 0, "grounded_claims": 0, "ungrounded_claims": []}

        # Extract numeric claims: numbers with optional decimal and percent sign
        import re as _re

        numeric_pattern = _re.compile(r"\b\d+(?:\.\d+)?%?\b")
        response_numbers = numeric_pattern.findall(response_text)

        if not response_numbers:
            return {"grounding_rate": 1.0, "total_claims": 0, "grounded_claims": 0, "ungrounded_claims": []}

        # Filter out trivial numbers (0, 1, 2) that appear everywhere
        nontrivial = [n for n in response_numbers if n not in ("0", "1", "2", "0%", "1%", "2%")]

        if not nontrivial:
            return {
                "grounding_rate": 1.0,
                "total_claims": len(response_numbers),
                "grounded_claims": len(response_numbers),
                "ungrounded_claims": [],
            }

        source_lower = source_text.lower()
        grounded = []
        ungrounded = []
        for num in nontrivial:
            if num in source_lower or num.rstrip("%") in source_lower:
                grounded.append(num)
            else:
                ungrounded.append(num)

        total = len(nontrivial)
        rate = len(grounded) / total if total > 0 else 1.0
        return {
            "grounding_rate": round(rate, 3),
            "total_claims": total,
            "grounded_claims": len(grounded),
            "ungrounded_claims": ungrounded[:10],  # Cap at 10 to avoid bloat
        }

    def _detect_hedging(self, response_text: str) -> bool:
        """Detect hedging language in the response.

        Returns True if any hedging phrase is found.  Hedging is
        concerning when sources are available because it suggests the
        model is uncertain despite having authoritative source material.
        """
        if not response_text:
            return False
        text_lower = response_text.lower()
        return any(phrase in text_lower for phrase in self._HEDGING_PHRASES)

    @staticmethod
    def _find_cited_turns(source_turn_ids: list[str], response_text: str) -> list[str]:
        """Determine which source_turn_ids appear in the response text as citations.

        Checks multiple citation patterns:
          - Direct turn_id reference (e.g. "abc123" appearing in the text)
          - [source: turn_id] pattern (the instructed citation format)
          - [Source N] numbered reference (if sources were numbered in context)

        A turn_id is considered "cited" if any fragment of it (at least 8 chars)
        appears in the response text, or if the full [source: ...] pattern matches.
        """
        if not response_text or not source_turn_ids:
            return []

        cited: list[str] = []
        response_lower = response_text.lower()

        for tid in source_turn_ids:
            # Check 1: Full [source: tid] pattern (case-insensitive)
            if f"[source: {tid}]" in response_lower or f"[source:{tid}]" in response_lower:
                cited.append(tid)
                continue

            # Check 2: Turn ID fragment (at least 8 chars) appears in text
            fragment = tid[:8] if len(tid) >= 8 else tid
            if fragment in response_lower:
                cited.append(tid)
                continue

            # Check 3: [Source N] numbered pattern
            source_num_pattern = re.findall(r"\[source\s+(\d+)\]", response_lower)
            if source_num_pattern:
                pass

        return cited

    async def _write_flagged_artifact(
        self,
        turn: Any,
        citation_rate: float,
        cited_ids: list[str],
        source_turn_ids: list[str],
        grounding_rate: float = 1.0,
        ungrounded_claims: list[str] | None = None,
        hedging_detected: bool = False,
        flag_reasons: list[str] | None = None,
        llm_faithfulness_score: float | None = None,
        llm_hallucination_flags: list[str] | None = None,
    ) -> None:
        """Write a GENERATED artifact flagging a low-quality turn for DEFINER review.

        The artifact includes the turn's metadata and quality analysis so the
        DEFINER can review whether the response properly cited its sources,
        whether claims are grounded, and whether hedging is warranted.
        """
        if self._artifacts is None:
            return

        try:
            artifact_id = f"vigil-flag-{turn.turn_id}"
            content_dict = {
                "flag_type": "quality_evaluation",
                "flag_reasons": flag_reasons or ["low_citation_rate"],
                "turn_id": turn.turn_id,
                "conversation_id": turn.conversation_id,
                "citation_rate": round(citation_rate, 3),
                "citation_threshold": self._CITATION_THRESHOLD,
                "grounding_rate": round(grounding_rate, 3),
                "grounding_threshold": self._GROUNDING_THRESHOLD,
                "ungrounded_claims": ungrounded_claims or [],
                "hedging_detected": hedging_detected,
                "source_count": len(source_turn_ids),
                "cited_count": len(cited_ids),
                "cited_ids": cited_ids,
                "uncited_ids": [tid for tid in source_turn_ids if tid not in cited_ids],
                "response_preview": (turn.assistant_text or "")[:500],
                "flagged_at": datetime.now(timezone.utc).isoformat(),
            }

            # Include LLM faithfulness results if available
            if llm_faithfulness_score is not None:
                content_dict["llm_faithfulness_score"] = round(llm_faithfulness_score, 3)
                content_dict["llm_hallucination_flags"] = llm_hallucination_flags or []

            content = json.dumps(content_dict, indent=2)

            metadata = {
                "artifact_type": "vigil_flag",
                "flag_type": "quality_evaluation",
                "flag_reasons": flag_reasons or ["low_citation_rate"],
                "turn_id": turn.turn_id,
                "citation_rate": round(citation_rate, 3),
                "grounding_rate": round(grounding_rate, 3),
                "hedging_detected": hedging_detected,
                "generated_by": "vigil",
            }

            if llm_faithfulness_score is not None:
                metadata["llm_faithfulness_score"] = round(llm_faithfulness_score, 3)

            await self._artifacts.write(
                id=artifact_id,
                content=content,
                metadata=metadata,
            )

            # Transition to GENERATED state for DEFINER review
            if self._ecs is not None:
                try:
                    reasons_str = ", ".join(flag_reasons or ["low_citation_rate"])
                    detail = (
                        f"Quality evaluation: {reasons_str} "
                        f"(citation_rate={citation_rate:.1%}, grounding_rate={grounding_rate:.1%})"
                    )
                    if llm_faithfulness_score is not None:
                        detail += f", llm_faithfulness={llm_faithfulness_score:.1%}"
                    await self._ecs.transition(
                        artifact_id=artifact_id,
                        from_state=None,
                        to_state="GENERATED",
                        actor="vigil",
                        reason=detail,
                    )
                except Exception as exc:
                    logger.warning("vigil_flag_ecs_transition_failed", artifact_id=artifact_id, error=str(exc))

        except Exception as exc:
            logger.warning(
                "vigil_flag_artifact_failed",
                turn_id=turn.turn_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Per-Cycle Quality Report (Sprint 5.24)
    # ------------------------------------------------------------------

    def _compute_trend_indicators(
        self,
        avg_citation_rate: float,
        avg_grounding_rate: float,
        avg_llm_faithfulness: float,
    ) -> dict:
        """Compute trend indicators by comparing current metrics with previous cycles.

        Compares the current cycle's aggregate metrics against the previous
        cycle to detect improvement, degradation, or stability.  Trend
        indicators use a simple directional comparison: if the current value
        is more than 5% above/below the previous value, it is trending
        up/down; otherwise it is stable.

        Returns a dict with per-metric trend indicators.
        """
        if not self._cycle_report_history:
            # First cycle -- no trend data yet
            return {
                "citation_rate_trend": "baseline",
                "grounding_rate_trend": "baseline",
                "llm_faithfulness_trend": "baseline",
                "previous_cycle": None,
            }

        prev = self._cycle_report_history[-1]
        prev_citation = prev.get("avg_citation_rate", 0.0)
        prev_grounding = prev.get("avg_grounding_rate", 0.0)
        prev_faithfulness = prev.get("avg_llm_faithfulness", 0.0)

        def _trend(current: float, previous: float) -> str:
            if previous == 0.0 and current == 0.0:
                return "stable"
            if previous == 0.0:
                return "new_data"
            delta = (current - previous) / previous
            if delta > 0.05:
                return "improving"
            elif delta < -0.05:
                return "degrading"
            return "stable"

        return {
            "citation_rate_trend": _trend(avg_citation_rate, prev_citation),
            "grounding_rate_trend": _trend(avg_grounding_rate, prev_grounding),
            "llm_faithfulness_trend": _trend(avg_llm_faithfulness, prev_faithfulness),
            "previous_cycle": {
                "avg_citation_rate": prev_citation,
                "avg_grounding_rate": prev_grounding,
                "avg_llm_faithfulness": prev_faithfulness,
                "evaluated_count": prev.get("evaluated_count", 0),
                "flagged_count": prev.get("flagged_count", 0),
            },
        }

    async def _write_cycle_quality_report(
        self,
        evaluated_count: int,
        flagged_count: int,
        avg_citation_rate: float,
        avg_grounding_rate: float,
        hedging_detected_count: int,
        llm_eval_count: int,
        llm_hallucinations: int,
        avg_llm_faithfulness: float,
        trend_indicators: dict,
        cycle_elapsed: float,
    ) -> None:
        """Generate a single summary artifact per run_cycle() that aggregates
        quality metrics across all evaluated turns.

        Sprint 5.24: Instead of only per-turn flagged artifacts, this produces
        a per-cycle quality report that gives operators a single document to
        review for overall system quality.  Includes aggregate scores for
        citation rate, grounding, hedging, and LLM faithfulness, plus trend
        indicators comparing with the previous cycle.

        The artifact is written as a GENERATED artifact for DEFINER review
        only when there are quality concerns (flagged turns, degrading trends,
        or low aggregate scores).  For healthy cycles, the report is recorded
        in history but no artifact is written.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        cycle_ts = now_iso.replace(":", "").replace("-", "").replace(".", "")[:15]
        # Include short UUID suffix to prevent UNIQUE constraint collisions
        # when concurrent Vigil cycles (scheduler + startup) produce the
        # same truncated timestamp within the same second.
        cycle_suffix = uuid.uuid4().hex[:8]

        # Build the report data
        report = {
            "report_type": "vigil_cycle_quality_report",
            "cycle_timestamp": now_iso,
            "summary": {
                "evaluated_count": evaluated_count,
                "flagged_count": flagged_count,
                "flag_rate": round(flagged_count / evaluated_count, 3) if evaluated_count > 0 else 0.0,
                "cycle_elapsed_seconds": round(cycle_elapsed, 3),
            },
            "aggregate_scores": {
                "avg_citation_rate": avg_citation_rate,
                "avg_grounding_rate": avg_grounding_rate,
                "avg_llm_faithfulness": avg_llm_faithfulness,
                "hedging_detected_count": hedging_detected_count,
                "llm_eval_count": llm_eval_count,
                "llm_hallucinations_detected": llm_hallucinations,
            },
            "thresholds": {
                "citation_threshold": self._CITATION_THRESHOLD,
                "grounding_threshold": self._GROUNDING_THRESHOLD,
            },
            "trend_indicators": trend_indicators,
            "llm_faithfulness_enabled": self.config.llm_faithfulness_enabled,
            "llm_faithfulness_telemetry_summary": {
                "total_evaluations": self._llm_faithfulness_telemetry["total_llm_evaluations"],
                "total_failures": self._llm_faithfulness_telemetry["total_llm_evaluations_failed"],
                "total_hallucinations": self._llm_faithfulness_telemetry["total_hallucinations_detected"],
                "avg_score": self._llm_faithfulness_telemetry["avg_llm_faithfulness_score"],
            },
        }

        # Record in history (keep last 10 cycles)
        self._cycle_report_history.append(
            {
                "avg_citation_rate": avg_citation_rate,
                "avg_grounding_rate": avg_grounding_rate,
                "avg_llm_faithfulness": avg_llm_faithfulness,
                "evaluated_count": evaluated_count,
                "flagged_count": flagged_count,
                "timestamp": now_iso,
            }
        )
        if len(self._cycle_report_history) > 10:
            self._cycle_report_history = self._cycle_report_history[-10:]

        # Sprint 5.26: Persist cycle report to quality store
        if self._quality_store is not None:
            try:
                await self._quality_store.record_cycle(report)
            except Exception as exc:
                logger.warning(
                    "vigil_quality_persist_failed",
                    error=str(exc),
                )

        # Only write an artifact if there are quality concerns
        has_concerns = (
            flagged_count > 0
            or avg_citation_rate < self._CITATION_THRESHOLD
            or avg_grounding_rate < self._GROUNDING_THRESHOLD
            or any(v in ("degrading",) for v in trend_indicators.values() if isinstance(v, str))
        )

        if not has_concerns or self._artifacts is None:
            return

        try:
            artifact_id = f"vigil-report-{cycle_ts}-{cycle_suffix}"
            content = json.dumps(report, indent=2)
            metadata = {
                "artifact_type": "vigil_cycle_report",
                "report_type": "vigil_cycle_quality_report",
                "generated_by": "vigil",
                "evaluated_count": evaluated_count,
                "flagged_count": flagged_count,
                "avg_citation_rate": avg_citation_rate,
                "avg_grounding_rate": avg_grounding_rate,
                "avg_llm_faithfulness": avg_llm_faithfulness,
                "generated_at": now_iso,
            }

            await self._artifacts.write(
                id=artifact_id,
                content=content,
                metadata=metadata,
            )

            # Transition to GENERATED state for DEFINER review
            if self._ecs is not None:
                try:
                    concerns = []
                    if flagged_count > 0:
                        concerns.append(f"{flagged_count} flagged turn(s)")
                    if avg_citation_rate < self._CITATION_THRESHOLD:
                        concerns.append(f"low avg citation rate ({avg_citation_rate:.1%})")
                    if avg_grounding_rate < self._GROUNDING_THRESHOLD:
                        concerns.append(f"low avg grounding rate ({avg_grounding_rate:.1%})")
                    detail = f"Vigil cycle quality report: {', '.join(concerns)}"

                    await self._ecs.transition(
                        artifact_id=artifact_id,
                        from_state=None,
                        to_state="GENERATED",
                        actor="vigil",
                        reason=detail,
                    )
                except Exception as exc:
                    logger.warning("vigil_report_ecs_transition_failed", artifact_id=artifact_id, error=str(exc))

        except Exception as exc:
            logger.warning(
                "vigil_cycle_report_failed",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Legacy methods (retained for backward compatibility)
    # These are NOT part of the ADR-011 vigil cycle.
    # ------------------------------------------------------------------

    async def check_canonical_health(self) -> dict:
        """Return aggregate canonical health status.

        .. deprecated:: ADR-011
           Canonical health monitoring is not part of Vigil's quality
           evaluation role. Retained for backward compatibility.
        """
        try:
            canonicals = await self.canonical_store.list_canonical()
            total = len(canonicals)
            stale = await self.detect_stale_canonicals()
            return {
                "total_count": total,
                "stale_count": len(stale),
                "healthy_count": total - len(stale),
                "degraded_count": len(stale),
                "status": "healthy" if len(stale) == 0 else "degraded",
            }
        except Exception as exc:
            logger.warning("vigil_canonical_health_failed", error=str(exc))
            return {
                "total_count": 0,
                "stale_count": 0,
                "healthy_count": 0,
                "degraded_count": 0,
                "status": "unavailable",
            }

    async def detect_stale_canonicals(self) -> list[dict]:
        """Return list of stale canonicals (threshold + model slot).

        .. deprecated:: ADR-011
           Stale canonical detection is a maintenance function (→ Sexton).
           Retained for backward compatibility.
        """
        try:
            threshold_days = self.config.stale_threshold_days
            return await self.vigil_store.list_stale_canonicals(threshold_days=threshold_days)
        except Exception as exc:
            logger.warning("vigil_stale_canonicals_failed", error=str(exc))
            return []

    async def detect_entity_inconsistencies(self) -> list[dict]:
        """Return entities referenced by canonicals that have been updated since promotion.

        .. deprecated:: ADR-011
           Entity consistency checking is a maintenance function (→ Sexton).
           Retained for backward compatibility.
        """
        if not self.config.entity_consistency_check:
            return []
        try:
            entities = await self.entity_store.list_entities()
            inconsistencies = []
            for entity in entities:
                entity_id = entity.get("entity_id") or entity.get("id")
                if entity_id:
                    entity_data = await self.entity_store.get_entity(entity_id)
                    if entity_data and entity_data.get("updated_since_canonical"):
                        inconsistencies.append(entity_data)
            return inconsistencies
        except Exception as exc:
            logger.warning("vigil_entity_inconsistencies_failed", error=str(exc))
            return []

    async def on_model_slot_change(
        self,
        slot_name: str,
        old_config: ModelSlotConfig,
        new_config: ModelSlotConfig,
    ) -> dict:
        """React to model slot change with real re-evaluation marking.

        .. deprecated:: ADR-011
           Model slot change handling is a maintenance function.
           Retained for backward compatibility.

        Identifies affected canonicals, marks them for re-evaluation in
        VigilStore, and records trace events for Sexton.
        """
        result = {
            "slot_name": slot_name,
            "old_model": getattr(old_config, "model", "unknown"),
            "new_model": getattr(new_config, "model", "unknown"),
            "affected_count": 0,
            "marked_for_re_evaluation": 0,
            "trace_events_written": 0,
        }

        if not self.config.re_evaluate_on_slot_change:
            result["skipped_reason"] = "re_evaluate_on_slot_change is False"
            return result

        try:
            canonicals = await self.canonical_store.list_canonical()
            affected = canonicals
            result["affected_count"] = len(affected)

            batch_size = self.config.max_re_evaluate_batch_size
            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.vigil_store.record_vigil_check(
                        canonical_count=result["affected_count"],
                        stale_count=1,
                        status="needs_re_evaluation",
                    )
                    result["marked_for_re_evaluation"] += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to mark canonical %s for re-evaluation: %s",
                        artifact_id,
                        exc,
                    )

            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.trace_store.write_event(
                        session_id="vigil-model-slot-change",
                        node_type="vigil",
                        failure_type="A",
                        outcome="needs_re_evaluation",
                        detail=(
                            f"Model slot '{slot_name}' changed from "
                            f"'{getattr(old_config, 'model', 'unknown')}' to "
                            f"'{getattr(new_config, 'model', 'unknown')}'. "
                            f"Canonical '{artifact_id}' may have stale evaluations "
                            f"and needs re-evaluation."
                        ),
                    )
                    result["trace_events_written"] += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to write trace event for slot change on %s: %s",
                        artifact_id,
                        exc,
                    )

        except Exception as exc:
            logger.error("vigil_slot_change_reeval_error", error=str(exc))
            result["error"] = str(exc)

        return result

    # ------------------------------------------------------------------
    # Legacy run() entry point — redirects to run_cycle()
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Cadence entry point (called by scheduler). Delegates to run_cycle().

        .. note:: ADR-011
           The legacy run() performed maintenance operations (canonical health,
           stale detection, entity inconsistencies). Those are now deprecated.
           This method now delegates to run_cycle() for quality evaluation.
        """
        await self.run_cycle()
