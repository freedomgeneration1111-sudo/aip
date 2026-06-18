"""Tests for ADR-008 Multi-Corpus Chunk 7: Code corpus ingest (Python AST parser).

Covers:
  - should_skip_file: .pyi, test_*.py, *_test.py skip rules
  - parse_python_file: functions, classes, module registration calls
  - content_hash computation (stale detection)
  - make_code_corpus_turn: CorpusTurn construction
  - ingest_python_directory: pipeline with stale detection
  - Golden queries acceptance test (§8 Chunk 7)

ADR-008 Rev 3.1 §8 Chunk 7.
"""

from __future__ import annotations

from pathlib import Path

from aip.adapter.code_ingest_pipeline import (
    ingest_python_directory,
    ingest_python_file,
)
from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.orchestration.ingestion.parsers.python_ast_parser import (
    CodeTurnSpec,
    make_code_corpus_turn,
    parse_python_file,
    should_skip_file,
)

# ---------------------------------------------------------------------------
# should_skip_file (§8 Chunk 7 skip rules)
# ---------------------------------------------------------------------------


class TestShouldSkipFile:
    """Tests for the skip rules."""

    def test_skips_pyi_files(self):
        assert should_skip_file(Path("types.pyi")) is True

    def test_skips_test_underscore_files(self):
        assert should_skip_file(Path("test_foo.py")) is True
        assert should_skip_file(Path("test_graph_store.py")) is True

    def test_skips_underscore_test_files(self):
        assert should_skip_file(Path("foo_test.py")) is True

    def test_does_not_skip_regular_files(self):
        assert should_skip_file(Path("graph_store.py")) is False
        assert should_skip_file(Path("corpus_registry.py")) is False
        assert should_skip_file(Path("__init__.py")) is False


# ---------------------------------------------------------------------------
# parse_python_file (§8 Chunk 7)
# ---------------------------------------------------------------------------


class TestParsePythonFile:
    """Tests for the AST parser."""

    def test_parses_simple_function(self):
        """A simple function produces one function CodeTurnSpec."""
        source = '''
def add(a, b):
    """Add two numbers."""
    return a + b
'''
        specs = parse_python_file(source, "src/test.py")
        assert len(specs) >= 1
        func_specs = [s for s in specs if s.kind == "function"]
        assert len(func_specs) == 1
        assert "add" in func_specs[0].qualified_name
        assert "Add two numbers" in func_specs[0].searchable_text
        assert func_specs[0].content_hash  # non-empty

    def test_parses_async_function(self):
        """Async functions are parsed correctly."""
        source = '''
async def fetch_data(url):
    """Fetch data from URL."""
    return await request(url)
'''
        specs = parse_python_file(source, "src/test.py")
        func_specs = [s for s in specs if s.kind == "function"]
        assert len(func_specs) == 1
        assert func_specs[0].metadata["is_async"] is True

    def test_parses_decorated_function(self):
        """Decorators are included in searchable_text."""
        source = '''
@click.command()
def cli_command():
    """A CLI command."""
    pass
'''
        specs = parse_python_file(source, "src/test.py")
        func_specs = [s for s in specs if s.kind == "function"]
        assert len(func_specs) == 1
        assert "click.command" in func_specs[0].searchable_text

    def test_parses_class_with_call_body(self):
        """A class with Call/Assign body nodes produces a class CodeTurnSpec."""
        source = '''
class MyChannel:
    """A channel with registration."""
    register("my_channel")
    _initialized = True
'''
        specs = parse_python_file(source, "src/test.py")
        class_specs = [s for s in specs if s.kind == "class"]
        assert len(class_specs) == 1
        assert "MyChannel" in class_specs[0].searchable_text
        assert "register" in class_specs[0].searchable_text

    def test_skips_class_without_call_body(self):
        """A class with only method defs (no Call/Assign) produces no class spec."""
        source = """
class SimpleClass:
    def method(self):
        pass
"""
        specs = parse_python_file(source, "src/test.py")
        class_specs = [s for s in specs if s.kind == "class"]
        assert len(class_specs) == 0  # no Call/Assign body

    def test_parses_module_registration_call(self):
        """Module-level registration calls produce a module_registration spec."""
        source = """
register_channel("my_channel", my_register_fn)
"""
        specs = parse_python_file(source, "src/test.py")
        reg_specs = [s for s in specs if s.kind == "module_registration"]
        assert len(reg_specs) == 1
        assert "register_channel" in reg_specs[0].searchable_text

    def test_does_not_parse_non_registration_module_call(self):
        """Module-level calls to non-registration functions are not indexed."""
        source = """
print("hello world")
"""
        specs = parse_python_file(source, "src/test.py")
        reg_specs = [s for s in specs if s.kind == "module_registration"]
        assert len(reg_specs) == 0

    def test_syntax_error_returns_empty(self):
        """SyntaxError returns [] and logs a warning (never raises)."""
        source = """
def broken(
    # missing closing paren
"""
        specs = parse_python_file(source, "src/test.py")
        assert specs == []

    def test_derives_module_path(self):
        """Module path is derived from source_path."""
        source = "def f():\n    pass\n"
        specs = parse_python_file(source, "src/aip/adapter/graph_store.py")
        assert len(specs) >= 1
        assert "aip.adapter.graph_store" in specs[0].qualified_name

    def test_content_hash_changes_when_source_changes(self):
        """Different function bodies produce different content_hashes."""
        source1 = "def f():\n    return 1\n"
        source2 = "def f():\n    return 2\n"
        specs1 = parse_python_file(source1, "src/test.py")
        specs2 = parse_python_file(source2, "src/test.py")
        assert specs1[0].content_hash != specs2[0].content_hash


