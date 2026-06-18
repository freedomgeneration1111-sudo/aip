"""AIP CLI entrypoint.

Implements the declared pyproject script `aip = "aip.cli.main:cli"`.
Uses Click. Composes via Protocols / AipContainer where possible (offline-first).
"""

from __future__ import annotations

from pathlib import Path

import click

from aip.cli import artifact as artifact_cmd
from aip.cli import ask as ask_cmd
from aip.cli import audit as audit_cmd
from aip.cli import backup as backup_cmd
from aip.cli import codex as codex_cmd
from aip.cli import config as config_cmd
from aip.cli import corpus as corpus_cmd
from aip.cli import corpus_migrate as corpus_migrate_cmd
from aip.cli import eval as eval_cmd
from aip.cli import export as export_cmd
from aip.cli import history as history_cmd
from aip.cli import ingest as ingest_cmd
from aip.cli import init as init_cmd
from aip.cli import project as project_cmd
from aip.cli import review as review_cmd
from aip.cli import session as session_cmd
from aip.cli import status as status_cmd


@click.group()
@click.version_option(version="0.1", prog_name="aip")
def cli() -> None:
    """AIP 0.1 — AI Poiesis local-first harness.

    Primary offline DEFINER surface. All privileged operations go through AutonomyGate.

    Common commands:
      aip init      Initialize databases and config for this machine
      aip status    Show system status and database health
      aip config    Read/write configuration values
      aip validate  Validate config for production-safety issues
      aip project   Manage projects (list, create, show)
      aip session   Manage sessions (start, resume, list)
      aip ingest    Import conversations into the knowledge substrate
      aip ask       Ask a source-grounded question about a project
      aip review    Review, approve, reject generated artifacts
      aip artifact  Manage artifact lifecycle (create, inspect)
      aip history   Browse stored conversation turns (ingest + chat auto-save)
      aip corpus    Manage turn-level corpus (new atomic CorpusTurn unit)
      aip codex     Manage the CODEX — internal corpus librarian
      aip eval      Run retrieval quality evaluation
      aip export    Export artifacts to markdown
      aip backup    Create consistent backup of all stores
    """
    pass


@click.command("validate")
@click.option("--config-path", default="config/aip.config.toml", help="Path to config file")
def validate(config_path: str) -> None:
    """Validate configuration for production-safety issues.

    Checks for unsafe combinations such as:
    - Production mode with auth disabled
    - Public API bind with auth disabled
    - Default/weak database passwords
    - Fixture/CI providers in production

    Exits with code 1 if any validation errors are found.
    """
    path = Path(config_path)
    if not path.exists():
        click.echo(f"Config file not found: {path}")
        click.echo("Run `aip init` to create a configuration file.")
        raise SystemExit(1)

    click.echo(f"Validating config: {path}")
    if config_cmd.validate_config_file(path):
        click.echo("Config validation passed. No safety issues found.")
    else:
        click.echo("\nConfig validation FAILED. Fix the errors above before deploying.", err=True)
        raise SystemExit(1)


# Register subcommands
cli.add_command(init_cmd.init)
cli.add_command(status_cmd.status)
cli.add_command(config_cmd.config)
cli.add_command(project_cmd.project)
cli.add_command(session_cmd.session)
cli.add_command(ingest_cmd.ingest)
cli.add_command(ask_cmd.ask_cmd)
cli.add_command(artifact_cmd.artifact)
cli.add_command(review_cmd.review)
cli.add_command(history_cmd.history)
cli.add_command(corpus_cmd.corpus)
cli.add_command(corpus_migrate_cmd.corpus_migrate)
cli.add_command(codex_cmd.codex)
cli.add_command(eval_cmd.eval_cmd)
cli.add_command(export_cmd.export)
cli.add_command(backup_cmd.backup)
cli.add_command(audit_cmd.audit)
cli.add_command(validate)


if __name__ == "__main__":
    cli()
