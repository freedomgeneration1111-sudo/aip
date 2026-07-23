"""CLI commands for the turn-level corpus (aip corpus ...).

Provides ``aip corpus ingest`` for importing source conversation exports
and project documents into the CorpusTurnStore (the new atomic turn-level corpus).

Sprint 9: Enhanced to support documents (markdown, text, PDF), directories,
dedup detection, provenance tracking, and corpus audit commands.

This command is SEPARATE from the legacy ``aip ingest`` (which continues
to feed the old artifact + chunk pipeline for backward compat).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import click

from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.cli._db_path import ensure_db_dir, get_default_db_path

# For direct Beast tagging from CLI (beast_provider via resolver, dummies for vector/embed)
try:
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
except Exception:
    VersionedArtifactStore = None  # type: ignore

try:
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
except Exception:
    PersistentEcsStore = None  # type: ignore


@click.group("corpus")
def corpus() -> None:
    """Manage the turn-level AIP corpus (CorpusTurn as atomic unit).

    New ingestion path for source exports (Claude, GPT, etc.).
    Turns are complete user+assistant exchanges — the foundation for
    all future retrieval, Beast work, and knowledge.

    Existing `aip ingest` is untouched.
    """
    pass


@corpus.command("ingest")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--source-model",
    default=None,
    type=click.Choice(["claude", "gpt", "deepseek", "glm", "gemini", "grok", "document"], case_sensitive=False),
    help="Source model that produced the export (auto-detected if not specified).",
)
@click.option(
    "--source-account",
    default=None,
    help="Human identifier for this export batch (e.g. claude_export_june_2026).",
)
@click.option(
    "--export-date",
    default=None,
    help="ISO date of the export (defaults to today).",
)
@click.option(
    "--recursive/--no-recursive",
    default=False,
    help="Recurse into subdirectories (for directory ingestion).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_ingest_cmd(
    path: str,
    source_model: str | None,
    source_account: str | None,
    export_date: str | None,
    recursive: bool,
    db_path: str | None,
) -> None:
    """Ingest a source file or directory into the corpus.

    Sprint 9: Now supports documents (markdown, text, PDF) in addition to
    conversation exports (Claude, ChatGPT). Directories are scanned for
    supported files.

    Dedup: If content hasn't changed (same content_hash), the turn is skipped.
    Re-ingest: If content changed, doc_version is incremented.

    Examples:
      aip corpus ingest conversations.json --source-model claude
      aip corpus ingest docs/ARCHITECTURE.md
      aip corpus ingest docs/ --recursive
    """
    try:
        if not export_date:
            export_date = date.today().isoformat()
        if not source_account:
            source_account = "cli_ingest"

        resolved_db_path = db_path or get_default_db_path()

        from aip.orchestration.ingestion.corpus_ingest_pipeline import (
            CorpusIngestConfig,
            ingest_directory_to_corpus,
            ingest_file_to_corpus,
        )

        config = CorpusIngestConfig(
            source_model=source_model or "",
            source_account=source_account,
            export_date=export_date,
            db_path=resolved_db_path,
            recursive=recursive,
        )

        if os.path.isdir(path):
            # Directory ingestion
            async def _ingest_dir():
                store = CorpusTurnStore(db_path=resolved_db_path)
                await store.initialize()
                try:
                    results = await ingest_directory_to_corpus(path, store, config)
                    return results
                finally:
                    await store.close()

            results = asyncio.run(_ingest_dir())

            total_ingested = sum(r.turns_ingested for r in results)
            total_skipped = sum(r.turns_skipped for r in results)
            total_updated = sum(r.turns_updated for r in results)
            total_failed = sum(r.turns_failed for r in results)
            files_processed = len([r for r in results if r.source_type != "directory"])

            click.echo(f"Processed {files_processed} files from {path}")
            click.echo(f"  Ingested: {total_ingested} turns")
            if total_skipped:
                click.echo(f"  Skipped (unchanged): {total_skipped} turns")
            if total_updated:
                click.echo(f"  Updated (content changed): {total_updated} turns")
            if total_failed:
                click.echo(f"  Failed: {total_failed} turns")

        else:
            # Single file ingestion
            async def _ingest_file():
                store = CorpusTurnStore(db_path=resolved_db_path)
                await store.initialize()
                try:
                    return await ingest_file_to_corpus(path, store, config)
                finally:
                    await store.close()

            result = asyncio.run(_ingest_file())

            click.echo(f"Ingested {path}:")
            click.echo(f"  Type: {result.source_type}")
            click.echo(f"  New turns: {result.turns_ingested}")
            if result.turns_skipped:
                click.echo(f"  Skipped (unchanged): {result.turns_skipped}")
            if result.turns_updated:
                click.echo(f"  Updated (content changed): {result.turns_updated}")
            if result.turns_failed:
                click.echo(f"  Failed: {result.turns_failed}")

            if result.warnings:
                for w in result.warnings[:5]:
                    click.echo(f"  Warning: {w}")
            if result.errors:
                for e in result.errors[:5]:
                    click.echo(f"  Error: {e}", err=True)

        # Get total
        async def _get_total():
            store = CorpusTurnStore(db_path=resolved_db_path)
            await store.initialize()
            try:
                return await store.total_turns()
            finally:
                await store.close()

        total = asyncio.run(_get_total())
        click.echo(f"Corpus now contains {total} turns.")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# QW11 (2026-07-23): aip corpus ingest-code — populate the codeforge corpus
# ADR-008 §8 Chunk 7 / Phase 1.6 Codebase-as-Corpus
# ---------------------------------------------------------------------------


@corpus.command("ingest-code")
@click.argument("path", type=click.Path(exists=True, file_okay=False, dir_okay=True), required=False)
@click.option(
    "--db-path",
    default=None,
    help="SQLite database path for the codeforge corpus (default: derived from config or db/codeforge.db).",
)
@click.option(
    "--corpus-id",
    default="codeforge",
    help="Corpus ID to ingest into (default: codeforge).",
)
@click.option(
    "--force/--no-force",
    default=False,
    help="Re-ingest all files even if content_hash matches (default: skip unchanged).",
)
def corpus_ingest_code_cmd(
    path: str | None,
    db_path: str | None,
    corpus_id: str,
    force: bool,
) -> None:
    """Ingest a Python source directory into the codeforge corpus.

    QW11 (2026-07-23) — ADR-008 §8 Chunk 7 / Phase 1.6 Codebase-as-Corpus.
    Walks PATH recursively, parses each .py file with the AST parser,
    and writes CorpusTurns to the codeforge corpus. Uses content_hash
    for stale detection — unchanged turns are skipped (unless --force).

    If PATH is not given, defaults to 'src/aip/' (AIP's own source tree),
    enabling the "AIP asks about AIP" self-referential use case.

    The codeforge corpus is registered empty at startup (QW1). This
    command populates it. For real-time updates, see QW13 (Sexton
    file-watcher, planned).

    Examples:
      aip corpus ingest-code                    # ingest src/aip/ into codeforge
      aip corpus ingest-code /path/to/repo      # ingest a different repo
      aip corpus ingest-code --force            # re-ingest all files
    """
    try:
        source_dir = Path(path) if path else Path("src/aip")
        if not source_dir.exists():
            click.echo(f"Error: source directory not found: {source_dir}", err=True)
            click.echo("Specify a path, or run from the AIP_Brain root (defaults to src/aip/).", err=True)
            sys.exit(1)
        if not source_dir.is_dir():
            click.echo(f"Error: {source_dir} is not a directory.", err=True)
            sys.exit(1)

        # Derive the codeforge db path from the main db path's directory.
        # The main db is db/state.db; codeforge.db lives alongside it.
        main_db_path = db_path or get_default_db_path()
        db_dir = os.path.dirname(main_db_path)
        codeforge_db = os.path.join(db_dir, f"{corpus_id}.db")
        ensure_db_dir(codeforge_db)

        click.echo(f"Ingesting Python source from: {source_dir.resolve()}")
        click.echo(f"Target corpus: {corpus_id} ({codeforge_db})")
        click.echo(f"Force re-ingest: {force}")
        click.echo("")

        from aip.adapter.code_ingest_pipeline import ingest_python_directory
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        async def _run_ingest() -> dict[str, int]:
            turn_store = CorpusTurnStore(codeforge_db)
            try:
                # Ensure the schema exists (idempotent — safe if the
                # app server already created the db via the registry).
                await turn_store.initialize()
                counts = await ingest_python_directory(
                    source_dir=source_dir,
                    turn_store=turn_store,
                    corpus_id=corpus_id,
                    skip_existing=not force,
                )
                return counts
            finally:
                await turn_store.close()

        counts = asyncio.run(_run_ingest())

        click.echo("Ingest complete:")
        click.echo(f"  Files scanned:    {counts['files_scanned']}")
        click.echo(f"  Files skipped:    {counts['files_skipped']} (.pyi, test_*, etc.)")
        click.echo(f"  Files parsed:     {counts['files_parsed']}")
        click.echo(f"  Turns created:    {counts['turns_created']}")
        click.echo(f"  Skipped (stale):  {counts['turns_skipped_stale']}")
        click.echo(f"  Superseded:       {counts['turns_superseded']}")
        click.echo("")
        click.echo(f"Corpus '{corpus_id}' now searchable. Restart the AIP server if running")
        click.echo("to pick up the new corpus content in retrieval.")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@corpus.command("tag")
@click.option(
    "--limit",
    default=200,
    type=int,
    help="Max turns to tag this run (default 200). No upper limit enforced "
    "for manual sessions (pass large values at your own risk/cost).",
)
@click.option(
    "--retag",
    is_flag=True,
    default=False,
    help="If set, also re-tag turns that already have tagging_version > 0 (low-importance first).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_tag_cmd(limit: int, retag: bool, db_path: str | None) -> None:
    """Run Beast turn tagging directly (batch 8 turns per LLM call to the beast slot).

    Loads the domain registry, pulls untagged (or retag) turns, calls the beast
    provider with the exact mandated prompt, parses/validates results, updates
    via update_beast_tags (increments tagging_version), and files any proposals
    as GENERATED beast_domain_proposal artifacts.

    This is the manual trigger for initial corpus tagging or spot re-tagging.
    Background scheduler will also tag up to 200 untagged per cycle when a
    beast_provider is configured.
    """
    if limit < 1:
        limit = 1

    resolved_db_path = db_path or get_default_db_path()

    async def _run_tagging():
        # Resolve beast_provider exactly like app lifespan (ModelSlotResolver + config/env)
        import tomllib
        from pathlib import Path

        from aip.adapter.model_slot_resolver import ModelSlotResolver
        from aip.foundation.schemas import BeastCadenceConfig
        from aip.orchestration.actors.beast import Beast

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)

        resolver = None
        try:
            resolver = ModelSlotResolver(cfg)
            bslot = cfg.get("models", {}).get("beast", {}) if isinstance(cfg.get("models"), dict) else {}
            if not (isinstance(bslot, dict) and bslot.get("provider") and bslot.get("model")):
                resolver = None
        except Exception as exc:
            click.echo(f"Warning: beast slot resolver failed ({exc}); tagging will be skipped if no provider.")
            resolver = None

        if resolver is None:
            click.echo(
                "No [models.beast] slot with model configured (or AIP_BEAST_* env). "
                "Tagging requires a beast LLM. Configure and retry.",
                err=True,
            )
            # Still proceed to exercise registry etc; _run will return skipped.

        # Stores (CorpusTurnStore required; artifact+ecs so proposals become GENERATED)
        cts = CorpusTurnStore(db_path=resolved_db_path)
        await cts.initialize()

        arts = None
        if VersionedArtifactStore is not None:
            try:
                arts = VersionedArtifactStore(db_path=resolved_db_path)
                await arts.initialize()
            except Exception:
                arts = None

        ecs = None
        if PersistentEcsStore is not None:
            try:
                ecs = PersistentEcsStore(db_path=resolved_db_path, event_store=None)
                await ecs.initialize()
            except Exception:
                ecs = None

        # Minimal dummies so Beast ctor succeeds (tagging path does not exercise maint/embed)
        class _DummyVectorStore:
            async def health_check(self) -> dict:
                return {"connected": True}

            async def list_stale_vectors(self, **kwargs: Any) -> list[dict]:
                return []

            async def upsert(self, **kwargs: Any) -> None:
                return None

        class _DummyEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                # 8-dim fake; never used by tagging
                return [0.0] * 8

        dummy_vec = _DummyVectorStore()
        dummy_emb = _DummyEmbeddingProvider()

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=dummy_vec,
            embedding_provider=dummy_emb,
            beast_provider=resolver,
            artifact_store=arts,
            ecs_store=ecs,
            corpus_turn_store=cts,
        )

        try:
            total = await cts.total_turns()
            # probe for "X of T total" (even if 0, shows intent)
            probe = await cts.get_untagged_turns(limit=1)
            # Load here too for the exact mandated CLI UX line (count + connectors)
            try:
                from aip.orchestration.actors.domain_registry import load_registry

                reg = load_registry("docs/beast_domain_registry_v1.md")
                click.echo(f"Loading domain registry... {len(reg.domains)} domains, {len(reg.connectors)} connectors")
            except Exception as e:
                click.echo(f"Loading domain registry... (error: {e})")
            click.echo(f"Getting untagged turns... {len(probe) or 0} of {total} total (cap {limit})")

            stats = await beast._run_turn_tagging(limit=limit, retag=retag)

            tagged_n = stats.get("turns_tagged", 0)
            failed_n = stats.get("turns_failed", 0)
            prop_n = stats.get("proposals_filed", 0)
            click.echo(f"Tagged {tagged_n} turns. {failed_n} failed. {prop_n} proposals filed.")

            dist = stats.get("domain_distribution", {}) or {}
            if dist:
                # sort desc, show all or top
                top = sorted(dist.items(), key=lambda kv: -kv[1])
                dist_str = ", ".join(f"{d}:{c}" for d, c in top)
                click.echo(f"Domain distribution: {dist_str}")
            else:
                click.echo("Domain distribution: (none this run)")

            if stats.get("note"):
                click.echo(f"Note: {stats['note']}")
            if stats.get("skipped"):
                click.echo(f"Skipped: {stats['skipped']}")
            return stats
        finally:
            await cts.close()

    try:
        asyncio.run(_run_tagging())
    except Exception as exc:
        click.echo(f"Error during tagging: {exc}", err=True)
        sys.exit(1)


@corpus.command("wiki")
@click.option("--domain", default=None, help="Generate wiki for one specific domain.")
@click.option("--force", is_flag=True, default=False, help="Regenerate even if wiki exists and is recent.")
@click.option(
    "--all", "all_domains", is_flag=True, default=False, help="Generate for all domains (default if no --domain)."
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_wiki_cmd(domain: str | None, force: bool, all_domains: bool, db_path: str | None) -> None:
    """Generate domain wiki articles from tagged corpus turns.

    Beast generates one wiki article per active domain using top-importance
    tagged turns as evidence. Articles go into GENERATED state for DEFINER
    review before becoming canonical.

    Examples:
      uv run aip corpus wiki --domain aip
      uv run aip corpus wiki --domain theology_research --force
      uv run aip corpus wiki --all
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run_wiki():
        import tomllib
        from pathlib import Path

        from aip.adapter.model_slot_resolver import ModelSlotResolver
        from aip.foundation.schemas import BeastCadenceConfig
        from aip.orchestration.actors.beast import Beast

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)

        resolver = None
        try:
            resolver = ModelSlotResolver(cfg)
            bslot = cfg.get("models", {}).get("beast", {}) if isinstance(cfg.get("models"), dict) else {}
            if not (isinstance(bslot, dict) and bslot.get("provider") and bslot.get("model")):
                resolver = None
        except Exception as exc:
            click.echo(f"Warning: beast slot resolver failed ({exc})", err=True)
            resolver = None

        if resolver is None:
            click.echo(
                "No [models.beast] slot with model configured. "
                "Wiki generation requires a beast LLM. Configure and retry.",
                err=True,
            )
            sys.exit(1)

        cts = CorpusTurnStore(db_path=resolved_db_path)
        await cts.initialize()

        arts = None
        if VersionedArtifactStore is not None:
            try:
                arts = VersionedArtifactStore(db_path=resolved_db_path)
                await arts.initialize()
            except Exception:
                arts = None

        ecs = None
        if PersistentEcsStore is not None:
            try:
                ecs = PersistentEcsStore(db_path=resolved_db_path, event_store=None)
                await ecs.initialize()
            except Exception:
                ecs = None

        class _DummyVectorStore:
            async def health_check(self) -> dict:
                return {"connected": True}

            async def list_stale_vectors(self, **kwargs: Any) -> list[dict]:
                return []

            async def upsert(self, **kwargs: Any) -> None:
                return None

        class _DummyEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                return [0.0] * 8

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=_DummyVectorStore(),
            embedding_provider=_DummyEmbeddingProvider(),
            beast_provider=resolver,
            artifact_store=arts,
            ecs_store=ecs,
            corpus_turn_store=cts,
        )

        try:
            force_domains = [domain] if domain else None
            # CLI --all or no --domain: no cap
            max_per = 9999 if (all_domains or domain is None) else 1

            if force:
                # Pass force_domains even for --all to trigger force logic
                if force_domains is None:
                    # Load registry to get all domain ids
                    try:
                        from aip.orchestration.actors.domain_registry import load_registry

                        reg = load_registry("docs/beast_domain_registry_v1.md")
                        excluded = {"quarantine", "unclassified"}
                        force_domains = [d for d in reg.get_domain_ids() if d not in excluded]
                    except Exception:
                        pass  # will still work; force handled inside _run_wiki_generation

            stats = await beast._run_wiki_generation(
                force_domains=force_domains if force else (force_domains),
                max_per_cycle=max_per,
            )

            gen_n = stats.get("domains_generated", 0)
            skip_n = stats.get("domains_skipped", 0)
            err_n = stats.get("errors", 0)
            click.echo(f"Generated {gen_n} wiki articles. {skip_n} skipped (recent).")
            if err_n:
                click.echo(f"Errors: {err_n}", err=True)
            if stats.get("skipped"):
                click.echo(f"Skipped reason: {stats['skipped']}")
        finally:
            await cts.close()

    try:
        asyncio.run(_run_wiki())
    except SystemExit:
        raise
    except Exception as exc:
        click.echo(f"Error during wiki generation: {exc}", err=True)
        sys.exit(1)


