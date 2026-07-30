"""FTS5 query sanitization — cross-layer shared utility.

Pure function with zero aip.* imports so that both the orchestration
and adapter layers can depend on it without violating the import boundary.
"""

from __future__ import annotations

import re


def sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH syntax.

    FTS5 has special syntax for operators like AND, OR, NOT, NEAR, *, ^, etc.
    Questions from users often contain ?, !, /, and other characters that
    are not valid in FTS5 MATCH expressions.  This function extracts
    clean word tokens and joins them with AND for FTS5 matching.

    The ``/`` character is included because file paths like
    ``gui/pages/ask.py`` are common in code queries and FTS5 treats
    ``/`` as syntax (causing "syntax error near /" OperationalError).
    """
    cleaned = re.sub(r'[?!.*+\-^(){}|~"\\/]', " ", query)
    tokens = cleaned.split()
    stop_words = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "by",
        "from",
        "it",
        "its",
        "we",
        "our",
        "you",
        "your",
        "this",
        "that",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "about",
        "there",
        "here",
        "these",
        "those",
        "been",
        "some",
        "very",
        "also",
        "just",
        "than",
        "then",
        "so",
        "if",
        "or",
        "not",
        "no",
        "but",
        "and",
        "up",
        "out",
        "into",
        "over",
    }
    meaningful = [t for t in tokens if len(t) >= 2 and t.lower() not in stop_words]

    if not meaningful:
        meaningful = [t for t in tokens if len(t) >= 1 and t.lower() not in stop_words]

    if not meaningful:
        meaningful = [t for t in tokens[:3] if t]

    if not meaningful:
        return query

    return " AND ".join(meaningful)
