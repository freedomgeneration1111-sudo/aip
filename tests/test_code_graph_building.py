"""Phase β-1 (2026-07-23) — Code dependency graph building tests.

Verifies that the AST parser now extracts imports + calls, and that
build_code_graph() creates FUNCTION/CLASS nodes + imports/calls edges
in the per-corpus GraphStore.

This is the "Graph B" from PLANNED_FEATURES.md: code dependency graph
with imports, calls edges.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aip.adapter.python_ast_parser import (
    CodeTurnSpec,
    _extract_calls,
    _extract_imports,
    make_code_corpus_turn,
    parse_python_file,
)
from aip.adapter.code_ingest_pipeline import build_code_graph, ingest_python_directory
from aip.adapter.corpus_registry import CorpusRegistry
from aip.foundation.corpus_types import CorpusType


class TestImportExtraction:
    """Verify _extract_imports extracts module-level imports."""

    def test_extract_simple_imports(self):
        """import asyncio, import os → ["asyncio", "os"]"""
        import ast
        tree = ast.parse("import asyncio\nimport os\n")
        imports = _extract_imports(tree)
        assert "asyncio" in imports
        assert "os" in imports

    def test_extract_from_imports(self):
        """from pathlib import Path → ["pathlib.Path"]"""
        import ast
        tree = ast.parse("from pathlib import Path\n")
        imports = _extract_imports(tree)
        assert "pathlib.Path" in imports

    def test_extract_dotted_imports(self):
        """from aip.adapter.graph_store import GraphStore → ["aip.adapter.graph_store.GraphStore"]"""
        import ast
        tree = ast.parse("from aip.adapter.graph_store import GraphStore\n")
        imports = _extract_imports(tree)
        assert "aip.adapter.graph_store.GraphStore" in imports


class TestCallExtraction:
    """Verify _extract_calls extracts function calls from a node's body."""

    def test_extract_simple_calls(self):
        """def f(): print(x) → calls includes "print" """
        import ast
        tree = ast.parse("def f():\n    print(x)\n")
        func_node = tree.body[0]
        calls = _extract_calls(func_node)
        assert "print" in calls

    def test_extract_method_calls(self):
        """def f(): registry.register(x) → calls includes "registry.register" """
        import ast
        tree = ast.parse("def f():\n    registry.register(x)\n")
        func_node = tree.body[0]
        calls = _extract_calls(func_node)
        assert "registry.register" in calls

    def test_extract_multiple_calls(self):
        """Multiple calls are all extracted, deduplicated."""
        import ast
        tree = ast.parse("def f():\n    logger.info(x)\n    logger.info(y)\n    print(z)\n")
        func_node = tree.body[0]
        calls = _extract_calls(func_node)
        assert "logger.info" in calls
        assert "print" in calls
        # Deduplicated — logger.info appears once
        assert calls.count("logger.info") == 1


class TestCodeTurnSpecFields:
    """Verify CodeTurnSpec has imports + calls fields populated."""

    def test_spec_has_imports_field(self):
        """CodeTurnSpec defaults imports to [] when not provided."""
        spec = CodeTurnSpec(
            qualified_name="test",
            searchable_text="test",
            content_hash="abc",
            source_path="test.py",
            kind="function",
            metadata={},
        )
        assert spec.imports == []
        assert spec.calls == []

    def test_parse_file_populates_imports(self):
        """parse_python_file populates the imports field on each spec."""
        source = (
            "import asyncio\n"
            "from pathlib import Path\n"
            "\n"
            "def greet(name):\n"
            '    """Greet."""\n'
            '    return f"Hello, {name}"\n'
        )
        specs = parse_python_file(source, "test.py")
        assert len(specs) > 0
        for spec in specs:
            assert "asyncio" in spec.imports
            assert "pathlib.Path" in spec.imports

    def test_parse_file_populates_calls(self):
        """parse_python_file populates the calls field on function specs."""
        source = (
            "def greet(name):\n"
            '    """Greet."""\n'
            '    print(f"Hello, {name}")\n'
            "    logger.info('greeted')\n"
        )
        specs = parse_python_file(source, "test.py")
        func_specs = [s for s in specs if s.kind == "function"]
        assert len(func_specs) > 0
        calls = func_specs[0].calls
        assert "print" in calls
        assert "logger.info" in calls

    def test_metadata_includes_imports_and_calls(self):
        """make_code_corpus_turn includes imports + calls in metadata_json."""
        spec = CodeTurnSpec(
            qualified_name="test.func",
            searchable_text="def func(): pass",
            content_hash="abc",
            source_path="test.py",
            kind="function",
            metadata={"function_name": "func"},
            imports=["asyncio", "os"],
            calls=["print", "logger.info"],
        )
        turn = make_code_corpus_turn(spec, turn_index=0)
        import json
        meta = json.loads(turn.metadata_json)
        assert meta["imports"] == ["asyncio", "os"]
        assert meta["calls"] == ["print", "logger.info"]