@corpus.command("clear-vectors")
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Required to confirm deletion of all vectors.",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_clear_vectors_cmd(confirm: bool, db_path: str | None) -> None:
    """Clear all vectors from the vector store (vectors.db).

    This removes stale or old vectors (e.g. from previous chunked ingestion).
    Vectors will be re-embedded by the embedding pipeline.
    Requires --confirm flag (no interactive prompt).
    """
    if not confirm:
        click.echo("Error: --confirm flag is required. This will delete ALL vectors.", err=True)
        click.echo("Usage: uv run aip corpus clear-vectors --confirm", err=True)
        sys.exit(1)

    async def _clear():
        import sqlite3
        import tomllib
        from pathlib import Path

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)
        vec_db = cfg.get("vector_backend", {}).get("db_path", "db/vectors.db")
        conn = sqlite3.connect(vec_db)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM vector_metadata")
            before = cur.fetchone()[0] or 0
            conn.execute("DELETE FROM vector_metadata")
            try:
                conn.execute("DELETE FROM vss_vectors")
            except Exception:
                pass  # no vss table or not available
            conn.commit()
            click.echo(f"Cleared {before} vectors.")
        finally:
            conn.close()

        # Also reset embedded flags in main db so status reflects 0 after clear
        try:
            from aip.cli._db_path import get_default_db_path

            main_db = db_path or get_default_db_path()
            connm = sqlite3.connect(main_db)
            try:
                connm.execute("UPDATE corpus_turns SET embedded = 0")
                connm.commit()
            finally:
                connm.close()
        except Exception:
            pass  # best effort

    try:
        asyncio.run(_clear())
    except Exception as exc:
        click.echo(f"Error clearing vectors: {exc}", err=True)
        sys.exit(1)