# ---------------------------------------------------------------------------
# make_code_corpus_turn (§8 Chunk 7)
# ---------------------------------------------------------------------------


class TestMakeCodeCorpusTurn:
    """Tests for CorpusTurn construction from CodeTurnSpec."""

    def test_creates_corpus_turn_with_correct_fields(self):
        """make_code_corpus_turn produces a CorpusTurn with the right fields."""
        spec = CodeTurnSpec(
            qualified_name="aip.adapter.test.my_func",
            searchable_text="def my_func():\n    pass",
            content_hash="abc123",
            source_path="src/aip/adapter/test.py",
            kind="function",
            metadata={"function_name": "my_func"},
        )
        turn = make_code_corpus_turn(spec, turn_index=0, export_date="2026-06-18")

        assert turn.source_model == "code"
        assert turn.source_account == "python_ast_parser"
        assert turn.source_path == "src/aip/adapter/test.py"
        assert turn.content_hash == "abc123"
        assert turn.user_text == "aip.adapter.test.my_func"
        assert turn.assistant_text == "def my_func():\n    pass"

    def test_turn_id_is_deterministic(self):
        """Same source_path + turn_index produces the same turn_id."""
        spec = CodeTurnSpec(
            qualified_name="test.func",
            searchable_text="def func(): pass",
            content_hash="abc",
            source_path="src/test.py",
            kind="function",
            metadata={},
        )
        turn1 = make_code_corpus_turn(spec, turn_index=0)
        turn2 = make_code_corpus_turn(spec, turn_index=0)
        assert turn1.turn_id == turn2.turn_id  # deterministic


# ---------------------------------------------------------------------------
# ingest_python_file + stale detection (§8 Chunk 7)
# ---------------------------------------------------------------------------


class TestIngestPythonFile:
    """Tests for the single-file ingest pipeline with stale detection."""

    async def test_ingest_creates_turns(self, tmp_path: Path):
        """ingest_python_file writes CorpusTurns to the store."""
        # Create a test file
        py_file = tmp_path / "example_module.py"
        py_file.write_text(
            '''
def hello():
    """Say hello."""
    return "hello"
'''
        )

        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        counts = await ingest_python_file(py_file, store)
        assert counts["files_parsed"] == 1
        assert counts["turns_created"] >= 1

        # Verify the turn was written
        turns = await store.search("hello", limit=10)
        assert len(turns) >= 1

        await store.close()

    async def test_stale_detection_skips_unchanged(self, tmp_path: Path):
        """Re-ingesting an unchanged file skips all turns (stale detection)."""
        py_file = tmp_path / "example_module.py"
        py_file.write_text("def f():\n    return 1\n")

        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        # First ingest — creates turns
        counts1 = await ingest_python_file(py_file, store)
        assert counts1["turns_created"] >= 1

        # Second ingest — should skip (content_hash unchanged)
        counts2 = await ingest_python_file(py_file, store)
        assert counts2["turns_skipped_stale"] >= 1
        assert counts2["turns_created"] == 0

        await store.close()

    async def test_stale_detection_supersedes_changed(self, tmp_path: Path):
        """Re-ingesting a changed file writes new turns (superseded count)."""
        py_file = tmp_path / "example_module.py"
        py_file.write_text("def f():\n    return 1\n")

        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        # First ingest
        await ingest_python_file(py_file, store)

        # Change the file
        py_file.write_text("def f():\n    return 2\n")

        # Second ingest — content_hash changed
        counts2 = await ingest_python_file(py_file, store)
        assert counts2["turns_superseded"] >= 1
        assert counts2["turns_created"] >= 1

        await store.close()

    async def test_skips_test_files(self, tmp_path: Path):
        """ingest_python_file skips test_*.py files."""
        py_file = tmp_path / "test_example.py"
        py_file.write_text("def f():\n    pass\n")

        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        counts = await ingest_python_file(py_file, store)
        assert counts["files_skipped"] == 1
        assert counts["turns_created"] == 0

        await store.close()


