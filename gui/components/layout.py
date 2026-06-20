"""AIP Operator Console Layout — top bar, left nav, right rail.

Provides the three-region layout shell that every page renders inside.
All styling uses tokens from gui.theme.

ADR-014 Amendment A1: Extension UI sidebar visibility via known-list
health polling. A ui.timer (5s interval) polls each known extension's
health endpoint. ui.refreshable re-renders the extension nav section
when status changes. KNOWN_EXTENSIONS is defined in config/aip.config.toml
under [extensions.known].
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx

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
# ROOT CAUSE (width collapse): NiceGUI's ``ui.column()`` defaults to
# ``display: flex; flex-direction: column`` but has NO default
# ``width: 100%``. Quasar's ``.q-page`` (the parent of every page's main
# content column) is ``display: block`` by default, so the column's
# ``flex: 1`` class only stretches it along the MAIN axis (height) — the
# cross-axis (width) collapses to the column's content width. The result:
# main content renders ~500px wide with the browser's white body
# background showing through on the right.
#
# ROOT CAUSE (drawer overlay): NiceGUI's ``ui.left_drawer()`` defaults to
# ``value=None`` → Quasar's ``show-if-above=True`` + ``model-value=None``.
# The drawer's visibility is resolved by JavaScript AFTER the WebSocket
# connects; until then Quasar renders it as an OVERLAY (floats on top of
# content, clipping the left edge). Passing ``value=True`` fixes this.
#
# FIX: Three CSS rules that work together:
#   1. ``.q-page`` → flex column so ``flex-1`` children stretch both axes
#   2. ``.q-drawer`` → force 100px width (belt-and-suspenders; the Quasar
#      ``width`` prop should do this but the semicolon-separated prop
#      string can leave a trailing ';' in the value, e.g. ``width='100;'``
#      which Quasar may reject)
#   3. ``.q-page-container`` → margin-left:100px so even if the drawer
#      ends up in overlay mode for any reason, the page content is still
#      offset to the right of the 100px sidebar
_LEFT_NAV_WIDTH_PX = 100
_LAYOUT_CSS = f"""
<style>
.q-page {{ display: flex !important; flex-direction: column !important; }}
.q-drawer.left {{ width: {_LEFT_NAV_WIDTH_PX}px !important; min-width: {_LEFT_NAV_WIDTH_PX}px !important; }}
.q-page-container {{ padding-left: {_LEFT_NAV_WIDTH_PX}px !important; }}
</style>
"""


# ---------------------------------------------------------------------------
# ADR-014 Amendment A1: Extension UI sidebar visibility
# ---------------------------------------------------------------------------

# Module-level extension status store. Keyed by extension name → bool (alive).
_extension_status: dict[str, bool] = {}


def _load_known_extensions() -> list:
    """Load KNOWN_EXTENSIONS from config/aip.config.toml.

    Returns the list of known extension dicts (name, health_url, nav).
    Falls back to an empty list if the config file or section is missing.
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return []

    candidates = [
        Path.cwd() / "config" / "aip.config.toml",
        Path(__file__).resolve().parent.parent.parent / "config" / "aip.config.toml",
    ]
    for path in candidates:
        if path.is_file():
            try:
                with open(path, "rb") as f:
                    cfg = tomllib.load(f)
                return cfg.get("extensions", {}).get("known", [])
            except Exception:
                pass
    return []


async def _poll_extension_health(config: dict) -> None:
    """Best-effort health check for each known extension.

    Never raises — failures silently set status to False.
    Updates _extension_status and triggers sidebar refresh only
    when status actually changes (avoids unnecessary redraws).
    """
    known = config.get("extensions", {}).get("known", [])
    changed = False
    for ext in known:
        name = ext.get("name", "")
        url = ext.get("health_url", "")
        if not name or not url:
            continue
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=1.0)
                alive = r.status_code == 200
        except Exception:
            alive = False
        if _extension_status.get(name) != alive:
            _extension_status[name] = alive
            changed = True
    if changed:
        try:
            _render_extension_nav.refresh()
        except Exception:
            pass  # NiceGUI loop not available (e.g. in tests) — status still updated


