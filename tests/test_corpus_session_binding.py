"""Tests for ADR-008 Multi-Corpus Chunk 5: Session/project binding + custom-channel scoping.

Covers:
  - Session corpus binding: get_active_corpus_ids, get_branham_allowlist,
    build_session_meta_update (§5, §3.4 Layer 2)
  - Branham allowlist stripping when policy disabled (§5 — prevents escalation)
  - Custom-channel scoping: ScopedCorpusStores, resolve_scoped_stores (§A14)
  - Custom channel cannot reach Branham without policy approval (§A14 AC)

ADR-008 Rev 3.1 §5, §3.4, Amendment §A14.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aip.adapter.corpus_registry import CorpusRegistry
from aip.adapter.custom_channel_scoping import (
    ScopedCorpusStores,
    resolve_scoped_stores,
    wrap_custom_channel_register,
)
from aip.adapter.session_corpus_binding import (
    build_session_meta_update,
    get_active_corpus_ids,
    get_branham_allowlist,
    is_sensitive_corpus,
)
from aip.foundation.corpus_types import CorpusType

# ---------------------------------------------------------------------------
# Session corpus binding (§5)
# ---------------------------------------------------------------------------


class TestGetActiveCorpusIds:
    """Tests for get_active_corpus_ids."""

    def test_returns_definer_when_no_meta(self):
        assert get_active_corpus_ids(None) == ["definer"]

    def test_returns_definer_when_empty_meta(self):
        assert get_active_corpus_ids({}) == ["definer"]

    def test_returns_defender_when_not_set(self):
        assert get_active_corpus_ids({"other_key": "value"}) == ["definer"]

    def test_returns_explicit_ids(self):
        result = get_active_corpus_ids({"active_corpus_ids": ["codeforge", "branham"]})
        # definer is always added if not present
        assert "definer" in result
        assert "codeforge" in result
        assert "branham" in result

    def test_includes_definer_when_already_present(self):
        result = get_active_corpus_ids({"active_corpus_ids": ["definer", "codeforge"]})
        assert result == ["definer", "codeforge"]

    def test_empty_list_returns_definer(self):
        assert get_active_corpus_ids({"active_corpus_ids": []}) == ["definer"]

    def test_non_list_value_returns_definer(self):
        assert get_active_corpus_ids({"active_corpus_ids": "not a list"}) == ["definer"]


class TestGetBranhamAllowlist:
    """Tests for get_branham_allowlist."""

    def test_returns_false_when_no_meta(self):
        assert get_branham_allowlist(None) is False

    def test_returns_false_when_not_set(self):
        assert get_branham_allowlist({}) is False

    def test_returns_true_when_set(self):
        assert get_branham_allowlist({"branham_allowlist": True}) is True

    def test_returns_false_when_false(self):
        assert get_branham_allowlist({"branham_allowlist": False}) is False


class TestBuildSessionMetaUpdate:
    """Tests for build_session_meta_update — the policy enforcement point."""

    def test_strips_branham_allowlist_when_policy_disabled(self):
        """§5: branham_allowlist is NEVER persisted when policy disabled."""
        update = build_session_meta_update(
            active_corpus_ids=["definer"],
            allowed_restricted_corpora=["branham"],
            restricted_policy_enabled=False,
        )
        # Explicitly set to False, not omitted — clears any prior True value
        assert update["branham_allowlist"] is False

    def test_persists_branham_allowlist_when_policy_enabled(self):
        """§5: branham_allowlist IS persisted when policy enabled."""
        update = build_session_meta_update(
            active_corpus_ids=["definer"],
            allowed_restricted_corpora=["branham"],
            restricted_policy_enabled=True,
        )
        assert update["branham_allowlist"] is True

    def test_active_corpus_ids_defaults_to_definer(self):
        update = build_session_meta_update(
            active_corpus_ids=[],
            allowed_restricted_corpora=[],
            restricted_policy_enabled=False,
        )
        assert update["active_corpus_ids"] == ["definer"]

    def test_active_corpus_ids_adds_definer_if_missing(self):
        update = build_session_meta_update(
            active_corpus_ids=["codeforge"],
            allowed_restricted_corpora=[],
            restricted_policy_enabled=False,
        )
        assert "definer" in update["active_corpus_ids"]
        assert "codeforge" in update["active_corpus_ids"]

    def test_none_active_corpus_ids_preserves_existing(self):
        """When active_corpus_ids is None, the field is not updated."""
        update = build_session_meta_update(
            active_corpus_ids=None,
            allowed_restricted_corpora=[],
            restricted_policy_enabled=False,
        )
        assert "active_corpus_ids" not in update

    def test_prevents_allowlist_escalation_via_replay(self):
        """§5: an attacker can't replay a session with allowlist=True after policy disabled."""
        # Simulate: session was created with allowlist=True (policy enabled)
        # Now policy is disabled — the update must clear the allowlist
        update = build_session_meta_update(
            active_corpus_ids=["definer"],
            allowed_restricted_corpora=["branham"],  # attacker tries to keep it True
            restricted_policy_enabled=False,  # but policy is now disabled
        )
        assert update["branham_allowlist"] is False  # stripped


