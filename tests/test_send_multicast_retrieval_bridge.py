"""Tests for Step 2-B — GUI wiring of the Phase 1 retrieval bridge.

Step 2-B activates the retrieval bridge end-to-end:
  - ``gui/api_client.py::run_model_council`` forwards the new
    ``assemble_augmented_context`` flag in the POST payload
  - ``gui/pages/ask.py::_send_multicast`` sends a non-empty ``turn_id``
    (the session_id, used as a per-session signal) AND
    ``assemble_augmented_context=(state.current_mode == 'augmented')``
    so the backend calls the shared
    ``routes/_augmented_context.py::assemble_augmented_context()``
    helper and prepends corpus/wiki/graph/definer context to each
    panel call's user prompt.

This fixes the AIP-acronym bug from the Fusion report's Part I —
Multi-Cast in augmented mode no longer answers blind.

Test coverage:
  1. ``AipApiClient.run_model_council`` accepts ``assemble_augmented_context``
     param and includes it in the POST payload.
  2. ``_send_multicast`` passes ``assemble_augmented_context=True``
     when ``state.current_mode == 'augmented'``.
  3. ``_send_multicast`` passes ``assemble_augmented_context=False``
     when ``state.current_mode == 'normal'`` (default).
  4. ``_send_multicast`` passes a non-empty ``turn_id`` when augmented
     mode is on (so the backend's helper gate passes).
  5. ``_send_multicast`` passes ``turn_id=""`` when normal mode (no
     retrieval needed).
  6. ``_send_multicast`` passes ``skip_default_slots=True`` (carried
     over from the prior cycle — models NOT tied to actor slots/roles).
  7. ``_send_multicast`` passes ``selected_model_slots=[]`` (carried
     over — models NOT tied to actor slots/roles).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest


# ── Path helpers ────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GUI_API_CLIENT = _REPO_ROOT / "gui" / "api_client.py"
_ASK_PY = _REPO_ROOT / "gui" / "pages" / "ask.py"


def _read_api_client_source() -> str:
    return _GUI_API_CLIENT.read_text(encoding="utf-8")


def _read_ask_source() -> str:
    return _ASK_PY.read_text(encoding="utf-8")


def _extract_func(source: str, func_name: str) -> str | None:
    """Extract a function's source code (from ``def`` to next top-level def/class)."""
    pattern = rf"(async\s+)?def\s+{re.escape(func_name)}.*?(?=\n(?:async\s+)?def\s+\w+|\nclass\s+\w+|\Z)"
    match = re.search(pattern, source, re.DOTALL)
    return match.group() if match else None


# ── 1. api_client.run_model_council forwards assemble_augmented_context ─


class TestApiClientForwardsAssembleAugmentedContext:
    """``AipApiClient.run_model_council`` accepts and forwards
    ``assemble_augmented_context`` in the POST payload."""

    def test_run_model_council_accepts_assemble_augmented_context(self):
        """The method signature includes ``assemble_augmented_context: bool = False``."""
        from gui.api_client import AipApiClient

        sig = inspect.signature(AipApiClient.run_model_council)
        assert "assemble_augmented_context" in sig.parameters, (
            "run_model_council must accept an assemble_augmented_context parameter "
            "— the GUI uses it to opt into the Phase 1 retrieval bridge."
        )
        param = sig.parameters["assemble_augmented_context"]
        assert param.default is False, (
            "assemble_augmented_context must default to False — backward compat "
            "with existing callers that don't send the flag."
        )

    def test_run_model_council_includes_assemble_augmented_context_in_payload(self):
        """The method includes ``assemble_augmented_context`` in the POST payload dict."""
        source = _read_api_client_source()
        match = re.search(
            r"async\s+def\s+run_model_council.*?(?=\n    async\s+def\s+\w+|\n    def\s+\w+|\Z)",
            source,
            re.DOTALL,
        )
        assert match is not None, "run_model_council not found in api_client.py"
        method_body = match.group()
        assert '"assemble_augmented_context"' in method_body, (
            "run_model_council must include 'assemble_augmented_context' in the "
            "POST payload dict so the backend receives the flag."
        )

    def test_run_model_council_payload_key_matches_backend_field_name(self):
        """The payload key ``assemble_augmented_context`` matches the
        ``ModelCouncilRequest.assemble_augmented_context`` field name
        (contract check — the bug is always in the gap)."""
        source = _read_api_client_source()
        match = re.search(
            r"async\s+def\s+run_model_council.*?(?=\n    async\s+def\s+\w+|\n    def\s+\w+|\Z)",
            source,
            re.DOTALL,
        )
        assert match is not None
        method_body = match.group()
        # The payload key must be exactly "assemble_augmented_context"
        # (matches the Pydantic field name on ModelCouncilRequest)
        assert '"assemble_augmented_context": assemble_augmented_context' in method_body, (
            "Payload key must be 'assemble_augmented_context' (matching the "
            "ModelCouncilRequest field name) — contract check."
        )


