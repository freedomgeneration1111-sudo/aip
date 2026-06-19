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
import gui.pages.graph  # noqa: F401, E402 — registers "/graph"
import gui.pages.maintenance  # noqa: F401, E402 — registers "/maintenance"
import gui.pages.models  # noqa: F401, E402 — registers "/models"
import gui.pages.retrieval_lab  # noqa: F401, E402 — registers "/retrieval"
import gui.pages.settings  # noqa: F401, E402 — registers "/settings"
import gui.pages.wiki  # noqa: F401, E402 — registers "/wiki"

# ADR-014 v1.1: dynamically discover extension GUI pages via entry points.
# Extensions that have a GUI declare an "aip.extension_gui" entry point in
# their pyproject.toml. We scan for it and load each module — the @ui.page
# decorator inside registers the route. No named imports — fully dynamic.
try:
    from importlib.metadata import entry_points as _entry_points

    try:
        _gui_eps = _entry_points(group="aip.extension_gui")
    except TypeError:
        _gui_eps = _entry_points().get("aip.extension_gui", [])

    for _ep in _gui_eps:
        try:
            _ep.load()  # imports the module, triggering @ui.page registration
            log.info("extension GUI mounted: %s", _ep.name)
        except Exception as _exc:
            log.warning("extension GUI %s failed to mount: %s", _ep.name, _exc)
except Exception as _exc:
    log.debug("extension GUI discovery skipped: %s", _exc)

GUI_PORT = int(os.getenv("AIP_GUI_PORT", "8080"))
GUI_RELOAD = os.getenv("AIP_GUI_RELOAD", "false").lower() in ("true", "1", "yes")

if __name__ == "__main__":
    ui.run(
        title="AIP_Brain Operator Console",
        port=GUI_PORT,
        reload=GUI_RELOAD,
    )
