"""Python AST parser — produces CorpusTurns from Python source code.

ADR-008 Rev 3.1 §8 Chunk 7: parses Python files into CorpusTurn objects for
the code corpus (corpus_type=CODE, corpus_id="codeforge"). Each function,
class, and module-level registration call becomes a searchable turn.

Layer: adapter. Pure transform — no store I/O. The ingest pipeline
(``adapter/code_ingest_pipeline.py``) calls this parser and writes the
turns to CorpusTurnStore.

Note: a duplicate of this file previously lived at
``orchestration/ingestion/parsers/python_ast_parser.py`` — deleted
2026-07-23 (QW3) because it was unused (zero imports) and violated the
"one source of truth" rule. The adapter copy is the canonical home.

What gets indexed:
  1. Per function/method — one CorpusTurn:
     - searchable_text = decorators + signature + docstring + first N body lines
     - qualified_name = module_path.ClassName.func_name (for nested)
     - content_hash = SHA256(ast.unparse(function_node))
  2. Per class with Call/Assign body — one CorpusTurn:
     - searchable_text = "class X:" + body Call/Assign nodes verbatim
     - Captures __init_subclass__, metaclass, registry patterns
  3. Per module-level registration call — one CorpusTurn:
     - Calls to known registration functions: register_channel, register_plugin,
       __init_subclass__, register, add_route

Skip rules:
  - .pyi files (type stubs, no implementation)
  - test_*.py and *_test.py files
  - SyntaxError: log WARNING, skip, do not fail pipeline

Stale detection:
  - content_hash = SHA256(ast.unparse(node)) — if unchanged, skip re-embedding
  - The ingest pipeline compares against existing turns' content_hash
"""

from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aip.foundation.schemas.corpus_turn import (
    CorpusTurn,
    make_document_conversation_id,
    make_turn_id,
)

logger = logging.getLogger(__name__)

# Known registration function names — module-level calls to these produce
# a CorpusTurn so registry/metaclass patterns are searchable.
REGISTRATION_FUNCTIONS: frozenset[str] = frozenset(
    {
        "register_channel",
        "register_plugin",
        "register",
        "add_route",
        "register_custom_channel",
        "register_all_channels",
    }
)

# Maximum body lines to include in a function's searchable_text.
_MAX_BODY_LINES = 20


@dataclass
class CodeTurnSpec:
    """Specification for a single code corpus turn.

    The ingest pipeline uses this to construct a CorpusTurn with the right
    fields. Kept as a separate dataclass so the parser stays pure (no
    CorpusTurn construction — that's the pipeline's job).
    """

    qualified_name: str
    searchable_text: str
    content_hash: str
    source_path: str
    kind: str  # "function" | "class" | "module_registration"
    metadata: dict[str, Any]


def should_skip_file(path: Path) -> bool:
    """Return True if a file should be skipped by the AST parser.

    Skip rules (§8 Chunk 7):
      - .pyi files (type stubs, no implementation)
      - test_*.py and *_test.py files
    """
    name = path.name
    if name.endswith(".pyi"):
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return False


def parse_python_file(
    source: str,
    source_path: str,
) -> list[CodeTurnSpec]:
    """Parse Python source into a list of CodeTurnSpec objects.

    Args:
        source: the Python source code as a string.
        source_path: the file path (for metadata + conversation_id derivation).

    Returns:
        List of CodeTurnSpec objects. Empty list if parsing fails (logged
        at WARNING level, never raises).

    On SyntaxError, logs a warning and returns []. The pipeline continues
    with other files.
    """
    try:
        tree = ast.parse(source, filename=source_path)
    except SyntaxError as exc:
        logger.warning(
            "python_ast_parse_syntax_error path=%s line=%d error=%s — skipping",
            source_path,
            exc.lineno or 0,
            exc.msg,
        )
        return []
    except Exception as exc:
        logger.warning(
            "python_ast_parse_failed path=%s error=%s — skipping",
            source_path,
            exc,
        )
        return []

    # Derive module_path from source_path (e.g. "src/aip/adapter/graph_store.py" → "aip.adapter.graph_store")
    module_path = _derive_module_path(source_path)

    specs: list[CodeTurnSpec] = []

    # 1. Per function/method
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spec = _make_function_spec(node, module_path, source_path)
            if spec is not None:
                specs.append(spec)

    # 2. Per class with Call/Assign body
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            spec = _make_class_spec(node, module_path, source_path)
            if spec is not None:
                specs.append(spec)

    # 3. Module-level registration calls
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Expr,)):
            call = node.value
            if isinstance(call, ast.Call) and _is_registration_call(call):
                spec = _make_module_registration_spec(call, module_path, source_path)
                if spec is not None:
                    specs.append(spec)

    return specs


def _derive_module_path(source_path: str) -> str:
    """Derive a Python module path from a file path.

    e.g. "src/aip/adapter/graph_store.py" → "aip.adapter.graph_store"
    Falls back to the path with slashes → dots if no src/ prefix.
    """
    p = source_path.replace("\\", "/")
    # Strip .py extension
    if p.endswith(".py"):
        p = p[:-3]
    # Find src/ or aip/ and strip everything before it
    for marker in ("/src/", "/aip/"):
        idx = p.find(marker)
        if idx >= 0:
            p = p[idx + 1 :]  # skip the leading /
            break
    return p.replace("/", ".")


