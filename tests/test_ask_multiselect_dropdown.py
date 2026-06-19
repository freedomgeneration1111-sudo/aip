"""Tests for the multi-select model dropdown pattern (current cycle).

The Ask page chat header now uses a SINGLE multi-select checkbox dropdown
for picking N models from the unified "available models" pool. The send
handler auto-routes based on count — no separate "Multi-Cast" button is
required:
  - 0 selected  → notify "pick a model" and bail
  - 1 selected  → normal single-model chat (WS route)
  - ≥2 selected → Multi-Cast Fusion (POST /beast/compare-models with
    ``skip_default_slots=True``). The ``beast`` slot is used ONLY for
    the Judge+Synth synthesis stages, not as a panel model. Models are
    NOT tied to actor slots/roles.

This file verifies:

  1. The old Multi-Cast button + slot checkbox row helpers
     (``_toggle_multicast``, ``_toggle_multicast_slot``,
     ``_toggle_multicast_model_id``) are GONE from ask.py.
  2. The new multi-select dropdown handler ``_on_chat_models_changed``
     is defined and is async.
  3. ``_dispatch_send`` no longer branches on ``state.multicast_enabled``
     — it branches on ``len(state.multicast_selected_model_ids) >= 2``.
  4. ``_send_multicast`` calls ``run_model_council`` with
     ``selected_model_slots=[]`` and ``skip_default_slots=True`` so the
     backend does NOT auto-add the default TOML slots.
  5. ``ModelCouncilRequest`` has a ``skip_default_slots: bool = False``
     field, and ``_resolve_comparison_slots`` honors it (returns []
     when ``skip_default_slots=True`` and ``requested_slots=[]``).
  6. ``AipApiClient.run_model_council`` accepts and forwards
     ``skip_default_slots``.
  7. Backward compat: when ``skip_default_slots=False`` (default), the
     resolver still falls back to ``_DEFAULT_COMPARISON_SLOTS`` —
     existing tests and external API clients are unaffected.
  8. The ``beast`` slot is NOT one of the selected_model_ids in the
     GUI's request (the user picks models from the unified dropdown,
     not slot names).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Path helpers ────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GUI_PAGES = _REPO_ROOT / "gui" / "pages"
_GUI_API_CLIENT = _REPO_ROOT / "gui" / "api_client.py"
_GUI_STATE = _REPO_ROOT / "gui" / "state.py"
_ASK_PY = _GUI_PAGES / "ask.py"
_MODEL_COUNCIL_PY = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "model_council.py"


def _read_ask_source() -> str:
    return _ASK_PY.read_text(encoding="utf-8")


def _read_api_client_source() -> str:
    return _GUI_API_CLIENT.read_text(encoding="utf-8")


def _read_state_source() -> str:
    return _GUI_STATE.read_text(encoding="utf-8")


def _read_model_council_source() -> str:
    return _MODEL_COUNCIL_PY.read_text(encoding="utf-8")


def _extract_func(source: str, func_name: str) -> str | None:
    """Extract a function's source code (from ``def`` to next top-level def/class)."""
    pattern = rf"(async\s+)?def\s+{re.escape(func_name)}.*?(?=\n(?:async\s+)?def\s+\w+|\nclass\s+\w+|\Z)"
    match = re.search(pattern, source, re.DOTALL)
    return match.group() if match else None


# ── 1. Old Multi-Cast helpers are GONE ─────────────────────────────────


