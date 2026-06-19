"""Extension host loaders — ADR-014 §9.

Net-new loaders that bridge extension-contributed artifacts to the existing
core infrastructure:
  - migration_loader: .sql files → LoadedMigration → applied to corpus DB
    via a separate `extension_applied_migrations` table (does not contaminate
    the core CorpusMigrationRunner's fingerprint).
"""
from __future__ import annotations

from aip.adapter.extensions.loaders.migration_loader import (
    LoadedMigration,
    apply_extension_migrations,
    load_migrations_dir,
)

__all__ = [
    "LoadedMigration",
    "apply_extension_migrations",
    "load_migrations_dir",
]