@ui.refreshable
def _render_extension_nav(config: dict) -> None:
    """Renders extension nav links — only for live extensions.

    Called by build_left_nav after the core nav items. The ui.refreshable
    decorator allows _poll_extension_health to trigger a re-render when
    extension status changes, without rebuilding the entire sidebar.
    """
    known = config.get("extensions", {}).get("known", [])
    for ext in known:
        if _extension_status.get(ext.get("name", ""), False):
            with ui.column().classes("w-full items-center"):
                for item in ext.get("nav", []):
                    with (
                        ui.column()
                        .classes("w-full items-center cursor-pointer")
                        .style(
                            f"padding:8px 4px; background:transparent; "
                            f"border-left:2px solid transparent; transition:background 0.15s;"
                        )
                        .on("click", lambda p=item["path"]: ui.navigate.to(p))
                    ):
                        ui.icon(item.get("icon", "extension"), size="20px").style(
                            f"color:{C_CREAM};"
                        )
                        ui.label(item["label"]).style(
                            f"font-size:9px; font-family:{F_SANS}; color:{C_CREAM}; "
                            f"font-weight:400; text-align:center; margin-top:2px; "
                            f"line-height:1.1; overflow:hidden; text-overflow:ellipsis; "
                            f"white-space:nowrap; max-width:88px;"
                        )


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

    ADR-014 v1.1: merges built-in _NAV_ITEMS with extension-contributed
    nav items from container.extensions.nav_items(). Extension pages appear
    after built-in pages, sorted by their `order` field.

    ADR-014 Amendment A1: extension nav items are rendered via
    _render_extension_nav (ui.refreshable), which only shows items for
    extensions whose health endpoint is alive (polled every 5s by
    _poll_extension_health via ui.timer).

    Three Quasar gotchas addressed here:

    1. **Width via Quasar prop, not CSS**: ``q-drawer`` re-applies its own
       inline pixel width on render, overriding any CSS width set via
       ``.style()``. Using ``.props("width=100")`` tells Quasar at the
       component level so the drawer actually shrinks.

    2. **``value=True`` to force push-mode (not overlay)**.

    3. **Belt-and-suspenders CSS** in ``_LAYOUT_CSS``.
    """
    # Load KNOWN_EXTENSIONS from config for the health-polling sidebar.
    _known = _load_known_extensions()
    _config = {"extensions": {"known": _known}} if _known else {}

    # ADR-014 v1.1: collect nav items from built-in + extensions
    nav_items = list(_NAV_ITEMS)  # built-in: (label, route, icon) tuples

    # Dynamically fetch extension nav items from /health/extensions.
    # The response includes nav_items per MOUNTED extension — no hardcoded
    # extension names. Any future extension declares its own nav entry in
    # hooks.py via host.register_page() and appears here automatically.
    try:
        _base_url = os.getenv("AIP_BACKEND_URL", "http://127.0.0.1:8000")
        try:
            _resp = httpx.get(f"{_base_url}/health/extensions", timeout=2.0)
            if _resp.status_code == 200:
                _data = _resp.json()
                for _ext in _data.get("extensions", []):
                    if _ext.get("state") == "MOUNTED":
                        for _nav in _ext.get("nav_items", []):
                            nav_items.append((
                                _nav["label"],
                                _nav["route"],
                                _nav["icon"],
                            ))
        except Exception:
            pass  # Server not reachable — extensions don't show in nav
    except Exception:
        pass  # httpx not available — built-in nav only

    with (
        ui.left_drawer(value=True)
        .props("width=100 mini=false bordered=false")
        .style(
            f"background:{C_SURFACE}; border-right:0.5px solid {C_INK40}; padding:0;"
        )
    ):
        for label, route, icon in nav_items:
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

        # ADR-014 Amendment A1: render extension nav items via ui.refreshable.
        # Only shows items for extensions whose health endpoint is alive.
        if _config:
            _render_extension_nav(_config)

            # Fire one poll immediately on page load (don't wait 5s for first render).
            ui.timer(
                0.1,
                lambda: asyncio.create_task(_poll_extension_health(_config)),
                once=True,
            )

            # Poll every 5 seconds. ui.timer creates a task on each tick.
            ui.timer(
                5.0,
                lambda: asyncio.create_task(_poll_extension_health(_config)),
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