class TestOldMulticastHelpersRemoved:
    """The separate Multi-Cast button + slot/library checkbox row was removed.

    The chat header now uses a single multi-select dropdown — no separate
    toggle button, no second row of checkboxes."""

    def test_toggle_multicast_removed(self):
        """``_toggle_multicast`` (the ON/OFF button handler) is gone."""
        source = _read_ask_source()
        assert not re.search(r"^\s*def\s+_toggle_multicast\s*\(", source, re.MULTILINE), (
            "_toggle_multicast must be removed — the Multi-Cast ON/OFF button "
            "is gone. The dropdown auto-routes based on count now."
        )

    def test_toggle_multicast_slot_removed(self):
        """``_toggle_multicast_slot`` (slot checkbox handler) is gone."""
        source = _read_ask_source()
        assert not re.search(r"^\s*def\s+_toggle_multicast_slot\s*\(", source, re.MULTILINE), (
            "_toggle_multicast_slot must be removed — the slot checkbox row is gone."
        )

    def test_toggle_multicast_model_id_removed(self):
        """``_toggle_multicast_model_id`` (library checkbox handler) is gone."""
        source = _read_ask_source()
        assert not re.search(r"^\s*def\s+_toggle_multicast_model_id\s*\(", source, re.MULTILINE), (
            "_toggle_multicast_model_id must be removed — the library checkbox "
            "row is gone (merged into the unified dropdown)."
        )

    def test_multicast_btn_reference_removed(self):
        """The ``multicast_btn`` variable (the ON/OFF button element) is gone."""
        source = _read_ask_source()
        assert "multicast_btn" not in source, (
            "multicast_btn reference must be removed — the Multi-Cast button is gone."
        )

    def test_multicast_row_reference_removed(self):
        """The ``multicast_row`` variable (the second checkbox row) is gone."""
        source = _read_ask_source()
        assert "multicast_row" not in source, (
            "multicast_row reference must be removed — the slot/library "
            "checkbox row is gone (merged into the unified dropdown)."
        )


# ── 2. New multi-select dropdown handler is defined ────────────────────


class TestNewMultiselectHandler:
    """The new ``_on_chat_models_changed`` handler drives the multi-select dropdown."""

    def test_on_chat_models_changed_defined(self):
        """``_on_chat_models_changed`` is defined in ask.py."""
        source = _read_ask_source()
        assert re.search(
            r"^\s*async\s+def\s+_on_chat_models_changed\s*\(", source, re.MULTILINE
        ), "_on_chat_models_changed must be defined (async) in ask.py"

    def test_on_chat_models_changed_is_async(self):
        """``_on_chat_models_changed`` must be ``async def`` (awaits backend slot update)."""
        source = _read_ask_source()
        func = _extract_func(source, "_on_chat_models_changed")
        assert func is not None, "_on_chat_models_changed not found in ask.py"
        assert func.startswith("async"), (
            "_on_chat_models_changed must be async def — it awaits "
            "_on_chat_model_changed for the single-model backend update."
        )

    def test_on_chat_models_changed_takes_list_arg(self):
        """The handler accepts a list of model IDs (the multi-select value)."""
        source = _read_ask_source()
        func = _extract_func(source, "_on_chat_models_changed")
        assert func is not None
        sig_line = func.split(":", 1)[0] + ":"  # up to first colon (return type annotation)
        # The first parameter after self/state should accept list[str]
        assert "models" in sig_line, (
            "_on_chat_models_changed first arg should be named 'models' to "
            "reflect that it accepts a list of selected model IDs."
        )

    def test_on_chat_models_changed_writes_state(self):
        """The handler updates ``state.multicast_selected_model_ids``."""
        source = _read_ask_source()
        func = _extract_func(source, "_on_chat_models_changed")
        assert func is not None
        assert "multicast_selected_model_ids" in func, (
            "_on_chat_models_changed must update state.multicast_selected_model_ids"
        )

    def test_on_chat_models_changed_derives_multicast_enabled(self):
        """The handler derives ``state.multicast_enabled`` from the count.

        ``multicast_enabled`` is now a derived property — it's True iff
        ``len(state.multicast_selected_model_ids) >= 2``. The flag is
        kept for back-compat with any consumer that still reads it.
        """
        source = _read_ask_source()
        func = _extract_func(source, "_on_chat_models_changed")
        assert func is not None
        assert "multicast_enabled" in func, (
            "_on_chat_models_changed must derive state.multicast_enabled from the count"
        )

    def test_dropdown_uses_multiple_true(self):
        """The chat header uses ``ui.select(..., multiple=True)`` for multi-select."""
        source = _read_ask_source()
        # Find the ui.select call in the chat header section
        # We can't be too specific about whitespace, but multiple=True must be present
        assert "multiple=True" in source, (
            "The chat header must use ui.select(..., multiple=True) to enable "
            "checkbox multi-select in the dropdown."
        )

    def test_dropdown_on_change_calls_on_chat_models_changed(self):
        """The dropdown's on_change handler calls ``_on_chat_models_changed``."""
        source = _read_ask_source()
        # Look for the on_change lambda that references _on_chat_models_changed
        assert "_on_chat_models_changed" in source, (
            "The dropdown must call _on_chat_models_changed on selection change."
        )


