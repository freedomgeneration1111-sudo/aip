"""AIP_Brain Operator Console — Full Dogfood Mode.

Entry point for the NiceGUI-based operator console shell.
Communicates exclusively through the AIP FastAPI backend's REST and WebSocket endpoints.

Start: python -m gui.app
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from nicegui import ui

log = logging.getLogger("gui.app")

# Load .env before any env var reads
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# Import page modules to register their @ui.page routes
import gui.pages.artifacts  # noqa: F401, E402 — registers "/artifacts"
import gui.pages.ask  # noqa: F401, E402 — registers "/ask"
import gui.pages.corpus  # noqa: F401, E402 — registers "/corpus"
import gui.pages.dashboard  # noqa: F401, E402 — registers "/"
import gui.pages.maintenance  # noqa: F401, E402 — registers "/maintenance"
import gui.pages.models  # noqa: F401, E402 — registers "/models"
import gui.pages.retrieval_lab  # noqa: F401, E402 — registers "/retrieval"
import gui.pages.settings  # noqa: F401, E402 — registers "/settings"
import gui.pages.wiki  # noqa: F401, E402 — registers "/wiki"

GUI_PORT = int(os.getenv("AIP_GUI_PORT", "8080"))
GUI_RELOAD = os.getenv("AIP_GUI_RELOAD", "false").lower() in ("true", "1", "yes")

if __name__ == "__main__":
    ui.run(
        title="AIP_Brain Operator Console",
        port=GUI_PORT,
        reload=GUI_RELOAD,
    )
