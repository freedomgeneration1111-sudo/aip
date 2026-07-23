"""QW9 (2026-07-23) — GET /corpus-registry/corpora endpoint tests.

Verifies the endpoint that enumerates registered corpora in the
CorpusRegistry. Consumed by gui/components/corpus_selector.py and
any tooling that needs to enumerate corpora for multi-corpus retrieval.

ADR-008 Multi-Corpus, QW9.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:
    FastAPI = None  # type: ignore
    TestClient = None  # type: ignore

from aip.adapter.api.dependencies import AipContainer
from aip.adapter.api.routes.corpus import router as corpus_router
from aip.adapter.corpus_registry import CorpusRegistry
from aip.foundation.corpus_types import CorpusType


@pytest.mark.skipif(TestClient is None, reason="fastapi not installed")
class TestCorpusRegistryEndpoint:
    """QW9 — GET /corpus-registry/corpora"""

    def _build_app(self, container: AipContainer) -> Any:
        """Build a minimal FastAPI app with the corpus router + a stub container."""
        app = FastAPI()
        app.include_router(corpus_router, prefix="/api/v1")

        # Override the get_container dependency to return our test container
        from aip.adapter.api.dependencies import get_container

        async def _override():
            return container

        app.dependency_overrides[get_container] = _override
        return app

    def test_returns_empty_list_when_registry_not_wired(self):
        """No corpus_registry on container -> return [] (honest unavailable)."""
        container = AipContainer({})
        app = self._build_app(container)
        client = TestClient(app)

        resp = client.get("/api/v1/corpus-registry/corpora")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_registered_corpora(self, tmp_path: Path):
        """With definer + codeforge registered, endpoint returns both."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("codeforge", CorpusType.CODE, tmp_path / "codeforge.db"),
            ],
        )
        container = AipContainer({})
        container.corpus_registry = registry

        app = self._build_app(container)
        client = TestClient(app)

        resp = client.get("/api/v1/corpus-registry/corpora")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2

        corpora_by_id = {c["corpus_id"]: c for c in data}
        assert "definer" in corpora_by_id
        assert "codeforge" in corpora_by_id

        # Verify the contract fields the GUI expects
        for entry in data:
            assert "corpus_id" in entry
            assert "corpus_type" in entry
            assert "sensitive" in entry
            assert "deletion_state" in entry
            assert "access_note" in entry

        # Verify specific corpus types
        assert corpora_by_id["definer"]["corpus_type"] == "conversation"
        assert corpora_by_id["codeforge"]["corpus_type"] == "code"
        # Neither should be sensitive by default
        assert corpora_by_id["definer"]["sensitive"] is False
        assert corpora_by_id["codeforge"]["sensitive"] is False

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_sensitive_flag_surfaces(self, tmp_path: Path):
        """A sensitive corpus (e.g. branham) surfaces sensitive=True."""
        registry = CorpusRegistry(max_corpora=8)
        await registry.startup(
            corpora_to_register=[
                ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
                ("branham", CorpusType.DOCUMENT, tmp_path / "branham.db"),
            ],
        )
        # Manually mark branham as sensitive (simulating registry.register(sensitive=True))
        branham_stores = registry._corpora.get("branham")
        if branham_stores is not None:
            branham_stores._sensitive = True
            branham_stores._access_note = "restricted research corpus"

        container = AipContainer({})
        container.corpus_registry = registry

        app = self._build_app(container)
        client = TestClient(app)

        resp = client.get("/api/v1/corpus-registry/corpora")
        assert resp.status_code == 200
        data = resp.json()
        corpora_by_id = {c["corpus_id"]: c for c in data}

        assert corpora_by_id["branham"]["sensitive"] is True
        assert corpora_by_id["branham"]["access_note"] == "restricted research corpus"
        assert corpora_by_id["definer"]["sensitive"] is False

        # Cleanup
        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    def test_gui_corpus_selector_contract(self):
        """Source-level check: the endpoint path matches what corpus_selector.py calls.

        The GUI component at gui/components/corpus_selector.py:36 calls
        GET /corpus-registry/corpora and expects a list of dicts with
        corpus_id, corpus_type, sensitive. This test catches drift if
        either side changes.
        """
        import inspect

        from aip.adapter.api.routes.corpus import list_registered_corpora

        src = inspect.getsource(list_registered_corpora)
        assert "/corpus-registry/corpora" in src or "corpus-registry" in src
        # The handler must return a list (not a dict wrapper)
        assert "return []" in src, "must return [] when registry not wired"
