"""Phase β-2 (2026-07-23) — Wiki → User Manual Evolution tests.

Verifies:
1. prerequisite_of is a valid crosslink relation type (for chapter ordering)
2. manual_chapter is a recognized artifact type in the wiki route
3. aip export manual CLI command exists and works
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from click.testing import CliRunner

from aip.cli.export import export


class TestPrerequisiteOfRelationType:
    """Verify prerequisite_of is a valid crosslink relation type."""

    def test_prerequisite_of_in_valid_types(self):
        """VALID_RELATION_TYPES must include 'prerequisite_of'."""
        from aip.adapter.api.routes.links import VALID_RELATION_TYPES

        assert "prerequisite_of" in VALID_RELATION_TYPES, (
            "prerequisite_of must be a valid relation type for wiki → manual chapter ordering"
        )


class TestManualChapterArtifactType:
    """Verify manual_chapter is recognized in the wiki route."""

    def test_wiki_route_classifies_manual_prefix(self):
        """The wiki route _row_to_article must classify manual:* IDs as manual_chapter."""
        from aip.adapter.api.routes import wiki

        src = inspect.getsource(wiki)
        assert "manual_chapter" in src, (
            "wiki route must classify manual:* IDs as manual_chapter artifact_type"
        )

    def test_wiki_like_patterns_include_manual(self):
        """The wiki SQL LIKE patterns must include manual:%."""
        from aip.adapter.api.routes import wiki

        src = inspect.getsource(wiki)
        assert "LIKE 'manual:%'" in src, (
            "wiki SQL queries must include manual:% in LIKE patterns"
        )


class TestExportManualCLI:
    """Verify aip export manual CLI command."""

    def test_help_lists_command(self):
        """'aip export --help' must list the manual command."""
        runner = CliRunner()
        result = runner.invoke(export, ["--help"])
        assert result.exit_code == 0
        assert "manual" in result.output

    def test_manual_help(self):
        """'aip export manual --help' shows the docstring."""
        runner = CliRunner()
        result = runner.invoke(export, ["manual", "--help"])
        assert result.exit_code == 0
        assert "Compile all APPROVED wiki articles" in result.output
        assert "domain" in result.output.lower()

    def test_manual_export_no_articles(self, tmp_path: Path):
        """Export with no matching articles returns an error."""
        runner = CliRunner()
        result = runner.invoke(
            export,
            ["manual", "nonexistent_domain", "--out", str(tmp_path / "manual.md"),
             "--db-path", str(tmp_path / "state.db")],
        )
        assert result.exit_code == 1
        assert "No wiki articles" in result.output or "Error" in result.output

    def test_manual_export_with_articles(self, tmp_path: Path):
        """Export with matching articles produces a markdown manual."""
        import asyncio
        import aiosqlite
        import json
        from datetime import datetime, timezone

        db_path = tmp_path / "state.db"

        async def _setup():
            conn = await aiosqlite.connect(str(db_path))
            # Create artifacts + ecs_state tables
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    metadata_json TEXT,
                    version INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ecs_state (
                    artifact_id TEXT PRIMARY KEY,
                    current_state TEXT
                )
            """)
            # Insert two APPROVED wiki articles in the 'aip' domain
            now = datetime.now(timezone.utc).isoformat()
            for i, title in enumerate(["Architecture Overview", "Getting Started"]):
                art_id = f"wiki:aip:{title.lower().replace(' ', '_')}:{i}"
                meta = json.dumps({"domain": "aip", "title": title, "summary": f"Summary {i}"})
                await conn.execute(
                    "INSERT INTO artifacts (id, content, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (art_id, f"# {title}\n\nContent for {title}.", meta, now, now),
                )
                await conn.execute(
                    "INSERT INTO ecs_state (artifact_id, current_state) VALUES (?, 'APPROVED')",
                    (art_id,),
                )
            await conn.commit()
            await conn.close()

        asyncio.run(_setup())

        out_path = tmp_path / "manual.md"
        runner = CliRunner()
        result = runner.invoke(
            export,
            ["manual", "aip", "--out", str(out_path), "--db-path", str(db_path)],
        )

        assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
        assert "Manual export complete" in result.output
        assert "Articles: 2" in result.output
        assert out_path.exists(), "manual.md not created"

        # Verify the manual content
        content = out_path.read_text()
        assert "Aip — User Manual" in content or "AIP — User Manual" in content
        assert "Table of Contents" in content
        assert "Architecture Overview" in content
        assert "Getting Started" in content
        assert "Chapter 1" in content
        assert "Chapter 2" in content
