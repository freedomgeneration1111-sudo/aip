"""Ask-related types.

Schemas for the source-grounded ask pipeline: source references,
ask results, and source selection types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AskSource = Literal["ingested", "artifacts", "all"]


@dataclass
class SourceReference:
    """A reference to a source used in generating an answer.

    Captures provenance back to the original ingested conversation
    or project artifact, enabling audit trails and verification.
    """

    source_id: str  # chunk_id or artifact_id
    source_type: str  # "conversation_chunk" | "artifact" | "compiled_knowledge"
    title: str  # conversation title or artifact name
    score: float  # retrieval score (lexical rank or vector similarity)
    content_snippet: str  # first ~200 chars of source content
    domain: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class AskResult:
    """Outcome of an ask query against the AIP knowledge substrate.

    Captures the full provenance chain: what was asked, what sources
    were found, what model was used, what answer was generated, and
    whether any artifacts were saved.

    Failure modes are explicit: status indicates the overall outcome
    and errors lists any problems encountered.

    Chunk 5 addition: ``retrieval_degradation`` carries an honest
    account of what retrieval backends were available, degraded, or
    absent.  The system is required to surface this information
    rather than silently pretending retrieval was healthier than it was.

    ADR-017 WS-4 addition: ``web_grounding`` and ``web_sources`` carry
    ephemeral web-grounding provenance.  When ``web_grounding=True``,
    the answer may draw on current web sources in addition to the
    corpus.  ``web_sources`` is a list of dicts (one per fetched
    source) with url, title, retrieved_at, content_hash,
    extraction_method, and warnings.  These are distinct from the
    corpus ``sources`` list so the GUI can render them separately.
    ``web_failures`` carries per-source fetch/extract failures
    (ADR-017 honesty rule: never silently drop a failed source).
    """

    status: str  # "OK" | "NO_PROJECT" | "NO_PROJECT_MEMORY" | "NEEDS_CONFIGURATION"
    # | "MODEL_FAILURE" | "ARTIFACT_SAVE_FAILURE"
    answer: str  # generated answer or error message
    sources: list[SourceReference] = field(default_factory=list)
    model_slot: str = ""
    model_provider: str = ""
    artifact_id: str = ""  # set when --save-artifact succeeds
    session_id: str = ""
    project_id: str = ""
    project_name: str = ""
    prompt: str = ""
    errors: list[str] = field(default_factory=list)
    # Chunk 5: Retrieval honesty — honest degradation metadata
    retrieval_degradation: dict = field(default_factory=dict)
    # Sprint 10: Visible retrieval warnings — human-readable list of
    # retrieval problems surfaced to the user.  Example:
    #   ["Vector channel unavailable", "Graph channel returned 0 results",
    #    "Lexical channel supplied primary evidence"]
    retrieval_warnings: list[str] = field(default_factory=list)
    # ADR-017 WS-4: Web grounding provenance (ephemeral, not written to corpus)
    web_grounding: bool = False
    web_sources: list[dict] = field(default_factory=list)
    web_failures: list[dict] = field(default_factory=list)


__all__ = [
    "AskSource",
    "SourceReference",
    "AskResult",
]