# ── 3. _dispatch_send auto-routes by count ─────────────────────────────


class TestDispatchSendAutoRoutes:
    """``_dispatch_send`` no longer checks ``state.multicast_enabled`` — it
    branches on ``len(state.multicast_selected_model_ids) >= 2``."""

    def test_dispatch_send_reads_count_not_flag(self):
        """``_dispatch_send`` reads ``len(state.multicast_selected_model_ids)``,
        not ``state.multicast_enabled`` (the legacy flag)."""
        source = _read_ask_source()
        func = _extract_func(source, "_dispatch_send")
        assert func is not None, "_dispatch_send not found in ask.py"
        assert "multicast_selected_model_ids" in func, (
            "_dispatch_send must read state.multicast_selected_model_ids to "
            "auto-route based on count."
        )
        assert "n_selected" in func or "len(" in func, (
            "_dispatch_send must compute the count of selected models to "
            "decide whether to multi-cast."
        )

    def test_dispatch_send_does_not_read_multicast_enabled_flag(self):
        """``_dispatch_send`` does NOT branch on the legacy
        ``state.multicast_enabled`` flag — it derives the routing from
        the count of selected models instead."""
        source = _read_ask_source()
        func = _extract_func(source, "_dispatch_send")
        assert func is not None
        # The legacy flag may still be referenced for back-compat, but
        # the BRANCHING decision must be on the count, not the flag.
        # Specifically, there must NOT be a line like
        #   if state.multicast_enabled:
        # that controls the routing.
        assert not re.search(r"if\s+state\.multicast_enabled\s*:", func), (
            "_dispatch_send must NOT branch on state.multicast_enabled — "
            "it derives routing from len(state.multicast_selected_model_ids)."
        )

    def test_dispatch_send_routes_zero_to_notify(self):
        """When 0 models are selected, the handler notifies the user
        instead of silently doing nothing."""
        source = _read_ask_source()
        func = _extract_func(source, "_dispatch_send")
        assert func is not None
        # Must check for 0-selection case
        assert "n_selected == 0" in func or "n_selected < 1" in func, (
            "_dispatch_send must handle the n_selected == 0 case (notify user)."
        )

    def test_dispatch_send_routes_two_or_more_to_multicast(self):
        """When ≥2 models are selected, the handler calls _send_multicast."""
        source = _read_ask_source()
        func = _extract_func(source, "_dispatch_send")
        assert func is not None
        assert "_send_multicast" in func, (
            "_dispatch_send must call _send_multicast when n_selected >= 2."
        )

    def test_dispatch_send_routes_one_to_single_model(self):
        """When exactly 1 model is selected, the handler calls _send_prompt."""
        source = _read_ask_source()
        func = _extract_func(source, "_dispatch_send")
        assert func is not None
        assert "_send_prompt" in func, (
            "_dispatch_send must call _send_prompt when n_selected == 1."
        )


# ── 4. _send_multicast uses skip_default_slots + empty slots list ──────


