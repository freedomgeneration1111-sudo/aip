"""TDD contract for the extension platform — ADR-014.

RED STATE BY DESIGN. This file is written before `aip.adapter.extensions`
exists. It fails to collect (ImportError) until the host is built, and then
each test pins one behavior of the lifecycle contract. Implement against it;
do not loosen a test to make it pass — change the design discussion instead.

Lifecycle under test (host-owned, sandbox-wrapped per extension):
    discover -> validate -> migrate -> register -> [mount v1.1] -> ready
A failing extension reaches DEGRADED/FAILED in isolation; the host stays up.

Run:  CI=true uv run pytest tests/test_extension_lifecycle.py -v
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

# These imports do not exist yet — that is the point (red state).
from aip.adapter.extensions.host import ExtensionHost
from aip.adapter.extensions.state import ExtensionState


# --------------------------------------------------------------------------
# Helpers — write a minimal, valid extension into an operator-owned dir.
#
# Per ADR-014 §5.3, the manifest's `actors`/`channels` lists are ADVISORY.
# Actual registration happens in `hooks.py::on_load(host)`. The helper writes
# a hooks.py that calls `host.register_actor("demo_actor", factory)` so the
# registration test exercises the real path.
# --------------------------------------------------------------------------

_HOOKS_PY = textwrap.dedent(
    """\
    \"\"\"Demo extension on_load hook — registers one actor.\"\"\"
    from __future__ import annotations

    from aip.foundation.protocols.actors import ActorResult


    class _DemoActor:
        \"\"\"Minimal actor conforming to foundation.protocols.actors.Actor.

        cadence=0 means manual-only — runs one cycle on start, then waits
        for cancellation. This is the ARISTOTLE shape (tutoring state
        machine driven by user turns, not by a timer).
        \"\"\"
        name = "demo_actor"
        cadence = 0.0

        async def run_cycle(self, ctx):
            # A real actor would do work here (query the corpus, call a
            # model, update state). This demo just returns success.
            return ActorResult(ok=True)

        def health(self):
            return {"state": "active", "last_run": None, "error_count": 0}


    def on_load(host):
        host.register_actor("demo_actor", _DemoActor, cadence=0.0)


    def on_unload(host):
        pass
    """
)


def _write_extension(
    root: Path,
    ext_id: str = "demo",
    *,
    manifest_version: int = 1,
    extra_manifest: str = "",
    with_bad_migration: bool = False,
    with_gui: bool = False,
    disabled: bool = False,
    with_hooks: bool = True,
    with_invalid_config_schema: bool = False,
) -> Path:
    """Write a minimal valid extension into root/extensions/<ext_id>/."""
    ext_dir = root / "extensions" / ext_id
    (ext_dir / "migrations").mkdir(parents=True, exist_ok=True)
    gui_line = (
        "  gui: { nav: { label: Demo, icon: school, order: 30 },"
        " pages: 'demo_ext.gui:register_pages' }\n"
        if with_gui
        else ""
    )
    disabled_line = "enabled: false\n            " if disabled else ""
    # config is a TOP-LEVEL key per ADR-014 §6 (not under `contributes:`).
    if with_invalid_config_schema:
        config_line = (
            "config:\n"
            "              schema: nonexistent_pkg.missing_mod:MissingClass\n"
        )
    else:
        config_line = "config: {}\n"
    (ext_dir / "extension.yaml").write_text(
        textwrap.dedent(
            f"""\
            manifest_version: {manifest_version}
            id: {ext_id}
            name: "Demo Extension"
            version: 0.1.0
            {disabled_line}depends: []
            contributes:
              corpora:
                - {{ role: demo, type: document, sensitive: false }}
              actors: [demo_actor]
              channels: []
              workflows_dir: workflows
              migrations: migrations
            {gui_line}{config_line}{extra_manifest}
            """
        )
    )
    sql = (
        "CREATE TABLE demo_concept (id TEXT PRIMARY KEY, name TEXT);"
        if not with_bad_migration
        else "CREATE TABLE demo_concept (this is not valid sql"
    )
    (ext_dir / "migrations" / "M001_demo.sql").write_text(sql)
    if with_hooks:
        (ext_dir / "hooks.py").write_text(_HOOKS_PY)
    return ext_dir


# --------------------------------------------------------------------------
# Minimal container stub — the host receives a container (ADR-014 §1.4).
# In production this is the real AipContainer; in tests we build a minimal
# stand-in with a real CorpusRegistry (so migration tests hit a real DB).
# --------------------------------------------------------------------------


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
            # The host reads this off the container; stub it for tests.
            self.vigil = None
            self.beast = None
            self.sexton_actor = None

    return _MinimalContainer()


@pytest.fixture
def host(tmp_path: Path, container) -> ExtensionHost:
    # The host discovers only under the operator-owned extensions dir.
    # It RECEIVES the container (does not create one).
    return ExtensionHost(
        extensions_dir=tmp_path / "extensions",
        container=container,
        manifest_version_range=(1, 1),
    )


# --------------------------------------------------------------------------
# The contract tests (ADR-014 §8 step 1).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovers_extension_from_manifest(tmp_path: Path, host: ExtensionHost):
    _write_extension(tmp_path, "demo")
    found = await host.discover()
    assert [e.id for e in found] == ["demo"]
    assert host.state("demo") is ExtensionState.DISCOVERED


@pytest.mark.asyncio
async def test_validates_manifest_schema(tmp_path: Path, host: ExtensionHost):
    # manifest_version outside the host's supported range -> FAILED, host intact.
    _write_extension(tmp_path, "demo", manifest_version=999)
    await host.discover()
    await host.validate()
    assert host.state("demo") is ExtensionState.FAILED
    assert any("manifest_version" in f.reason for f in host.failures("demo"))


@pytest.mark.asyncio
async def test_extension_with_invalid_config_fails_at_validate(
    tmp_path: Path, host: ExtensionHost
):
    # config.schema points to a missing class -> FAILED at stage 1 with a
    # `config`-tagged failure (ADR-014 §4.1).
    _write_extension(tmp_path, "demo", with_invalid_config_schema=True)
    await host.discover()
    await host.validate()
    assert host.state("demo") is ExtensionState.FAILED
    assert any("config" in f.reason or f.contribution == "config"
               for f in host.failures("demo"))


@pytest.mark.asyncio
async def test_two_extensions_with_same_id_fails_cleanly(
    tmp_path: Path, host: ExtensionHost
):
    # Two extensions declaring the same manifest id -> the second is FAILED at
    # stage 1 with an id-collision failure; the first proceeds normally.
    # Records are keyed by directory name (the unique physical key), so both
    # survive — one VALIDATED, one FAILED.
    _write_extension(tmp_path, "demo")
    # Write a second extension with the same manifest id in a different directory.
    other = tmp_path / "extensions" / "other_demo"
    other.mkdir(parents=True)
    (other / "extension.yaml").write_text(
        textwrap.dedent(
            """\
            manifest_version: 1
            id: demo
            name: "Other Demo"
            version: 0.1.0
            depends: []
            contributes:
              corpora: []
              actors: []
              channels: []
              workflows_dir: workflows
              migrations: migrations
            config: {}
            """
        )
    )
    (other / "migrations").mkdir()
    (other / "migrations" / "M001.sql").write_text(
        "CREATE TABLE other (id TEXT PRIMARY KEY);"
    )
    await host.discover()
    await host.validate()
    # Both records survive (keyed by directory name). Exactly one is VALIDATED
    # and the other is FAILED with an id-collision failure reason.
    # NOTE: do NOT call discover() again — it resets states to DISCOVERED.
    # Read from the registry directly.
    records = host.registry.records()
    states_by_dir = {rec.id: host.state(rec.id) for rec in records}
    assert ExtensionState.VALIDATED in states_by_dir.values()
    assert ExtensionState.FAILED in states_by_dir.values()
    # The FAILED record's failure reason mentions the id collision.
    failed_dirs = [d for d, s in states_by_dir.items() if s is ExtensionState.FAILED]
    assert failed_dirs, "expected at least one FAILED extension"
    failed_rec_failures = host.failures(failed_dirs[0])
    assert any("collid" in f.reason.lower() or "id" in f.reason.lower()
               for f in failed_rec_failures)


@pytest.mark.asyncio
async def test_runs_extension_migrations(
    tmp_path: Path, host: ExtensionHost, container
):
    _write_extension(tmp_path, "demo")
    await host.start()  # discover -> validate -> migrate -> register
    # The contributed table exists in the extension's corpus.
    # Corpus id is namespaced {ext_id}:{role} per ADR-014 §6.2.
    stores = await container.corpus_registry.get_stores("demo:demo")
    assert await _table_exists(stores, "demo_concept")
    assert host.state("demo") is ExtensionState.REGISTERED


@pytest.mark.asyncio
async def test_registers_extension_actors(tmp_path: Path, host: ExtensionHost):
    # Manifest's `actors` is advisory; registration happens in on_load
    # (ADR-014 §5.3). The helper writes a hooks.py that registers demo_actor.
    _write_extension(tmp_path, "demo", with_hooks=True)
    await host.start()
    assert "demo_actor" in host.registered_actors()


@pytest.mark.asyncio
async def test_mounts_extension_gui_pages(tmp_path: Path, host: ExtensionHost):
    # v1.1: a mounted extension exposes a nav entry + route.
    # The test extension's hooks.py calls host.register_page() which
    # records a NavItem. After start(), the extension transitions to
    # MOUNTED and host.nav_items() includes the registered route.
    _write_extension(tmp_path, "demo", with_gui=False, with_hooks=True)
    # The standard _HOOKS_PY registers an actor, not a page.
    # For this test we need hooks.py to call host.register_page().
    # We'll write a custom hooks.py that registers a page.
    hooks_path = tmp_path / "extensions" / "demo" / "hooks.py"
    hooks_path.write_text(
        "def on_load(host):\n"
        "    host.register_page('/demo', 'Demo', 'school', lambda: None, order=30)\n"
        "\n"
        "def on_unload(host):\n"
        "    pass\n"
    )
    await host.start()
    nav = host.nav_items()
    assert any(item.route.endswith("/demo") for item in nav)
    assert host.state("demo") is ExtensionState.MOUNTED


@pytest.mark.asyncio
async def test_failed_extension_does_not_break_host(
    tmp_path: Path, host: ExtensionHost
):
    # A broken migration must isolate to DEGRADED and let a healthy peer run.
    _write_extension(tmp_path, "broken", with_bad_migration=True)
    _write_extension(tmp_path, "healthy")
    await host.start()
    assert host.state("broken") is ExtensionState.DEGRADED
    assert host.state("healthy") is ExtensionState.REGISTERED
    # The host itself is usable.
    assert host.is_running()


@pytest.mark.asyncio
async def test_disabled_extension_does_not_mount(tmp_path: Path, host: ExtensionHost):
    _write_extension(tmp_path, "demo", disabled=True)
    await host.start()
    assert host.state("demo") is ExtensionState.DISABLED
    assert "demo_actor" not in host.registered_actors()


@pytest.mark.asyncio
async def test_extension_state_surfaces_in_health(
    tmp_path: Path, host: ExtensionHost
):
    _write_extension(tmp_path, "broken", with_bad_migration=True)
    _write_extension(tmp_path, "healthy")
    await host.start()
    health = host.health()
    by_id = {h["id"]: h for h in health}
    assert by_id["healthy"]["state"] == "REGISTERED"
    assert by_id["broken"]["state"] == "DEGRADED"
    # The failure carries enough to debug without reading logs.
    assert by_id["broken"]["failures"]
    f = by_id["broken"]["failures"][0]
    assert {"stage", "contribution", "reason"} <= set(f)


@pytest.mark.asyncio
async def test_stop_cancels_extension_actors(
    tmp_path: Path, host: ExtensionHost
):
    # ADR-014 §4.2: host.stop() cancels every actor scheduler task and marks
    # every extension DISABLED. No background tasks leak.
    _write_extension(tmp_path, "demo", with_hooks=True)
    await host.start()
    assert "demo_actor" in host.registered_actors()
    assert host.is_running()
    await host.stop()
    assert not host.is_running()
    assert host.state("demo") is ExtensionState.DISABLED
    # registered_actors() is empty after stop (schedulers cancelled, refs cleared).
    assert host.registered_actors() == []


# --------------------------------------------------------------------------
async def _table_exists(stores, table: str) -> bool:
    """Check a SQLite table exists in the corpus's write connection."""
    conn = stores.connection_manager.write_conn
    cur = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None
