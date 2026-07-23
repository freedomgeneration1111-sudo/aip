"""QW11 (2026-07-23) — aip corpus ingest-code CLI command tests.

Verifies the CLI command that ingests a Python source directory into
the codeforge corpus. ADR-008 §8 Chunk 7 / Phase 1.6 Codebase-as-Corpus.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from aip.cli.corpus import corpus


class TestCorpusIngestCodeCli:
    """QW11 — aip corpus ingest-code"""

    def test_help_lists_command(self):
        """The ingest-code command appears in 'aip corpus --help'."""
        runner = CliRunner()
        result = runner.invoke(corpus, ["--help"])
        assert result.exit_code == 0
        assert "ingest-code" in result.output

    def test_ingest_code_help(self):
        """'aip corpus ingest-code --help' shows the docstring."""
        runner = CliRunner()
        result = runner.invoke(corpus, ["ingest-code", "--help"])
        assert result.exit_code == 0
        assert "Ingest a Python source directory" in result.output
        assert "codeforge" in result.output

    def test_nonexistent_path_rejected_by_click(self):
        """Click's exists=True validation catches nonexistent paths (exit code 2)."""
        runner = CliRunner()
        result = runner.invoke(corpus, ["ingest-code", "/nonexistent/path/xyz"])
        assert result.exit_code == 2  # Click's standard validation error

    def test_ingests_python_file_and_creates_db(self, tmp_path: Path):
        """Ingesting a directory with one .py file creates codeforge.db + 1 turn."""
        # Create a small .py file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "sample.py").write_text(
            'def hello():\n    """Say hello."""\n    return "world"\n'
        )

        # Use a db path in tmp_path so we don't pollute the real db/
        db_path = tmp_path / "state.db"

        runner = CliRunner()
        result = runner.invoke(corpus, ["ingest-code", str(src_dir), "--db-path", str(db_path)])
        assert result.exit_code == 0, f"expected 0, got {result.exit_code}: {result.output}"
        assert "Ingest complete" in result.output
        assert "Turns created:    1" in result.output

        # codeforge.db should exist alongside state.db
        codeforge_db = tmp_path / "codeforge.db"
        assert codeforge_db.exists(), "codeforge.db not created"

    def test_stale_detection_skips_unchanged_files(self, tmp_path: Path):
        """Re-ingesting the same file skips it (content_hash matches)."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "sample.py").write_text(
            'def hello():\n    """Say hello."""\n    return "world"\n'
        )

        db_path = tmp_path / "state.db"
        runner = CliRunner()

        # First ingest
        result1 = runner.invoke(corpus, ["ingest-code", str(src_dir), "--db-path", str(db_path)])
        assert result1.exit_code == 0
        assert "Turns created:    1" in result1.output

        # Second ingest (should skip)
        result2 = runner.invoke(corpus, ["ingest-code", str(src_dir), "--db-path", str(db_path)])
        assert result2.exit_code == 0
        assert "Turns created:    0" in result2.output
        assert "Skipped (stale):  1" in result2.output

    def test_force_flag_reingests(self, tmp_path: Path):
        """--force re-ingests even if content_hash matches."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "sample.py").write_text(
            'def hello():\n    """Say hello."""\n    return "world"\n'
        )

        db_path = tmp_path / "state.db"
        runner = CliRunner()

        # First ingest
        result1 = runner.invoke(corpus, ["ingest-code", str(src_dir), "--db-path", str(db_path)])
        assert result1.exit_code == 0
        assert "Turns created:    1" in result1.output

        # Second ingest with --force
        result2 = runner.invoke(
            corpus, ["ingest-code", str(src_dir), "--db-path", str(db_path), "--force"]
        )
        assert result2.exit_code == 0
        assert "Turns created:    1" in result2.output  # re-created
        assert "Skipped (stale):  0" in result2.output

    def test_skips_test_files_and_pyi(self, tmp_path: Path):
        """The parser skips test_*.py, *_test.py, and .pyi files.

        Note: rglob("*.py") only matches .py files, not .pyi — so .pyi
        files are not even counted in 'files scanned'. The skip rules
        apply to test_*.py and *_test.py among .py files.
        """
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "real.py").write_text('def real():\n    """real func"""\n    return 1\n')
        (src_dir / "test_real.py").write_text('def test_real(): assert True\n')
        (src_dir / "types.pyi").write_text('def typed(x: int) -> int: ...\n')

        db_path = tmp_path / "state.db"
        runner = CliRunner()
        result = runner.invoke(corpus, ["ingest-code", str(src_dir), "--db-path", str(db_path)])
        assert result.exit_code == 0
        # 2 .py files scanned (real.py + test_real.py), 1 skipped (test_real.py), 1 parsed
        # (.pyi is not counted — rglob("*.py") doesn't match it)
        assert "Files scanned:    2" in result.output
        assert "Files skipped:    1" in result.output
        assert "Files parsed:     1" in result.output
