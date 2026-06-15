#!/bin/bash
# AIP_Brain startup script — starts backend, waits for readiness, then starts GUI.
# Uses bounded health polling instead of a fixed sleep.
set -euo pipefail

cd "$(dirname "$0")/.."

# --- Configuration ---
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="${AIP_BACKEND_PORT:-8000}"
GUI_PORT="${AIP_GUI_PORT:-8080}"
HEALTH_URL="http://${BACKEND_HOST}:${BACKEND_PORT}/api/v1/health"
READINESS_TIMEOUT="${AIP_READINESS_TIMEOUT:-60}"
POLL_INTERVAL=1

# Ensure UV_CACHE_DIR is writable (default cache may not be accessible)
if [ -z "${UV_CACHE_DIR:-}" ]; then
    export UV_CACHE_DIR="$(cd "$(dirname "$0")/.." && pwd)/.uv_cache"
fi

# --- Child process tracking ---
CHILD_PIDS=()

cleanup() {
    echo ""
    echo "Cleaning up child processes..."
    for pid in "${CHILD_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    # Wait briefly for graceful shutdown
    sleep 0.5
    for pid in "${CHILD_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    echo "Cleanup complete."
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# --- Ensure DB directory exists ---
# The backend creates DB parent dirs too (app.py lifespan), but this provides
# a clear early error message from the shell script if the directory is unwritable.
DB_DIR="${AIP_DB_DIR:-db}"
if ! mkdir -p "${DB_DIR}" 2>/dev/null; then
    echo "ERROR: Cannot create database directory ${DB_DIR}." >&2
    echo "Ensure the directory is writable or set AIP_DB_DIR to a writable path." >&2
    exit 1
fi
echo "Database directory ensured: ${DB_DIR}/"

# --- First-run seed bootstrap ---
# Auto-populates an empty DB with graph nodes and seed conversations.
# Skipped when: AIP_AUTO_SEED=false, sentinel exists, or DB is non-empty.
if [ "${AIP_AUTO_SEED:-true}" != "false" ]; then
    echo "Checking first-run seed bootstrap..."
    uv run python -m aip.cli._seed_bootstrap || {
        echo "WARNING: Seed bootstrap failed. Continuing with empty DB." >&2
        echo "You can manually run: python -m aip.cli._seed_bootstrap" >&2
    }
fi

# --- Start backend ---
echo "Starting AIP_Brain backend on ${BACKEND_HOST}:${BACKEND_PORT}..."
uv run uvicorn "aip.adapter.api.app:create_app" --factory \
    --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" &
BACKEND_PID=$!
CHILD_PIDS+=("$BACKEND_PID")

# --- Wait for backend readiness ---
echo "Waiting for backend health at ${HEALTH_URL}..."
elapsed=0
while [ "$elapsed" -lt "$READINESS_TIMEOUT" ]; do
    if curl -sf --max-time 2 "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "Backend ready."
        break
    fi
    # Check if backend process is still running
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "ERROR: Backend process (PID ${BACKEND_PID}) exited unexpectedly."
        echo "See backend logs above."
        exit 1
    fi
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
done

if [ "$elapsed" -ge "$READINESS_TIMEOUT" ]; then
    echo "ERROR: Backend failed to become healthy within ${READINESS_TIMEOUT}s."
    echo "See backend logs above."
    exit 1
fi

# --- Start GUI ---
echo "Starting Operator Console on ${BACKEND_HOST}:${GUI_PORT}..."
uv run python -m gui.app &
GUI_PID=$!
CHILD_PIDS+=("$GUI_PID")

echo "Backend PID: ${BACKEND_PID}"
echo "GUI PID: ${GUI_PID}"
echo "Open http://${BACKEND_HOST}:${GUI_PORT}"

# --- Wait for children ---
wait
