"""aip status command — real system status from Protocols (offline-first).

Attempts to connect to real stores when available; falls back to
graceful degradation with clear "not configured" indicators.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import click

# Default timeout for Ollama reachability probe (seconds).
# Override with AIP_STATUS_OLLAMA_TIMEOUT env var.
_DEFAULT_OLLAMA_TIMEOUT = 5


def _check_db(db_path: str) -> dict:
    """Probe a SQLite database for table count and row stats."""
    info: dict = {"path": db_path, "exists": False, "tables": 0, "row_counts": {}}
    p = Path(db_path)
    if not p.exists():
        return info
    info["exists"] = True
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row["name"] for row in cur.fetchall()]
        info["tables"] = len(tables)
        for t in tables[:10]:  # cap at 10 tables to avoid noise
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if count > 0:
                    info["row_counts"][t] = count
            except Exception:
                pass
        conn.close()
    except Exception as exc:
        info["error"] = str(exc)
    return info


def _check_ollama() -> dict:
    """Check if Ollama is reachable within a bounded timeout.

    Timeout is configurable via ``AIP_STATUS_OLLAMA_TIMEOUT`` env var
    (default: 5 seconds).  When Ollama is missing, unreachable, or
    times out, the result honestly reports it as unavailable with an
    actionable message — never a fake healthy state.
    """
    timeout_seconds = _get_ollama_timeout()
    info: dict = {
        "reachable": False,
        "models": [],
        "timed_out": False,
        "error": None,
    }
    try:
        import httpx

        r = httpx.get(
            "http://127.0.0.1:11434/api/tags",
            timeout=timeout_seconds,
        )
        if r.status_code == 200:
            info["reachable"] = True
            data = r.json()
            info["models"] = [m.get("name", "?") for m in data.get("models", [])[:5]]
    except httpx.TimeoutException:
        info["timed_out"] = True
        info["error"] = (
            f"Ollama not reachable within {timeout_seconds}s; "
            "local model slot unavailable. "
            "Start Ollama or increase AIP_STATUS_OLLAMA_TIMEOUT."
        )
    except httpx.ConnectError:
        info["error"] = (
            "Ollama not running on 127.0.0.1:11434; "
            "local model slot unavailable. "
            "Install/start Ollama if local synthesis is desired."
        )
    except Exception as exc:
        info["error"] = f"Ollama check failed: {exc}"
    return info


def _get_ollama_timeout() -> int:
    """Return Ollama probe timeout in seconds from env or default."""
    try:
        val = int(os.environ.get("AIP_STATUS_OLLAMA_TIMEOUT", ""))
        if val > 0:
            return val
    except (ValueError, TypeError):
        pass
    return _DEFAULT_OLLAMA_TIMEOUT


@click.command("status")
def status() -> None:
    """Print system status from Protocols (offline-first).

    Probes local databases, config files, and Ollama availability.
    When stores are not initialized, reports "not configured" clearly
    instead of showing fake placeholder data.
    """
    from aip.cli._db_path import get_default_db_path, get_default_lexical_db_path

    click.echo("=== AIP Status ===")

    # --- Config file ---
    config_path = Path("config/aip.config.toml")
    if config_path.exists():
        click.echo(f"config: {config_path} (found)")
        # Read key settings
        try:
            content = config_path.read_text()
            for line in content.splitlines():
                stripped = line.strip()
                if (
                    stripped.startswith("provider")
                    or stripped.startswith("host")
                    or stripped.startswith("port")
                    or stripped.startswith("db_path")
                ):
                    click.echo(f"  {stripped}")
        except Exception:
            click.echo("  (could not read config)")
    else:
        click.echo("config: not found (run `aip init` to create)")

    # --- Effective database paths ---
    main_db = get_default_db_path()
    lexical_db = get_default_lexical_db_path()
    click.echo("\nEffective database paths:")
    click.echo(f"  Main DB:    {main_db}")
    click.echo(f"  Lexical DB: {lexical_db}")

    # --- Databases ---
    db_dir = Path("db")
    expected_dbs = {
        "state.db": "Core: artifacts, projects, events, ECS, canonicals",
        "trace.db": "Trace events and routing outcomes",
        "events.db": "Event store (append-only, legacy)",
        "lexical.db": "FTS5 full-text search index",
        "vectors.db": "SQLite-VSS vector index",
        "ace_playbook.db": "ACE playbook rules",
    }

    db_found = 0
    db_total_rows = 0
    for name, description in expected_dbs.items():
        db_path = db_dir / name
        if db_path.exists():
            info = _check_db(str(db_path))
            total = sum(info.get("row_counts", {}).values())
            db_found += 1
            db_total_rows += total
            if total > 0:
                click.echo(f"db/{name}: {info['tables']} tables, {total} rows — {description}")
                if name == "state.db" and info.get("row_counts"):
                    for table, count in info["row_counts"].items():
                        click.echo(f"    {table}: {count} rows")
            else:
                click.echo(f"db/{name}: empty ({info['tables']} tables) — {description}")
        else:
            click.echo(f"db/{name}: not found — {description}")

    if db_found == 0:
        click.echo("(no databases found — run `aip init` to create)")

    # --- Ollama / Model provider ---
    ollama = _check_ollama()
    if ollama["reachable"]:
        models_str = ", ".join(ollama["models"]) if ollama["models"] else "none listed"
        click.echo(f"ollama: reachable — models: {models_str}")
    elif ollama.get("timed_out"):
        click.echo(f"ollama: {ollama['error']}")
    elif ollama.get("error"):
        click.echo(f"ollama: {ollama['error']}")
    else:
        click.echo("ollama: not reachable (embedding and local synthesis will use API fallback)")

    # --- Vector backend ---
    # NOTE: We check importability without a static import to preserve layer discipline.
    # CLI (foundation layer) should not directly import adapter code.
    try:
        import importlib

        importlib.import_module("aip.adapter.vector.factory")
        click.echo("vector_backend: factory available (will auto-select pgvector → sqlite-vss → in-memory)")
    except ImportError:
        click.echo("vector_backend: factory not importable")

    # Vector rows (for embedding pipeline verification)
    try:
        import sqlite3

        vdb = db_dir / "vectors.db"
        if vdb.exists():
            connv = sqlite3.connect(str(vdb))
            try:
                vcount = connv.execute("SELECT COUNT(*) FROM vector_metadata").fetchone()[0]
                click.echo(f"vectors.db: {vcount} rows")
            finally:
                connv.close()
    except Exception:
        pass

    # --- Beast ---
    try:
        from aip.orchestration.actors.beast import Beast  # noqa: F401 -- import to test availability

        click.echo("beast: actor module available (runs via API lifespan scheduler)")
    except ImportError:
        click.echo("beast: not available")

    # --- Model slots (incl. beast for LLM intelligence) ---
    try:
        config_path = Path("config/aip.config.toml")
        if config_path.exists():
            import tomllib

            with open(config_path, "rb") as f:
                cfg = tomllib.load(f)
            from aip.adapter.model_slot_resolver import ModelSlotResolver

            resolver = ModelSlotResolver(cfg)
            slot_names = resolver.list_slots()
            if slot_names:
                details = []
                for s in slot_names:
                    try:
                        r = resolver._resolve_slot_config(s)
                        m = r.get("model", "<unset>")
                        has_key = bool(r.get("api_key"))
                        details.append(f"{s}={m}{' (key)' if has_key else ''}")
                    except Exception:
                        details.append(f"{s}=<error>")
                click.echo("model_slots: " + ", ".join(details))
            else:
                click.echo("model_slots: none parsed from resolver")
        else:
            click.echo("model_slots: config not found")
    except Exception as exc:
        click.echo(f"model_slots: error reading ({exc})")

    # --- Corpus turns (turn-level corpus, new atomic unit) ---
    try:
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.cli._db_path import get_default_db_path

        db_path = get_default_db_path()
        if Path(db_path).exists():
            # Trigger sync migration for embedded column (ALTER is idempotent)
            try:
                _ = CorpusTurnStore(db_path)
            except Exception:
                pass
            # We can't easily await in sync status; use a tiny runner or just count via direct sql for status
            # For simplicity and to keep status sync, do a direct read (status is best-effort)
            import sqlite3

            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                total = conn.execute("SELECT COUNT(*) FROM corpus_turns").fetchone()[0]
                untagged = conn.execute("SELECT COUNT(*) FROM corpus_turns WHERE tagging_version = 0").fetchone()[0]
                tagged = total - untagged
                embedded = 0
                unembedded = total
                try:
                    embedded = conn.execute("SELECT COUNT(*) FROM corpus_turns WHERE embedded = 1").fetchone()[0]
                    unembedded = total - embedded
                except Exception:
                    # column may not exist yet (pre-migration)
                    pass

                # by_domain top 5 (only tagged turns)
                by_dom = {}
                for row in conn.execute(
                    "SELECT primary_domain, COUNT(*) as c FROM corpus_turns "
                    "WHERE tagging_version > 0 GROUP BY primary_domain ORDER BY c DESC LIMIT 5"
                ):
                    by_dom[row[0] or ""] = row[1]
                dom_str = ", ".join(f"{(k or ''):<18}:{v}" for k, v in list(by_dom.items())[:5]) or "none"

                by_src = {}
                for row in conn.execute("SELECT source_model, COUNT(*) as c FROM corpus_turns GROUP BY source_model"):
                    by_src[row[0] or "unknown"] = row[1]
                by_src_str = ", ".join(f"{k}={v}" for k, v in sorted(by_src.items()))

                # proposals_pending: beast_domain_proposal artifacts (GENERATED until reviewed)
                proposals_pending = 0
                try:
                    proposals_pending = conn.execute(
                        (
                            "SELECT COUNT(*) FROM artifacts "
                            'WHERE metadata_json LIKE \'%"artifact_type": "beast_domain_proposal"%\''
                        )
                    ).fetchone()[0]
                except Exception:
                    pass

                click.echo("corpus_turns:")
                click.echo(f"  total: {total}")
                click.echo(f"  tagged: {tagged} (tagging_version > 0)")
                click.echo(f"  untagged: {untagged} (tagging_version == 0)")
                click.echo(f"  embedded: {embedded}")
                click.echo(f"  unembedded: {unembedded}")
                click.echo(f"  by_domain: {dom_str}")
                click.echo(
                    f"  proposals_pending: {proposals_pending} (beast_domain_proposal artifacts in GENERATED state)"
                )
                click.echo(f"  by_source: {by_src_str}")
            finally:
                conn.close()
        else:
            click.echo("corpus_turns: no state.db yet")
    except Exception as exc:
        click.echo(f"corpus_turns: not initialized or error ({exc})")

    # --- Wiki articles (beast_wiki artifacts) ---
    try:
        main_db_for_wiki = get_default_db_path()
        if Path(main_db_for_wiki).exists():
            conn_w = sqlite3.connect(f"file:{main_db_for_wiki}?mode=ro", uri=True)
            try:
                # Load active domains from registry to compute "domains without wiki"
                active_domains: list[str] = []
                try:
                    import sys as _sys

                    _sys.path.insert(0, "src")
                    from aip.orchestration.actors.domain_registry import load_registry as _load_reg

                    _reg = _load_reg("docs/beast_domain_registry_v1.md")
                    _excluded = {"quarantine", "unclassified"}
                    active_domains = [d for d in _reg.get_domain_ids() if d not in _excluded]
                except Exception:
                    pass

                total_wiki = (
                    conn_w.execute(
                        'SELECT COUNT(*) FROM artifacts WHERE metadata_json LIKE \'%"artifact_type": "beast_wiki"%\''
                    ).fetchone()[0]
                    or 0
                )

                # Per state counts require ECS — approximate from latest version metadata
                # Use the ecs_transitions table if available
                approved_wiki = 0
                generated_wiki = 0
                try:
                    # ecs_transitions: artifact_id, from_state, to_state
                    ecs_rows = conn_w.execute(
                        "SELECT artifact_id, to_state FROM ecs_transitions "
                        "WHERE artifact_id LIKE 'beast:wiki:%' ORDER BY id DESC"
                    ).fetchall()
                    # For each artifact, take last transition
                    last_state: dict[str, str] = {}
                    for aid, to_st in ecs_rows:
                        if aid not in last_state:
                            last_state[aid] = to_st
                    approved_wiki = sum(1 for s in last_state.values() if s == "APPROVED")
                    generated_wiki = sum(1 for s in last_state.values() if s == "GENERATED")
                except Exception:
                    pass

                # Domains with any wiki (GENERATED or APPROVED)
                wiki_domain_rows = conn_w.execute(
                    "SELECT DISTINCT json_extract(metadata_json, '$.domain') "
                    'FROM artifacts WHERE metadata_json LIKE \'%"artifact_type": "beast_wiki"%\''
                ).fetchall()
                domains_with_wiki = {r[0] for r in wiki_domain_rows if r[0]}
                domains_without = sorted([d for d in active_domains if d not in domains_with_wiki])

                click.echo("wiki_articles:")
                click.echo(f"  total_domains: {len(active_domains)}")
                click.echo(f"  articles_generated: {total_wiki} (GENERATED + APPROVED)")
                click.echo(f"  articles_approved: {approved_wiki} (APPROVED only)")
                click.echo(f"  articles_pending_review: {generated_wiki} (GENERATED only)")
                if domains_without:
                    click.echo(f"  domains_without_wiki: {domains_without}")
                else:
                    click.echo("  domains_without_wiki: (all domains have at least one wiki article)")
            finally:
                conn_w.close()
    except Exception as exc:
        click.echo(f"wiki_articles: error ({exc})")

    # --- Knowledge graph ---
    try:
        from aip.adapter.graph_store import GraphStore
        from aip.cli._db_path import get_default_db_path as _gdp

        _kg_path = _gdp()
        if Path(_kg_path).exists():

            async def _kg_status():
                _kg_store = GraphStore(_kg_path)
                _kg_nodes = await _kg_store.get_all_nodes()
                _kg_edges = await _kg_store.get_all_edges()
                await _kg_store.close()
                return _kg_nodes, _kg_edges

            _kg_nodes, _kg_edges = asyncio.run(asyncio.wait_for(_kg_status(), timeout=10))
            _by_source: dict[str, int] = {}
            _domain_nodes: set[str] = set()
            for _n in _kg_nodes:
                _by_source[_n.source] = _by_source.get(_n.source, 0) + 1
                if _n.entity_type == "DOMAIN":
                    _domain_nodes.add(_n.id)
            _bridge_edges = sum(1 for _e in _kg_edges if _e.bridge_tag is not None)
            _extracted_edges = sum(1 for _e in _kg_edges if _e.bridge_tag is None)
            click.echo("knowledge_graph:")
            click.echo(f"  nodes: {len(_kg_nodes)}")
            click.echo(f"  edges: {len(_kg_edges)}")
            click.echo(f"  bridge_edges: {_bridge_edges} (from bridge tags)")
            click.echo(f"  extracted_edges: {_extracted_edges} (from Beast extraction)")
            click.echo(f"  domains_in_graph: {len(_domain_nodes)}")
            click.echo(f"  by_source: {_by_source}")
    except asyncio.TimeoutError:
        click.echo("knowledge_graph: timed out (graph store query exceeded 10s)")
    except Exception as _kg_exc:
        click.echo(f"knowledge_graph: error ({_kg_exc})")

    # --- Summary ---
    click.echo(f"\nDatabases: {db_found}/{len(expected_dbs)} found, {db_total_rows} total rows")
    if db_found == 0:
        click.echo("Tip: run `aip init` to set up databases and config.")

    # --- Config validation ---
    if config_path.exists():
        try:
            from aip.cli.config import validate_config_file

            if not validate_config_file(config_path):
                click.echo("\n⚠  Config validation failed. Run `aip validate` for details.")
        except Exception:
            pass  # Validation is best-effort in status command
