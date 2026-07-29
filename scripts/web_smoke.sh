#!/usr/bin/env bash
# AIP Web Source Acquisition — manual dogfood smoke test (ADR-017 WS-3).
#
# This script calls the LIVE Tavily API.  It is NOT run in CI.
# Use it to verify the WS-3 surface end-to-end after wiring the
# Integrator changes (config + .env + DI).
#
# Prerequisites:
#   1. cp config/aip.config.toml.example config/aip.config.toml
#   2. Edit config/aip.config.toml: set [web] enabled = true
#   3. echo "AIP_WEB_SEARCH_API_KEY=tvly-YOUR_REAL_KEY" > .env
#   4. uv run python scripts/start.py  (or your usual start command)
#   5. In another terminal: bash scripts/web_smoke.sh
#
# Usage:
#   AIP_BACKEND_URL=http://127.0.0.1:8000 bash scripts/web_smoke.sh

set -euo pipefail

BACKEND="${AIP_BACKEND_URL:-http://127.0.0.1:8000}"
QUERY="${1:-python type hints}"

echo "=== 1. /health (web block) ==="
curl -sS "${BACKEND}/api/v1/health" | python3 -c "
import json, sys
data = json.load(sys.stdin)
web = data.get('web', {})
print(json.dumps(web, indent=2))
if web.get('provider_state') != 'available':
    print('FAIL: web provider is not available. Check AIP_WEB_SEARCH_API_KEY and [web] enabled=true.')
    sys.exit(1)
print('OK: web provider is available')
"

echo
echo "=== 2. POST /api/v1/web/search ==="
curl -sS -X POST "${BACKEND}/api/v1/web/search" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"${QUERY}\", \"limit\": 3}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f'provider: {data[\"provider\"]}')
print(f'count: {data[\"count\"]}')
for r in data['results'][:3]:
    print(f'  [{r[\"rank\"]}] {r[\"title\"][:60]}')
    print(f'      {r[\"url\"]}')
"

echo
echo "=== 3. POST /api/v1/web/ground ==="
curl -sS -X POST "${BACKEND}/api/v1/web/ground" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"${QUERY}\", \"limit\": 3, \"fetch_top_n\": 2}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f'provider: {data[\"provider\"]}')
print(f'search_count: {data[\"search_count\"]}')
print(f'fetched_count: {data[\"fetched_count\"]}')
print(f'failures: {len(data[\"failures\"])}')
for s in data['sources'][:2]:
    print(f'  [{s[\"rank\"]}] {s[\"title\"][:60]}')
    print(f'      {s[\"url\"]}')
    print(f'      text_chars: {s[\"text_chars\"]}, method: {s[\"extraction_method\"]}')
"

echo
echo "=== Smoke complete ==="
