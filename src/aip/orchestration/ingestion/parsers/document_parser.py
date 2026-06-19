"""Document parser — converts markdown/text documents into CorpusTurns.

Sprint 9: Canonical corpus ingest for project documentation.

Unlike conversation parsers that produce ImportedConversation objects,
this parser directly creates CorpusTurn objects with source_model="document".
Each section (defined by markdown headings) becomes a separate turn, enabling
granular retrieval and provenance tracking.

Section-to-turn mapping:
  user_text      → section heading (e.g., "# Architecture Overview")
  assistant_text → section content (everything under the heading)
  source_model   → "document"
  conversation_id → derived from source_path (stable across re-ingests)
  turn_index     → sequential section index within the document
  content_hash   → SHA256 of searchable_text (auto-computed by CorpusTurn)
  source_path    → original file path
  metadata_json  → section_heading, offset, ingest_timestamp

For plain text files without headings, the entire file becomes a single turn.
For PDF files (if support available), each page becomes a turn.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from aip.logging import get_logger

logger = get_logger(__name__)

from aip.foundation.schemas.corpus_turn import (  # noqa: E402
    CorpusTurn,
    make_document_conversation_id,
    make_turn_id,
)

# Markdown heading pattern: # Heading, ## Heading, etc.
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def parse_markdown_document(
    text: str,
    source_path: str,
    source_account: str = "file_ingest",
    export_date: str | None = None,
) -> list[CorpusTurn]:
    """Parse a markdown document into CorpusTurns, one per section.

    Splits the document at heading boundaries (# through ######). Each section
    becomes a CorpusTurn with the heading as user_text and the content as
    assistant_text. This preserves document structure while enabling granular
    retrieval of specific sections.

    If no headings are found, the entire document becomes a single turn with
    the filename as the "heading".

    Args:
        text: The markdown content.
        source_path: Path to the source file (for provenance).
        source_account: Identifier for this ingest batch.
        export_date: ISO date string (defaults to today).

    Returns:
        List of CorpusTurn objects, one per section.
    """
    if not export_date:
        export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conversation_id = make_document_conversation_id(source_path)
    conversation_name = _derive_conversation_name(text, source_path)

    # Split document into sections at headings
    sections = _split_at_headings(text)

    if not sections:
        # Entire document as one turn (no headings found)
        sections = [(conversation_name, text)]

    turns: list[CorpusTurn] = []
    char_offset = 0

    for turn_index, (heading, content) in enumerate(sections):
        content_stripped = content.strip()
        if not content_stripped:
            char_offset += len(content) + len(heading)
            continue

        # Build metadata with provenance
        metadata: dict[str, Any] = {
            "section_heading": heading,
            "offset": char_offset,
            "ingest_timestamp": datetime.now(timezone.utc).isoformat(),
            "source_type": "markdown_document",
        }

        turn = CorpusTurn(
            turn_id=make_turn_id(conversation_id, turn_index),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            turn_index=turn_index,
            source_model="document",
            source_account=source_account,
            export_date=export_date,
            source_path=source_path,
            user_text=heading,
            assistant_text=content_stripped,
            turn_timestamp="",
            metadata_json=json.dumps(metadata),
        )

        turns.append(turn)
        char_offset += len(heading) + len(content)

    return turns


def parse_text_document(
    text: str,
    source_path: str,
    source_account: str = "file_ingest",
    export_date: str | None = None,
    max_chars: int = 4000,
    overlap_chars: int = 200,
) -> list[CorpusTurn]:
    """Parse a plain text document into CorpusTurns.

    For plain text (no markdown headings), splits by paragraph boundaries
    with a maximum character limit per turn. Long documents are split into
    multiple overlapping turns.

    Args:
        text: The plain text content.
        source_path: Path to the source file (for provenance).
        source_account: Identifier for this ingest batch.
        export_date: ISO date string (defaults to today).
        max_chars: Maximum characters per turn (default 4000).
        overlap_chars: Overlap between turns (default 200).

    Returns:
        List of CorpusTurn objects.
    """
    if not export_date:
        export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conversation_id = make_document_conversation_id(source_path)
    conversation_name = _derive_conversation_name(text, source_path)

    # Split into chunks at paragraph boundaries
    chunks = _split_text_by_paragraphs(text, max_chars, overlap_chars)

    turns: list[CorpusTurn] = []
    char_offset = 0

    for turn_index, chunk in enumerate(chunks):
        chunk_stripped = chunk.strip()
        if not chunk_stripped:
            continue

        # First line or first 80 chars as the "heading"
        first_line = chunk_stripped.split("\n")[0][:80]
        heading = first_line if first_line else f"Section {turn_index + 1}"

        metadata: dict[str, Any] = {
            "section_heading": heading,
            "offset": char_offset,
            "ingest_timestamp": datetime.now(timezone.utc).isoformat(),
            "source_type": "text_document",
        }

        turn = CorpusTurn(
            turn_id=make_turn_id(conversation_id, turn_index),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            turn_index=turn_index,
            source_model="document",
            source_account=source_account,
            export_date=export_date,
            source_path=source_path,
            user_text=heading,
            assistant_text=chunk_stripped,
            turn_timestamp="",
            metadata_json=json.dumps(metadata),
        )

        turns.append(turn)
        char_offset += len(chunk)

    return turns


def parse_document_file(
    file_path: str,
    source_account: str = "file_ingest",
    export_date: str | None = None,
) -> list[CorpusTurn]:
    """Parse a document file into CorpusTurns (auto-detect format).

    Supports:
    - Markdown (.md, .markdown): section-based splitting
    - Plain text (.txt, .text, .log): paragraph-based splitting
    - PDF (.pdf): not yet supported (graceful skip)
    - Other: treated as plain text

    Args:
        file_path: Path to the file to parse.
        source_account: Identifier for this ingest batch.
        export_date: ISO date string (defaults to today).

    Returns:
        List of CorpusTurn objects.

    Raises:
        FileNotFoundError: If file_path does not exist.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".md", ".markdown"):
        return parse_markdown_document(content, file_path, source_account, export_date)
    elif ext == ".pdf":
        # PDF support not yet available — attempt text extraction
        # If PyPDF2 or similar is available, use it; otherwise skip gracefully
        return _try_parse_pdf(file_path, source_account, export_date)
    else:
        # Treat as plain text (includes .txt, .text, .log, .toml, .json, etc.)
        return parse_text_document(content, file_path, source_account, export_date)