@corpus.command("embed")
@click.option(
    "--limit",
    default=500,
    type=int,
    help="Max turns to embed this run (default 500).",
)
@click.option(
    "--reembed",
    is_flag=True,
    default=False,
    help="If set, re-embed turns that already have embedded=1.",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_embed_cmd(limit: int, reembed: bool, db_path: str | None) -> None:
    """Run corpus embedding pass directly (batch ~32 turns per embedding call)."""
    if limit < 1:
        limit = 1

    resolved_db_path = db_path or get_default_db_path()

    async def _run_embed():
        import tomllib
        from pathlib import Path

        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.orchestration.actors.beast import Beast

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)

        eslot = cfg.get("models", {}).get("embedding", {}) if isinstance(cfg.get("models"), dict) else {}
        if not (isinstance(eslot, dict) and eslot.get("provider") and eslot.get("model")):
            click.echo(
                "No [models.embedding] slot configured (or AIP_EMBEDDING_* env). "
                "Embedding requires an embedding model. Configure and retry.",
                err=True,
            )
            sys.exit(1)

        cts = CorpusTurnStore(db_path=resolved_db_path)
        await cts.initialize()

        from aip.adapter.api.app import _create_embedding_provider

        embedding_provider = _create_embedding_provider(cfg)
        if embedding_provider is None:
            click.echo("Failed to create embedding provider.", err=True)
            sys.exit(1)

        class _DummyVectorStore:
            async def health_check(self):
                return {"connected": True}

            async def list_stale_vectors(self, **k):
                return []

            async def upsert(self, **k):
                return None

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        from aip.foundation.schemas import BeastCadenceConfig

        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=_DummyVectorStore(),
            embedding_provider=embedding_provider,
            corpus_turn_store=cts,
        )

        try:
            total = await cts.total_turns()
            click.echo(f"Loading embedding model... {eslot.get('model', 'unknown')}")
            if reembed:
                unemb = total
            else:
                unemb = await cts.count_unembedded()
            click.echo(f"Getting unembedded turns... {unemb} total (cap {limit})")

            stats = await beast._run_embedding_pass(limit=limit, reembed=reembed)

            emb_n = stats.get("embedded", 0)
            failed_n = stats.get("failed", 0)
            skipped_n = stats.get("skipped", 0)
            click.echo(f"Embedded {emb_n} turns. {failed_n} failed. {skipped_n} skipped.")

            return stats
        finally:
            await cts.close()

    try:
        asyncio.run(_run_embed())
    except Exception as exc:
        click.echo(f"Error during embedding: {exc}", err=True)
        sys.exit(1)


@corpus.command("graph")
@click.option(
    "--build-from-bridges",
    "build_bridges",
    is_flag=True,
    default=False,
    help="Build seed graph from bridge tags in corpus_turns + entity alias registry.",
)
@click.option("--extract", is_flag=True, default=False, help="Run Beast entity extraction on high-importance turns.")
@click.option("--limit", default=50, type=int, help="Max turns to extract (for --extract).")
@click.option("--stats", is_flag=True, default=False, help="Show graph node/edge counts.")
@click.option("--neighbors", "neighbors_node", default=None, help="Show direct neighbors of a domain/node.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_graph_cmd(
    build_bridges: bool,
    extract: bool,
    limit: int,
    stats: bool,
    neighbors_node: str | None,
    db_path: str | None,
) -> None:
    """Build and query the knowledge graph.

    Phase 1: bridge tag seed graph (no LLM). Fast.
    Phase 2: Beast entity extraction on high-importance turns (LLM).

    Examples:
      uv run aip corpus graph --build-from-bridges
      uv run aip corpus graph --extract --limit 20
      uv run aip corpus graph --stats
      uv run aip corpus graph --neighbors nbcm
    """
    if not any([build_bridges, extract, stats, neighbors_node is not None]):
        click.echo("Specify an action: --build-from-bridges, --extract, --stats, or --neighbors DOMAIN")
        sys.exit(1)

    resolved_db_path = db_path or get_default_db_path()

    from aip.adapter.graph_store import GraphStore

    if stats:
        try:

            async def _stats():
                store = GraphStore(resolved_db_path)
                h = await store.health_check()
                click.echo("knowledge_graph:")
                click.echo(f"  nodes: {h.get('nodes', 0)}")
                click.echo(f"  edges: {h.get('edges', 0)}")
                nodes = await store.get_all_nodes()
                edges = await store.get_all_edges()
                by_type: dict[str, int] = {}
                by_src: dict[str, int] = {}
                for n in nodes:
                    by_type[n.entity_type] = by_type.get(n.entity_type, 0) + 1
                    by_src[n.source] = by_src.get(n.source, 0) + 1
                for k, v in sorted(by_type.items()):
                    click.echo(f"  {k}: {v}")
                click.echo(f"  by_source: {by_src}")
                bridge_edges = sum(1 for e in edges if e.bridge_tag is not None)
                click.echo(f"  bridge_edges: {bridge_edges}")
                await store.close()

            asyncio.run(_stats())
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        return

    if neighbors_node is not None:
        try:

            async def _neighbors():
                store = GraphStore(resolved_db_path)
                neighbors = await store.get_neighbors(neighbors_node, min_confidence=0.0)
                if not neighbors:
                    click.echo(f"No neighbors found for '{neighbors_node}' (node may not exist or have no edges).")
                else:
                    click.echo(f"Neighbors of '{neighbors_node}':")
                    for n in neighbors:
                        click.echo(f"  {n.entity_type:12s} {n.canonical_name} (confidence: {n.confidence:.2f})")
                await store.close()

            asyncio.run(_neighbors())
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        return

    if build_bridges:
        _run_build_from_bridges(resolved_db_path)

    if extract:
        _run_graph_extract(resolved_db_path, limit)


def _run_build_from_bridges(db_path: str) -> None:
    """Build seed graph from bridge tags and entity alias registry."""
    import sqlite3

    from aip.adapter.entity_alias_loader import EntityAliasRegistry
    from aip.adapter.graph_store import GraphEdge, GraphNode, GraphStore

    async def _build():
        store = GraphStore(db_path)
        await store.initialize()

        # Load entity alias registry
        alias_path = "docs/entity_aliases.md"
        registry = EntityAliasRegistry(alias_path)
        click.echo(f"Loading entity aliases... {len(registry)} entries")

        # Seed nodes from alias registry
        alias_nodes = 0
        for cn in registry.all_canonical_names():
            entry = registry.get_entry(cn)
            if entry is None:
                continue
            node_id = cn.lower().replace(" ", "_")
            node = GraphNode(
                id=node_id,
                entity_type=entry.entity_type,
                canonical_name=cn,
                domain=entry.domain or None,
                confidence=1.0,
                source="manual",
                aliases=entry.aliases,
            )
            await store.upsert_node(node)
            alias_nodes += 1

        click.echo("Processing bridge tags from corpus...")

        # Read all bridge-tagged turns
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT turn_id, bridges FROM corpus_turns WHERE bridges != '[]'").fetchall()
        finally:
            conn.close()

        click.echo(f"Found {len(rows)} turns with bridge tags")

        # Collect bridge evidence: bridge_tag -> list of turn_ids
        bridge_evidence: dict[str, list[str]] = {}
        for turn_id, bridges_json in rows:
            try:
                bridges = json.loads(bridges_json or "[]")
            except Exception:
                continue
            for tag in bridges:
                tag = tag.strip()
                if "->" not in tag:
                    continue
                if tag not in bridge_evidence:
                    bridge_evidence[tag] = []
                bridge_evidence[tag].append(turn_id)

        # Build domain nodes and edges from bridge tags
        domain_nodes_created = 0
        bridge_edges_created = 0

        for bridge_tag, turn_ids in bridge_evidence.items():
            parts = bridge_tag.split("->", 1)
            if len(parts) != 2:
                continue
            domain_a = parts[0].strip()
            domain_b = parts[1].strip()

            for dom in (domain_a, domain_b):
                # Create domain node if it doesn't exist
                existing = await store.get_node(dom)
                if existing is None:
                    node = GraphNode(
                        id=dom,
                        entity_type="DOMAIN",
                        canonical_name=dom,
                        domain=dom,
                        confidence=1.0,
                        source="bridge",
                    )
                    await store.upsert_node(node)
                    domain_nodes_created += 1

            # Create or update the bridge edge (weight = number of turns)
            edge_id = f"{domain_a}__CONNECTS__{domain_b}"
            existing_edge = None
            try:
                # Check if edge exists to accumulate evidence
                all_edges = await store.get_all_edges()
                for e in all_edges:
                    if e.id == edge_id:
                        existing_edge = e
                        break
            except Exception:
                pass

            all_evidence = list({*turn_ids, *(existing_edge.evidence_turn_ids if existing_edge else [])})
            edge = GraphEdge(
                id=edge_id,
                source_id=domain_a,
                target_id=domain_b,
                relationship_type="CONNECTS",
                bridge_tag=bridge_tag,
                confidence=1.0,
                evidence_turn_ids=all_evidence,
                weight=float(len(all_evidence)),
            )
            await store.upsert_edge(edge)
            bridge_edges_created += 1

        total_nodes = await store.node_count()
        total_edges = await store.edge_count()

        click.echo(f"Created {domain_nodes_created} domain nodes from bridge tags")
        click.echo(f"Created/updated {bridge_edges_created} bridge edges")
        click.echo(f"Entity alias nodes: {alias_nodes} nodes seeded from entity_aliases.md")
        click.echo(f"Built seed graph: {total_nodes} nodes, {total_edges} edges")

        if any("aip_methodology" in tag for tag in bridge_evidence.keys()):
            click.echo("")
            click.echo("Note: aip_methodology bridge nodes are pre-rename artifacts.")
            click.echo("  Run 'aip corpus graph --merge-nodes aip_methodology aip'")
            click.echo("  after full corpus retag to consolidate.")

        await store.close()

    asyncio.run(_build())


