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

# ---------------------------------------------------------------------------
# Auto-update: pull latest code before starting.
#
# This block ensures `./start.sh` always runs the latest code from the
# current branch. It handles the common failure mode where the user has
# local changes to config/aip.config.toml (API keys, model names) that
# would block a plain `git pull`.
#
# Behavior:
#   1. Detects the current branch (skips if detached HEAD or not a git repo)
#   2. Checks for uncommitted changes (git status --porcelain)
#   3. If dirty: stashes with a descriptive message, pulls, then restores
#   4. If clean: pulls directly (ff-only — refuses to merge divergent history)
#   5. Prints the new commit hash if anything changed
#   6. Does the same for ~/AIP_Aristotle if it exists
#
# Opt out: AIP_AUTO_PULL=false ./start.sh
#
# Safety:
#   - Never force-pushes or hard-resets
#   - Stash pop conflicts are LEFT in the stash (not silently clobbered)
#   - Network failures warn but don't block startup (you can run offline)
#   - Only pulls the current branch (never switches branches on you)
# ---------------------------------------------------------------------------
auto_pull_repo() {
    local repo_dir="$1"
    local repo_name="$2"
    local orig_dir
    orig_dir="$(pwd)"

    if [ ! -d "$repo_dir/.git" ]; then
        return 0  # not a git repo — skip silently
    fi

    cd "$repo_dir" || return 0

    # Check if we're on a branch (skip if detached HEAD)
    local branch
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
        echo "[$repo_name] detached HEAD — skipping auto-pull"
        cd "$orig_dir" || true
        return 0
    fi

    # Check for uncommitted changes
    local dirty
    dirty=$(git status --porcelain 2>/dev/null || echo "")

    local stashed="false"
    if [ -n "$dirty" ]; then
        echo "[$repo_name] local changes detected — stashing before pull..."
        if git stash push -m "auto-pull-stash $(date -u +%Y-%m-%dT%H:%M:%SZ)" >/dev/null 2>&1; then
            stashed="true"
            echo "[$repo_name] stashed local changes (will restore after pull)"
        else
            echo "[$repo_name] WARNING: stash failed — pulling anyway (may fail if conflicts)"
        fi
    fi

    # Fetch + ff-only merge. --ff-only refuses to create a merge commit,
    # so divergent history fails loudly instead of silently merging.
    # NOTE: temporarily disable `set -e` so a failed pull doesn't exit
    # the whole script — we want to warn and continue (offline mode).
    local old_hash new_hash
    old_hash=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    set +e
    git pull --ff-only origin "$branch" 2>&1 | sed "s/^/[$repo_name] /"
    local pull_rc=${PIPESTATUS[0]}
    set -e
    if [ "$pull_rc" -eq 0 ]; then
        new_hash=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
        if [ "$old_hash" != "$new_hash" ]; then
            echo "[$repo_name] updated: $(echo "$old_hash" | cut -c1-8) → $(echo "$new_hash" | cut -c1-8)"
            git log --oneline -3 2>/dev/null | sed "s/^/[$repo_name]   /"
        else
            echo "[$repo_name] already up to date ($(echo "$new_hash" | cut -c1-8))"
        fi
    else
        echo "[$repo_name] WARNING: pull failed (network? conflicts?) — continuing with current code"
    fi

    # Restore stashed changes
    if [ "$stashed" = "true" ]; then
        echo "[$repo_name] restoring stashed local changes..."
        set +e
        git stash pop 2>&1 | sed "s/^/[$repo_name] /"
        local pop_rc=$?
        set -e
        if [ "$pop_rc" -eq 0 ]; then
            echo "[$repo_name] stash restored"
        else
            echo "[$repo_name] WARNING: stash pop conflict — your changes are still in 'git stash list'"
            echo "[$repo_name]   resolve manually: git stash show -p | less  # to see what's stashed"
        fi
    fi

    cd "$orig_dir" || true
}

if [ "${AIP_AUTO_PULL:-true}" != "false" ]; then
    echo "=== Auto-pulling latest code (set AIP_AUTO_PULL=false to skip) ==="
    auto_pull_repo "$(pwd)" "AIP_Brain"
    # Also pull AIP_Aristotle if it's a sibling directory
    if [ -d "$(pwd)/../AIP_Aristotle" ]; then
        auto_pull_repo "$(pwd)/../AIP_Aristotle" "AIP_Aristotle"
    fi
    echo "=== Auto-pull complete ==="
    echo ""
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
# Exit codes: 0 = seeded or skipped (both normal), 1 = actual failure.
if [ "${AIP_AUTO_SEED:-true}" != "false" ]; then
    echo "Checking first-run seed bootstrap..."
    if uv run python -m aip.cli._seed_bootstrap; then
        echo "Seed bootstrap check passed (seeded or already complete)."
    else
        echo "ERROR: Seed bootstrap failed!" >&2
        echo "This means first-run corpus initialization encountered an error." >&2
        echo "The system will continue but Corpus may show 0 documents." >&2
        echo "To retry: rm -f db/.seed_bootstrapped && python -m aip.cli._seed_bootstrap" >&2
        echo "" >&2
    fi
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
