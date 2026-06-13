#!/usr/bin/env bash
# AIP Seed Bootstrap — fresh install corpus initialization
#
# Populates a new AIP install with:
#   1. Database schemas (via aip init)
#   2. Graph nodes (28 entities), edges (5 domain bridges), default project
#   3. AIP self-knowledge corpus (all conversation JSON files)
#
# Safe to run multiple times — all inserts use OR IGNORE.
#
# Usage:
#   bash examples/seed_corpus/seed_bootstrap.sh
#   # or from repo root after chmod +x:
#   ./examples/seed_corpus/seed_bootstrap.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "=== AIP Seed Bootstrap ==="
echo "Repo root: $REPO_ROOT"
echo ""

# 1. Initialize databases (creates tables in db/)
echo "--- Step 1: Initialize databases ---"
uv run aip init
echo ""

# 2. Run SQL bootstrap (graph nodes, edges, default project)
echo "--- Step 2: Bootstrap graph and default project ---"
python3 -c "
import sqlite3, os

db_path = 'db/state.db'
sql_path = 'examples/seed_corpus/seed_bootstrap.sql'

if not os.path.exists(db_path):
    print(f'ERROR: {db_path} not found. Did aip init succeed?')
    exit(1)

conn = sqlite3.connect(db_path)
with open(sql_path) as f:
    conn.executescript(f.read())
conn.commit()

# Verify
nodes = conn.execute('SELECT COUNT(*) FROM graph_nodes').fetchone()[0]
edges = conn.execute('SELECT COUNT(*) FROM graph_edges').fetchone()[0]
projects = conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
print(f'Graph nodes: {nodes}')
print(f'Graph edges: {edges}')
print(f'Projects: {projects}')
conn.close()
"
echo ""

# 3. Ingest AIP self-knowledge corpus (all JSON files in conversations/)
echo "--- Step 3: Ingest AIP self-knowledge corpus ---"
for f in "$SCRIPT_DIR/conversations/"*.json; do
    echo "Ingesting $(basename "$f")..."
    uv run aip corpus ingest "$f" \
      --source-model claude \
      --source-account aip_seed
done
echo ""

echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  1. Tag corpus turns:  uv run aip corpus tag --limit 500"
echo "  2. Build graph:       uv run aip corpus graph --build-from-bridges"
echo "  3. Start AIP:         ./scripts/start.sh"
echo ""
echo "The seed corpus teaches AIP about itself."
echo "Augmented chat can now answer questions about AIP's design,"
echo "actors, domains, and operational workflow."