# ── 2. _send_multicast passes assemble_augmented_context when augmented ──


class TestSendMulticastPassesAugmentedFlag:
    """``_send_multicast`` passes ``assemble_augmented_context=True`` when
    ``state.current_mode == 'augmented'``, and ``False`` otherwise."""

    def test_send_multicast_reads_state_current_mode(self):
        """``_send_multicast`` reads ``state.current_mode`` to decide
        whether to pass the augmented flag."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None, "_send_multicast not found in ask.py"
        assert "state.current_mode" in func, (
            "_send_multicast must read state.current_mode to decide whether "
            "to pass assemble_augmented_context=True."
        )

    def test_send_multicast_passes_assemble_augmented_context_kwarg(self):
        """``_send_multicast`` passes the ``assemble_augmented_context``
        kwarg to ``run_model_council``."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        assert "assemble_augmented_context=" in func, (
            "_send_multicast must pass assemble_augmented_context=... to "
            "run_model_council — this is the Phase 1 retrieval bridge activation."
        )

    def test_send_multicast_uses_is_augmented_variable(self):
        """``_send_multicast`` computes ``is_augmented = state.current_mode == 'augmented'``
        and passes it as the flag value."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        # The function must compute is_augmented from state.current_mode
        assert "is_augmented" in func, (
            "_send_multicast must compute is_augmented from state.current_mode "
            "— this is the variable that drives the assemble_augmented_context flag."
        )
        assert "state.current_mode == \"augmented\"" in func or (
            "state.current_mode == 'augmented'" in func
        ), "is_augmented must be derived from state.current_mode == 'augmented'"
        # The flag must be passed as assemble_augmented_context=is_augmented
        assert "assemble_augmented_context=is_augmented" in func, (
            "_send_multicast must pass assemble_augmented_context=is_augmented "
            "— the flag is True when augmented mode is on."
        )


# ── 3. _send_multicast passes non-empty turn_id when augmented ─────────


class TestSendMulticastPassesTurnId:
    """``_send_multicast`` passes a non-empty ``turn_id`` when augmented
    mode is on (so the backend's helper gate passes), and ``turn_id=""``
    when normal mode."""

    def test_send_multicast_passes_turn_id_kwarg(self):
        """``_send_multicast`` passes the ``turn_id`` kwarg to
        ``run_model_council``."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        assert "turn_id=" in func, (
            "_send_multicast must pass turn_id=... to run_model_council — "
            "the backend's helper gate requires a non-empty turn_id."
        )

    def test_send_multicast_passes_session_id_as_turn_id_when_augmented(self):
        """When augmented mode is on, ``_send_multicast`` passes
        ``turn_id=session_id`` (a non-empty signal that gates the
        backend's helper call). The helper itself uses ``session_id``
        for session_meta lookup, not ``turn_id`` — so ``session_id``
        as the turn_id signal is sufficient."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        # The function must pass turn_id=session_id when is_augmented
        assert "turn_id=session_id if is_augmented" in func, (
            "_send_multicast must pass turn_id=session_id when is_augmented "
            "— the session_id is a non-empty signal that gates the backend's "
            "helper call."
        )

    def test_send_multicast_passes_empty_turn_id_when_normal(self):
        """When normal mode, ``_send_multicast`` passes ``turn_id=""``
        (no retrieval needed — the backend's helper gate won't fire)."""
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        # The ternary must have the empty-string fallback for normal mode
        assert 'turn_id=session_id if is_augmented else ""' in func, (
            "_send_multicast must pass turn_id='' when normal mode — "
            "the backend's helper gate won't fire (assemble_augmented_context=False "
            "AND turn_id='' both prevent the helper call)."
        )


# ── 4. _send_multicast still passes skip_default_slots + empty slots ────


class TestSendMulticastContractUnchanged:
    """Carry-over contract from the prior cycle: ``_send_multicast``
    still passes ``skip_default_slots=True`` and
    ``selected_model_slots=[]`` (models NOT tied to actor slots/roles)."""

    def test_send_multicast_passes_skip_default_slots_true(self):
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        assert "skip_default_slots=True" in func, (
            "_send_multicast must still pass skip_default_slots=True — "
            "models are NOT tied to actor slots/roles (carried over from "
            "the prior cycle)."
        )

    def test_send_multicast_passes_empty_selected_model_slots(self):
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        assert "selected_model_slots=[]" in func, (
            "_send_multicast must still pass selected_model_slots=[] — "
            "models are NOT tied to actor slots/roles (carried over from "
            "the prior cycle)."
        )

    def test_send_multicast_passes_selected_model_ids(self):
        source = _read_ask_source()
        func = _extract_func(source, "_send_multicast")
        assert func is not None
        assert "selected_model_ids=selected_model_ids" in func, (
            "_send_multicast must pass selected_model_ids=selected_model_ids "
            "— the user's multi-select dropdown choices."
        )


# ── 5. End-to-end contract: payload matches ModelCouncilRequest fields ──


class TestEndToEndPayloadContract:
    """The GUI's POST payload keys match the backend's
    ``ModelCouncilRequest`` field names exactly (contract check —
    the bug is always in the gap)."""

    def test_all_payload_keys_match_backend_fields(self):
        """Every key in the GUI's run_model_council payload dict has
        a matching field on ModelCouncilRequest."""
        from aip.adapter.api.routes.model_council import ModelCouncilRequest

        source = _read_api_client_source()

        # First extract the run_model_council method body
        method_match = re.search(
            r"async\s+def\s+run_model_council.*?(?=\n    async\s+def\s+\w+|\n    def\s+\w+|\Z)",
            source,
            re.DOTALL,
        )
        assert method_match is not None, "run_model_council not found in api_client.py"
        method_body = method_match.group()

        # Then extract ONLY the payload dict within the method
        payload_match = re.search(
            r'payload:\s*dict\[str,\s*Any\]\s*=\s*\{([^}]+)\}',
            method_body,
            re.DOTALL,
        )
        assert payload_match is not None, (
            "Could not find the `payload: dict[str, Any] = {...}` block in "
            "run_model_council. The GUI must build a payload dict for the POST."
        )
        payload_body = payload_match.group(1)

        # Extract payload keys from the dict literal
        payload_keys = set(re.findall(r'"(\w+)":\s', payload_body))

        # Every payload key must be a field on ModelCouncilRequest
        backend_fields = set(ModelCouncilRequest.model_fields.keys())
        missing = payload_keys - backend_fields
        assert not missing, (
            f"GUI payload keys {missing} are NOT fields on ModelCouncilRequest. "
            f"Backend fields: {backend_fields}. The bug is always in the gap "
            f"between producer (GUI payload) and consumer (Pydantic model)."
        )