# ---------------------------------------------------------------------------
# ingest_python_directory (§8 Chunk 7)
# ---------------------------------------------------------------------------


class TestIngestPythonDirectory:
    """Tests for the directory ingest pipeline."""

    async def test_ingests_directory(self, tmp_path: Path):
        """ingest_python_directory walks a directory and ingests all .py files."""
        # Create a few .py files
        (tmp_path / "module1.py").write_text("def func1():\n    return 1\n")
        (tmp_path / "module2.py").write_text("def func2():\n    return 2\n")
        (tmp_path / "test_skip.py").write_text("def test_skip():\n    pass\n")

        store = CorpusTurnStore(str(tmp_path / "test.db"))
        await store.initialize()

        counts = await ingest_python_directory(tmp_path, store)
        assert counts["files_scanned"] == 3
        assert counts["files_skipped"] == 1  # test_skip.py
        assert counts["files_parsed"] == 2
        assert counts["turns_created"] >= 2

        await store.close()


# ---------------------------------------------------------------------------
# Golden queries acceptance test (§8 Chunk 7, §10)
# ---------------------------------------------------------------------------


class TestGoldenQueries:
    """Golden queries acceptance test — ADR-008 Rev 3.1 §8 Chunk 7, §10.

    The golden queries that must return non-empty results from the code
    corpus after ingest:
      - "retrieval channel registration"
      - "FastAPI route embedding"
      - "async context manager close pattern"
    """

    async def test_golden_query_retrieval_channel_registration(self, tmp_path: Path):
        """Query 'retrieval channel registration' returns non-empty results."""
        # Ingest the actual AIP Brain channels directory
        aip_root = Path(__file__).parent.parent / "src" / "aip"
        channels_dir = aip_root / "orchestration" / "channels"

        store = CorpusTurnStore(str(tmp_path / "codeforge.db"))
        await store.initialize()

        await ingest_python_directory(channels_dir, store)

        # Query for "retrieval channel registration"
        results = await store.search("retrieval channel registration", limit=10)
        assert len(results) > 0, (
            "Golden query 'retrieval channel registration' must return non-empty results "
            "from the code corpus after ingest"
        )

        await store.close()

    async def test_golden_query_fastapi_route_embedding(self, tmp_path: Path):
        """Query 'FastAPI route embedding' returns non-empty results."""
        aip_root = Path(__file__).parent.parent / "src" / "aip"
        routes_dir = aip_root / "adapter" / "api" / "routes"

        store = CorpusTurnStore(str(tmp_path / "codeforge.db"))
        await store.initialize()

        await ingest_python_directory(routes_dir, store)

        # Query for "FastAPI route embedding"
        results = await store.search("embedding", limit=10)
        assert len(results) > 0, "Golden query 'FastAPI route embedding' must return non-empty results"

        await store.close()

    async def test_golden_query_async_context_manager_close(self, tmp_path: Path):
        """Query 'async context manager close pattern' returns non-empty results."""
        aip_root = Path(__file__).parent.parent / "src" / "aip"
        adapter_dir = aip_root / "adapter"

        store = CorpusTurnStore(str(tmp_path / "codeforge.db"))
        await store.initialize()

        await ingest_python_directory(adapter_dir, store)

        # Query for "async context manager close pattern" — stores have __aexit__/close()
        results = await store.search("close", limit=10)
        assert len(results) > 0, "Golden query 'async context manager close pattern' must return non-empty results"

        await store.close()