def _try_parse_pdf(
    file_path: str,
    source_account: str = "file_ingest",
    export_date: str | None = None,
) -> list[CorpusTurn]:
    """Attempt to parse a PDF file. Returns empty list if no PDF library available.

    Tries PyPDF2/pdfplumber if installed. If neither is available, returns
    an empty list with a warning rather than failing.
    """
    if not export_date:
        export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Try pypdf first (lighter dependency)
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(file_path)
        conversation_id = make_document_conversation_id(file_path)
        conversation_name = os.path.basename(file_path)
        turns: list[CorpusTurn] = []

        for page_num, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            if not page_text.strip():
                continue

            metadata: dict[str, Any] = {
                "section_heading": f"Page {page_num + 1}",
                "offset_page": page_num + 1,
                "ingest_timestamp": datetime.now(timezone.utc).isoformat(),
                "source_type": "pdf_document",
            }

            turn = CorpusTurn(
                turn_id=make_turn_id(conversation_id, page_num),
                conversation_id=conversation_id,
                conversation_name=conversation_name,
                turn_index=page_num,
                source_model="document",
                source_account=source_account,
                export_date=export_date,
                source_path=file_path,
                user_text=f"Page {page_num + 1}",
                assistant_text=page_text.strip(),
                turn_timestamp="",
                metadata_json=json.dumps(metadata),
            )
            turns.append(turn)

        return turns
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pdf_parse_pymupdf_failed", file_path=file_path, error=str(exc))

    # Try pdfplumber
    try:
        import pdfplumber  # type: ignore

        conversation_id = make_document_conversation_id(file_path)
        conversation_name = os.path.basename(file_path)
        turns = []

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                if not page_text.strip():
                    continue

                metadata = {
                    "section_heading": f"Page {page_num + 1}",
                    "offset_page": page_num + 1,
                    "ingest_timestamp": datetime.now(timezone.utc).isoformat(),
                    "source_type": "pdf_document",
                }

                turn = CorpusTurn(
                    turn_id=make_turn_id(conversation_id, page_num),
                    conversation_id=conversation_id,
                    conversation_name=conversation_name,
                    turn_index=page_num,
                    source_model="document",
                    source_account=source_account,
                    export_date=export_date,
                    source_path=file_path,
                    user_text=f"Page {page_num + 1}",
                    assistant_text=page_text.strip(),
                    turn_timestamp="",
                    metadata_json=json.dumps(metadata),
                )
                turns.append(turn)

        return turns
    except ImportError:
        return []  # No PDF library available — graceful skip
    except Exception as exc:
        logger.warning("pdf_parse_pdfplumber_failed", file_path=file_path, error=str(exc))
        return []


