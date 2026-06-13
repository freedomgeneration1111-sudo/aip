#!/bin/bash
# start-aip.sh — legacy entry point; delegates to scripts/start.sh
#
# This wrapper exists for backwards compatibility. The canonical
# startup script is scripts/start.sh, which uses bounded health
# polling and binds to 127.0.0.1.
#
# All arguments are forwarded to scripts/start.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/scripts/start.sh" "$@"
