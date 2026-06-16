"""Regression tests for wiki artifact_type contract (Bug: sexton_wiki vs beast_wiki).

The bug: Sexton wrote wiki artifacts with artifact_type="sexton_wiki" but consumers
(wiki_channel.py, chat.py) expected artifact_type="beast_wiki". The /wiki/articles
API route also didn't match sexton:wiki:* ID patterns.

These tests verify:
1. Sexton._write_wiki_artifact writes artifact_type="beast_wiki"
2. Sexton._wiki_needs_generation reads artifact_type="beast_wiki"
3. Wiki channel reads artifact_type="beast_wiki"
4. Chat route reads artifact_type="beast_wiki"
5. /wiki/articles API route matches sexton:wiki:* IDs
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test 1: Sexton writes beast_wiki, not sexton_wiki
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sexton_writes_beast_wiki_artifact_type():
    """Sexton._write_wiki_artifact must write artifact_type='beast_wiki'."""
    from aip.orchestration.actors.sexton import Sexton

    # Create mock stores
    mock_artifacts = AsyncMock()
    mock_ecs = AsyncMock()
    mock_events = AsyncMock()

    sexton = Sexton(
        artifact_store=mock_artifacts,
        ecs_store=mock_ecs,
        event_store=mock_events,
    )

    # Mock domain entry
    domain_entry = MagicMock()
    domain_entry.domain_id = "aip_brain"

    # Call _write_wiki_artifact
    result = await sexton._write_wiki_artifact(
        domain_id="aip_brain",
        domain_entry=domain_entry,
        wiki_content="## Overview\nTest wiki content for the aip_brain domain.",
        domain_data={
            "total_turns": 10,
            "avg_importance": 0.8,
            "top_tags": ["test"],
            "sample_turns": [],
            "max_tagging_version": 1,
        },
        cycle_num=12345,
    )

    # Verify write was called
    assert mock_artifacts.write.called

    # Extract the metadata argument
    call_args = mock_artifacts.write.call_args
    meta = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("metadata", {})

    # THE CONTRACT: artifact_type must be "beast_wiki"
    assert meta["artifact_type"] == "beast_wiki", (
        f"Contract violation: artifact_type is '{meta['artifact_type']}', "
        f"expected 'beast_wiki'. Consumers (wiki_channel.py, chat.py) "
        f"filter on artifact_type='beast_wiki'."
    )


# ---------------------------------------------------------------------------
# Test 2: Sexton reads beast_wiki when checking needs_generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sexton_reads_beast_wiki_for_needs_check():
    """Sexton._wiki_needs_generation must query artifact_type='beast_wiki'."""
    from aip.orchestration.actors.sexton import Sexton

    mock_artifacts = AsyncMock()
    # Return empty list → no existing wiki → needs generation
    mock_artifacts.list_artifacts_by_metadata = AsyncMock(return_value=[])

    mock_events = AsyncMock()

    sexton = Sexton(
        artifact_store=mock_artifacts,
        event_store=mock_events,
    )

    needs_gen, last_ts = await sexton._wiki_needs_generation("aip_brain")

    # Should return True because no wiki exists
    assert needs_gen is True
    assert last_ts is None

    # Verify it queried for "beast_wiki", not "sexton_wiki"
    call_args = mock_artifacts.list_artifacts_by_metadata.call_args
    key = call_args[0][0] if len(call_args[0]) > 0 else call_args[1].get("key")
    value = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("value")

    assert key == "artifact_type"
    assert value == "beast_wiki", (
        f"Contract violation: _wiki_needs_generation queries for '{value}', "
        f"expected 'beast_wiki'. Must match what _write_wiki_artifact writes."
    )


# ---------------------------------------------------------------------------
# Test 3: Wiki channel reads beast_wiki
# ---------------------------------------------------------------------------


def test_wiki_channel_reads_beast_wiki():
    """Wiki channel must filter by artifact_type='beast_wiki'."""
    import ast

    from aip.orchestration.channels.wiki_channel import register

    # Verify the source code contains beast_wiki (not sexton_wiki)
    source_file = Path(__file__).parent.parent / "src" / "aip" / "orchestration" / "channels" / "wiki_channel.py"
    source = source_file.read_text()

    assert 'value="beast_wiki"' in source or "value='beast_wiki'" in source, (
        "Contract violation: wiki_channel.py must read artifact_type='beast_wiki'"
    )
    # Ensure it's NOT reading the old value
    assert "sexton_wiki" not in source, (
        "Contract violation: wiki_channel.py should not reference 'sexton_wiki'"
    )


# ---------------------------------------------------------------------------
# Test 4: Chat route reads beast_wiki
# ---------------------------------------------------------------------------


def test_chat_route_reads_beast_wiki():
    """Chat route must filter by artifact_type='beast_wiki'."""
    from pathlib import Path

    source_file = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "chat.py"
    source = source_file.read_text()

    assert 'value="beast_wiki"' in source or "value='beast_wiki'" in source, (
        "Contract violation: chat.py must read artifact_type='beast_wiki'"
    )


# ---------------------------------------------------------------------------
# Test 5: Wiki API route matches sexton:wiki:* IDs
# ---------------------------------------------------------------------------


def test_wiki_api_matches_sexton_wiki_ids():
    """The /wiki/articles API must include sexton:wiki:* in ID pattern."""
    from pathlib import Path

    source_file = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
    source = source_file.read_text()

    assert "sexton:wiki:%" in source, (
        "Contract violation: wiki.py must include 'sexton:wiki:%' in LIKE conditions "
        "so that Sexton-generated wiki articles are visible on the /wiki page."
    )


# ---------------------------------------------------------------------------
# Test 6: Wiki API classifies sexton:wiki:* as "wiki" type
# ---------------------------------------------------------------------------


def test_wiki_api_classifies_sexton_ids_as_wiki():
    """The _row_to_article function must classify sexton:wiki:* as 'wiki' type."""
    from pathlib import Path

    source_file = Path(__file__).parent.parent / "src" / "aip" / "adapter" / "api" / "routes" / "wiki.py"
    source = source_file.read_text()

    assert 'startswith("sexton:wiki:")' in source, (
        "Contract violation: wiki.py must recognize sexton:wiki:* IDs as 'wiki' type "
        "in the _row_to_article artifact_type classification."
    )


# ---------------------------------------------------------------------------
# Test 7: Backfill script correctly updates metadata
# ---------------------------------------------------------------------------


def test_backfill_script_updates_sexton_wiki_to_beast_wiki():
    """The wiki_contract_fix.py script must update sexton_wiki → beast_wiki."""
    from pathlib import Path

    script_file = Path(__file__).parent.parent / "scripts" / "wiki_contract_fix.py"
    assert script_file.exists(), "Backfill script must exist"

    source = script_file.read_text()
    assert "beast_wiki" in source
    assert "sexton_wiki" in source  # It references the old value to find and update


# ---------------------------------------------------------------------------
# Test 8: No remaining sexton_wiki references in production code
# ---------------------------------------------------------------------------


def test_no_sexton_wiki_as_artifact_type_in_production_code():
    """No production code should use 'sexton_wiki' as an artifact_type VALUE.

    Log event names like 'sexton_wiki_pass_complete' are fine — they're
    structured log keys, not artifact metadata values. The contract violation
    is when artifact_type is set to or compared against 'sexton_wiki' instead
    of 'beast_wiki'.

    Exception: The backfill script references it for migration purposes.
    Exception: AGENTS.md files may reference it as a Known Gotcha.
    """
    import os

    project_root = Path(__file__).parent.parent
    violations = []

    # Patterns that indicate sexton_wiki being used as artifact_type VALUE
    bad_patterns = [
        '"artifact_type": "sexton_wiki"',       # dict literal
        "'artifact_type': 'sexton_wiki'",       # dict literal (single quotes)
        'value="sexton_wiki"',                   # metadata query
        "value='sexton_wiki'",                   # metadata query (single quotes)
        'value = "sexton_wiki"',                 # with spaces
    ]

    for root, dirs, files in os.walk(project_root / "src"):
        # Skip __pycache__
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            filepath = Path(root) / f
            try:
                content = filepath.read_text()
                for pattern in bad_patterns:
                    if pattern in content:
                        violations.append(f"{filepath}: contains '{pattern}'")
            except Exception:
                pass

    assert not violations, (
        f"Found 'sexton_wiki' used as artifact_type value in production code "
        f"(should be 'beast_wiki'):\n"
        + "\n".join(violations)
    )