class TestIsSensitiveCorpus:
    """Tests for is_sensitive_corpus."""

    async def test_returns_true_for_branham(self, tmp_path: Path):
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()
        await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=tmp_path / "branham.db",
            sensitive=True,
        )
        assert is_sensitive_corpus("branham", registry) is True

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_returns_false_for_non_sensitive(self, tmp_path: Path):
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        assert is_sensitive_corpus("definer", registry) is False

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    def test_returns_false_when_no_registry(self):
        assert is_sensitive_corpus("branham", None) is False


# ---------------------------------------------------------------------------
# Custom-channel scoping (§A14)
# ---------------------------------------------------------------------------


class TestScopedCorpusStores:
    """Tests for ScopedCorpusStores — the custom-channel safe view."""

    def test_get_stores_returns_none_for_unresolved(self):
        """A corpus not in the resolved set returns None."""
        scoped = ScopedCorpusStores({"definer": object()})
        assert scoped.get_stores("branham") is None

    def test_get_stores_returns_stores_for_resolved(self):
        """A corpus in the resolved set returns its stores."""
        marker = object()
        scoped = ScopedCorpusStores({"definer": marker})
        assert scoped.get_stores("definer") is marker

    def test_contains_checks_resolved_set(self):
        scoped = ScopedCorpusStores({"definer": object()})
        assert "definer" in scoped
        assert "branham" not in scoped

    def test_available_corpus_ids(self):
        scoped = ScopedCorpusStores({"definer": object(), "codeforge": object()})
        assert set(scoped.available_corpus_ids) == {"definer", "codeforge"}

    def test_len_returns_count(self):
        scoped = ScopedCorpusStores({"definer": object(), "codeforge": object()})
        assert len(scoped) == 2


class TestResolveScopedStores:
    """Tests for resolve_scoped_stores — the resolution point."""

    async def test_resolves_active_corpora(self, tmp_path: Path):
        """resolve_scoped_stores returns all active corpora."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="codeforge",
            corpus_type=CorpusType.CODE,
            db_path=tmp_path / "codeforge.db",
        )

        container = _make_container(registry)
        scoped = await resolve_scoped_stores(
            container=container,
            active_corpus_ids=["definer", "codeforge"],
            allowed_restricted_corpora=[],
        )
        assert "definer" in scoped
        assert "codeforge" in scoped
        assert len(scoped) == 2

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_branham_omitted_without_allowlist(self, tmp_path: Path):
        """§A14 AC: custom channel cannot reach branham without policy approval."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=tmp_path / "branham.db",
            sensitive=True,
        )

        container = _make_container(registry)
        # Request branham WITHOUT allowlist — should be omitted
        scoped = await resolve_scoped_stores(
            container=container,
            active_corpus_ids=["definer", "branham"],
            allowed_restricted_corpora=[],
        )
        assert "definer" in scoped
        assert "branham" not in scoped  # omitted — no allowlist
        assert scoped.get_stores("branham") is None

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_branham_included_with_allowlist(self, tmp_path: Path):
        """With allowlist=True, branham IS included in the scoped set."""
        registry = CorpusRegistry(max_corpora=4)
        await registry.startup()
        await registry.register(
            corpus_id="definer",
            corpus_type=CorpusType.CONVERSATION,
            db_path=tmp_path / "definer.db",
        )
        await registry.register(
            corpus_id="branham",
            corpus_type=CorpusType.DOCUMENT,
            db_path=tmp_path / "branham.db",
            sensitive=True,
        )

        container = _make_container(registry)
        scoped = await resolve_scoped_stores(
            container=container,
            active_corpus_ids=["definer", "branham"],
            allowed_restricted_corpora=["branham"],
        )
        assert "definer" in scoped
        assert "branham" in scoped  # included — allowlist present

        for cid in await registry.list_corpora():
            try:
                await registry.delete_corpus(cid)
            except Exception:
                pass

    async def test_returns_empty_when_no_registry(self):
        """resolve_scoped_stores returns empty ScopedCorpusStores when no registry."""
        container = _make_container(None)
        scoped = await resolve_scoped_stores(
            container=container,
            active_corpus_ids=["definer"],
            allowed_restricted_corpora=[],
        )
        assert len(scoped) == 0


class TestWrapCustomChannelRegister:
    """Tests for wrap_custom_channel_register."""

    def test_wrapper_passes_scoped_stores(self):
        """The wrapped register_fn receives the ScopedCorpusStores, not the raw stores."""
        received_stores: list[Any] = []

        def register_fn(orchestrator, stores, config):
            received_stores.append(stores)
            return []

        scoped = ScopedCorpusStores({"definer": object()})
        wrapped = wrap_custom_channel_register(register_fn, scoped)

        # Call the wrapped function with raw stores — it should pass scoped instead
        raw_stores = object()
        wrapped(orchestrator=None, _stores=raw_stores, config=None)

        assert len(received_stores) == 1
        assert received_stores[0] is scoped  # got the scoped stores, not raw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container(registry: Any) -> Any:
    """Create a mock container with corpus_registry."""

    class FakeContainer:
        def __init__(self):
            self.corpus_registry = registry

    return FakeContainer()
