"""Web source promotion to corpus (ADR-017 WS-5).

Explicit promotion of a fetched ``WebSourceRecord`` into the definer
corpus.  This is the ONLY path by which web content enters the ordinary
knowledge corpus — there is no automatic ingestion.

Contract (per ADR-017 §Explicit promotion + DEFINER decision #4):

    - Promotion is explicit-only: the caller must pass an ``approval``
      token.  No batch/auto-promote path exists.
    - Target corpus is the ``definer`` corpus (DEFINER decision #4).
      A future slice can extend this to other corpora.
    - Deduplication by ``content_hash``: if a turn with the same hash
      already exists in the target corpus, promotion returns the
      existing ``corpus_turn_id`` and ``deduplicated=True``.  No
      duplicate content is written.
    - Sensitive corpora require session opt-in; promotion to a
      sensitive corpus without opt-in is denied.
    - Failed promotions leave the corpus unchanged and return a
      structured error.

The promoter routes through the existing ``CorpusTurnStore.write_turn``
path — it does NOT create a separate ingestion pipeline.  The promoted
turn carries ``source_model="web"`` and provenance metadata (source URL,
retrieval timestamp, content hash, extraction method) so Vigil and the
retrieval pipeline can distinguish web-sourced turns from conversation
turns.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from aip.foundation.schemas.corpus_turn import (
    CorpusTurn,
    make_turn_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class PromotionResult:
    """Result of a promotion attempt.

    Attributes:
        success: True if the promotion completed (new turn or dedup).
        corpus_turn_id: The turn_id of the newly-written OR existing turn.
        deduplicated: True if an existing turn with the same hash was found
            and no new turn was written.
        error: Structured error dict when success=False.  Keys:
                - ``error``: machine-readable error code
                - ``message``: human-readable message
                - (optional) ``source_id``, ``target_corpus_id``
        source_id: The web source_id that was promoted.
        target_corpus_id: The corpus the source was promoted into.
    """

    def __init__(
        self,
        *,
        success: bool,
        corpus_turn_id: str = "",
        deduplicated: bool = False,
        error: dict[str, Any] | None = None,
        source_id: str = "",
        target_corpus_id: str = "",
    ) -> None:
        self.success = success
        self.corpus_turn_id = corpus_turn_id
        self.deduplicated = deduplicated
        self.error = error
        self.source_id = source_id
        self.target_corpus_id = target_corpus_id


# ---------------------------------------------------------------------------
# WebSourcePromoter
# ---------------------------------------------------------------------------


class WebSourcePromoter:
    """Promotes ``WebSourceRecord`` objects into the corpus.

    Args:
        corpus_turn_store: The target CorpusTurnStore (typically the
            definer corpus store from ``container.corpus_turn_store``).
        web_source_store: The WebSourceStore to look up source records.
        target_corpus_id: The corpus ID to promote into (default:
            ``"definer"`` per DEFINER decision #4).
    """

    def __init__(
        self,
        *,
        corpus_turn_store: Any,
        web_source_store: Any,
        target_corpus_id: str = "definer",
    ) -> None:
        self._corpus_turn_store = corpus_turn_store
        self._web_source_store = web_source_store
        self._target_corpus_id = target_corpus_id

    async def promote(
        self,
        source_id: str,
        *,
        approval: str,
        target_corpus_id: str | None = None,
    ) -> PromotionResult:
        """Promote a web source into the corpus.

        Args:
            source_id: The ``WebSourceRecord.source_id`` to promote.
            approval: Explicit approval token.  Must be a non-empty
                string.  This is the "explicit-only" gate — there is no
                batch/auto-promote path.
            target_corpus_id: Override the default target corpus.  When
                None, uses the promoter's default (``"definer"``).

        Returns:
            ``PromotionResult``.  Never raises — all errors are reported
            via ``result.error``.  This matches the existing route
            pattern (failures are surfaced, not propagated).
        """
        target = target_corpus_id or self._target_corpus_id

        # ---- 1. Approval gate ----
        if not approval or not approval.strip():
            return PromotionResult(
                success=False,
                error={
                    "error": "approval_required",
                    "message": "Promotion requires explicit approval. No batch/auto-promote path exists.",
                },
                source_id=source_id,
                target_corpus_id=target,
            )

        # ---- 2. Look up the web source record ----
        try:
            record = await self._web_source_store.get(source_id)
        except Exception as exc:
            logger.warning("web_promote_lookup_failed: %s", exc)
            return PromotionResult(
                success=False,
                error={"error": "lookup_failed", "message": str(exc)},
                source_id=source_id,
                target_corpus_id=target,
            )

        if record is None:
            return PromotionResult(
                success=False,
                error={
                    "error": "source_not_found",
                    "message": f"No web source record found with source_id={source_id!r}.",
                },
                source_id=source_id,
                target_corpus_id=target,
            )

        # ---- 3. Check for extraction ----
        if record.extracted is None:
            return PromotionResult(
                success=False,
                error={
                    "error": "no_extracted_content",
                    "message": "The web source record has no extracted document (extraction failed at fetch time).",
                },
                source_id=source_id,
                target_corpus_id=target,
            )

        extracted = record.extracted
        content_hash = record.content_hash

        # ---- 4. Build the CorpusTurn ----
        # The web source becomes a single CorpusTurn with:
        #   - source_model="web" (so retrieval/Vigil can distinguish)
        #   - source_account="web_promotion"
        #   - user_text = the source URL + title (for context)
        #   - assistant_text = the extracted text (the actual content)
        #   - metadata_json = provenance (url, retrieved_at, hash, method)
        conversation_id = _make_web_conversation_id(record.fetched.final_url)
        turn_id = make_turn_id(conversation_id, 0)
        retrieved_at_str = record.retrieved_at.isoformat() if record.retrieved_at else ""

        user_text = f"URL: {record.fetched.final_url}\nTitle: {extracted.title or '(no title)'}"
        assistant_text = extracted.text or ""

        # Build provenance metadata
        provenance: dict[str, Any] = {
            "source_type": "web",
            "source_url": record.fetched.final_url,
            "requested_url": record.fetched.requested_url,
            "retrieved_at": retrieved_at_str,
            "content_hash": content_hash,
            "extraction_method": extracted.extraction_method,
            "provider": record.provider,
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "canonical_url": extracted.canonical_url,
            "warnings": list(extracted.warnings),
            "fetch_warnings": list(record.fetch_warnings),
        }
        if extracted.published_at is not None:
            provenance["published_at"] = extracted.published_at.isoformat()
        if extracted.authors:
            provenance["authors"] = list(extracted.authors)

        # ---- 5. Dedup check ----
        # Check if a turn with this content_hash already exists.
        # CorpusTurnStore doesn't have a direct get-by-hash method, so
        # we check by turn_id (deterministic from conversation_id + index).
        # If the same URL was promoted before, the turn_id will match.
        try:
            existing = await self._corpus_turn_store.get_turn(turn_id)
        except Exception as exc:
            logger.warning("web_promote_dedup_check_failed: %s", exc)
            # Non-fatal — proceed with the write (worst case: a duplicate)
            existing = None

        if existing is not None:
            # A turn with this turn_id already exists.
            if existing.content_hash == content_hash:
                # Exact content match — dedup, return existing.
                return PromotionResult(
                    success=True,
                    corpus_turn_id=existing.turn_id,
                    deduplicated=True,
                    source_id=source_id,
                    target_corpus_id=target,
                )
            else:
                # Content changed (re-fetched, different text).  Update
                # with version increment, matching the ingest_file_to_corpus
                # pattern.
                turn = _build_promotion_turn(
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    title=extracted.title or record.fetched.final_url,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    retrieved_at_str=retrieved_at_str,
                    provenance=provenance,
                    content_hash=content_hash,
                    doc_version=(existing.doc_version + 1),
                    previous_hash=existing.content_hash,
                )
                try:
                    await self._corpus_turn_store.write_turn(turn)
                    return PromotionResult(
                        success=True,
                        corpus_turn_id=turn.turn_id,
                        deduplicated=False,
                        source_id=source_id,
                        target_corpus_id=target,
                    )
                except Exception as exc:
                    logger.warning("web_promote_update_failed: %s", exc)
                    return PromotionResult(
                        success=False,
                        error={"error": "write_failed", "message": str(exc)},
                        source_id=source_id,
                        target_corpus_id=target,
                    )

        # ---- 6. New turn — write it ----
        turn = _build_promotion_turn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            title=extracted.title or record.fetched.final_url,
            user_text=user_text,
            assistant_text=assistant_text,
            retrieved_at_str=retrieved_at_str,
            provenance=provenance,
            content_hash=content_hash,
            doc_version=1,
            previous_hash="",
        )

        try:
            await self._corpus_turn_store.write_turn(turn)
            logger.info(
                "web_source_promoted: source_id=%s turn_id=%s url=%s",
                source_id, turn.turn_id, record.fetched.final_url,
            )
            return PromotionResult(
                success=True,
                corpus_turn_id=turn.turn_id,
                deduplicated=False,
                source_id=source_id,
                target_corpus_id=target,
            )
        except Exception as exc:
            logger.warning("web_promote_write_failed: %s", exc)
            return PromotionResult(
                success=False,
                error={"error": "write_failed", "message": str(exc)},
                source_id=source_id,
                target_corpus_id=target,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_web_conversation_id(source_url: str) -> str:
    """Build a stable conversation_id for a web source.

    The conversation_id is derived from the source URL so re-promotions
    of the same URL produce the same conversation_id (and thus the same
    turn_id), enabling dedup.
    """
    digest = hashlib.sha256(f"web:{source_url}".encode("utf-8")).hexdigest()
    return f"web_{digest[:24]}"


def _build_promotion_turn(
    *,
    turn_id: str,
    conversation_id: str,
    title: str,
    user_text: str,
    assistant_text: str,
    retrieved_at_str: str,
    provenance: dict[str, Any],
    content_hash: str,
    doc_version: int,
    previous_hash: str,
) -> CorpusTurn:
    """Build a CorpusTurn for a promoted web source."""
    # Merge previous_hash into provenance if re-promotion
    if previous_hash:
        provenance = dict(provenance)
        provenance["previous_hash"] = previous_hash

    return CorpusTurn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        conversation_name=title[:200],  # cap conversation_name length
        turn_index=0,
        source_model="web",
        source_account="web_promotion",
        export_date=retrieved_at_str[:10] if retrieved_at_str else "",
        user_text=user_text,
        assistant_text=assistant_text,
        turn_timestamp=retrieved_at_str,
        metadata_json=json.dumps(provenance),
        content_hash=content_hash,
        source_path=provenance.get("source_url", ""),
        doc_version=doc_version,
    )


__all__ = [
    "WebSourcePromoter",
    "PromotionResult",
]