def _run_graph_extract(db_path: str, limit: int) -> None:
    """Run Beast entity extraction on high-importance turns via CLI."""
    import asyncio as _asyncio
    import tomllib
    from pathlib import Path

    async def _extract_async():
        from aip.adapter.model_slot_resolver import ModelSlotResolver
        from aip.foundation.schemas import BeastCadenceConfig
        from aip.orchestration.actors.beast import Beast

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)

        resolver = None
        try:
            resolver = ModelSlotResolver(cfg)
            bslot = cfg.get("models", {}).get("beast", {}) if isinstance(cfg.get("models"), dict) else {}
            if not (isinstance(bslot, dict) and bslot.get("provider") and bslot.get("model")):
                resolver = None
        except Exception as exc:
            click.echo(f"Warning: beast slot resolver failed ({exc})", err=True)
            resolver = None

        if resolver is None:
            click.echo("No [models.beast] slot configured. Entity extraction requires a beast LLM.", err=True)
            sys.exit(1)

        cts = CorpusTurnStore(db_path=db_path)
        await cts.initialize()

        arts = None
        if VersionedArtifactStore is not None:
            try:
                arts = VersionedArtifactStore(db_path=db_path)
                await arts.initialize()
            except Exception:
                arts = None

        ecs = None
        if PersistentEcsStore is not None:
            try:
                ecs = PersistentEcsStore(db_path=db_path, event_store=None)
                await ecs.initialize()
            except Exception:
                ecs = None

        class _DummyVectorStore:
            async def health_check(self) -> dict:
                return {"connected": True}

            async def list_stale_vectors(self, **kw: Any) -> list[dict]:
                return []

            async def upsert(self, **kw: Any) -> None:
                return None

        class _DummyEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                return [0.0] * 8

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=_DummyVectorStore(),
            embedding_provider=_DummyEmbeddingProvider(),
            beast_provider=resolver,
            artifact_store=arts,
            ecs_store=ecs,
            corpus_turn_store=cts,
        )

        try:
            stats = await beast._run_graph_extraction(limit=limit)
            turns_n = stats.get("turns_processed", 0)
            entities_n = stats.get("entities_created", 0)
            rels_n = stats.get("relationships_created", 0)
            click.echo(f"Processed {turns_n} turns. Created {entities_n} entities, {rels_n} relationships.")
            if stats.get("skipped"):
                click.echo(f"Skipped: {stats['skipped']}")
        finally:
            await cts.close()

    try:
        _asyncio.run(_extract_async())
    except SystemExit:
        raise
    except Exception as exc:
        click.echo(f"Error during graph extraction: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Sprint 6.3: Corpus integrity verification
# ---------------------------------------------------------------------------


@corpus.command("verify")
@click.option(
    "--repair",
    is_flag=True,
    default=False,
    help="Attempt to repair inconsistencies found (reset orphaned embedded flags, clear orphaned vectors).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_verify_cmd(repair: bool, db_path: str | None) -> None:
    """Verify corpus integrity across corpus_turns, vector store, and FTS5 indexes.

    Checks for:
    1. Orphaned embedded flags: turns marked embedded=1 but no vector in the vector store.
    2. Orphaned vectors: vectors in the vector store with no matching corpus_turn.
    3. Missing FTS5 entries: turns in corpus_turns but not in the FTS5 index.
    4. Stale re-embed flags: turns marked needs_reembed=1 that should have been processed.
    5. Model consistency: turns embedded with different models than the current one.

    Use --repair to automatically fix safe-to-repair inconsistencies:
    - Reset orphaned embedded flags (embedded=1 but no vector) back to embedded=0.
    - Clear orphaned vectors (no matching turn) from the vector store.

    Examples:
      uv run aip corpus verify
      uv run aip corpus verify --repair
    """
    import sqlite3 as _sqlite3
    import tomllib
    from pathlib import Path

    resolved_db_path = db_path or get_default_db_path()

    # Determine vector DB path from config
    cfg: dict = {}
    config_p = Path("config/aip.config.toml")
    if config_p.exists():
        with open(config_p, "rb") as f:
            cfg = tomllib.load(f)
    vec_db_path = cfg.get("vector_backend", {}).get("db_path", "db/vectors.db")

    issues_found = 0
    issues_repaired = 0

    # --- Check 1: Orphaned embedded flags ---
    click.echo("Checking for orphaned embedded flags (embedded=1 but no vector)...")

    # Get all embedded turn IDs from corpus_turns
    main_conn = _sqlite3.connect(resolved_db_path)
    main_conn.row_factory = _sqlite3.Row
    try:
        embedded_rows = main_conn.execute(
            "SELECT turn_id, embedding_model FROM corpus_turns WHERE embedded = 1"
        ).fetchall()
        embedded_ids = {row["turn_id"]: row["embedding_model"] for row in embedded_rows}
    finally:
        main_conn.close()

    # Get all vector IDs from vector store
    vec_ids: set[str] = set()
    try:
        vec_conn = _sqlite3.connect(vec_db_path)
        vec_conn.row_factory = _sqlite3.Row
        try:
            vec_rows = vec_conn.execute("SELECT id FROM vector_metadata").fetchall()
            vec_ids = {row["id"] for row in vec_rows}
        finally:
            vec_conn.close()
    except Exception as exc:
        click.echo(f"  Warning: Could not read vector store: {exc}", err=True)
        click.echo("  Skipping vector-related checks.")
        vec_ids = set()

    # Orphaned embedded: marked embedded=1 but no vector
    orphaned_embedded = {tid for tid in embedded_ids if tid not in vec_ids}
    if orphaned_embedded:
        issues_found += len(orphaned_embedded)
        click.echo(f"  FOUND: {len(orphaned_embedded)} turns marked embedded=1 but have no vector")
        if len(orphaned_embedded) <= 10:
            for tid in sorted(orphaned_embedded):
                click.echo(f"    - {tid} (model: {embedded_ids.get(tid, 'unknown')})")
        else:
            for tid in sorted(orphaned_embedded)[:5]:
                click.echo(f"    - {tid} (model: {embedded_ids.get(tid, 'unknown')})")
            click.echo(f"    ... and {len(orphaned_embedded) - 5} more")

        if repair:
            main_conn = _sqlite3.connect(resolved_db_path)
            try:
                cursor = main_conn.execute(
                    "UPDATE corpus_turns SET embedded = 0, needs_reembed = 0 WHERE embedded = 1 AND turn_id IN "
                    f"({','.join('?' * len(orphaned_embedded))})",
                    list(orphaned_embedded),
                )
                main_conn.commit()
                repaired = cursor.rowcount
                issues_repaired += repaired
                click.echo(f"  REPAIRED: Reset {repaired} orphaned embedded flags to embedded=0")
            finally:
                main_conn.close()
    else:
        click.echo("  OK: No orphaned embedded flags found")

    # --- Check 2: Orphaned vectors ---
    click.echo("Checking for orphaned vectors (vector exists but no corpus_turn)...")

    if vec_ids:
        corpus_ids: set[str] = set()
        main_conn = _sqlite3.connect(resolved_db_path)
        try:
            rows = main_conn.execute("SELECT turn_id FROM corpus_turns").fetchall()
            corpus_ids = {row[0] for row in rows}
        finally:
            main_conn.close()

        orphaned_vectors = {vid for vid in vec_ids if vid not in corpus_ids}
        if orphaned_vectors:
            issues_found += len(orphaned_vectors)
            click.echo(f"  FOUND: {len(orphaned_vectors)} vectors with no matching corpus turn")
            if len(orphaned_vectors) <= 10:
                for vid in sorted(orphaned_vectors):
                    click.echo(f"    - {vid}")
            else:
                for vid in sorted(orphaned_vectors)[:5]:
                    click.echo(f"    - {vid}")
                click.echo(f"    ... and {len(orphaned_vectors) - 5} more")

            if repair:
                vec_conn = _sqlite3.connect(vec_db_path)
                try:
                    cursor = vec_conn.execute(
                        f"DELETE FROM vector_metadata WHERE id IN ({','.join('?' * len(orphaned_vectors))})",
                        list(orphaned_vectors),
                    )
                    try:
                        vec_conn.execute(
                            "DELETE FROM vss_vectors WHERE rowid IN "
                            "(SELECT rowid FROM vector_metadata WHERE id IN "
                            f"({','.join('?' * len(orphaned_vectors))}))",
                            list(orphaned_vectors),
                        )
                    except Exception:
                        pass  # vss table may not exist
                    vec_conn.commit()
                    repaired = cursor.rowcount
                    issues_repaired += repaired
                    click.echo(f"  REPAIRED: Deleted {repaired} orphaned vectors")
                finally:
                    vec_conn.close()
        else:
            click.echo("  OK: No orphaned vectors found")
    else:
        click.echo("  SKIPPED: Vector store not accessible")

    # --- Check 3: FTS5 consistency ---
    click.echo("Checking FTS5 index consistency...")

    main_conn = _sqlite3.connect(resolved_db_path)
    try:
        # Count corpus_turns vs FTS5 entries
        ct_count = main_conn.execute("SELECT COUNT(*) FROM corpus_turns").fetchone()[0]
        fts_count = main_conn.execute("SELECT COUNT(*) FROM corpus_turns_fts").fetchone()[0]

        if ct_count != fts_count:
            issues_found += 1
            diff = ct_count - fts_count
            click.echo(f"  FOUND: corpus_turns has {ct_count} rows but FTS5 has {fts_count} entries (diff: {diff})")
            if repair:
                # Rebuild FTS5 index by deleting and re-inserting
                click.echo("  REPAIR: Rebuilding FTS5 index...")
                main_conn.execute("DELETE FROM corpus_turns_fts")
                main_conn.execute(
                    "INSERT INTO corpus_turns_fts(rowid, turn_id, conversation_name, searchable_text, primary_domain) "
                    "SELECT rowid, turn_id, conversation_name, searchable_text, primary_domain FROM corpus_turns"
                )
                main_conn.commit()
                new_fts = main_conn.execute("SELECT COUNT(*) FROM corpus_turns_fts").fetchone()[0]
                issues_repaired += 1
                click.echo(f"  REPAIRED: FTS5 rebuilt — now {new_fts} entries (was {fts_count})")
        else:
            click.echo(f"  OK: FTS5 index consistent ({fts_count} entries)")
    except Exception as exc:
        click.echo(f"  Warning: FTS5 check failed: {exc}", err=True)
    finally:
        main_conn.close()

    # --- Check 4: Stale re-embed flags ---
    click.echo("Checking for stale re-embed flags...")

    main_conn = _sqlite3.connect(resolved_db_path)
    try:
        stale_count = main_conn.execute(
            "SELECT COUNT(*) FROM corpus_turns WHERE needs_reembed = 1 AND embedded = 1"
        ).fetchone()[0]
        if stale_count > 0:
            issues_found += 1
            click.echo(f"  FOUND: {stale_count} turns with both needs_reembed=1 AND embedded=1 (contradictory)")
            if repair:
                cursor = main_conn.execute(
                    "UPDATE corpus_turns SET embedded = 0 WHERE needs_reembed = 1 AND embedded = 1"
                )
                main_conn.commit()
                issues_repaired += cursor.rowcount
                click.echo(f"  REPAIRED: Reset {cursor.rowcount} contradictory turns to embedded=0")
        else:
            click.echo("  OK: No contradictory re-embed flags")

        # Also check for very old needs_reembed flags (stale >7 days)
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        week_ago = _dt.now(_tz.utc).isoformat()
        stale_old = main_conn.execute(
            "SELECT COUNT(*) FROM corpus_turns WHERE needs_reembed = 1 AND updated_at < ?",
            (week_ago,),
        ).fetchone()[0]
        if stale_old > 0:
            click.echo(
                f"  NOTE: {stale_old} turns with needs_reembed=1 older than current session "
                "(may indicate interrupted re-embed pass)"
            )
    finally:
        main_conn.close()

    # --- Check 5: Model consistency ---
    click.echo("Checking embedding model consistency...")

    main_conn = _sqlite3.connect(resolved_db_path)
    try:
        model_dist = main_conn.execute(
            "SELECT embedding_model, COUNT(*) as c FROM corpus_turns "
            "WHERE embedded = 1 GROUP BY embedding_model ORDER BY c DESC"
        ).fetchall()
        if model_dist:
            models = {row[0] or "(empty)": row[1] for row in model_dist}
            if len(models) > 1:
                click.echo(f"  NOTE: Multiple embedding models in use: {models}")
                click.echo("  This is normal after a model change. Re-embedding should converge.")
            else:
                model_name = list(models.keys())[0]
                click.echo(f"  OK: Single embedding model: {model_name} ({list(models.values())[0]} turns)")
        else:
            click.echo("  OK: No embedded turns yet")
    finally:
        main_conn.close()

    # --- Summary ---
    click.echo("")
    if issues_found == 0:
        click.echo("Corpus integrity: OK — no inconsistencies found.")
    else:
        click.echo(f"Corpus integrity: {issues_found} issue(s) found.")
        if repair:
            click.echo(f"  {issues_repaired} issue(s) repaired.")
        else:
            click.echo("  Run with --repair to fix safe-to-repair issues.")


# ---------------------------------------------------------------------------
# Sprint 9: Corpus status, audit, backfill, list commands
# ---------------------------------------------------------------------------


@corpus.command("status")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_status_cmd(db_path: str | None) -> None:
    """Show corpus status summary.

    Quick overview of corpus health: total turns, embedding coverage,
    tagging coverage, documents, conversations, and any active issues.

    Examples:
      aip corpus status
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _status():
        store = CorpusTurnStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            return await store.get_corpus_status()
        finally:
            await store.close()

    status = asyncio.run(_status)

    click.echo("Corpus Status:")
    click.echo(f"  Total turns:       {status.get('total_turns', 0)}")
    click.echo(f"  Conversations:     {status.get('conversations', 0)}")
    click.echo(f"  Documents:         {status.get('documents', 0)}")
    click.echo(f"  Embedded:          {status.get('embedded', 0)} ({status.get('embed_coverage', 0)}%)")
    click.echo(f"  Tagged:            {status.get('tagged', 0)} ({status.get('tag_coverage', 0)}%)")
    click.echo(f"  Needs re-embed:    {status.get('needs_reembed', 0)}")
    click.echo(f"  Embed failures:    {status.get('embed_failures', 0)}")

    if status.get("embed_failures", 0) > 0:
        click.echo("")
        click.echo("  WARNING: Embedding failures detected. Run 'aip corpus backfill' to retry.")
    if status.get("needs_reembed", 0) > 0:
        click.echo("")
        click.echo("  NOTE: Some turns need re-embedding. Run 'aip corpus embed --reembed'.")


@corpus.command("audit")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_audit_cmd(db_path: str | None) -> None:
    """Run comprehensive corpus audit.

    Checks for integrity issues, duplicate content, missing hashes,
    embedding failures, and coverage gaps. Returns a detailed report
    of the corpus state.

    Examples:
      aip corpus audit
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _audit():
        store = CorpusTurnStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            return await store.get_corpus_audit()
        finally:
            await store.close()

    audit = asyncio.run(_audit)

    click.echo("Corpus Audit Report")
    click.echo("=" * 50)

    # Summary
    click.echo("\nSummary:")
    click.echo(f"  Total turns:         {audit.get('total_turns', 0)}")
    click.echo(f"  Embedded:            {audit.get('embedded', 0)}")
    click.echo(f"  Unembedded:          {audit.get('unembedded', 0)}")
    click.echo(f"  Needs re-embed:      {audit.get('needs_reembed', 0)}")
    click.echo(f"  Untagged:            {audit.get('untagged', 0)}")
    click.echo(f"  Embed failures:      {audit.get('embed_failures', 0)}")
    click.echo(f"  Missing content_hash:{audit.get('missing_content_hash', 0)}")

    # Source model distribution
    by_model = audit.get("by_source_model", {})
    if by_model:
        click.echo("\nBy Source Model:")
        for model, count in sorted(by_model.items(), key=lambda x: -x[1]):
            click.echo(f"  {model:20s} {count}")

    # Domain distribution
    by_domain = audit.get("by_domain", {})
    if by_domain:
        click.echo("\nBy Domain:")
        for domain, count in sorted(by_domain.items(), key=lambda x: -x[1])[:15]:
            click.echo(f"  {domain:20s} {count}")

    # Source path distribution (documents)
    by_path = audit.get("by_source_path", {})
    if by_path:
        click.echo("\nBy Source Path (top 10):")
        for path, count in sorted(by_path.items(), key=lambda x: -x[1])[:10]:
            click.echo(f"  {path:50s} {count} turns")

    # Duplicate hashes
    dupes = audit.get("duplicate_hashes", [])
    if dupes:
        click.echo(f"\nDuplicate Content Hashes ({len(dupes)}):")
        for d in dupes:
            click.echo(f"  {d['content_hash'][:16]}... → {d['count']} turns")

    # Embed failures
    failures = audit.get("recent_embed_failures", [])
    if failures:
        click.echo(f"\nEmbed Failures ({len(failures)} most recent):")
        for f in failures:
            click.echo(f"  {f['turn_id']}: {f['fail_count']} failures ({f['last_error'][:80]})")

    # Issues
    issues = audit.get("issues", [])
    if issues:
        click.echo(f"\nIssues ({len(issues)}):")
        for issue in issues:
            click.echo(f"  - {issue}")
    else:
        click.echo("\nNo issues found. Corpus is healthy.")

    healthy = audit.get("healthy", False)
    click.echo(f"\nOverall: {'HEALTHY' if healthy else 'ISSUES DETECTED'}")


@corpus.command("backfill")
@click.option(
    "--limit",
    default=100,
    type=int,
    help="Max turns to embed this run (default 100).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_backfill_cmd(limit: int, db_path: str | None) -> None:
    """Run embedding backfill for turns that need embedding or had failures.

    Prioritizes turns with previous embedding failures, then turns needing
    re-embedding, then first-time unembedded turns (by importance).

    Examples:
      aip corpus backfill
      aip corpus backfill --limit 50
    """
    if limit < 1:
        limit = 1

    resolved_db_path = db_path or get_default_db_path()

    async def _backfill():
        import tomllib
        from pathlib import Path

        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.adapter.embedding.factory import create_embedding_provider

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)

        # Create embedding provider
        embedding_provider = create_embedding_provider(cfg)
        if embedding_provider is None:
            return None, "No embedding provider configured"

        # Create vector store via factory (indirect import to preserve layer boundary)
        import importlib

        _vec_factory_mod = importlib.import_module("aip.adapter.vector.factory")
        _create_vector_store = _vec_factory_mod.create_vector_store
        vector_store = await _create_vector_store(cfg, embedding_provider=embedding_provider)

        store = CorpusTurnStore(db_path=resolved_db_path)
        await store.initialize()

        try:
            # Get backfill queue
            queue = await store.get_backfill_queue(limit=limit)
            if not queue:
                return [], "No turns need backfill"

            embedded_count = 0
            failed_count = 0
            model_name = cfg.get("models", {}).get("embedding", {}).get("model", "unknown")

            for turn in queue:
                try:
                    # Generate embedding
                    text_to_embed = turn.searchable_text[:2000]
                    if not text_to_embed.strip():
                        continue

                    embedding = await embedding_provider.embed(text_to_embed)
                    if not embedding or len(embedding) == 0:
                        raise ValueError("Empty embedding returned")

                    # Upsert to vector store
                    await vector_store.upsert(
                        id=turn.turn_id,
                        embedding=embedding,
                        content=text_to_embed,
                        metadata={
                            "type": "corpus_turn",
                            "conversation_id": turn.conversation_id,
                            "domain": turn.primary_domain,
                            "source_model": turn.source_model,
                        },
                        domain=turn.primary_domain or None,
                    )

                    # Mark as embedded and clear failure state
                    await store.mark_embedded(turn.turn_id, embedding_model=model_name)
                    await store.clear_embed_failure(turn.turn_id)
                    embedded_count += 1

                except Exception as exc:
                    # Record failure
                    await store.record_embed_failure(turn.turn_id, str(exc)[:500])
                    failed_count += 1

            return queue, f"Embedded {embedded_count}/{len(queue)} turns. {failed_count} failed."
        finally:
            await store.close()
            await vector_store.close()

    result = asyncio.run(_backfill())

    if result[0] is None:
        click.echo(f"Error: {result[1]}", err=True)
        sys.exit(1)

    queue, message = result
    click.echo(message)

    if queue and len(queue) > 0:
        # Show remaining
        async def _remaining():
            store = CorpusTurnStore(db_path=resolved_db_path)
            await store.initialize()
            try:
                return await store.count_unembedded()
            finally:
                await store.close()

        remaining = asyncio.run(_remaining)
        click.echo(f"Remaining unembedded: {remaining}")


@corpus.command("list")
@click.option(
    "--unembedded",
    is_flag=True,
    default=False,
    help="List only unembedded turns.",
)
@click.option(
    "--failed",
    is_flag=True,
    default=False,
    help="List only turns with embedding failures.",
)
@click.option(
    "--document",
    "source_path",
    default=None,
    help="List turns from a specific source path.",
)
@click.option(
    "--limit",
    default=20,
    type=int,
    help="Max turns to list (default 20).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_list_cmd(
    unembedded: bool,
    failed: bool,
    source_path: str | None,
    limit: int,
    db_path: str | None,
) -> None:
    """List corpus turns with optional filters.

    Examples:
      aip corpus list
      aip corpus list --unembedded
      aip corpus list --failed
      aip corpus list --document docs/ARCHITECTURE.md
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _list():
        store = CorpusTurnStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            turns = []

            if failed:
                # Get turns with embed failures
                queue = await store.get_backfill_queue(limit=limit)
                turns = [t for t in queue if t.embed_fail_count > 0]
            elif unembedded:
                turns = await store.get_unembedded_turns(limit=limit)
            elif source_path:
                turns = await store.find_by_source_path(source_path)
            else:
                # Default: show recent turns
                turns = await store.get_untagged_turns(limit=limit)

            return turns
        finally:
            await store.close()

    turns = asyncio.run(_list)

    if not turns:
        click.echo("No turns found matching criteria.")
        return

    click.echo(f"Found {len(turns)} turns:")
    click.echo("")

    for t in turns:
        # Truncate for display
        user_preview = (t.user_text or "")[:60].replace("\n", " ")
        path_info = f" [{t.source_path}]" if t.source_path else ""
        embed_status = "embedded" if t.embedded else "NOT embedded"
        fail_info = f" (fails: {t.embed_fail_count})" if t.embed_fail_count > 0 else ""
        version_info = f" v{t.doc_version}" if t.doc_version > 0 else ""

        click.echo(f"  {t.turn_id}{version_info}: {user_preview}...{path_info}")
        click.echo(f"    model={t.source_model} domain={t.primary_domain or 'untagged'} {embed_status}{fail_info}")

    if len(turns) >= limit:
        click.echo(f"\n  (showing first {limit}; use --limit to show more)")