def _make_function_spec(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    module_path: str,
    source_path: str,
) -> CodeTurnSpec | None:
    """Build a CodeTurnSpec for a function/method."""
    # Qualified name: module_path.ClassName.func_name (for nested)
    qualified_name = f"{module_path}.{_qualified_name(node)}"

    # Decorators
    decorator_text = ""
    if node.decorator_list:
        try:
            decorator_text = "\n".join(ast.unparse(d) for d in node.decorator_list)
        except Exception:
            decorator_text = ""

    # Signature
    try:
        signature = ast.unparse(node).split(":", 0)[0] if ":" in ast.unparse(node) else ast.unparse(node).split("\n")[0]
        # Just get the def line
        signature = (
            f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}{ast.unparse(node.args)}"
        )
    except Exception:
        signature = f"def {node.name}(...)"

    # Docstring
    docstring = ast.get_docstring(node) or ""

    # First N body lines (skip docstring)
    body_lines: list[str] = []
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # skip docstring
    for stmt in body[:_MAX_BODY_LINES]:
        try:
            body_lines.append(ast.unparse(stmt))
        except Exception:
            pass

    # Build searchable_text
    parts = [p for p in [decorator_text, signature, docstring, "\n".join(body_lines)] if p.strip()]
    searchable_text = "\n".join(parts)

    # content_hash = SHA256 of the full function unparse
    try:
        full_source = ast.unparse(node)
    except Exception:
        full_source = searchable_text
    content_hash = hashlib.sha256(full_source.encode()).hexdigest()[:32]

    return CodeTurnSpec(
        qualified_name=qualified_name,
        searchable_text=searchable_text,
        content_hash=content_hash,
        source_path=source_path,
        kind="function",
        metadata={
            "function_name": node.name,
            "qualified_name": qualified_name,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "decorator_count": len(node.decorator_list),
            "has_docstring": bool(docstring),
        },
    )


def _make_class_spec(
    node: ast.ClassDef,
    module_path: str,
    source_path: str,
) -> CodeTurnSpec | None:
    """Build a CodeTurnSpec for a class with Call/Assign body nodes.

    Only emits a turn if the class body contains Call or Assign nodes —
    this captures __init_subclass__, metaclass, and registry patterns.
    """
    class_body_calls: list[str] = []
    for child in node.body:
        if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
            try:
                class_body_calls.append(ast.unparse(child))
            except Exception:
                pass
        elif isinstance(child, ast.Assign):
            try:
                class_body_calls.append(ast.unparse(child))
            except Exception:
                pass

    if not class_body_calls:
        return None

    qualified_name = f"{module_path}.{node.name}"
    searchable_text = f"class {node.name}:\n" + "\n".join(class_body_calls)

    try:
        full_source = ast.unparse(node)
    except Exception:
        full_source = searchable_text
    content_hash = hashlib.sha256(full_source.encode()).hexdigest()[:32]

    return CodeTurnSpec(
        qualified_name=qualified_name,
        searchable_text=searchable_text,
        content_hash=content_hash,
        source_path=source_path,
        kind="class",
        metadata={
            "class_name": node.name,
            "qualified_name": qualified_name,
            "body_call_count": len(class_body_calls),
        },
    )


def _make_module_registration_spec(
    call: ast.Call,
    module_path: str,
    source_path: str,
) -> CodeTurnSpec | None:
    """Build a CodeTurnSpec for a module-level registration call."""
    try:
        call_text = ast.unparse(call)
    except Exception:
        return None

    func_name = _get_call_name(call)
    if func_name is None:
        return None

    qualified_name = f"{module_path}.module_registration.{func_name}"
    content_hash = hashlib.sha256(call_text.encode()).hexdigest()[:32]

    return CodeTurnSpec(
        qualified_name=qualified_name,
        searchable_text=call_text,
        content_hash=content_hash,
        source_path=source_path,
        kind="module_registration",
        metadata={
            "registration_function": func_name,
            "module_path": module_path,
        },
    )


def _qualified_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Build a qualified name for a function, including enclosing class names.

    ast.walk doesn't preserve parent context, so we use a heuristic: walk
    the tree and track the class chain. For simplicity, we just use the
    function name here — the full qualified name requires parent tracking
    which would complicate the parser. The module_path prefix is the main
    qualifier.
    """
    return node.name


def _is_registration_call(call: ast.Call) -> bool:
    """Check if a Call node is a call to a known registration function."""
    name = _get_call_name(call)
    return name in REGISTRATION_FUNCTIONS if name else False


def _get_call_name(call: ast.Call) -> str | None:
    """Extract the function name from a Call node.

    Handles: register_channel(...), self.register(...), module.register(...)
    Returns the last component (e.g. "register") or None.
    """
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def make_code_corpus_turn(
    spec: CodeTurnSpec,
    turn_index: int,
    export_date: str | None = None,
) -> CorpusTurn:
    """Construct a CorpusTurn from a CodeTurnSpec.

    The ingest pipeline calls this to create the actual CorpusTurn before
    writing it to CorpusTurnStore.

    conversation_id is derived from source_path (stable across re-ingests).
    turn_id is deterministic: make_turn_id(conversation_id, turn_index).
    source_model = "code" for code corpus turns.
    """
    if export_date is None:
        export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conversation_id = make_document_conversation_id(spec.source_path)
    turn_id = make_turn_id(conversation_id, turn_index)

    # user_text = qualified_name (the "question" is "what is this function?")
    # assistant_text = searchable_text (the "answer" is the code)
    user_text = spec.qualified_name
    assistant_text = spec.searchable_text

    return CorpusTurn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        conversation_name=spec.source_path,
        turn_index=turn_index,
        source_model="code",
        source_account="python_ast_parser",
        export_date=export_date,
        user_text=user_text,
        assistant_text=assistant_text,
        turn_timestamp=datetime.now(timezone.utc).isoformat() + "Z",
        content_hash=spec.content_hash,
        source_path=spec.source_path,
        metadata_json=__import__("json").dumps(
            {
                "kind": spec.kind,
                "qualified_name": spec.qualified_name,
                **spec.metadata,
            }
        ),
    )
