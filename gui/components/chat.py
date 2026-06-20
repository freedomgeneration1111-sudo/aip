"""AIP Chat Components — message bubbles and input field.

Provides chat UI primitives used by the Ask page.
"""

from __future__ import annotations

import asyncio
import logging

from nicegui import ui

from gui.state import GuiState
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_INK40,
    C_MUTED,
    C_RAISED,
    C_SURFACE,
    F_MONO,
    R_MD,
    R_SM,
    btn_primary,
)

log = logging.getLogger("gui.components.chat")


def add_message(container, role: str, text: str, model: str | None = None, latency_ms: int | None = None) -> None:
    """Add a chat message bubble to the chat container.

    Uses the AIP dark theme design tokens.
    """
    with container:
        with ui.row().classes("w-full items-start").style("margin-bottom:8px;"):
            # Role label
            if role == "user":
                display = "You"
                role_color = C_CREAM
                bubble_bg = C_RAISED
            else:
                display = model or "Assistant"
                role_color = C_AMBER
                bubble_bg = C_SURFACE

            label_text = f"**{display}**"
            if latency_ms is not None:
                label_text += f"  ({latency_ms}ms)"
            ui.markdown(label_text).style(f"font-size:11px; color:{role_color}; font-family:{F_MONO};")

        with ui.row().classes("w-full"):
            ui.markdown(text).style(
                f"font-size:13px; color:{C_CREAM}; background:{bubble_bg}; "
                f"border:0.5px solid {C_INK40}; border-radius:{R_MD}; "
                f"padding:8px 12px; max-width:85%; line-height:1.5;"
            )


def add_system_message(container, text: str) -> None:
    """Add a system/info message to the chat container."""
    with container:
        with ui.row().classes("w-full justify-center"):
            ui.label(text).style(f"font-size:10px; font-family:{F_MONO}; color:{C_MUTED}; padding:2px 8px;")


def build_chat_input(state: GuiState, chat_container, send_fn) -> ui.input:
    """Build the chat input field + send button. Returns the input element.

    Args:
        state: Per-session GuiState
        chat_container: The chat column element
        send_fn: Async callable to invoke on send

    Returns:
        The ui.input element (for focus control etc.)
    """
    with (
        ui.row()
        .classes("w-full items-center")
        .style(
            f"padding:{R_SM}; background:{C_SURFACE}; border-top:0.5px solid {C_INK40}; "
            f"position:sticky; bottom:0; z-index:10;"
        )
    ):
        input_field = (
            ui.input(placeholder="Ask anything...")
            .props("outlined dense dark")
            .classes("flex-grow")
            .style(
                f"font-size:13px; color:{C_CREAM}; background:{C_RAISED}; "
                f"border:0.5px solid {C_INK40}; border-radius:{R_SM};"
            )
        )
        input_field.on("keydown.enter", lambda: asyncio.create_task(send_fn()))

        # + menu (UI_CONVENTIONS.md §4 — Brain core feature)
        plus_btn = ui.button("+").props("flat dense").style(
            f"color:{C_MUTED}; font-size:16px; font-weight:300; "
            f"padding:0 6px; min-width:28px;"
        )
        with ui.menu().props("auto-close") as plus_menu:
            ui.menu_item(
                "Upload PDF",
                on_click=lambda: ui.notify("Upload PDF — coming soon", color="info"),
            )
            ui.menu_item(
                "Upload Image",
                on_click=lambda: ui.notify("Upload Image — coming soon", color="info"),
            )
            ui.menu_item(
                "Voice mode",
                on_click=lambda: ui.notify("Voice mode — coming soon", color="info"),
            )
            ui.menu_item(
                "Chat settings",
                on_click=lambda: ui.navigate.to("/settings"),
            )
        plus_btn.on("click", plus_menu.open)

        ui.button("Send", on_click=lambda: asyncio.create_task(send_fn())).style(btn_primary()).props("dense")

    return input_field