class TestSendMulticastContract:
    """``_send_multicast`` sends ``selected_model_slots=[]`` and
    ``skip_default_slots=True`` so the backend does NOT auto-add the
    default TOML slots (synthesis/evaluation/beast)."""

    def test_send_multicast_sends_empty_slots(self):
        """``_send_multicast`` passes ``selected_model_slots=[]``."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        # Either explicitly [] or via state.multicast_selected_slots which is always []
        # The user requirement: models NOT tied to actor slots/roles.
        assert "selected_model_slots=[]" in func or (
            "selected_model_slots" in func and "[]" in func
        ), (
            "_send_multicast must pass selected_model_slots=[] to "
            "run_model_council — models are NOT tied to actor slots/roles."
        )

    def test_send_multicast_sets_skip_default_slots_true(self):
        """``_send_multicast`` passes ``skip_default_slots=True``."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        assert "skip_default_slots=True" in func, (
            "_send_multicast must pass skip_default_slots=True so the backend "
            "does NOT auto-add the default TOML slots (synthesis/evaluation/beast)."
        )

    def test_send_multicast_sends_selected_model_ids(self):
        """``_send_multicast`` passes ``selected_model_ids=selected_model_ids``
        (the user's multi-select dropdown choices)."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        assert "selected_model_ids=selected_model_ids" in func, (
            "_send_multicast must pass selected_model_ids=selected_model_ids — "
            "these are the user's multi-select dropdown choices."
        )


# ── 5. Backend: skip_default_slots field + resolver behavior ───────────


class TestBackendSkipDefaultSlots:
    """``ModelCouncilRequest.skip_default_slots`` is the new field that lets
    the GUI send only library IDs without the backend auto-adding default
    TOML slots."""

    def test_request_has_skip_default_slots_field(self):
        """``ModelCouncilRequest`` has a ``skip_default_slots: bool = False`` field."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        # Field exists
        assert hasattr(ModelCouncilRequest, "model_fields"), "ModelCouncilRequest must be a pydantic model"
        assert "skip_default_slots" in ModelCouncilRequest.model_fields, (
            "ModelCouncilRequest must have a 'skip_default_slots' field — "
            "the GUI uses it to opt out of the default TOML slot fallback."
        )

    def test_request_skip_default_slots_defaults_false(self):
        """The default is ``False`` (backward compat with existing callers/tests)."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test")
        assert req.skip_default_slots is False, (
            "skip_default_slots must default to False — preserves the existing "
            "fallback to _DEFAULT_COMPARISON_SLOTS for external API clients."
        )

    def test_request_skip_default_slots_can_be_true(self):
        """The field can be set to ``True``."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        req = ModelCouncilRequest(prompt="test", skip_default_slots=True)
        assert req.skip_default_slots is True

    def test_resolver_skips_defaults_when_flag_true_and_slots_empty(self):
        """``_resolve_comparison_slots(provider, [], skip_default_slots=True)``
        returns ``[]`` even when the provider has default slots configured."""
        from aip.adapter.api.routes.model_council import _resolve_comparison_slots

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "evaluation", "beast", "embedding"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "evaluation": {"provider": "openai_compatible", "model": "claude-3-opus", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek-chat", "api_key": "k"},
            "embedding": {"provider": "openai_compatible", "model": "emb", "api_key": "k"},
        }.get(slot, {})

        # Without the flag: defaults are returned
        result_default = _resolve_comparison_slots(provider, [])
        assert "synthesis" in result_default
        assert "beast" in result_default

        # With the flag: NO defaults are returned
        result_skip = _resolve_comparison_slots(provider, [], skip_default_slots=True)
        assert result_skip == [], (
            "When skip_default_slots=True and requested_slots is empty, the "
            "resolver must return [] — the panel will be built ONLY from "
            "selected_model_ids (library IDs)."
        )

    def test_resolver_skips_defaults_when_flag_true_and_slots_none(self):
        """Same as above but with ``requested_slots=None`` (e.g. an external
        API client that didn't send the field at all)."""
        from aip.adapter.api.routes.model_council import _resolve_comparison_slots

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "beast"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4"},
            "beast": {"provider": "openai_compatible", "model": "deepseek"},
        }.get(slot, {})

        result = _resolve_comparison_slots(provider, None, skip_default_slots=True)
        assert result == []

    def test_resolver_still_uses_requested_slots_when_flag_true(self):
        """If the caller passes explicit ``requested_slots`` (non-empty),
        those are still honored even when ``skip_default_slots=True``."""
        from aip.adapter.api.routes.model_council import _resolve_comparison_slots

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "evaluation", "beast"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4"},
            "evaluation": {"provider": "openai_compatible", "model": "claude"},
            "beast": {"provider": "openai_compatible", "model": "deepseek"},
        }.get(slot, {})

        # Caller explicitly requests ["beast"] with skip_default_slots=True
        result = _resolve_comparison_slots(provider, ["beast"], skip_default_slots=True)
        assert result == ["beast"], (
            "Explicit requested_slots must be honored even when "
            "skip_default_slots=True — the flag only suppresses the EMPTY-list "
            "fallback, not explicit selections."
        )

    def test_resolver_backwards_compat_default_false(self):
        """Backward compat: when ``skip_default_slots=False`` (default),
        the resolver falls back to ``_DEFAULT_COMPARISON_SLOTS`` as before."""
        from aip.adapter.api.routes.model_council import _resolve_comparison_slots

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "evaluation", "beast"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4"},
            "evaluation": {"provider": "openai_compatible", "model": "claude"},
            "beast": {"provider": "openai_compatible", "model": "deepseek"},
        }.get(slot, {})

        # Default behavior — no skip_default_slots kwarg
        result = _resolve_comparison_slots(provider, [])
        assert "synthesis" in result
        assert "evaluation" in result
        assert "beast" in result


