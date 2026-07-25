"""CLI commands for exporting artifacts to markdown.

Provides ``aip export`` with subcommands:
- artifact: Export a single artifact to markdown
- project: Export all approved artifacts for a project

Chunk 7 — Review/export gate integrity:
    - --force is an explicit emergency/debug path, not a casual override.
    - When --force is used, a loud warning is printed and confirmation is
      required (unless --yes is given for CI/scripts).
    - --reason is strongly recommended with --force; the reason is recorded
      in the audit trail.
    - Every force-export writes a ``force_export`` audit event.
    - Normal export only exports APPROVED artifacts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click


@click.group("export")
def export() -> None:
    """Export artifacts to markdown.

    Exported files include metadata frontmatter and source/provenance footer.
    Normal export only exports APPROVED artifacts.
    Use --force for emergency/debug export of non-APPROVED artifacts
    (audit event will be recorded).
    """
    pass


@export.command("artifact")
@click.argument("artifact_id")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "text"]),
    default="markdown",
    help="Export format (default: markdown).",
)
@click.option("--out", required=True, help="Output file path.")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="EMERGENCY/DEBUG: Force export of non-APPROVED artifacts. Audit event will be recorded.",
)
@click.option(
    "--reason", default="", help="Reason for force-export (recorded in audit trail). Strongly recommended with --force."
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt (for CI/scripts). Only meaningful with --force.",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def export_artifact(
    artifact_id: str, fmt: str, out: str, force: bool, reason: str, yes: bool, db_path: str | None
) -> None:
    """Export an artifact to a markdown file.

    Includes metadata frontmatter and source/provenance footer.
    Normal export: only APPROVED artifacts.
    Force export (emergency/debug): non-APPROVED artifacts with audit trail.
    """
    # Force-export gate: warn and confirm
    if force:
        _print_force_warning(artifact_id, reason)
        if not yes:
            if not click.confirm("\n  Proceed with force-export?", default=False):
                click.echo("Aborted.")
                sys.exit(0)
        if not reason:
            click.echo(
                "  WARNING: No --reason provided. The audit event will record "
                "'(no explicit reason provided)'. Consider providing --reason.",
                err=True,
            )

    try:
        result = asyncio.run(_export_artifact_async(artifact_id, out, fmt, force, reason, db_path))
        _print_export_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@export.command("project")
@click.argument("project_name")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "text"]),
    default="markdown",
    help="Export format (default: markdown).",
)
@click.option("--out", required=True, help="Output file path.")
@click.option(
    "--include-unreviewed",
    is_flag=True,
    default=False,
    help="Include GENERATED/REVIEWED artifacts (sovereign override with audit trail). Default: APPROVED only.",
)
@click.option(
    "--reason",
    default="",
    help="Reason for including unreviewed artifacts (recorded in audit trail). Recommended with --include-unreviewed.",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt (for CI/scripts). Only meaningful with --include-unreviewed.",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def export_project(
    project_name: str, fmt: str, out: str, include_unreviewed: bool, reason: str, yes: bool, db_path: str | None
) -> None:
    """Export approved artifacts for a project to a markdown bundle.

    Includes an artifact index and provenance metadata.
    Default: APPROVED artifacts only.
    With --include-unreviewed: also includes GENERATED/REVIEWED artifacts
    (each recorded as a sovereign override with audit event).
    REJECTED artifacts are always excluded.
    """
    # Include-unreviewed gate: warn and confirm
    if include_unreviewed:
        _print_include_unreviewed_warning(project_name, reason)
        if not yes:
            if not click.confirm("\n  Proceed with including unreviewed artifacts?", default=False):
                click.echo("Aborted.")
                sys.exit(0)
        if not reason:
            click.echo(
                "  WARNING: No --reason provided. Audit events will record "
                "'(no explicit reason provided)'. Consider providing --reason.",
                err=True,
            )

    try:
        result = asyncio.run(_export_project_async(project_name, out, fmt, include_unreviewed, reason, db_path))
        _print_export_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Async implementations
# ---------------------------------------------------------------------------


def _get_db_path(db_path: str | None) -> str:
    from aip.cli._db_path import ensure_db_dir, get_default_db_path

    if db_path is None:
        db_path = get_default_db_path()
    ensure_db_dir(db_path)
    return db_path


async def _export_artifact_async(
    artifact_id: str, out: str, fmt: str, force: bool, force_reason: str, db_path: str | None
):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_artifact

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await export_artifact(artifact_id, out, stores, format=fmt, force=force, force_reason=force_reason)
    finally:
        await stores.close()


async def _export_project_async(
    project_name: str, out: str, fmt: str, include_unreviewed: bool, force_reason: str, db_path: str | None
):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_project

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await export_project(
            project_name, out, stores, format=fmt, include_unreviewed=include_unreviewed, force_reason=force_reason
        )
    finally:
        await stores.close()


# ---------------------------------------------------------------------------
# Warning helpers
# ---------------------------------------------------------------------------


def _print_force_warning(artifact_id: str, reason: str) -> None:
    """Print a loud warning when --force is used."""
    click.echo("", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("  SOVEREIGN OVERRIDE: FORCE-EXPORT", err=True)
    click.echo("  ============================================================", err=True)
    click.echo(f"  Artifact '{artifact_id}' is NOT in APPROVED state.", err=True)
    click.echo("  Force-export bypasses the DEFINER review gate.", err=True)
    click.echo("  This action will be recorded in the audit trail.", err=True)
    if reason:
        click.echo(f"  Reason: {reason}", err=True)
    else:
        click.echo("  Reason: (not provided — use --reason for a clear audit trail)", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("", err=True)


def _print_include_unreviewed_warning(project_name: str, reason: str) -> None:
    """Print a loud warning when --include-unreviewed is used."""
    click.echo("", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("  SOVEREIGN OVERRIDE: INCLUDING UNREVIEWED ARTIFACTS", err=True)
    click.echo("  ============================================================", err=True)
    click.echo(f"  Project '{project_name}': exporting non-APPROVED artifacts.", err=True)
    click.echo("  Each unreviewed artifact will be recorded as a sovereign", err=True)
    click.echo("  override in the audit trail.", err=True)
    if reason:
        click.echo(f"  Reason: {reason}", err=True)
    else:
        click.echo("  Reason: (not provided — use --reason for a clear audit trail)", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("", err=True)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase β-2 (2026-07-23): aip export manual — compile wiki articles into a manual
# ---------------------------------------------------------------------------


@export.command("manual")
@click.argument("domain")
@click.option("--out", required=True, help="Output markdown file path.")
@click.option(
    "--include-unreviewed",
    is_flag=True,
    default=False,
    help="Include GENERATED/REVIEWED wiki articles (default: APPROVED only).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def export_manual(domain: str, out: str, include_unreviewed: bool, db_path: str | None) -> None:
    """Compile all APPROVED wiki articles in a domain into a structured manual.

    Phase β-2 (2026-07-23) — Wiki → User Manual Evolution.

    Collects all wiki articles (beast:wiki:*, wiki:*, sexton:wiki:*, manual:*)
    with metadata.domain == DOMAIN and ECS state APPROVED (or all states with
    --include-unreviewed). Compiles them into a single markdown file with:
      - Title page (domain name + export date)
      - Table of contents (article titles + links)
      - One chapter per article (title, summary, body, source links)
      - Cross-references via prerequisite_of links (if any)

    Articles are ordered by created_at. Future enhancement: order by
    prerequisite_of edges (topological sort).

    Examples:
      aip export manual aip --out docs/aip_manual.md
      aip export manual theology_research --out docs/theology_manual.md --include-unreviewed
    """
    try:
        resolved_db_path = db_path or get_default_db_path()

        async def _run_manual_export() -> dict:
            import aiosqlite

            states_filter = (
                "AND e.current_state = 'APPROVED'"
                if not include_unreviewed
                else ""
            )

            async with aiosqlite.connect(resolved_db_path) as conn:
                conn.row_factory = __import__("sqlite3").Row
                cursor = await conn.execute(
                    f"""
                    SELECT a.id, a.content, a.metadata_json, a.created_at, a.updated_at,
                           e.current_state
                    FROM artifacts a
                    LEFT JOIN ecs_state e ON a.id = e.artifact_id
                    WHERE (a.id LIKE 'beast:wiki:%' OR a.id LIKE 'wiki:%'
                           OR a.id LIKE 'sexton:wiki:%' OR a.id LIKE 'manual:%')
                    {states_filter}
                    ORDER BY a.created_at ASC
                    """,
                )
                rows = await cursor.fetchall()

            if not rows:
                return {"error": f"No wiki articles found for domain '{domain}'" + (
                    " (try --include-unreviewed)" if not include_unreviewed else ""
                )}

            # Filter by domain in metadata
            import json

            articles: list[dict] = []
            for row in rows:
                meta = json.loads(row["metadata_json"] or "{}")
                article_domain = meta.get("domain", "")
                if article_domain == domain:
                    articles.append({
                        "id": row["id"],
                        "title": meta.get("title", row["id"]),
                        "summary": meta.get("summary", ""),
                        "content": row["content"] or "",
                        "state": row["current_state"] or "UNKNOWN",
                        "created_at": row["created_at"],
                        "tags": meta.get("tags", []),
                    })

            if not articles:
                return {"error": f"No wiki articles with domain='{domain}' found"}

            # Build the manual markdown
            from datetime import date

            lines: list[str] = []
            lines.append(f"# {domain.replace('_', ' ').title()} — User Manual")
            lines.append("")
            lines.append(f"*Compiled: {date.today().isoformat()}*")
            lines.append(f"*Articles: {len(articles)}*")
            lines.append(f"*Source: AIP Brain wiki corpus*")
            lines.append("")
            lines.append("---")
            lines.append("")

            # Table of contents
            lines.append("## Table of Contents")
            lines.append("")
            for i, art in enumerate(articles, 1):
                anchor = art["title"].lower().replace(" ", "-").replace("/", "-")
                lines.append(f"{i}. [{art['title']}](#{anchor})")
            lines.append("")
            lines.append("---")
            lines.append("")

            # Chapters
            for i, art in enumerate(articles, 1):
                lines.append(f"## Chapter {i}: {art['title']}")
                lines.append("")
                if art["summary"]:
                    lines.append(f"*{art['summary']}*")
                    lines.append("")
                lines.append(art["content"])
                lines.append("")
                if art["tags"]:
                    lines.append(f"**Tags:** {', '.join(art['tags'])}")
                    lines.append("")
                lines.append(f"*Artifact ID: `{art['id']}` | State: {art['state']}*")
                lines.append("")
                lines.append("---")
                lines.append("")

            # Write output
            output_path = Path(out)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("\n".join(lines), encoding="utf-8")

            return {
                "domain": domain,
                "articles": len(articles),
                "out_path": str(output_path),
                "bytes_written": output_path.stat().st_size,
            }

        result = asyncio.run(_run_manual_export())

        if "error" in result:
            click.echo(f"Error: {result['error']}", err=True)
            sys.exit(1)

        click.echo("Manual export complete.")
        click.echo(f"  Domain:   {result['domain']}")
        click.echo(f"  Articles: {result['articles']}")
        click.echo(f"  Output:   {result['out_path']}")
        click.echo(f"  Size:     {result['bytes_written']} bytes")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


def _print_export_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error ({err['code']}): {err['message']}", err=True)
        sys.exit(1)

    if result.get("artifacts_exported") is not None:
        # Project export
        click.echo("Project export complete.")
        click.echo(f"  Project:  {result['project']}")
        click.echo(f"  Exported: {result['artifacts_exported']} artifacts")
        if result.get("sovereign_override_count"):
            click.echo(
                f"  Sovereign overrides: {result['sovereign_override_count']} artifact(s) "
                "exported from non-APPROVED state (audit recorded)",
            )
        click.echo(f"  Output:   {result['out_path']}")
        click.echo(f"  Size:     {result.get('bytes_written', 0)} bytes")
    else:
        # Artifact export
        click.echo("Artifact exported.")
        click.echo(f"  ID:       {result['artifact_id']}")
        click.echo(f"  State:    {result.get('lifecycle_state', '')}")
        if result.get("force_bypass"):
            click.echo(f"  ** SOVEREIGN OVERRIDE: exported from {result.get('force_bypass_state', '')} state **")
            click.echo("  Audit:    Recorded (force_export event)")
            click.echo(f"  Reason:   {result.get('force_reason', '')}")
        click.echo(f"  Output:   {result['out_path']}")
        click.echo(f"  Size:     {result.get('bytes_written', 0)} bytes")