def _split_at_headings(text: str) -> list[tuple[str, str]]:
    """Split markdown text into (heading, content) pairs at heading boundaries.

    Returns list of (heading_text, section_content) tuples. Content includes
    everything from after the heading line to the start of the next heading.
    The first section (before any heading) uses the document title as heading.
    """
    matches = list(_HEADING_PATTERN.finditer(text))

    if not matches:
        # No headings — return entire text as one section
        return [] if not text.strip() else [("", text)]

    sections: list[tuple[str, str]] = []

    # Content before first heading (preamble)
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(("Introduction", preamble))

    # Sections defined by headings
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        content_start = match.end()

        if i + 1 < len(matches):
            content_end = matches[i + 1].start()
        else:
            content_end = len(text)

        content = text[content_start:content_end].strip()
        sections.append((heading, content))

    return sections


def _split_text_by_paragraphs(
    text: str,
    max_chars: int = 4000,
    overlap_chars: int = 200,
) -> list[str]:
    """Split plain text into chunks at paragraph boundaries.

    Respects paragraph boundaries (double newlines) when possible.
    Falls back to sentence boundaries and then character boundaries
    for very long paragraphs.
    """
    if not text.strip():
        return []

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_len = len(para)

        # If single paragraph exceeds max_chars, split it
        if para_len > max_chars:
            # Flush current chunk
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0

            # Split long paragraph by sentences
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                if current_len + len(sentence) > max_chars and current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    # Overlap: keep last part
                    overlap_text = "\n\n".join(current_chunk)[-overlap_chars:] if overlap_chars > 0 else ""
                    current_chunk = [overlap_text] if overlap_text else []
                    current_len = len(overlap_text)
                current_chunk.append(sentence)
                current_len += len(sentence)
        else:
            if current_len + para_len > max_chars and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                overlap_text = "\n\n".join(current_chunk)[-overlap_chars:] if overlap_chars > 0 else ""
                current_chunk = [overlap_text] if overlap_text else []
                current_len = len(overlap_text)

            current_chunk.append(para)
            current_len += para_len

    # Flush remaining
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def _derive_conversation_name(text: str, source_path: str) -> str:
    """Derive a human-readable conversation name from the document."""
    # Try first heading
    match = _HEADING_PATTERN.search(text)
    if match:
        return match.group(2).strip()[:100]

    # Try first non-empty line
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) <= 100:
            return line

    # Fall back to filename
    basename = os.path.basename(source_path)
    name, _ = os.path.splitext(basename)
    return name.replace("_", " ").replace("-", " ").title() if name else "Untitled Document"
