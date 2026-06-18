"""Code corpus ingest pipeline — ADR-008 Rev 3.1 §8 Chunk 7.

Walks a Python source directory, parses each file with the AST parser,
and writes CorpusTurns to the codeforge corpus's CorpusTurnStore. Uses
content_hash for stale detection — unchanged turns are skipped.

Layer: adapter. Imports from orchestration (the AST parser) via the
container-mediated pattern (the parser is injected, not imported directly
by routes). For CLI use, the pipeline imports the parser directly.

Stale detection (§8 Chunk 7):
  - For each CodeTurnSpec, check if a turn with the same conversation_id +
    turn_index exists with the same content_hash. If so, skip (no re-embed).
  - If the content_hash differs, the old turn is superseded (ARCHIVED) and
    the new turn is written as GENERATED.
  - If no existing turn, write as new GENERATED.

Skip rules (delegated to parser):
  - .pyi files (type stubs)
  - test_*.py and *_test.py files
  - SyntaxError: logged, skipped, never fails the pipeline
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aip.orchestration.ingestion.parsers.python_ast_parser import (
    make_code_corpus_turn,
    parse_python_file,
    should_skip_file,
)

logger = logging.getLogger(__name__)


async def ingest_python_directory(
    source_dir: Path,
    turn_store: Any,
    *,
    corpus_id: str = "codeforge",
    skip_existing: bool = True,
) -> dict[str, int]:
    """Ingest a Python directory into the code corpus.

    Walks source_dir recursively, parses each .py file, and writes CorpusTurns
    to turn_store. Uses content_hash for stale detection.

    Args:
        source_dir: root directory to scan for .py files.
        turn_store: CorpusTurnStore for the codeforge corpus.
        corpus_id: the corpus_id (for logging; default "codeforge").
        skip_existing: if True (default), skip turns whose content_hash
            matches an existing turn. If False, re-write all turns.

    Returns:
        Dict with counts: {"files_scanned", "files_skipped", "files_parsed",
        "turns_created", "turns_skipped_stale", "turns_superseded"}.
    """
    counts = {
        "files_scanned": 0,
        "files_skipped": 0,
        "files_parsed": 0,
        "turns_created": 0,
        "turns_skipped_stale": 0,
        "turns_superseded": 0,
    }

    # Collect all .py files
    py_files: list[Path] = []
    for path in source_dir.rglob("*.py"):
        counts["files_scanned"] += 1
        if should_skip_file(path):
            counts["files_skipped"] += 1
            continue
        py_files.append(path)

    logger.info(
        "code_ingest_start corpus=%s dir=%s files_scanned=%d files_skipped=%d",
        corpus_id,
        str(source_dir),
        counts["files_scanned"],
        counts["files_skipped"],
    )

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("code_ingest_read_failed path=%s error=%s", py_file, exc)
            continue

        specs = parse_python_file(source, str(py_file))
        if not specs:
            continue

        counts["files_parsed"] += 1

        # Write each spec as a CorpusTurn
        for turn_index, spec in enumerate(specs):
            turn = make_code_corpus_turn(spec, turn_index=turn_index)

            if skip_existing:
                existing = await turn_store.get_turn(turn.turn_id)
                if existing is not None and existing.content_hash == turn.content_hash:
                    counts["turns_skipped_stale"] += 1
                    continue
                # Content changed — write the new turn (supersedes the old one)
                if existing is not None:
                    counts["turns_superseded"] += 1

            await turn_store.write_turn(turn)
            counts["turns_created"] += 1

    logger.info(
        "code_ingest_complete corpus=%s files_parsed=%d turns_created=%d skipped_stale=%d superseded=%d",
        corpus_id,
        counts["files_parsed"],
        counts["turns_created"],
        counts["turns_skipped_stale"],
        counts["turns_superseded"],
    )

    return counts


async def ingest_python_file(
    source_path: Path,
    turn_store: Any,
    *,
    corpus_id: str = "codeforge",
    skip_existing: bool = True,
) -> dict[str, int]:
    """Ingest a single Python file into the code corpus.

    Convenience wrapper for ingest_python_directory with a single file.
    """
    if should_skip_file(source_path):
        return {
            "files_scanned": 1,
            "files_skipped": 1,
            "files_parsed": 0,
            "turns_created": 0,
            "turns_skipped_stale": 0,
            "turns_superseded": 0,
        }

    source = source_path.read_text(encoding="utf-8")
    specs = parse_python_file(source, str(source_path))

    counts = {
        "files_scanned": 1,
        "files_skipped": 0,
        "files_parsed": 1 if specs else 0,
        "turns_created": 0,
        "turns_skipped_stale": 0,
        "turns_superseded": 0,
    }

    for turn_index, spec in enumerate(specs):
        turn = make_code_corpus_turn(spec, turn_index=turn_index)

        if skip_existing:
            existing = await turn_store.get_turn(turn.turn_id)
            if existing is not None and existing.content_hash == turn.content_hash:
                counts["turns_skipped_stale"] += 1
                continue
            # Content changed — write the new turn (supersedes the old one)
            if existing is not None:
                counts["turns_superseded"] += 1

        await turn_store.write_turn(turn)
        counts["turns_created"] += 1

    return counts
