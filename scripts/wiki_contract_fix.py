#!/usr/bin/env python3
"""Wiki artifact_type contract fix — diagnostic + backfill.

BUG DIAGNOSIS:
  Sexton writes wiki artifacts with artifact_type="sexton_wiki" but
  consumers (wiki_channel.py, chat.py) expect artifact_type="beast_wiki".
  The /wiki/articles API route also didn't match sexton:wiki:* ID patterns.

  This script:
  1. Diagnoses: shows all artifacts by artifact_type, flags orphaned sexton_wiki
  2. Backfills: updates sexton_wiki → beast_wiki in metadata_json for existing artifacts

USAGE:
  # Dry run (diagnostic only, no changes):
  python scripts/wiki_contract_fix.py --db db/state.db

  # Apply backfill:
  python scripts/wiki_contract_fix.py --db db/state.db --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def diagnose(db_path: str) -> dict:
    """Print diagnostic report on wiki artifact types in the database."""
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        return {"error": "db_not_found"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. All artifact_type values and counts
    print("=" * 60)
    print("DIAGNOSTIC: Artifact types in database")
    print("=" * 60)

    cursor = conn.execute(
        """
        SELECT
            json_extract(metadata_json, '$.artifact_type') AS artifact_type,
            COUNT(*) AS cnt
        FROM artifacts
        GROUP BY artifact_type
        ORDER BY cnt DESC
        """
    )
    type_counts = {}
    for row in cursor:
        at = row["artifact_type"] or "(null)"
        cnt = row["cnt"]
        type_counts[at] = cnt
        flag = "  ⚠️  ORPHANED — no consumer reads this type" if at == "sexton_wiki" else ""
        print(f"  {at:30s}  {cnt:5d}{flag}")

    # 2. Sexton wiki artifacts (the orphaned ones)
    print()
    print("=" * 60)
    print("ORPHANED: sexton_wiki artifacts (invisible to wiki consumers)")
    print("=" * 60)

    cursor = conn.execute(
        """
        SELECT id, version, created_at,
               json_extract(metadata_json, '$.domain') AS domain,
               json_extract(metadata_json, '$.word_count') AS word_count,
               length(content) AS content_len
        FROM artifacts
        WHERE json_extract(metadata_json, '$.artifact_type') = 'sexton_wiki'
        ORDER BY created_at DESC
        """
    )
    orphaned = cursor.fetchall()
    if not orphaned:
        print("  (none found — no backfill needed)")
    for row in orphaned:
        print(f"  ID: {row['id']}")
        print(
            f"    domain: {row['domain']}, words: {row['word_count']}, "
            f"content_len: {row['content_len']}, created: {row['created_at']}"
        )

    # 3. Beast wiki artifacts (the expected type)
    print()
    print("=" * 60)
    print("EXPECTED: beast_wiki artifacts (visible to wiki consumers)")
    print("=" * 60)

    cursor = conn.execute(
        """
        SELECT id, version, created_at,
               json_extract(metadata_json, '$.domain') AS domain,
               json_extract(metadata_json, '$.word_count') AS word_count
        FROM artifacts
        WHERE json_extract(metadata_json, '$.artifact_type') = 'beast_wiki'
        ORDER BY created_at DESC
        """
    )
    expected = cursor.fetchall()
    if not expected:
        print("  (none found)")
    for row in expected:
        print(f"  ID: {row['id']}")
        print(f"    domain: {row['domain']}, words: {row['word_count']}, created: {row['created_at']}")

    # 4. ECS states for all wiki artifacts
    print()
    print("=" * 60)
    print("ECS STATES: Wiki artifact lifecycle states")
    print("=" * 60)

    cursor = conn.execute(
        """
        SELECT e.artifact_id, e.current_state, e.updated_at
        FROM ecs_state e
        WHERE e.artifact_id LIKE 'beast:wiki:%'
           OR e.artifact_id LIKE 'sexton:wiki:%'
           OR e.artifact_id LIKE 'wiki:%'
        ORDER BY e.updated_at DESC
        """
    )
    ecs_rows = cursor.fetchall()
    if not ecs_rows:
        print("  (none found)")
    for row in ecs_rows:
        print(f"  {row['artifact_id']:60s}  state={row['current_state']:15s}  updated={row['updated_at']}")

    # 5. Vigil artifacts (the "cryptic reports" the user sees)
    print()
    print("=" * 60)
    print("VIGIL: vigil_flag / vigil_cycle_report artifacts (what user sees instead)")
    print("=" * 60)

    cursor = conn.execute(
        """
        SELECT
            json_extract(metadata_json, '$.artifact_type') AS artifact_type,
            COUNT(*) AS cnt
        FROM artifacts
        WHERE json_extract(metadata_json, '$.artifact_type') IN ('vigil_flag', 'vigil_cycle_report')
        GROUP BY artifact_type
        """
    )
    for row in cursor:
        print(f"  {row['artifact_type']:30s}  {row['cnt']:5d}")

    conn.close()

    return {
        "type_counts": type_counts,
        "orphaned_count": len(orphaned),
        "expected_count": len(expected),
    }


def backfill(db_path: str) -> int:
    """Update sexton_wiki → beast_wiki in artifact metadata.

    Returns the number of artifacts updated.
    """
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        return 0

    conn = sqlite3.connect(db_path)

    # Find all sexton_wiki artifacts
    cursor = conn.execute(
        """
        SELECT id, version, metadata_json
        FROM artifacts
        WHERE json_extract(metadata_json, '$.artifact_type') = 'sexton_wiki'
        """
    )
    rows = cursor.fetchall()
    if not rows:
        print("No sexton_wiki artifacts found — nothing to backfill.")
        conn.close()
        return 0

    updated = 0
    for row in rows:
        artifact_id = row[0]
        version = row[1]
        metadata_json = row[2]
        try:
            meta = json.loads(metadata_json)
            meta["artifact_type"] = "beast_wiki"
            new_json = json.dumps(meta)
            conn.execute(
                "UPDATE artifacts SET metadata_json = ? WHERE id = ? AND version = ?",
                (new_json, artifact_id, version),
            )
            updated += 1
            print(f"  ✅ Updated {artifact_id} (v{version}): sexton_wiki → beast_wiki")
        except Exception as exc:
            print(f"  ❌ Failed to update {artifact_id}: {exc}")

    conn.commit()
    conn.close()
    print(f"\nBackfilled {updated} artifact(s).")
    return updated


def main():
    parser = argparse.ArgumentParser(description="Wiki artifact_type contract fix")
    parser.add_argument("--db", required=True, help="Path to state.db")
    parser.add_argument("--apply", action="store_true", help="Apply backfill (default: dry-run diagnostic only)")
    args = parser.parse_args()

    print(f"Database: {args.db}")
    print(f"Mode: {'APPLY (backfill)' if args.apply else 'DRY RUN (diagnostic only)'}")
    print()

    result = diagnose(args.db)

    if args.apply and result.get("orphaned_count", 0) > 0:
        print()
        print("=" * 60)
        print("APPLYING BACKFILL: sexton_wiki → beast_wiki")
        print("=" * 60)
        backfill(args.db)
    elif args.apply:
        print("\nNo orphaned sexton_wiki artifacts to backfill.")


if __name__ == "__main__":
    main()
