"""AIP Operator Console Layout — top bar, left nav, right rail.

Provides the three-region layout shell that every page renders inside.
All styling uses tokens from gui.theme.
"""

from __future__ import annotations

import logging

from nicegui import ui

from gui.state import GuiState
from gui.theme import (
    _AIP_MARK,
    C_AMBER,
    C_CREAM,
    C_DOGFOOD_BARE,
    C_DOGFOOD_DEGRADED,
    C_DOGFOOD_FULL,
    C_ERR_FG,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_SURFACE,
    F_MONO,
    F_SANS,
    R_SM,
    SP_MD,
    SP_SM,
)

log = logging.getLogger("gui.components.layout")

# Navigation items: (label, route, icon)
_NAV_ITEMS = [
    ("Dashboard", "/", "dashboard"),
    ("Ask", "/ask", "chat"),
    ("Models", "/models", "model_training"),
    ("Corpus", "/corpus", "storage"),
    ("Graph", "/graph", "hub"),
    ("Retrieval Lab", "/retrieval", "science"),
    ("Wiki", "/wiki", "menu_book"),
    ("Artifacts", "/artifacts", "folder"),
    ("Maintenance", "/maintenance", "build"),
    ("Settings", "/settings", "settings"),
]


# Layout CSS injected once per page via build_top_bar().
#
# ROOT CAUSE: NiceGUI's ``ui.column()`` defaults to ``display: flex;
# flex-direction: column`` but has NO default ``width: 100%``. Quasar's
# ``.q-page`` (the parent of every page's main content column) is
# ``display: block`` by default, so the column's ``flex: 1`` class only
# stretches it along the MAIN axis (height) — the cross-axis (width)
# collapses to the column's content width. The result: main content
# renders ~500px wide with the browser's white body background showing
# through on the right.
#
# FIX: make ``.q-page`` itself a flex column. Then ``align-items:
# stretch`` (the default) stretches every direct child horizontally to
# fill the q-page's width, and ``flex: 1`` on the main column grows it
# vertically to fill the height. This fixes ALL 11 pages with one rule.
_LAYOUT_CSS = """
<style>
.q-page { display: flex !important; flex-direction: column !important; }
</style>
"""


def build_top_bar(state: GuiState) -> None:
    """Build the top bar: AIP_Brain title, dogfood badge, backend status, DEFINER label.

    Also injects the global layout CSS that makes ``.q-page`` a flex
    column so the main content column's ``flex-1`` class stretches both
    height and width (see ``_LAYOUT_CSS`` comment for the root cause).
    Called by every page, so the CSS lands on every route.
    """
    ui.add_head_html(_LAYOUT_CSS)
    with (
        ui.header()
        .classes("w-full items-center")
        .style(
            f"background:{C_SURFACE}; border-bottom:0.5px solid {C_INK40}; "
            f"padding:{SP_SM} {SP_MD}; min-height:44px; z-index:100;"
        )
    ):
        # AIP Mark + title
        ui.html(_AIP_MARK).style("margin-right:8px;")
        ui.label("AIP_Brain").style(
            f"font-family:{F_SANS}; font-size:16px; font-weight:700; color:{C_CREAM}; letter-spacing:0.5px;"
        )

        # Dogfood mode badge
        _dogfood_badge(state.dogfood_mode)

        ui.space()

        # Backend status indicator
        status_color = C_OK_FG if state.backend_reachable else C_ERR_FG
        status_text = "BACKEND OK" if state.backend_reachable else "BACKEND DOWN"
        ui.label(status_text).style(
            f"font-size:10px; font-family:{F_MONO}; color:{status_color}; "
            f"border:0.5px solid {status_color}; border-radius:{R_SM}; "
            f"padding:2px 8px; letter-spacing:0.5px;"
        )

        # DEFINER identity label
        ui.label("DEFINER").style(
            f"font-size:10px; font-family:{F_MONO}; color:{C_AMBER}; "
            f"border:0.5px solid {C_AMBER}; border-radius:{R_SM}; "
            f"padding:2px 8px; letter-spacing:1px; margin-left:8px;"
        )


def _dogfood_badge(mode: str) -> None:
    """Render dogfood mode badge in the top bar."""
    mode_colors = {
        "FULL": (C_DOGFOOD_FULL, "#0E1F17"),
        "DEGRADED": (C_DOGFOOD_DEGRADED, "#1A1A0E"),
        "BARE": (C_DOGFOOD_BARE, "#1A0E0E"),
        "DIRECT MODEL ONLY": (C_DOGFOOD_BARE, "#1A0E0E"),
    }
    fg, bg = mode_colors.get(mode, (C_MUTED, "transparent"))
    label_text = mode if mode != "DIRECT MODEL ONLY" else "DIRECT MODEL ONLY"
    ui.label(label_text).style(
        f"font-size:9px; font-family:{F_MONO}; color:{fg}; background:{bg}; "
        f"border:0.5px solid {fg}; border-radius:{R_SM}; "
        f"padding:2px 8px; letter-spacing:0.5px; margin-left:12px;"
    )


def build_left_nav(state: GuiState, active_page: str = "") -> None:
    """Build the left navigation drawer.

    Width is set via Quasar's ``width`` prop (not CSS) because Quasar's
    q-drawer re-applies its own inline pixel width on render and would
    override any CSS width we set via ``.style()``. Using ``props("width=100")``
    tells Quasar at the component level so the drawer actually shrinks.
    """
    with (
        ui.left_drawer()
        .props("width=100; mini=false; bordered=false")
        .style(
            f"background:{C_SURFACE}; border-right:0.5px solid {C_INK40}; padding:0;"
        )
    ):
        for label, route, icon in _NAV_ITEMS:
            is_active = active_page == route or (active_page == "" and route == "/")
            bg = C_RAISED if is_active else "transparent"
            border_left = f"2px solid {C_AMBER}" if is_active else "2px solid transparent"
            fg = C_AMBER if is_active else C_CREAM

            with (
                ui.column()
                .classes("w-full items-center cursor-pointer")
                .style(f"padding:8px 4px; background:{bg}; border-left:{border_left}; transition:background 0.15s;")
                .on("click", lambda r=route: ui.navigate.to(r))
            ):
                ui.icon(icon, size="20px").style(f"color:{fg};")
                ui.label(label).style(
                    f"font-size:9px; font-family:{F_SANS}; color:{fg}; font-weight:{'600' if is_active else '400'}; "
                    f"text-align:center; margin-top:2px; line-height:1.1; "
                    f"overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:88px;"
                )


def build_right_rail(state: GuiState) -> None:
    """No-op — right rail has been removed.

    The status info formerly shown in the right rail (dogfood mode,
    actor status, retrieval health, pending gates, warnings) is now
    available in Settings and Maintenance pages where it belongs.

    This function is kept as a stub so that existing page imports
    don't break; it can be removed in a later cleanup pass.
    """
    pass


def _section_label(text: str) -> None:
    """Render a section label in the right rail."""
    ui.label(text).style(
        f"font-size:9px; font-weight:600; letter-spacing:1.5px; "
        f"color:{C_MUTED}; text-transform:uppercase; margin-bottom:4px;"
    )