class TestBuildCodeGraph:
    """Verify build_code_graph creates nodes + edges in GraphStore."""

    async def test_graph_nodes_created(self, tmp_path: Path):
        """build_code_graph creates FUNCTION/CLASS nodes in the graph store."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[("codeforge", CorpusType.CODE, tmp_path / "codeforge.db")]
        )
        stores = await registry.get_stores("codeforge")
        assert stores.graph_store is not None

        specs = [
            CodeTurnSpec(
                qualified_name="module.greet",
                searchable_text="def greet(): pass",
                content_hash="abc",
                source_path="module.py",
                kind="function",
                metadata={"function_name": "greet"},
                imports=["asyncio"],
                calls=["print"],
            ),
            CodeTurnSpec(
                qualified_name="module.MyClass",
                searchable_text="class MyClass: ...",
                content_hash="def",
                source_path="module.py",
                kind="class",
                metadata={"class_name": "MyClass"},
                imports=[],
                calls=[],
            ),
        ]

        counts = await build_code_graph(specs, stores.graph_store, corpus_id="codeforge")
        assert counts["nodes_created"] == 2
        assert counts["edges_created"] >= 2  # at least 1 import + 1 call edge

        # Verify nodes are in the graph
        node_count = await stores.graph_store.node_count()
        assert node_count == 2

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_graph_edges_created(self, tmp_path: Path):
        """build_code_graph creates imports + calls edges."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[("codeforge", CorpusType.CODE, tmp_path / "codeforge.db")]
        )
        stores = await registry.get_stores("codeforge")

        specs = [
            CodeTurnSpec(
                qualified_name="module.func_a",
                searchable_text="def func_a(): pass",
                content_hash="abc",
                source_path="module.py",
                kind="function",
                metadata={},
                imports=["asyncio", "os"],
                calls=["print", "logger.info"],
            ),
        ]

        counts = await build_code_graph(specs, stores.graph_store, corpus_id="codeforge")
        # 1 node + 2 import edges + 2 call edges = 4 edges
        assert counts["nodes_created"] == 1
        assert counts["edges_created"] == 4

        edge_count = await stores.graph_store.edge_count()
        assert edge_count == 4

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_ingest_with_graph_store(self, tmp_path: Path):
        """ingest_python_directory builds graph when graph_store is provided."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[("codeforge", CorpusType.CODE, tmp_path / "codeforge.db")]
        )
        stores = await registry.get_stores("codeforge")

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "sample.py").write_text(
            'import asyncio\n'
            'from pathlib import Path\n'
            '\n'
            'def greet(name):\n'
            '    """Greet."""\n'
            '    print(f"Hello, {name}")\n'
            '    return name\n'
        )

        counts = await ingest_python_directory(
            source_dir=src_dir,
            turn_store=stores.turn_store,
            corpus_id="codeforge",
            graph_store=stores.graph_store,
        )

        assert counts["turns_created"] > 0
        assert counts.get("graph_nodes", 0) > 0, "graph_nodes must be > 0"
        assert counts.get("graph_edges", 0) > 0, "graph_edges must be > 0"

        # Verify graph has nodes
        node_count = await stores.graph_store.node_count()
        assert node_count > 0

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass
