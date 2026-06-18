"""Integration test: the real ARISTOTLE extension mounts via ExtensionHost.

This is the dogfood test — it points the host at the repo's actual
`extensions/` directory and verifies ARISTOTLE (the first real extension)
loads, migrates, and registers its actor. Unlike test_extension_lifecycle.py
(which uses synthetic demo extensions), this test exercises the real
ARISTOTLE manifest, real migration SQL, real config schema, and real
SOCRATES actor.

Run:  CI=true uv run pytest tests/test_aristotle_extension.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aip.adapter.extensions.host import ExtensionHost
from aip.adapter.extensions.state import ExtensionState


# Path to the repo's real extensions/ directory.
_REPO_ROOT = Path(__file__).parent.parent
_EXTENSIONS_DIR = _REPO_ROOT / "extensions"
_ARISTOTLE_DIR = _EXTENSIONS_DIR / "aristotle"


@pytest.fixture
async def container(tmp_path: Path):
    """Minimal container with a real CorpusRegistry backed by tmp_path DBs."""
    from aip.adapter.corpus_registry import CorpusRegistry
    from aip.foundation.corpus_types import CorpusType

    registry = CorpusRegistry(max_corpora=4)
    await registry.startup(
        corpora_to_register=[
            ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
        ],
    )

    class _MinimalContainer:
        def __init__(self):
            self.corpus_registry = registry
            self.vigil = None
            self.beast = None
            self.sexton_actor = None

    return _MinimalContainer()


@pytest.fixture
def host(tmp_path: Path, container) -> ExtensionHost:
    """Host pointed at the real extensions/ dir.

    NOTE: this uses the repo's extensions/aristotle/ — the real extension.
    The host will add extensions/ to sys.path (ADR-014 §6.4), making
    `aristotle.config`, `aristotle.actors`, `aristotle.hooks` importable.
    """
    return ExtensionHost(
        extensions_dir=_EXTENSIONS_DIR,
        container=container,
        manifest_version_range=(1, 1),
    )


@pytest.mark.asyncio
async def test_aristotle_dir_exists():
    """Sanity: the real ARISTOTLE extension is in the repo."""
    assert _ARISTOTLE_DIR.exists(), f"expected {_ARISTOTLE_DIR} to exist"
    assert (_ARISTOTLE_DIR / "extension.yaml").exists()
    assert (_ARISTOTLE_DIR / "migrations" / "M001_aristotle.sql").exists()
    assert (_ARISTOTLE_DIR / "hooks.py").exists()
    assert (_ARISTOTLE_DIR / "actors" / "socrates.py").exists()
    assert (_ARISTOTLE_DIR / "config.py").exists()


@pytest.mark.asyncio
async def test_aristotle_manifest_validates(host: ExtensionHost):
    """ARISTOTLE's manifest parses + validates (manifest_version=1, id=aristotle)."""
    await host.discover()
    await host.validate()
    state = host.state("aristotle")
    # ARISTOTLE should be VALIDATED (not FAILED) — if it's FAILED, surface the failures.
    assert state is ExtensionState.VALIDATED, (
        f"ARISTOTLE manifest should validate; state={state}, "
        f"failures={[f.to_dict() for f in host.failures('aristotle')]}"
    )


@pytest.mark.asyncio
async def test_aristotle_migrations_create_tables(host: ExtensionHost, container):
    """M001_aristotle.sql creates aristotle_concept + aristotle_struggle_pattern."""
    await host.start()
    assert host.state("aristotle") is ExtensionState.REGISTERED, (
        f"ARISTOTLE should reach REGISTERED; failures="
        f"{[f.to_dict() for f in host.failures('aristotle')]}"
    )

    # The migration applies to the aristotle:textbook corpus (ADR-014 §6.2).
    stores = await container.corpus_registry.get_stores("aristotle:textbook")
    assert await _table_exists(stores, "aristotle_concept"), \
        "M001_aristotle.sql should create aristotle_concept table"
    assert await _table_exists(stores, "aristotle_struggle_pattern"), \
        "M001_aristotle.sql should create aristotle_struggle_pattern table"


@pytest.mark.asyncio
async def test_aristotle_registers_socrates_actor(host: ExtensionHost):
    """SOCRATES actor is registered via hooks.py::on_load."""
    await host.start()
    assert "socrates" in host.registered_actors(), (
        f"SOCRATES should be registered; registered_actors={host.registered_actors()}"
    )


@pytest.mark.asyncio
async def test_aristotle_socrates_conforms_to_actor_protocol():
    """SOCRATES conforms to the foundation Actor Protocol (ADR-014 §5.2)."""
    from aip.foundation.protocols.actors import Actor
    from aristotle.actors import SocratesActor

    actor = SocratesActor()
    assert isinstance(actor, Actor), (
        "SocratesActor must conform to foundation.protocols.actors.Actor — "
        "otherwise the host's isinstance check skips its scheduler."
    )
    assert actor.name == "socrates"
    assert actor.cadence == 0.0  # manual-only (ADR-ARISTOTLE §3)


@pytest.mark.asyncio
async def test_aristotle_config_schema_loads(host: ExtensionHost):
    """config.schema (aristotle.config:AristotleSettings) loads + instantiates."""
    await host.discover()
    await host.validate()
    rec = host.registry.get_record("aristotle")
    assert rec is not None
    assert rec.config is not None, (
        "AristotleSettings should be instantiated at validate; "
        f"failures={[f.to_dict() for f in host.failures('aristotle')]}"
    )
    # AristotleSettings defaults (ADR-ARISTOTLE §7 bilingual)
    assert rec.config.primary_language == "en"
    assert rec.config.alt_language == "ur"


@pytest.mark.asyncio
async def test_aristotle_health_surfaces_in_health(host: ExtensionHost):
    """ARISTOTLE appears in host.health() with state=REGISTERED."""
    await host.start()
    health = host.health()
    by_id = {h["id"]: h for h in health}
    assert "aristotle" in by_id, f"ARISTOTLE should be in health: {health}"
    assert by_id["aristotle"]["state"] == "REGISTERED"
    assert by_id["aristotle"]["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_aristotle_stop_cancels_socrates(host: ExtensionHost):
    """host.stop() cancels SOCRATES + marks ARISTOTLE DISABLED."""
    await host.start()
    assert "socrates" in host.registered_actors()
    await host.stop()
    assert not host.is_running()
    assert host.state("aristotle") is ExtensionState.DISABLED
    assert host.registered_actors() == []


# --------------------------------------------------------------------------
async def _table_exists(stores, table: str) -> bool:
    """Check a SQLite table exists in the corpus's write connection."""
    conn = stores.connection_manager.write_conn
    cur = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None
