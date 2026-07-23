"""Bug fix (2026-07-23) — code corpus turns must have importance >= 0.3.

The retrieval path (_search_corpus_turns in _augmented_context.py) filters
turns with min_importance=0.3. Code turns created by make_code_corpus_turn
previously defaulted to importance=0.0 (the CorpusTurn default), which meant
ALL code turns were filtered out — the codeforge corpus was invisible to
search despite having 2,407 turns ingested.

This test verifies the fix: make_code_corpus_turn now sets importance=1.0.

Discovered via dogfood: asking 'How does the CorpusRegistry enforce the
connection budget?' returned 'NO SOURCES' even with codeforge selected,
because all 2,407 code turns had importance=0.0 < 0.3 filter threshold.
"""

from __future__ import annotations

from aip.adapter.python_ast_parser import CodeTurnSpec, make_code_corpus_turn


class TestCodeTurnImportance:
    """Verify code turns have importance >= 0.3 so they pass the retrieval filter."""

    def test_code_turn_has_max_importance(self):
        """make_code_corpus_turn must set importance=1.0 (not the 0.0 default).

        The retrieval path filters with min_importance=0.3. Code turns are
        explicit, human-authored structure (functions, classes) — they
        deserve the max importance so they always pass the filter.
        """
        spec = CodeTurnSpec(
            kind="function",
            qualified_name="module.greet",
            searchable_text="def greet(name): return f'Hello, {name}'",
            content_hash="abc123",
            source_path="module.py",
            metadata={},
        )
        turn = make_code_corpus_turn(spec, turn_index=0)

        assert turn.importance == 1.0, (
            f"Code turns must have importance=1.0 to pass the min_importance=0.3 "
            f"retrieval filter. Got importance={turn.importance}. "
            f"Without this, the codeforge corpus is invisible to search."
        )

    def test_code_turn_passes_retrieval_threshold(self):
        """The importance must be >= 0.3 (the retrieval min_importance filter)."""
        spec = CodeTurnSpec(
            kind="class",
            qualified_name="module.DataProcessor",
            searchable_text="class DataProcessor: ...",
            content_hash="def456",
            source_path="module.py",
            metadata={},
        )
        turn = make_code_corpus_turn(spec, turn_index=0)

        # The retrieval filter in _search_corpus_turns uses min_importance=0.3
        assert turn.importance >= 0.3, (
            f"Code turn importance ({turn.importance}) must be >= 0.3 to pass "
            f"the retrieval filter. Got {turn.importance}."
        )

    def test_multiple_code_turns_all_have_importance(self):
        """Every code turn (function, class, registration) must have importance=1.0."""
        specs = [
            CodeTurnSpec(
                kind="function",
                qualified_name=f"module.func_{i}",
                searchable_text=f"def func_{i}(): pass",
                content_hash=f"hash_{i}",
                source_path="module.py",
                metadata={},
            )
            for i in range(5)
        ]

        for i, spec in enumerate(specs):
            turn = make_code_corpus_turn(spec, turn_index=i)
            assert turn.importance == 1.0, (
                f"Turn {i} ({spec.qualified_name}) has importance={turn.importance}, expected 1.0"
            )
