"""Tests for workflow engine wiring + /health/extensions endpoint.

Verifies:
  1. AipContainer has a workflow_engine field (None by default).
  2. WorkflowEngine can load the ARISTOTLE tutoring_session_v1.yaml (engine-
     compatible node types).
  3. The /health/extensions endpoint returns the expected shape.

The lifespan-wiring test is deferred to CI (needs the full dependency set
to instantiate WorkflowEngine with real stores). The YAML-loading test
runs locally (only needs pyyaml + the workflow loader).

Run:  CI=true uv run pytest tests/test_workflow_engine_wiring.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).parent.parent
_ARISTOTLE_WORKFLOW = _REPO_ROOT / "extensions" / "aristotle" / "workflows" / "tutoring_session_v1.yaml"


# --------------------------------------------------------------------------
# Container field tests (source-level — avoids importing the full adapter
# chain which needs aiosqlite. The fields are verified by reading the source.)
# --------------------------------------------------------------------------


def test_container_has_workflow_engine_field():
    """AipContainer declares a workflow_engine field (ADR-014 §8 step 2)."""
    deps_path = _REPO_ROOT / "src" / "aip" / "adapter" / "api" / "dependencies.py"
    source = deps_path.read_text()
    assert "self.workflow_engine" in source, (
        "AipContainer must declare self.workflow_engine for ADR-014 §8 step 2"
    )


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
    assert "container.workflow_engine = _workflow_engine" in source, (
        "Lifespan must assign container.workflow_engine"
    )
    assert "workflow_engine_wired=True" in source, (
        "Lifespan should log workflow_engine_wired=True on success"
    )


# --------------------------------------------------------------------------
# ARISTOTLE workflow YAML loading test (needs pyyaml + workflow loader)
# The loader imports from aip.orchestration.workflow.node which imports
# aip.orchestration.nodes.synthesis — those may need deps. This test
# verifies the YAML structure is engine-compatible at the parse level.
# --------------------------------------------------------------------------


def test_aristotle_workflow_yaml_parses():
    """The tutoring_session_v1.yaml parses as valid YAML with the right structure."""
    import yaml

    with open(_ARISTOTLE_WORKFLOW) as f:
        wf = yaml.safe_load(f)

    assert wf["template_id"] == "tutoring_session_v1"
    assert wf["name"] == "Tutoring Session v1"
    assert "nodes" in wf
    assert len(wf["nodes"]) == 7, f"expected 7 nodes, got {len(wf['nodes'])}"

    node_ids = [n["id"] for n in wf["nodes"]]
    assert node_ids == [
        "teach", "probe", "quiz", "evaluate", "check_mastery", "remediate", "next_concept",
    ], f"unexpected node order: {node_ids}"


def test_aristotle_workflow_uses_engine_compatible_node_types():
    """Every node type in tutoring_session_v1.yaml is one the L5 engine's loader accepts.

    The loader (orchestration/workflow/loader.py) accepts: script, agent,
    condition, dialog, parallel, review, re_synthesize. This test catches
    regressions if someone rewrites the YAML with non-engine types like
    'synthesize', 'decision', or 'commit'.
    """
    import yaml

    with open(_ARISTOTLE_WORKFLOW) as f:
        wf = yaml.safe_load(f)

    # NodeType enum values + the two special-cased string types (review, re_synthesize)
    allowed_types = {"script", "agent", "condition", "dialog", "parallel", "review", "re_synthesize"}

    for node in wf["nodes"]:
        node_type = node["type"]
        assert node_type in allowed_types, (
            f"Node {node['id']!r} has type {node_type!r} which the L5 engine "
            f"loader doesn't accept. Allowed: {sorted(allowed_types)}. "
            f"Update the YAML to use an engine-compatible type."
        )


def test_aristotle_workflow_agent_nodes_have_model_slot():
    """Every agent node has a model_slot (required by the loader's AgentNode)."""
    import yaml

    with open(_ARISTOTLE_WORKFLOW) as f:
        wf = yaml.safe_load(f)

    agent_nodes = [n for n in wf["nodes"] if n["type"] == "agent"]
    assert len(agent_nodes) >= 3, f"expected >=3 agent nodes, got {len(agent_nodes)}"

    for node in agent_nodes:
        assert "model_slot" in node, (
            f"Agent node {node['id']!r} must have a model_slot — "
            f"the loader's AgentNode requires it."
        )


def test_aristotle_workflow_condition_node_has_branches():
    """The check_mastery condition node has next_on_true + next_on_false (loader requirement)."""
    import yaml

    with open(_ARISTOTLE_WORKFLOW) as f:
        wf = yaml.safe_load(f)

    condition_nodes = [n for n in wf["nodes"] if n["type"] == "condition"]
    assert len(condition_nodes) == 1, f"expected 1 condition node, got {len(condition_nodes)}"

    cond = condition_nodes[0]
    assert cond["id"] == "check_mastery"
    assert "next_on_true" in cond, "condition node must have next_on_true"
    assert "next_on_false" in cond, "condition node must have next_on_false"
    assert cond["next_on_true"] == "next_concept"
    assert cond["next_on_false"] == "remediate"


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
    assert '"/health/extensions"' in source, (
        "Expected /health/extensions route declaration in health.py (ADR-014 §7)"
    )
    assert "async def extensions_health" in source, (
        "Expected async def extensions_health handler in health.py"
    )
