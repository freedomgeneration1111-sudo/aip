"""Regression test: corpus selection survives session reset (ADR-017 fix).

Reproduces the bug described in the Multi-Cast retrieval diagnosis:
  1. User selects codeforge as an active corpus.
  2. User changes panel models or switches to Augmented mode.
  3. GUI calls reset_session() (discards session_id).
  4. User presses Multi-Cast.
  5. ensure_session() creates a fresh session.
  6. BUG (pre-fix): fresh session has no active_corpus_ids → retrieval
     falls back to legacy single-corpus path → codeforge material never
     reaches the panel.
  7. FIX: active_corpus_ids is now persistent in GuiState, and
     ensure_session() re-applies it to the replacement session.

This test verifies the fix by simulating the sequence and asserting
that the replacement session's active_corpus_ids includes codeforge.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Stub API client
# ---------------------------------------------------------------------------


class StubApiClient:
    """Stub API client that records session creations and corpus updates."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.corpus_updates: list[tuple[str, list[str]]] = []
        self._next_id = 1

    async def create_session(
        self,
        *,
        role: str | None = None,
        model_slot: str = "synthesis",
        mode: str = "normal",
    ) -> dict[str, Any]:
        sid = f"session_{self._next_id}"
        self._next_id += 1
        self.sessions[sid] = {
            "id": sid,
            "role": role,
            "model_slot": model_slot,
            "mode": mode,
            "active_corpus_ids": ["definer"],  # server default
        }
        return {"id": sid}

    async def update_session_corpora(
        self,
        session_id: str,
        corpus_ids: list[str],
    ) -> bool:
        self.corpus_updates.append((session_id, list(corpus_ids)))
        if session_id in self.sessions:
            self.sessions[session_id]["active_corpus_ids"] = list(corpus_ids)
        return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_active_corpus_ids_survives_reset_session():
    """active_corpus_ids persists across reset_session() calls.

    This is the core fix: reset_session() no longer discards the
    workspace-level corpus selection.
    """
    from gui.state import GuiState

    state = GuiState()
    state.api_client = StubApiClient()

    # Simulate user selecting codeforge
    state.active_corpus_ids = ["definer", "codeforge"]

    # reset_session() is called when models/modes change
    state.reset_session()

    # active_corpus_ids should STILL be ["definer", "codeforge"]
    assert state.active_corpus_ids == ["definer", "codeforge"]


async def test_ensure_session_reapplies_corpus_selection():
    """ensure_session() applies active_corpus_ids to the new session.

    After reset_session() discards the old session, ensure_session()
    creates a new one AND immediately applies the persistent corpus
    selection — so the backend session has codeforge active.
    """
    from gui.state import GuiState

    state = GuiState()
    state.api_client = StubApiClient()
    state.active_corpus_ids = ["definer", "codeforge"]

    # Simulate: session exists → user changes models → reset → new session
    state.session_id = "old_session"
    state.reset_session()
    assert state.session_id is None

    # ensure_session creates a new session and applies corpus selection
    new_sid = await state.ensure_session()

    # The new session exists
    assert new_sid is not None
    assert new_sid != "old_session"

    # The corpus selection was applied via update_session_corpora
    stub: StubApiClient = state.api_client
    assert len(stub.corpus_updates) == 1
    updated_sid, updated_corpora = stub.corpus_updates[0]
    assert updated_sid == new_sid
    assert "codeforge" in updated_corpora
    assert "definer" in updated_corpora


async def test_ensure_session_skips_corpus_update_for_default_only():
    """When active_corpus_ids is just ["definer"], no update is sent.

    The default session already has definer-only, so there's no need
    to PATCH — avoids a redundant API call.
    """
    from gui.state import GuiState

    state = GuiState()
    state.api_client = StubApiClient()
    # active_corpus_ids is ["definer"] by default
    assert state.active_corpus_ids == ["definer"]

    new_sid = await state.ensure_session()
    assert new_sid is not None

    stub: StubApiClient = state.api_client
    # No corpus update should have been sent (default matches)
    assert len(stub.corpus_updates) == 0


async def test_full_user_path_select_codeforge_then_reset_then_send():
    """Full regression: select codeforge → reset → ensure_session → verify.

    This reproduces the exact user path from the diagnosis:
      1. Select codeforge
      2. Change models (triggers reset_session)
      3. Send Multi-Cast (triggers ensure_session)
      4. Assert the new session has codeforge active
    """
    from gui.state import GuiState

    state = GuiState()
    state.api_client = StubApiClient()

    # Step 1: user selects codeforge (simulates _on_update_corpora)
    state.active_corpus_ids = ["definer", "codeforge"]
    # If there was a session, PATCH it
    state.session_id = "session_1"
    state.api_client.sessions["session_1"] = {"id": "session_1", "active_corpus_ids": ["definer"]}
    await state.api_client.update_session_corpora("session_1", state.active_corpus_ids)

    # Step 2: user changes panel models → reset_session
    state.reset_session()
    assert state.session_id is None
    # active_corpus_ids survives
    assert state.active_corpus_ids == ["definer", "codeforge"]

    # Step 3: user presses Multi-Cast → ensure_session
    new_sid = await state.ensure_session()
    assert new_sid is not None

    # Step 4: the new session has codeforge active
    stub: StubApiClient = state.api_client
    # Two corpus updates: one for session_1, one for the new session
    assert len(stub.corpus_updates) == 2
    last_update_sid, last_update_corpora = stub.corpus_updates[-1]
    assert last_update_sid == new_sid
    assert "codeforge" in last_update_corpora
    assert "definer" in last_update_corpora


async def test_reset_session_does_not_clear_active_corpus_ids():
    """reset_session() clears session_id but NOT active_corpus_ids."""
    from gui.state import GuiState

    state = GuiState()
    state.api_client = StubApiClient()
    state.session_id = "test_session"
    state.active_corpus_ids = ["definer", "codeforge", "research"]
    state.pending_gate = {"some": "gate"}
    state.ingestion_status = "ingesting"
    state.chunks_indexed = 42

    state.reset_session()

    # session-related state is cleared
    assert state.session_id is None
    assert state.pending_gate is None
    assert state.ingestion_status == "idle"
    assert state.chunks_indexed == 0

    # BUT active_corpus_ids is preserved
    assert state.active_corpus_ids == ["definer", "codeforge", "research"]


async def test_default_active_corpus_ids_is_definer_only():
    """A fresh GuiState defaults to ["definer"] only."""
    from gui.state import GuiState

    state = GuiState()
    state.api_client = StubApiClient()
    assert state.active_corpus_ids == ["definer"]
