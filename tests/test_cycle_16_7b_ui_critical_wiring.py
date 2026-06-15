"""Cycle 16.7B — UI Critical Wiring regression tests.

Findings covered:
  #1 — Backend must not mount frozen legacy gui/shell.py
  #2 — websockets must be declared as an explicit project dependency
  #3 — gui/api_client.py must not call nonexistent httpx.AsyncClient.websocket_connect
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Finding #1 — Legacy shell mount removed
# ---------------------------------------------------------------------------


class TestFinding1LegacyShellMount:
    """Backend must not import or mount gui.shell at /."""

    def test_backend_does_not_import_gui_shell(self) -> None:
        """Creating the FastAPI app must not cause gui.shell to be imported."""
        # Snapshot modules before app creation
        before = set(sys.modules.keys())
        from aip.adapter.api.app import create_app

        create_app()
        after = set(sys.modules.keys())
        new_modules = after - before
        assert "shell" not in new_modules, (
            f"Importing create_app() pulled in 'shell' module. New modules: {sorted(new_modules)[:20]}"
        )

    def test_backend_no_mount_for_gui_shell(self) -> None:
        """The app must not have a Mount at '/' pointing to gui.shell."""
        from starlette.routing import Mount

        from aip.adapter.api.app import create_app

        app = create_app()
        mounts_at_root = [r for r in app.routes if isinstance(r, Mount) and r.path == "/"]
        assert len(mounts_at_root) == 0, (
            f"Found mount(s) at '/': {[type(r.app).__name__ for r in mounts_at_root]}. "
            "Backend must not mount gui.shell at root."
        )

    def test_backend_root_returns_json(self) -> None:
        """GET / on the backend must return a JSON info dict, not GUI content."""
        from fastapi.testclient import TestClient

        from aip.adapter.api.app import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data, f"Root response missing 'status' key: {data}"
        assert data["status"] == "ok"

    def test_health_still_works(self) -> None:
        """GET /api/v1/health must still work after removing shell mount."""
        from fastapi.testclient import TestClient

        from aip.adapter.api.app import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        # Health may report 'unhealthy' if DB files are absent (test
        # environment), but the endpoint itself must respond with JSON.
        assert "status" in data, f"Health response missing 'status' key: {data}"

    def test_app_py_source_no_shell_import(self) -> None:
        """The source file must not contain 'import shell' or 'from gui import shell'."""
        app_path = Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "app.py"
        source = app_path.read_text()
        assert "import shell" not in source, "app.py still contains 'import shell' — legacy mount not fully removed"
        assert "from gui import shell" not in source, (
            "app.py still contains 'from gui import shell' — legacy mount not fully removed"
        )


# ---------------------------------------------------------------------------
# Finding #2 — websockets declared explicitly
# ---------------------------------------------------------------------------


class TestFinding2WebsocketsDependency:
    """websockets must be an explicit project dependency."""

    def test_websockets_importable(self) -> None:
        """websockets package must be importable in the project environment."""
        import websockets

        assert websockets is not None

    def test_websockets_in_pyproject_dependencies(self) -> None:
        """pyproject.toml must list websockets in [project.dependencies]."""
        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        content = pyproject_path.read_text()
        # Find the dependencies section and check for websockets
        in_deps = False
        found = False
        for line in content.splitlines():
            if line.strip() == "dependencies = [":
                in_deps = True
                continue
            if in_deps and line.strip() == "]":
                in_deps = False
                break
            if in_deps and "websockets" in line:
                found = True
                break
        assert found, "websockets not found in [project.dependencies] in pyproject.toml"

    def test_websockets_declared_with_version(self) -> None:
        """The websockets dependency must have a version specifier."""
        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        content = pyproject_path.read_text()
        in_deps = False
        spec = ""
        for line in content.splitlines():
            if line.strip() == "dependencies = [":
                in_deps = True
                continue
            if in_deps and line.strip() == "]":
                break
            if in_deps and "websockets" in line:
                spec = line.strip()
                break
        assert ">=" in spec or ">=" in spec or "~=" in spec or "==" in spec, (
            f"websockets dependency lacks a version specifier: {spec}"
        )


# ---------------------------------------------------------------------------
# Finding #3 — Broken httpx WebSocket fallback removed
# ---------------------------------------------------------------------------


class TestFinding3BrokenHttpxFallback:
    """gui/api_client.py must not call httpx.AsyncClient.websocket_connect."""

    def test_no_websocket_connect_on_httpx_client(self) -> None:
        """api_client.py source must not contain .websocket_connect( on httpx.AsyncClient."""
        api_client_path = Path(__file__).resolve().parent.parent / "gui" / "api_client.py"
        source = api_client_path.read_text()
        assert ".websocket_connect(" not in source, (
            "gui/api_client.py still contains .websocket_connect( — broken httpx fallback not removed"
        )

    def test_no_chat_via_httpx_ws_method(self) -> None:
        """The _chat_via_httpx_ws method must not exist in AipApiClient."""
        from gui.api_client import AipApiClient

        assert not hasattr(AipApiClient, "_chat_via_httpx_ws"), (
            "AipApiClient still has _chat_via_httpx_ws method — dead code not removed"
        )

    def test_chat_ws_import_error_is_honest(self) -> None:
        """When websockets import fails, chat_ws must report an honest error."""
        from gui.api_client import AipApiClient

        client = AipApiClient(base_url="http://localhost:8000")
        errors: list[dict] = []

        # Patch websockets to raise ImportError
        with patch.dict("sys.modules", {"websockets": None}):
            import asyncio

            async def _run() -> None:
                await client.chat_via_websocket(
                    session_id="test-session",
                    message="hello",
                    on_response=lambda r: None,
                    on_error=lambda e: errors.append(e),
                    on_gate=lambda g: None,
                    model_slot="synthesis",
                )

            asyncio.run(_run())

        assert len(errors) > 0, "chat_ws did not call on_error when websockets is missing"
        error_content = errors[0].get("content", "")
        assert "websockets" in error_content.lower(), f"Error message does not mention websockets: {error_content}"
        # Must NOT be an AttributeError from httpx
        assert "AttributeError" not in error_content, (
            f"Error contains AttributeError (broken httpx path): {error_content}"
        )

    def test_send_gate_response_import_error_is_honest(self) -> None:
        """When websockets import fails, send_gate_response must report an honest error."""
        from gui.api_client import AipApiClient

        client = AipApiClient(base_url="http://localhost:8000")

        with patch.dict("sys.modules", {"websockets": None}):
            import asyncio

            result = asyncio.run(client.send_gate_response(session_id="test-session", approved=True))

        assert result.get("type") == "error", f"Expected error type, got: {result}"
        content = result.get("content", "")
        assert "websockets" in content.lower(), f"Error message does not mention websockets: {content}"
        assert "AttributeError" not in content, f"Error contains AttributeError (broken httpx path): {content}"
