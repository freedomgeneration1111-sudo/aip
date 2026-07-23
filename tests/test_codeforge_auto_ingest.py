"""QW13b (2026-07-23) — Codeforge auto-ingest background task tests.

Verifies the background task in app.py lifespan that automatically ingests
AIP's own source into the codeforge corpus and keeps it in sync. This is
the "truly automatic" version of QW13 — no separate CLI terminal needed.

Tests are structural/source-level (the scheduler is a long-running async
loop, not suitable for direct unit testing). Behavioral coverage comes from
the existing QW14 acceptance test (test_codeforge_e2e.py) which exercises
the same ingest_python_directory pipeline.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _lifespan_source() -> str:
    """Return the source of the app.py lifespan function."""
    from aip.adapter.api import app

    return inspect.getsource(app)


class TestCodeforgeAutoIngestWiring:
    """QW13b — verify the auto-ingest task is correctly wired into app.py lifespan."""

    def test_scheduler_function_exists(self):
        """The _codeforge_ingest_scheduler async function must exist in app.py."""
        src = _lifespan_source()
        assert "async def _codeforge_ingest_scheduler" in src, (
            "_codeforge_ingest_scheduler function missing from app.py lifespan"
        )

    def test_task_created_with_correct_name(self):
        """The task must be created with the name 'codeforge-ingest-scheduler'."""
        src = _lifespan_source()
        assert 'name="codeforge-ingest-scheduler"' in src, (
            "task must be created with name='codeforge-ingest-scheduler'"
        )

    def test_task_in_shutdown_cancellation_list(self):
        """The codeforge_ingest_task must be in the shutdown cancellation list."""
        src = _lifespan_source()
        assert '"codeforge_ingest", codeforge_ingest_task' in src, (
            "codeforge_ingest_task must be in the shutdown cancellation list "
            "alongside beast, vigil, sexton_actor, etc."
        )

    def test_config_keys_read(self):
        """The scheduler must read [codeforge] config: auto_ingest, source_dir, interval_seconds."""
        src = _lifespan_source()
        assert '_codeforge_auto_ingest' in src, "must read codeforge.auto_ingest config"
        assert '_codeforge_source_dir' in src, "must read codeforge.source_dir config"
        assert '_codeforge_interval' in src, "must read codeforge.interval_seconds config"

    def test_guards_against_missing_registry(self):
        """The scheduler must not start if corpus_registry is None."""
        src = _lifespan_source()
        assert 'corpus_registry", None) is not None' in src, (
            "must guard against corpus_registry being None"
        )

    def test_guards_against_missing_source_dir(self):
        """The scheduler must not start if source_dir doesn't exist."""
        src = _lifespan_source()
        assert '_codeforge_source_dir).exists()' in src, (
            "must guard against source_dir not existing"
        )

    def test_awaits_corpus_migration_ready(self):
        """The scheduler must await _await_corpus_migration_ready() before first ingest."""
        src = _lifespan_source()
        assert "_await_corpus_migration_ready()" in src, (
            "must await _await_corpus_migration_ready() (same pattern as Sexton)"
        )

    def test_uses_skip_existing_true(self):
        """The scheduler must use skip_existing=True for stale detection."""
        src = _lifespan_source()
        assert "skip_existing=True" in src, (
            "must use skip_existing=True (content_hash stale detection)"
        )

    def test_handles_cancelled_error(self):
        """The scheduler must handle asyncio.CancelledError for graceful shutdown."""
        src = _lifespan_source()
        assert "asyncio.CancelledError" in src, (
            "must handle asyncio.CancelledError for graceful shutdown"
        )

    def test_startup_status_log_includes_codeforge(self):
        """The startup status log must include codeforge_auto_ingest field."""
        src = _lifespan_source()
        assert "codeforge_auto_ingest" in src, (
            "startup status log must include codeforge_auto_ingest field"
        )


class TestCodeforgeAutoIngestConfigDefaults:
    """Verify the config defaults are sensible."""

    def test_defaults_documented_in_comment(self):
        """The config defaults must be documented in the comment block."""
        src = _lifespan_source()
        # The comment block documents the defaults
        assert "auto_ingest = true" in src, "default auto_ingest=true must be documented"
        assert 'source_dir = "src/aip"' in src, "default source_dir must be documented"
        assert "interval_seconds = 60" in src, "default interval_seconds=60 must be documented"

    def test_config_example_mentions_codeforge(self):
        """config/aip.config.toml.example should document the [codeforge] section.

        This is a drift guard — if the config section is added to the example,
        this test ensures it stays consistent with the lifespan code.
        """
        config_example = REPO_ROOT / "config" / "aip.config.toml.example"
        if not config_example.exists():
            pytest.skip("config/aip.config.toml.example not found")
        src = config_example.read_text()
        # Either the section is documented, or it's not yet added (QW13b follow-up)
        # This test will catch when someone adds the section but misspells it
        if "[codeforge]" in src:
            assert "auto_ingest" in src, "[codeforge] section must document auto_ingest"
            assert "source_dir" in src, "[codeforge] section must document source_dir"
            assert "interval_seconds" in src, "[codeforge] section must document interval_seconds"
        # If [codeforge] is not in the example yet, that's OK — the lifespan
        # has sensible defaults and logs when the section is absent.
