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
    # Extension-to-MIME mapping for upload handler.
    _EXT_TYPES = {
        "pdf": "application/pdf",
        "txt": "text/plain", "md": "text/markdown",
        "csv": "text/csv", "html": "text/html",
        "htm": "text/html", "json": "application/json",
        "yaml": "application/yaml", "yml": "application/yaml",
        "docx": ("application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document"),
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
        "bmp": "image/bmp", "tiff": "image/tiff",
    }

    async def _handle_upload(e) -> None:
        """POST uploaded file to ARISTOTLE /upload, inject result into chat."""
        import httpx

        filename = getattr(e, "name", "file")
        content = e.content.read()

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        content_type = _EXT_TYPES.get(ext, "application/octet-stream")

        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "http://localhost:8001/aristotle/upload",
                    content=content,
                    headers={
                        "Content-Type": content_type,
                        "Content-Disposition": f'attachment; filename="{filename}"',
                    },
                    timeout=15.0,
                )
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            ui.notify(f"Upload failed: {exc}", color="negative")
            return

        extracted = data.get("extracted_text", "")
        char_count = data.get("char_count", len(extracted))
        source_type = data.get("source_type", "file")

        if not extracted.strip():
            ui.notify(f"No text extracted from {filename}", color="warning")
            return

        with chat_container:
            ui.label(
                f"{filename} - {char_count:,} chars extracted ({source_type})"
            ).style(
                f"font-size:11px; color:{C_AMBER}; "
                f"font-family:{F_MONO}; padding:4px 8px; "
                f"background:#1A1200; border-radius:3px; "
                f"margin:4px 0; align-self:flex-start;"
            )

        ui.notify(
            f"{filename} ready - {char_count:,} chars",
            color="positive", timeout=3000,
        )

    def _start_voice_recognition(inp: ui.input) -> None:
        """Start browser Web Speech API recognition.

        Chrome/Edge only — graceful fallback via alert() for unsupported browsers.
        Transcript is injected into the chat input element.
        """
        ui.run_javascript("""
            (function() {
                const SpeechRecognition =
                    window.SpeechRecognition ||
                    window.webkitSpeechRecognition;

                if (!SpeechRecognition) {
                    alert('Voice input not supported in this browser. Use Chrome or Edge.');
                    return;
                }

                const recognition = new SpeechRecognition();
                recognition.lang = 'en-US';
                recognition.continuous = false;
                recognition.interimResults = false;
                recognition.maxAlternatives = 1;

                recognition.onresult = function(event) {
                    const transcript = event.results[0][0].transcript;
                    const inputs = document.querySelectorAll(
                        '.q-field__native[type="text"], '
                        + '.q-field__native:not([type])'
                    );
                    for (let i = inputs.length - 1; i >= 0; i--) {
                        if (inputs[i].offsetParent !== null) {
                            inputs[i].value = transcript;
                            inputs[i].dispatchEvent(
                                new Event('input', {bubbles: true})
                            );
                            break;
                        }
                    }
                };

                recognition.onerror = function(event) {
                    console.warn('Speech error:', event.error);
                };

                recognition.start();
            })();
        """)

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

        # Hidden upload widgets (one per type)
        doc_upload = (
            ui.upload(on_upload=lambda e: asyncio.create_task(_handle_upload(e)),
                      auto_upload=True, max_file_size=10_000_000)
            .props("accept='.pdf,.txt,.md,.markdown,.csv,.html,.htm,"
                   ".yaml,.yml,.json,.docx'")
            .style("display:none;")
        )
        img_upload = (
            ui.upload(on_upload=lambda e: asyncio.create_task(_handle_upload(e)),
                      auto_upload=True, max_file_size=10_000_000)
            .props("accept='.jpg,.jpeg,.png,.webp,.bmp,.tiff'")
            .style("display:none;")
        )

        # + menu (UI_CONVENTIONS.md §4 — Brain core feature)
        plus_btn = ui.button("+").props("flat dense").style(
            f"color:{C_MUTED}; font-size:16px; font-weight:300; "
            f"padding:0 6px; min-width:28px;"
        )
        with ui.menu().props("auto-close") as plus_menu:
            ui.menu_item(
                "Upload Document",
                on_click=lambda: doc_upload.run_method("pickFiles"),
            )
            ui.menu_item(
                "Upload Image (OCR)",
                on_click=lambda: img_upload.run_method("pickFiles"),
            )
            ui.menu_item(
                "Voice input",
                on_click=lambda: _start_voice_recognition(input_field),
            )
            ui.menu_item(
                "Chat settings",
                on_click=lambda: ui.navigate.to("/settings"),
            )
        plus_btn.on("click", plus_menu.open)

        ui.button("Send", on_click=lambda: asyncio.create_task(send_fn())).style(btn_primary()).props("dense")

    return input_field