# ── 6. api_client.run_model_council forwards skip_default_slots ────────


class TestApiClientForwardsSkipDefaultSlots:
    """``AipApiClient.run_model_council`` accepts and forwards
    ``skip_default_slots`` in the POST payload."""

    def test_run_model_council_accepts_skip_default_slots(self):
        """The method signature includes ``skip_default_slots: bool = False``."""
        from gui.api_client import AipApiClient

        sig = inspect.signature(AipApiClient.run_model_council)
        assert "skip_default_slots" in sig.parameters, (
            "run_model_council must accept a skip_default_slots parameter."
        )
        param = sig.parameters["skip_default_slots"]
        assert param.default is False, (
            "skip_default_slots must default to False — backward compat."
        )

    def test_run_model_council_forwards_skip_default_slots_in_payload(self):
        """The method includes ``skip_default_slots`` in the POST payload."""
        source = _read_api_client_source()
        # Find the run_model_council method body
        match = re.search(
            r"async\s+def\s+run_model_council.*?(?=\n    async\s+def\s+\w+|\n    def\s+\w+|\Z)",
            source,
            re.DOTALL,
        )
        assert match is not None, "run_model_council not found in api_client.py"
        method_body = match.group()
        assert "skip_default_slots" in method_body, (
            "run_model_council must reference skip_default_slots in its body "
            "(either in the payload dict or the method signature)."
        )
        # Must be in the payload dict that gets POSTed
        assert '"skip_default_slots"' in method_body or "skip_default_slots=" in method_body, (
            "skip_default_slots must be included in the POST payload dict."
        )


# ── 7. State.py contract ────────────────────────────────────────────────


class TestGuiStateContract:
    """``GuiState`` keeps the field names for back-compat but updates the
    semantics: ``multicast_selected_model_ids`` is the canonical selection
    list, ``multicast_enabled`` is derived, ``multicast_selected_slots``
    is always empty."""

    def test_state_has_multicast_selected_model_ids(self):
        """``GuiState`` has the ``multicast_selected_model_ids`` field."""
        from gui.state import GuiState

        state = GuiState()
        assert hasattr(state, "multicast_selected_model_ids")
        assert state.multicast_selected_model_ids == []

    def test_state_has_multicast_enabled_for_backcompat(self):
        """``multicast_enabled`` is kept for back-compat (derived property now)."""
        from gui.state import GuiState

        state = GuiState()
        assert hasattr(state, "multicast_enabled")
        assert state.multicast_enabled is False  # default before count is computed

    def test_state_has_multicast_selected_slots_for_backcompat(self):
        """``multicast_selected_slots`` is kept for back-compat (always empty)."""
        from gui.state import GuiState

        state = GuiState()
        assert hasattr(state, "multicast_selected_slots")
        assert state.multicast_selected_slots == []

    def test_state_docstring_clarifies_semantics(self):
        """The state.py source documents the new semantics (models NOT tied
        to actor slots/roles; beast slot only for synthesis)."""
        source = _read_state_source()
        # Look for the new comment block — the phrase may wrap across
        # multiple comment lines so we check for the key tokens.
        source_lower = source.lower()
        assert "not tied to actor" in source_lower, (
            "state.py must document the new 'models not tied to actor slots/roles' "
            "semantics in the field docstrings."
        )
        assert "beast" in source_lower and "synth" in source_lower, (
            "state.py must mention that the beast slot is used ONLY for "
            "the Judge+Synth synthesis stages, not as a panel model."
        )


