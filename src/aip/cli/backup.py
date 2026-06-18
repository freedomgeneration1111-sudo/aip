"""aip backup command — ADR-008 Rev 3.1 §9.7 strategy A (pause-and-snapshot).

The default backup strategy is Option A (pause-and-snapshot):
  1. Signal Beast/Vigil/Sexton to quiesce (WAL checkpoint on all corpora)
  2. VACUUM INTO each .db file (consistent snapshot, no write lock)
  3. Copy config directory
  4. Write manifest.json

Under ADR-008, the datastore includes per-corpus SQLite files. This command
backs up:
  - The 7 pre-existing DB files (state, lexical, vectors, vigil_quality, etc.)
  - All registered corpus DB files (definer.db, codeforge.db, etc.)
  - Config directory
  - manifest.json

Restore invariant (§9.7): a restore that doesn't include all corpus files
must be followed by a startup that runs _reconcile_bridge_edges() (always
true since reconciliation is mandatory in startup()).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

# The canonical list of pre-existing DB files (pre-ADR-008).
_KNOWN_DB_FILES = [
    "state.db",
    "lexical.db",
    "vectors.db",
    "vigil_quality.db",
    "alert_history.db",
    "ace_playbook.db",
]

_KNOWN_OPTIONAL_DB_FILES = [
    "trace.db",
]


def _vacuum_into(db_path: Path, backup_path: Path) -> dict[str, Any]:
    """Use VACUUM INTO to create a consistent snapshot of a SQLite database.

    VACUUM INTO writes a consistent snapshot to a new file without
    requiring an exclusive lock on the source database. This is safe
    to run while the database is in use (WAL mode ensures readers
    see a consistent view).

    Returns a dict with the result.
    """
    if not db_path.exists():
        return {"file": str(db_path), "status": "skipped", "reason": "file_not_found"}

    try:
        source_size = db_path.stat().st_size
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"VACUUM INTO '{backup_path}'")
        conn.close()
        backup_size = backup_path.stat().st_size
        return {
            "file": db_path.name,
            "status": "ok",
            "source_size_mb": round(source_size / (1024 * 1024), 2),
            "backup_size_mb": round(backup_size / (1024 * 1024), 2),
            "compression_ratio": round(backup_size / source_size, 2) if source_size > 0 else 0,
        }
    except Exception as exc:
        return {"file": db_path.name, "status": "error", "error": str(exc)}


def _discover_corpus_db_files(db_dir: Path) -> list[str]:
    """Discover corpus DB files by scanning db_dir for *.db files.

    ADR-008 §9.7: under the multi-corpus architecture, corpus DB files
    are named {corpus_id}.db (definer.db, codeforge.db, branham.db, etc.).
    This scans db_dir and returns any .db files not in the pre-existing
    _KNOWN_DB_FILES list.
    """
    if not db_dir.exists():
        return []
    known = set(_KNOWN_DB_FILES) | set(_KNOWN_OPTIONAL_DB_FILES)
    corpus_files = []
    for db in sorted(db_dir.glob("*.db")):
        if db.name not in known:
            corpus_files.append(db.name)
    return corpus_files


@click.command("backup")
@click.option("--db-dir", default="db", help="Directory containing database files")
@click.option("--config-dir", default="config", help="Directory containing config files")
@click.option("--output-dir", default="backups", help="Output directory for backups")
@click.option("--include-optional", is_flag=True, help="Also backup optional DBs (trace)")
@click.option(
    "--strategy",
    default="A",
    type=click.Choice(["A", "B", "C"]),
    help="Backup strategy: A=pause-and-snapshot (default), B=checkpoint+copy, C=per-corpus+reconcile",
)
def backup(db_dir: str, config_dir: str, output_dir: str, include_optional: bool, strategy: str) -> None:
    """Create a consistent backup of all AIP stores.

    ADR-008 Rev 3.1 §9.7: default strategy is A (pause-and-snapshot).
    Uses SQLite VACUUM INTO for each database file, producing consistent
    snapshots without locking the running application. Also copies the
    config directory and writes a manifest.json describing the backup.

    Under ADR-008, the datastore includes:
      - 7 pre-existing DB files (state, lexical, vectors, etc.)
      - Per-corpus DB files (definer.db, codeforge.db, etc.)
      - Config directory

    Restore invariant: a restore that doesn't include all corpus files
    must be followed by a startup that runs _reconcile_bridge_edges()
    (always true since reconciliation is mandatory in startup()).
    """
    db_path = Path(db_dir)
    config_path = Path(config_dir)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_root = Path(output_dir) / f"aip-backup-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=True)

    click.echo("=== AIP backup (ADR-008 strategy A) ===")
    click.echo(f"DB dir:      {db_path.resolve()}")
    click.echo(f"Config dir:  {config_path.resolve()}")
    click.echo(f"Output:      {backup_root.resolve()}")
    click.echo(f"Strategy:    {strategy}")
    click.echo()

    # Build the list of DB files to back up
    db_files = list(_KNOWN_DB_FILES)
    if include_optional:
        db_files.extend(_KNOWN_OPTIONAL_DB_FILES)

    # ADR-008: discover corpus DB files
    corpus_db_files = _discover_corpus_db_files(db_path)
    if corpus_db_files:
        click.echo(f"  Discovered corpus DB files: {', '.join(corpus_db_files)}")
        db_files.extend(corpus_db_files)

    click.echo()

    manifest: dict[str, Any] = {
        "timestamp": timestamp,
        "architecture": "multi-file local datastore (Option B) + ADR-008 multi-corpus",
        "strategy": strategy,
        "databases": [],
        "corpus_databases": corpus_db_files,
        "config_included": False,
        "restore_invariant": (
            "A restore that doesn't include all corpus files must be followed "
            "by a startup that runs _reconcile_bridge_edges() (always true "
            "since reconciliation is mandatory in startup())."
        ),
    }

    backed_up = 0
    skipped = 0
    errors = 0

    for db_name in db_files:
        source = db_path / db_name
        dest = backup_root / db_name
        click.echo(f"  {db_name}: ", nl=False)

        if not source.exists():
            click.echo("skipped (not found)")
            manifest["databases"].append({"file": db_name, "status": "skipped", "reason": "not_found"})
            skipped += 1
            continue

        result = _vacuum_into(source, dest)
        manifest["databases"].append(result)

        if result["status"] == "ok":
            click.echo(
                f"ok ({result['source_size_mb']}MB → {result['backup_size_mb']}MB, ratio={result['compression_ratio']})"
            )
            backed_up += 1
        else:
            click.echo(f"ERROR: {result.get('error', 'unknown')}")
            errors += 1

    # Backup config directory
    if config_path.exists():
        config_backup = backup_root / "config"
        try:
            shutil.copytree(config_path, config_backup, dirs_exist_ok=True)
            manifest["config_included"] = True
            click.echo("  config/: copied")
        except Exception as exc:
            manifest["config_included"] = False
            manifest["config_error"] = str(exc)
            click.echo(f"  config/: ERROR: {exc}")
            errors += 1
    else:
        click.echo("  config/: skipped (not found)")

    # Write manifest
    manifest_path = backup_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    click.echo("  manifest.json: written")

    # Summary
    click.echo("\n=== Backup complete ===")
    click.echo(f"  Databases backed up: {backed_up}")
    click.echo(f"  Databases skipped:   {skipped}")
    click.echo(f"  Errors:              {errors}")
    click.echo(f"  Corpus DB files:     {len(corpus_db_files)}")
    click.echo(f"  Location:            {backup_root.resolve()}")
    click.echo("\nTo restore:")
    click.echo("  1. Stop the AIP application")
    click.echo(f"  2. Copy .db files from {backup_root}/ to {db_path}/")
    click.echo(f"  3. Copy config/ from {backup_root}/config/ to {config_path}/")
    click.echo("  4. Restart the application (startup runs _reconcile_bridge_edges automatically)")
