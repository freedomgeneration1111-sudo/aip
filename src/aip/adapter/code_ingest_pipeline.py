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

from aip.adapter.python_ast_parser import (
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
    graph_store: Any = None,
) -> dict[str, int]:
    """Ingest a Python directory into the code corpus.

    Walks source_dir recursively, parses each .py file, and writes CorpusTurns
    to turn_store. Uses content_hash for stale detection.

    Phase β-1 (2026-07-23): when graph_store is provided, also builds code
    dependency graph nodes (FUNCTION/CLASS) + edges (imports/calls).

    Args:
        source_dir: root directory to scan for .py files.
        turn_store: CorpusTurnStore for the codeforge corpus.
        corpus_id: the corpus_id (for logging; default "codeforge").
        skip_existing: if True (default), skip turns whose content_hash
            matches an existing turn. If False, re-write all turns.
        graph_store: optional per-corpus GraphStore. When provided, the
            pipeline builds code dependency graph nodes + edges after
            writing turns (Phase β-1).

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

    all_specs: list = []  # Phase β-1: collect for graph building

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
        all_specs.extend(specs)  # Phase β-1

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

    # Phase β-1 (2026-07-23): build code dependency graph if a graph_store
    # is provided. Creates FUNCTION/CLASS nodes + imports/calls edges.
    if graph_store is not None and all_specs:
        try:
            graph_counts = await build_code_graph(
                specs=all_specs,
                graph_store=graph_store,
                corpus_id=corpus_id,
            )
            counts["graph_nodes"] = graph_counts["nodes_created"]
            counts["graph_edges"] = graph_counts["edges_created"]
        except Exception as exc:
            logger.warning("code_graph_build_failed corpus=%s error=%s", corpus_id, exc)
            counts["graph_nodes"] = 0
            counts["graph_edges"] = 0

    return counts


async def build_code_graph(
    specs: list,
    graph_store: Any,
    *,
    corpus_id: str = "codeforge",
) -> dict[str, int]:
    """Build code dependency graph nodes + edges from parsed specs.

    Phase β-1 (2026-07-23). For each CodeTurnSpec, creates:
      - A graph node (entity_type=FUNCTION|CLASS|MODULE_REGISTRATION)
      - `imports` edges from the node to each imported module
      - `calls` edges from the node to each called function

    The graph_store is the per-corpus GraphStore built by
    CorpusStoreFactory (Phase α-5). Nodes are idempotent (upsert by
    qualified_name).

    Returns a counts dict: {"nodes_created", "edges_created"}.
    """
    from aip.adapter.graph_store import GraphNode, GraphEdge

    counts = {"nodes_created": 0, "edges_created": 0}

    # Map kind → entity_type
    kind_to_type = {
        "function": "FUNCTION",
        "class": "CLASS",
        "module_registration": "MODULE_REGISTRATION",
    }

    for spec in specs:
        entity_type = kind_to_type.get(spec.kind, "FUNCTION")
        node_id = spec.qualified_name.replace(".", "_").lower()

        # Create the node
        node = GraphNode(
            id=node_id,
            entity_type=entity_type,
            canonical_name=spec.qualified_name,
            domain=spec.kind,
            confidence=1.0,
            source="code_ast_parser",
            metadata={
                "source_path": spec.source_path,
                "kind": spec.kind,
                **spec.metadata,
            },
        )
        try:
            await graph_store.upsert_node(node)
            counts["nodes_created"] += 1
        except Exception as exc:
            logger.warning("code_graph_node_failed name=%s error=%s", spec.qualified_name, exc)

        # Create `imports` edges
        for imp in (spec.imports or []):
            target_id = imp.replace(".", "_").lower()
            edge = GraphEdge(
                id=f"{node_id}__imports__{target_id}",
                source_id=node_id,
                target_id=target_id,
                relationship_type="imports",
                confidence=1.0,
                weight=1.0,
            )
            try:
                await graph_store.upsert_edge(edge)
                counts["edges_created"] += 1
            except Exception:
                pass  # best-effort; edge to non-existent node is OK

        # Create `calls` edges
        for call in (spec.calls or []):
            target_id = call.replace(".", "_").lower()
            edge = GraphEdge(
                id=f"{node_id}__calls__{target_id}",
                source_id=node_id,
                target_id=target_id,
                relationship_type="calls",
                confidence=0.8,
                weight=0.8,
            )
            try:
                await graph_store.upsert_edge(edge)
                counts["edges_created"] += 1
            except Exception:
                pass

    logger.info(
        "code_graph_built corpus=%s nodes=%d edges=%d",
        corpus_id,
        counts["nodes_created"],
        counts["edges_created"],
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
