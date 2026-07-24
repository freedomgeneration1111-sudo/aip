"""Phase α-2 (2026-07-23) — [corpora.{id}] TOML config section tests.

Verifies that app.py reads the [corpora.*] section from aip.config.toml
and registers additional corpora at startup. Operators can add corpora
without editing app.py — just declare them in TOML.

ADR-008 Multi-Corpus, Phase α-2.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


def _lifespan_source() -> str:
    """Return the source of the app.py lifespan function."""
    from aip.adapter.api import app

    return inspect.getsource(app)


class TestCorpusTomlConfigWiring:
    """Phase α-2 — verify the [corpora.*] TOML reading code is wired."""

    def test_reads_corpora_config_section(self):
        """app.py must read config.get('corpora', {}) for additional corpora."""
        src = _lifespan_source()
        assert 'config.get("corpora", {})' in src, (
            "app.py must read the [corpora] section from config"
        )

    def test_parses_corpus_type_from_toml(self):
        """Each [corpora.{id}] section must be parsed into a CorpusType."""
        src = _lifespan_source()
        assert "CorpusType(_ctype_str)" in src, (
            "app.py must parse the type string into a CorpusType enum"
        )

    def test_skips_definer_and_codeforge_in_toml(self):
        """Definer + codeforge are always registered by default — TOML sections
        for them must be skipped to avoid duplicates."""
        src = _lifespan_source()
        assert '_cid in ("definer", "codeforge")' in src, (
            "app.py must skip definer + codeforge in the TOML section (they're defaults)"
        )

    def test_sets_sensitive_flag_from_toml(self):
        """The sensitive flag from TOML must be applied post-registration."""
        src = _lifespan_source()
        assert "_stores._sensitive = True" in src, (
            "app.py must set the sensitive flag from TOML config"
        )
        assert "_stores._access_note" in src, (
            "app.py must set the access_note from TOML config"
        )

    def test_derives_db_path_from_corpus_id(self):
        """When db_path is not specified, it defaults to db/{corpus_id}.db."""
        src = _lifespan_source()
        assert '_db_dir / f"{_cid}.db"' in src, (
            "app.py must derive db_path as db/{corpus_id}.db when not specified"
        )

    def test_logs_unknown_type_warning(self):
        """Unknown corpus types must be logged and skipped, not crash startup."""
        src = _lifespan_source()
        assert "corpus_config_skipped" in src, (
            "app.py must log a warning for unknown corpus types"
        )


class TestCorpusTomlConfigExample:
    """Verify the config example documents the [corpora] section correctly."""

    def test_config_example_has_corpora_section_docs(self):
        """config/aip.config.toml.example must document the [corpora] section."""
        config_example = Path(__file__).resolve().parent.parent / "config" / "aip.config.toml.example"
        src = config_example.read_text()
        assert "Multi-Corpus Configuration" in src, (
            "config example must have a Multi-Corpus Configuration section"
        )
        assert "[corpora." in src, (
            "config example must show [corpora.{id}] section syntax"
        )
        assert "sensitive" in src, "must document the sensitive flag"
        assert "access_note" in src, "must document the access_note field"
        assert "type = \"document\"" in src or "type = 'document'" in src, (
            "must show an example with type = 'document'"
        )

    def test_config_example_states_defaults_not_declared(self):
        """The config example must state that definer + codeforge are auto-registered."""
        config_example = Path(__file__).resolve().parent.parent / "config" / "aip.config.toml.example"
        src = config_example.read_text()
        assert "definer" in src and "codeforge" in src, (
            "config example must mention definer + codeforge as defaults"
        )
        assert "ALWAYS registered" in src or "auto-registered" in src, (
            "config example must state that definer + codeforge are always registered"
        )
