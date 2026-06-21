"""Tests for the three-panel shell — right extension panel + mode shift.

Tests the extension session state store (set_active_extension /
clear_active_extension) and the refreshable panels.

Run: pytest tests/test_three_panel_shell.py -v
"""

from __future__ import annotations

import warnings

# Suppress NiceGUI refreshable warnings in test context
warnings.filterwarnings("ignore", message="coroutine.*was never awaited")


# ---------------------------------------------------------------------------
# Test 1: set_active_extension updates state
# ---------------------------------------------------------------------------


def test_set_active_extension_updates_state():
    """Call set_active_extension('aristotle', 'Tutoring').
    Assert _active_extension == {'name': 'aristotle', 'mode': 'Tutoring'}.
    """
    from gui.components.layout import _active_extension, set_active_extension

    _active_extension.clear()
    set_active_extension("aristotle", "Tutoring")
    assert _active_extension == {"name": "aristotle", "mode": "Tutoring"}


# ---------------------------------------------------------------------------
# Test 2: clear_active_extension empties state
# ---------------------------------------------------------------------------


def test_clear_active_extension_empties_state():
    """Set active extension, then clear.
    Assert _active_extension == {}.
    """
    from gui.components.layout import _active_extension, clear_active_extension, set_active_extension

    _active_extension.clear()
    set_active_extension("aristotle", "Tutoring")
    assert _active_extension != {}
    clear_active_extension()
    assert _active_extension == {}


# ---------------------------------------------------------------------------
# Test 3: set/clear are exported and callable
# ---------------------------------------------------------------------------


def test_set_active_extension_exported():
    """from gui.components.layout import set_active_extension, clear_active_extension.
    Assert both are callable.
    """
    from gui.components.layout import clear_active_extension, set_active_extension

    assert callable(set_active_extension)
    assert callable(clear_active_extension)
