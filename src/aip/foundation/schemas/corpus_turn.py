"""CorpusTurn schema — the atomic unit of the AIP knowledge corpus.

A CorpusTurn represents one complete user-assistant exchange (a "turn")
in a conversation, or one section of a document. This is the foundational
object for all retrieval, Beast tagging, vector indexing, and knowledge
graph work.

Unlike fixed-size text chunks (which split thoughts mid-sentence),
a turn preserves semantic coherence: the full user question + full
assistant response as a single atomic record.

For documents (markdown, text, PDF sections), the mapping is:
  user_text      → section heading or document title
  assistant_text → section content
  source_model   → "document"

All Beast metadata (domains, tags, importance, bridges) is stored
directly on the turn for efficient querying and re-evaluation.

Sprint 9 additions:
  content_hash   — SHA256 of searchable_text for dedup and integrity
  source_path    — original file path for provenance
  doc_version    — version counter for re-ingest tracking
  embed_fail_count — number of consecutive embedding failures
  last_embed_error — last embedding error message
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class CorpusTurn:
    """A single complete turn in a conversation — the atomic corpus unit.

    Identity fields (all required, no defaults):
      turn_id, conversation_id, conversation_name, turn_index

    Source provenance (all required):
      source_model, source_account, export_date

    Content (both sides preserved, required):
      user_text, assistant_text, turn_timestamp (empty str if unknown)

    Beast-assigned metadata (optional, default empty/unscored/untagged):
      domains, primary_domain, tags, importance, bridges,
      beast_confidence, tagging_version

    Computed (populated at construction if not provided):
      searchable_text = user_text + "\n\n" + assistant_text
      word_count = len(searchable_text.split())

    Layer: foundation only. No adapter/orchestration imports.
    """

    # Identity — all required, no defaults
    turn_id: str  # deterministic: sha256(conv_id + str(turn_index))[:16]
    conversation_id: str  # from source export UUID
    conversation_name: str  # human-readable conversation title
    turn_index: int  # 0-based position within conversation

    # Source provenance — required
    source_model: str  # "claude" | "gpt" | "deepseek" | "glm" | "gemini" | "grok" | "document"
    source_account: str  # identifier for which export file (e.g. "claude_export_2026")
    export_date: str  # ISO date string of when export was made

    # Content — both sides preserved, required
    user_text: str  # the human's message (may be long)
    assistant_text: str  # the AI's response (may be very long)
    turn_timestamp: str  # ISO timestamp of when turn occurred, empty string if unknown
    thinking_text: str = ""
    # Claude extended thinking blocks, preserved separately from assistant_text.
    # Populated by claude_parser when thinking content blocks are present.
    # Beast can retrieve and surface this as distinct reasoning context.
    # Empty string when no thinking blocks present or source model does not
    # support extended thinking.

    # Beast-assigned metadata — all optional, populated after ingestion
    domains: list[str] = field(default_factory=list)
    # Multi-domain allowed: ["nbcm", "theology"]
    # Valid domains: aip_loom, nbcm, theology, freedom_gen, freelance, misc
    primary_domain: str = ""
    # Beast's single best assignment, empty until Beast tags
    tags: list[str] = field(default_factory=list)
    # Freeform topic tags, Beast-assigned
    importance: float = 0.0
    # 0.0-1.0, Beast-scored. 0.0 = unscored, not "unimportant"
    bridges: list[str] = field(default_factory=list)
    # Cross-domain connection markers: ["nbcm→theology", "aip→freelance"]
    # Only assigned when Beast detects genuine domain bridging in the turn
    beast_confidence: float = 0.0
    # Beast's confidence in its tagging, 0.0-1.0
    tagging_version: int = 0
    # Increments each time Beast re-evaluates this turn
    # 0 = never tagged, 1 = first tagging, 2+ = re-evaluated

    embedded: int = 0
    # 0 = not embedded, 1 = has vector in vector store (for embedding pipeline)

    embedding_model: str = ""
    # Name of the model used to produce this turn's embedding (e.g. "nomic-embed-text").
    # Used to detect stale embeddings when the model slot changes.

    needs_reembed: int = 0
    # 0 = current, 1 = flagged for re-embedding (embedding model changed)

    last_embed_at: str | None = None
    # ISO timestamp of when this turn was last embedded. None = never embedded.

    metadata_json: str = "{}"
    # Arbitrary JSON metadata for Vigil provenance tracking and future extensions.
    # Stored as a TEXT column in SQLite (default '{}'). Vigil can write
    # quality scores, review decisions, and classification provenance here
    # without schema changes.
    # Sprint 9: Also used for document provenance:
    #   section_heading — heading text for document sections
    #   offset_page — page number or character offset
    #   ingest_timestamp — when this turn was ingested
    #   previous_hash — content_hash of previous version (if re-ingested)

    # Sprint 9: Document identity and provenance
    content_hash: str = ""
    # SHA256 hex digest of searchable_text. Used for dedup detection
    # and content integrity verification. Empty string = not yet computed.
    source_path: str = ""
    # Original file path or URI where this content came from.
    # e.g. "docs/ARCHITECTURE.md" or "exports/claude/conversations.json".
    # Empty for API-sourced turns.
    doc_version: int = 0
    # Version counter incremented on re-ingest when content changes.
    # 0 = original ingest, 1+ = re-ingested with content changes.

    # Sprint 9: Embedding backfill reliability
    embed_fail_count: int = 0
    # Number of consecutive embedding failures. Reset to 0 on success.
    # Turns with embed_fail_count > 0 appear in the backfill queue.

    last_embed_error: str = ""
    # Last embedding error message. Empty string = no error or last embed
    # succeeded. Useful for diagnosing systematic embedding failures.

    # ADR-008 Rev 3.1 §A12: Book revision chain (M001 migration).
    # For manuscript chapters: when a new chapter revision is created, the
    # old turn is ARCHIVED and the new turn sets revision_parent_id = old_turn_id.
    # This preserves the revision chain for history queries while excluding
    # archived turns from default retrieval. None = original (no parent).
    # Also used by code corpus (M001) and document corpus for revision tracking.
    revision_parent_id: str | None = None

    # Computed fields — populated at ingestion time
    searchable_text: str = ""
    # user_text + "\n\n" + assistant_text + (thinking if present)
    # This is what FTS5 and vector store index
    word_count: int = 0
    # len(searchable_text.split()) — for importance scoring

    def __post_init__(self):
        if not self.searchable_text:
            parts = [self.user_text, self.assistant_text]
            if self.thinking_text:
                parts.append(self.thinking_text)
            self.searchable_text = "\n\n".join(p for p in parts if p.strip()).strip()
        if not self.word_count:
            self.word_count = len(self.searchable_text.split())
        # Compute content_hash if not provided
        if not self.content_hash and self.searchable_text:
            self.content_hash = hashlib.sha256(self.searchable_text.encode()).hexdigest()[:32]


def make_turn_id(conversation_id: str, turn_index: int) -> str:
    """Generate deterministic 16-char hex turn_id from conv_id + index.

    Uses SHA256 for collision resistance while keeping ids short.
    """
    key = f"{conversation_id}:{turn_index}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def compute_content_hash(text: str) -> str:
    """Compute SHA256 content hash for dedup and integrity checking.

    Returns first 32 hex chars of SHA256 digest (128 bits of entropy).
    Truncated for storage efficiency while maintaining negligible collision risk
    at corpus scales (< 1 billion turns).
    """
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def make_document_conversation_id(source_path: str) -> str:
    """Generate stable conversation_id for a document source path.

    Uses SHA256 of the path so the same document always maps to the same
    conversation_id regardless of absolute path prefix. This enables
    re-ingest detection when the same file is ingested from different
    mount points or working directories.
    """
    # Use basename + parent dir for stability across path prefixes

    # Normalize: use relative-ish path (last 3 components)
    parts = source_path.replace("\\", "/").split("/")
    stable_key = "/".join(parts[-3:]) if len(parts) > 3 else source_path
    return f"doc:{hashlib.sha256(stable_key.encode()).hexdigest()[:12]}"
