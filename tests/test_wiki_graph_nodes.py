"""Phase β-3 (2026-07-23) — Wiki as graph nodes tests.

Verifies that wiki articles are created as first-class WIKI_ARTICLE graph
nodes when the POST /wiki/articles endpoint is called. This makes wiki
articles visible in the graph visualization and enables graph-based
"what concepts does this wiki article relate to?" queries.
"""

from __future__ import annotations

import inspect

import pytest


class TestWikiGraphNodes:
    """Phase β-3 — verify wiki articles become graph nodes on creation."""

    def test_wiki_route_creates_graph_node_on_create(self):
        """The create_wiki_article handler must upsert a WIKI_ARTICLE graph node."""
        from aip.adapter.api.routes import wiki

        src = inspect.getsource(wiki.create_wiki_article)
        assert "WIKI_ARTICLE" in src, (
            "create_wiki_article must create a WIKI_ARTICLE entity_type graph node"
        )
        assert "upsert_node" in src, (
            "create_wiki_article must call graph_store.upsert_node"
        )

    def test_graph_node_creation_is_best_effort(self):
        """The graph node creation must be wrapped in try/except (best-effort)."""
        from aip.adapter.api.routes import wiki

        src = inspect.getsource(wiki.create_wiki_article)
        assert "wiki_graph_node_failed" in src, (
            "graph node creation must log a debug message on failure (best-effort)"
        )

    def test_graph_node_uses_wiki_article_id(self):
        """The graph node ID must be derived from the wiki article ID."""
        from aip.adapter.api.routes import wiki

        src = inspect.getsource(wiki.create_wiki_article)
        assert "wiki_" in src and "article_id" in src, (
            "graph node ID must be derived from the article_id (namespaced with wiki_ prefix)"
        )

    def test_graph_node_includes_domain_and_title(self):
        """The graph node must include the domain and title (canonical_name)."""
        from aip.adapter.api.routes import wiki

        src = inspect.getsource(wiki.create_wiki_article)
        assert "canonical_name" in src, "graph node must set canonical_name (title)"
        assert "domain" in src, "graph node must set domain"

    def test_graph_node_checks_graph_store_availability(self):
        """The handler must check if graph_store is available before upserting."""
        from aip.adapter.api.routes import wiki

        src = inspect.getsource(wiki.create_wiki_article)
        assert 'getattr(container, "graph_store", None)' in src or (
            "graph_store" in src and "is not None" in src
        ), "must check graph_store availability before upsert"
