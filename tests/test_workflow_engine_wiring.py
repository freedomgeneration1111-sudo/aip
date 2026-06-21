"""Tests for workflow engine wiring + /health/extensions endpoint.

Verifies the platform-side wiring (container fields, lifespan construction,
route declaration). ARISTOTLE-specific workflow tests live in the
AIP_Aristotle repo (they test ARISTOTLE's workflow, not the platform).

Run:  CI=true uv run pytest tests/test_workflow_engine_wiring.py -v
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent


# --------------------------------------------------------------------------
# Container field tests (source-level — avoids importing the full adapter
# chain which needs aiosqlite. The fields are verified by reading the source.)
# --------------------------------------------------------------------------


def test_container_has_workflow_engine_field():
    """AipContainer declares a workflow_engine field (ADR-014 §8 step 2)."""
    deps_path = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "dependencies.py"
    source = deps_path.read_text()
    assert "self.workflow_engine" in source, "AipContainer must declare self.workflow_engine for ADR-014 §8 step 2"


def test_container_has_workflow_registry_field():
    """AipContainer declares a workflow_registry field (ADR-014 §5.4)."""
    deps_path = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "dependencies.py"
    source = deps_path.read_text()
    assert "self.workflow_registry" in source


def test_container_has_extensions_field():
    """AipContainer declares an extensions field (ADR-014 §2)."""
    deps_path = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "dependencies.py"
    source = deps_path.read_text()
    assert "self.extensions" in source


def test_lifespan_wires_workflow_engine():
    """The lifespan constructs WorkflowEngine and stores it on the container (ADR-014 §8 step 2)."""
    app_path = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "app.py"
    source = app_path.read_text()
    assert "from aip.orchestration.workflow.engine import WorkflowEngine" in source, (
        "Lifespan must import WorkflowEngine"
    )
    assert "container.workflow_engine = _workflow_engine" in source, "Lifespan must assign container.workflow_engine"
    assert "workflow_engine_wired=True" in source, "Lifespan should log workflow_engine_wired=True on success"


# --------------------------------------------------------------------------
# /health/extensions endpoint shape test (needs FastAPI + the route module)
# Deferred to CI — the route module imports the full adapter chain.
# --------------------------------------------------------------------------


def test_health_extensions_route_exists():
    """The /health/extensions route is registered in the health router."""
    # Read the source to verify the route is declared (avoids importing the
    # full adapter chain which needs aiosqlite etc.).
    health_route_path = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "routes" / "health.py"
    source = health_route_path.read_text()
    assert '"/health/extensions"' in source, "Expected /health/extensions route declaration in health.py (ADR-014 §7)"
    assert "async def extensions_health" in source, "Expected async def extensions_health handler in health.py"
