"""CLI commands for corpus audit log — ADR-008 Rev 3.1 §9.6, §A15.

Provides ``aip audit log`` to view corpus lifecycle events from the
``corpus_audit_log`` table in the definer corpus.

Usage:
    aip audit log                      # all entries, most recent first
    aip audit log --corpus definer     # filter by corpus_id
    aip audit log --action CORPUS_REGISTERED
    aip audit log --since 2026-06-01   # entries since ISO date
    aip audit log --limit 50           # cap results (default 100)
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone

import click


@click.group("audit")
def audit() -> None:
    """Corpus audit log — view lifecycle events (ADR-008 §9.6)."""
    pass


@audit.command("log")
@click.option("--corpus", default=None, help="Filter by corpus_id.")
@click.option("--action", "action_filter", default=None, help="Filter by action (e.g. CORPUS_REGISTERED).")
@click.option("--since", default=None, help="Show entries since ISO datetime (e.g. 2026-06-01).")
@click.option("--until", default=None, help="Show entries until ISO datetime.")
@click.option("--limit", default=100, help="Max entries to show (default 100).")
@click.option("--db-path", default=None, help="Definer corpus SQLite path (default: db/definer.db).")
def audit_log(
    corpus: str | None,
    action_filter: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    db_path: str | None,
) -> None:
    """View corpus audit log entries.

    Reads from corpus_audit_log table in the definer corpus. Entries are
    shown most-recent-first by default.

    Examples:
      aip audit log
      aip audit log --corpus definer --action CORPUS_REGISTERED
      aip audit log --since 2026-06-01 --limit 50
    """
    try:
        result = asyncio.run(_audit_log_async(corpus, action_filter, since, until, limit, db_path))
        _print_audit_log(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


async def _audit_log_async(
    corpus: str | None,
    action_filter: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    db_path: str | None,
) -> dict:
    """Fetch audit log entries from the definer corpus."""
    import aiosqlite

    from aip.cli._db_path import get_default_db_path

    # Default to definer corpus db
    if db_path is None:
        # Try definer.db first, fall back to state.db for pre-ADR-008 compat
        from pathlib import Path

        definer_path = Path("db/definer.db")
        if definer_path.exists():
            db_path = str(definer_path)
        else:
            db_path = get_default_db_path()

    # Build query
    sql = "SELECT id, ts, actor_id, corpus_id, action, outcome, detail FROM corpus_audit_log"
    conditions: list[str] = []
    params: list = []

    if corpus:
        conditions.append("corpus_id = ?")
        params.append(corpus)
    if action_filter:
        conditions.append("action = ?")
        params.append(action_filter)
    if since:
        try:
            since_ts = datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp()
            conditions.append("ts >= ?")
            params.append(since_ts)
        except ValueError:
            pass
    if until:
        try:
            until_ts = datetime.fromisoformat(until).replace(tzinfo=timezone.utc).timestamp()
            conditions.append("ts <= ?")
            params.append(until_ts)
        except ValueError:
            pass

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(int(limit))

    try:
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
    except Exception as exc:
        return {"error": {"message": str(exc)}, "entries": []}

    entries = []
    for row in rows:
        ts = datetime.fromtimestamp(row["ts"], tz=timezone.utc).isoformat() if row["ts"] else ""
        detail = {}
        if row["detail"]:
            try:
                detail = json.loads(row["detail"])
            except (json.JSONDecodeError, TypeError):
                detail = {"raw": row["detail"]}
        entries.append(
            {
                "id": row["id"],
                "ts": ts,
                "actor_id": row["actor_id"],
                "corpus_id": row["corpus_id"] or "",
                "action": row["action"],
                "outcome": row["outcome"],
                "detail": detail,
            }
        )

    return {"entries": entries, "count": len(entries), "db_path": db_path}


def _print_audit_log(result: dict) -> None:
    """Print audit log entries in a readable format."""
    if "error" in result:
        click.echo(f"Error: {result['error']['message']}", err=True)
        sys.exit(1)

    entries = result.get("entries", [])
    if not entries:
        click.echo("No audit log entries found.")
        return

    click.echo("=" * 70)
    click.echo("Corpus Audit Log")
    click.echo(f"Source: {result.get('db_path', 'unknown')}")
    click.echo(f"Entries: {result.get('count', 0)}")
    click.echo("=" * 70)

    for entry in entries:
        click.echo(f"\n  {entry['ts']}")
        click.echo(f"  Action:  {entry['action']}")
        click.echo(f"  Outcome: {entry['outcome']}")
        click.echo(f"  Actor:   {entry['actor_id']}")
        if entry["corpus_id"]:
            click.echo(f"  Corpus:  {entry['corpus_id']}")
        if entry["detail"]:
            click.echo(f"  Detail:  {json.dumps(entry['detail'], indent=2)}")
        click.echo(f"  ID:      {entry['id']}")