# ── 8. End-to-end: skip_default_slots lets GUI send only library IDs ───


class TestEndToEndSkipDefaultSlots:
    """When the GUI sends ``selected_model_slots=[]``,
    ``selected_model_ids=[X, Y]``, and ``skip_default_slots=True``,
    the backend builds the panel ONLY from [X, Y] — not from the
    default TOML slots."""

    @pytest.mark.asyncio
    async def test_skip_default_slots_yields_only_library_panel(self):
        """Verify the backend's compare_models respects skip_default_slots
        end-to-end: the per-model results contain ONLY the library IDs,
        not the default TOML slots."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        # Mock provider — has synthesis/evaluation/beast configured
        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "evaluation", "beast"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "evaluation": {"provider": "openai_compatible", "model": "claude", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek", "api_key": "k"},
        }.get(slot, {})
        # Slot calls would return JSON; we won't call them at all in this test
        provider.call = AsyncMock(return_value={"content": "{}", "model": "x", "usage": {}, "latency_ms": 1, "error": False})

        from aip.adapter.api.dependencies import AipContainer
        container = AipContainer({})
        container.model_provider = provider
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        # Mock _call_library_model_id so we don't actually hit OpenRouter
        async def fake_library_call(model_id, user_prompt=None, messages=None):
            return {
                "content": f"Answer from {model_id}",
                "model": model_id,
                "display_name": model_id,
                "usage": {},
                "latency_ms": 50,
                "cost_usd": 0.0,
                "error": False,
            }

        # Mock the fusion engine picker so it doesn't actually call models
        # for Judge+Synth stages — we want to verify the PANEL composition only.
        with (
            patch(
                "aip.adapter.api.routes.model_council._call_library_model_id",
                new=AsyncMock(side_effect=fake_library_call),
            ),
            patch(
                "aip.adapter.api.routes.model_council._call_fusion_engine",
                new=AsyncMock(return_value={
                    "content": '{"status":"completed","analysis":{"consensus":[],"contradictions":[],"partial_coverage":[],"unique_insights":[],"blind_spots":[]}}',
                    "model": "fake-judge",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }),
            ),
            patch(
                "aip.adapter.api.routes.model_council._pick_fusion_engine",
                return_value=("library", "model_a"),
            ),
        ):
            request = ModelCouncilRequest(
                prompt="test",
                selected_model_slots=[],  # GUI sends empty
                selected_model_ids=["model_a", "model_b"],  # 2 library IDs
                skip_default_slots=True,  # opt out of default slot fallback
            )
            result = await compare_models(request, container=container)

        # The panel must contain ONLY the 2 library IDs — NOT the default
        # synthesis/evaluation/beast slots.
        panel_ids = {pm.model_id for pm in result.selected_models}
        assert panel_ids == {"model_a", "model_b"}, (
            f"Panel must contain ONLY the library IDs [model_a, model_b], "
            f"got {panel_ids}. The skip_default_slots flag must prevent the "
            f"backend from auto-adding the default TOML slots."
        )
        # No slot-sourced results
        assert all(pm.source == "library" for pm in result.selected_models), (
            "All panelists must be library-sourced (source='library') when "
            "skip_default_slots=True and selected_model_slots=[]."
        )

    @pytest.mark.asyncio
    async def test_backwards_compat_no_skip_flag_still_uses_defaults(self):
        """When skip_default_slots is NOT set (default False), the backend
        falls back to _DEFAULT_COMPARISON_SLOTS — existing tests and
        external API clients are unaffected."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest, compare_models

        provider = MagicMock()
        provider.list_slots.return_value = ["synthesis", "evaluation", "beast"]
        provider._resolve_slot_config = lambda slot: {
            "synthesis": {"provider": "openai_compatible", "model": "gpt-4", "api_key": "k"},
            "evaluation": {"provider": "openai_compatible", "model": "claude", "api_key": "k"},
            "beast": {"provider": "openai_compatible", "model": "deepseek", "api_key": "k"},
        }.get(slot, {})

        async def fake_call(slot_name, messages, **kwargs):
            return {
                "content": f"Answer from {slot_name}",
                "model": slot_name,
                "usage": {},
                "latency_ms": 50,
                "cost_usd": 0.0,
                "error": False,
            }
        provider.call = AsyncMock(side_effect=fake_call)

        from aip.adapter.api.dependencies import AipContainer
        container = AipContainer({})
        container.model_provider = provider
        container.artifact_store = AsyncMock()
        container.ecs_store = AsyncMock()

        with (
            patch(
                "aip.adapter.api.routes.model_council._call_fusion_engine",
                new=AsyncMock(return_value={
                    "content": '{"status":"completed","analysis":{"consensus":[],"contradictions":[],"partial_coverage":[],"unique_insights":[],"blind_spots":[]}}',
                    "model": "fake-judge",
                    "usage": {},
                    "latency_ms": 10,
                    "cost_usd": 0.0,
                    "error": False,
                }),
            ),
            patch(
                "aip.adapter.api.routes.model_council._pick_fusion_engine",
                return_value=("slot", "beast"),
            ),
        ):
            # No skip_default_slots field — default False
            request = ModelCouncilRequest(prompt="test")
            result = await compare_models(request, container=container)

        # Default behavior: panel contains the 3 default slots
        panel_slots = {pm.model_slot for pm in result.selected_models}
        assert "synthesis" in panel_slots
        assert "evaluation" in panel_slots
        assert "beast" in panel_slots


