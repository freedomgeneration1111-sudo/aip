#!/usr/bin/env bash
# start.sh — canonical AIP_Brain launcher.
# Delegates to scripts/start.sh which uses bounded health polling,
# binds to 127.0.0.1, and auto-seeds the corpus on first run.
#
# Usage:
#   ./start.sh           — start both backend + frontend
#   ./start.sh backend   — start only the backend
#   ./start.sh frontend  — start only the frontend
#
# All arguments are forwarded to scripts/start.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/scripts/start.sh" "$@"
