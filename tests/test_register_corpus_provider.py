"""ND9 (2026-07-23) — register_corpus_provider dynamic hook tests.

Verifies that extensions can dynamically register corpora from on_load
via host.register_corpus_provider(). This is the dynamic counterpart to
manifest-static corpus declaration — extensions can register corpora
based on runtime conditions (config, feature flags) rather than only
manifest-static ones.

ADR-014 §6.2, ND9 from the 2026-07-23 tech-debt assessment.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from aip.adapter.extensions.registry import ExtensionRecord, PendingCorpusProvider


class TestPendingCorpusProvider:
    """Verify the PendingCorpusProvider dataclass."""

    def test_pending_corpus_provider_fields(self):
        """PendingCorpusProvider must have role, corpus_type, db_path, sensitive, access_note."""
        p = PendingCorpusProvider(
            role="textbook",
            corpus_type="document",
        )
        assert p.role == "textbook"
        assert p.corpus_type == "document"
        assert p.db_path is None  # default
        assert p.sensitive is False  # default
        assert p.access_note == ""  # default

    def test_pending_corpus_provider_with_all_fields(self):
        """All fields can be set."""
        p = PendingCorpusProvider(
            role="restricted_docs",
            corpus_type="document",
            db_path="/custom/path.db",
            sensitive=True,
            access_note="Restricted",
        )
        assert p.db_path == "/custom/path.db"
        assert p.sensitive is True
        assert p.access_note == "Restricted"

    def test_extension_record_has_pending_corpus_providers_field(self):
        """ExtensionRecord must have a pending_corpus_providers list (default empty)."""
        rec = ExtensionRecord(id="test-ext")
        assert hasattr(rec, "pending_corpus_providers")
        assert rec.pending_corpus_providers == []


class TestRegisterCorpusProviderAPI:
    """Verify the host.register_corpus_provider method signature + validation."""

    def test_method_exists(self):
        """ExtensionHost must have a register_corpus_provider method."""
        from aip.adapter.extensions.host import ExtensionHost

        assert hasattr(ExtensionHost, "register_corpus_provider"), (
            "ExtensionHost must have register_corpus_provider method (ND9)"
        )

    def test_method_signature(self):
        """register_corpus_provider must accept role, corpus_type + keyword args."""
        from aip.adapter.extensions.host import ExtensionHost

        sig = inspect.signature(ExtensionHost.register_corpus_provider)
        params = sig.parameters

        # role and corpus_type are positional
        assert "role" in params
        assert "corpus_type" in params

        # Keyword-only args (after *)
        assert "db_path" in params
        assert params["db_path"].default is None
        assert "sensitive" in params
        assert params["sensitive"].default is False
        assert "access_note" in params
        assert params["access_note"].default == ""

    def test_raises_outside_on_load(self):
        """register_corpus_provider must raise RuntimeError when called outside on_load."""
        from aip.adapter.extensions.host import ExtensionHost
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})
        host = ExtensionHost(
            extensions_dir=Path("/tmp/nonexistent"),
            container=container,
        )

        with pytest.raises(RuntimeError, match="on_load hook"):
            host.register_corpus_provider("test", "document")

    def test_rejects_role_with_colon(self):
        """register_corpus_provider must reject roles containing ':' (namespacing)."""
        from aip.adapter.extensions.host import ExtensionHost
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})
        host = ExtensionHost(
            extensions_dir=Path("/tmp/nonexistent"),
            container=container,
        )

        # Simulate being inside on_load by setting _current_ext_id
        host._current_ext_id = "test-ext"

        with pytest.raises(ValueError, match="must not contain ':'"):
            host.register_corpus_provider("bad:role", "document")

    def test_rejects_invalid_corpus_type(self):
        """register_corpus_provider must reject invalid corpus_type values."""
        from aip.adapter.extensions.host import ExtensionHost
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})
        host = ExtensionHost(
            extensions_dir=Path("/tmp/nonexistent"),
            container=container,
        )

        host._current_ext_id = "test-ext"

        with pytest.raises(ValueError, match="not a valid CorpusType"):
            host.register_corpus_provider("test", "invalid_type")

    def test_records_pending_provider(self):
        """When called inside on_load, a PendingCorpusProvider is recorded on the extension record."""
        from aip.adapter.extensions.host import ExtensionHost
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})
        host = ExtensionHost(
            extensions_dir=Path("/tmp/nonexistent"),
            container=container,
        )

        # Simulate being inside on_load
        host._current_ext_id = "test-ext"

        # Manually create the extension record so register_corpus_provider can find it
        rec = ExtensionRecord(id="test-ext")
        host._registry._records["test-ext"] = rec

        host.register_corpus_provider(
            "dynamic_corpus",
            "document",
            sensitive=True,
            access_note="Dynamically registered",
        )

        assert len(rec.pending_corpus_providers) == 1
        provider = rec.pending_corpus_providers[0]
        assert provider.role == "dynamic_corpus"
        assert provider.corpus_type == "document"
        assert provider.sensitive is True
        assert provider.access_note == "Dynamically registered"


class TestExecutePendingCorpusProviders:
    """Verify the host executes pending corpus providers after on_load."""

    def test_execution_method_exists(self):
        """ExtensionHost must have _execute_pending_corpus_providers method."""
        from aip.adapter.extensions.host import ExtensionHost

        assert hasattr(ExtensionHost, "_execute_pending_corpus_providers"), (
            "ExtensionHost must have _execute_pending_corpus_providers method"
        )

    def test_skips_when_registry_not_wired(self):
        """When corpus_registry is None, pending providers are skipped (not crash)."""
        from aip.adapter.extensions.host import ExtensionHost
        from aip.adapter.extensions.manifest import Manifest
        from aip.adapter.api.dependencies import AipContainer

        container = AipContainer({})  # no corpus_registry
        host = ExtensionHost(
            extensions_dir=Path("/tmp/nonexistent"),
            container=container,
        )

        rec = ExtensionRecord(id="test-ext", ext_dir=Path("/tmp"))
        rec.pending_corpus_providers.append(
            PendingCorpusProvider(role="test", corpus_type="document")
        )

        # Build a minimal manifest (the method needs manifest.id)
        manifest = Manifest(
            manifest_version=1,
            id="test-ext",
            name="Test",
            version="0.1.0",
            contributes={"corpora": [], "actors": [], "channels": [], "workflows_dir": "workflows", "migrations": "migrations"},
        )

        # Should not raise even though registry is None
        # (the method logs a warning and returns)
        import asyncio
        asyncio.run(host._execute_pending_corpus_providers(rec, manifest))