# ── 9. Beast slot is only used for synthesis, not as a panel model ─────


class TestBeastSlotOnlyForSynthesis:
    """When the GUI sends a multi-cast request, the ``beast`` slot must
    NOT appear as a panel model — only as the Judge+Synth engine."""

    def test_ask_py_does_not_send_beast_in_selected_model_ids(self):
        """The ask.py source does NOT hardcode 'beast' in the
        selected_model_ids list — the user picks models from the
        unified dropdown (not slot names)."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        # The function reads selected_model_ids from state — it does NOT
        # inject 'beast' as a slot name. Verify no hardcoded 'beast' string
        # is added to the model IDs list.
        # We're looking for the absence of patterns like:
        #   selected_model_ids.append("beast")
        #   selected_model_ids = ["beast", ...]
        assert not re.search(r'selected_model_ids\s*\.\s*append\s*\(\s*["\']beast["\']', func), (
            "_send_multicast must NOT inject 'beast' into selected_model_ids — "
            "the beast slot is only used for synthesis, not as a panel model."
        )

    def test_ask_py_sends_empty_selected_model_slots(self):
        """The ask.py source passes ``selected_model_slots=[]`` (or an
        empty list reference) — never a list containing 'beast' or any
        other slot name. The 'models not tied to actor slots/roles'
        rule means slot names are NOT sent from the GUI."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        # Verify no 'beast'/'synthesis'/'evaluation' appears in selected_model_slots
        # The function should pass selected_model_slots=[] explicitly.
        assert re.search(r"selected_model_slots\s*=\s*\[\s*\]", func), (
            "_send_multicast must pass selected_model_slots=[] — models are "
            "NOT tied to actor slots/roles."
        )
