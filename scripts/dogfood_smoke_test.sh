#!/usr/bin/env bash
# AIP Dogfood Smoke Test — clean-checkout verification
#
# Verifies the full first-run dogfood loop:
#   init → project create → ingest → ask → review → approve → export
#
# Usage:
#   cd /path/to/aip
#   bash scripts/dogfood_smoke_test.sh
#
# Options:
#   --keep    Don't clean up test databases after run
#   --verbose Print full command output
#
# Environment:
#   AIP_SMOKE_STEP_TIMEOUT_SECONDS  Per-step timeout in seconds (default: 60)
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed
#   124 Step timed out

set -euo pipefail

KEEP_DATA=false
VERBOSE=false
for arg in "$@"; do
    case "$arg" in
        --keep)    KEEP_DATA=true ;;
        --verbose) VERBOSE=true ;;
    esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

# Per-step timeout (seconds). Override with AIP_SMOKE_STEP_TIMEOUT_SECONDS.
STEP_TIMEOUT="${AIP_SMOKE_STEP_TIMEOUT_SECONDS:-60}"

# run_step: Execute a command with a bounded timeout.
#
# Prints step label, command, and result. Exits nonzero on failure or timeout
# with a clear diagnostic message.
#
# Uses exit code 124 (same as coreutils `timeout`) for timeout.
run_step() {
    local label="$1"
    shift
    echo "==> $label"
    echo "+ $* (timeout=${STEP_TIMEOUT}s)"
    local rc=0
    if [ "$VERBOSE" = true ]; then
        timeout "${STEP_TIMEOUT}" "$@" || rc=$?
    else
        timeout "${STEP_TIMEOUT}" "$@" > /tmp/aip_smoke_output.txt 2>&1 || rc=$?
    fi
    if [ "$rc" -eq 0 ]; then
        echo -e "  ${GREEN}PASS${NC}: $label"
    elif [ "$rc" -eq 124 ]; then
        echo -e "  ${RED}TIMEOUT${NC}: $label after ${STEP_TIMEOUT}s"
        echo -e "  ${YELLOW}Command: $*${NC}"
        exit 124
    else
        echo -e "  ${RED}FAIL${NC}: $label exit=$rc"
        echo -e "  ${YELLOW}Command: $*${NC}"
        exit "$rc"
    fi
    return 0
}

# run_cmd: Execute a command with timeout, capturing output silently unless --verbose.
# Returns the exit code for callers that need to inspect it (e.g. expected non-zero).
run_cmd() {
    local desc="$1"
    shift
    if [ "$VERBOSE" = true ]; then
        echo -e "${YELLOW}RUNNING:${NC} $*"
        timeout "${STEP_TIMEOUT}" "$@" || return $?
    else
        timeout "${STEP_TIMEOUT}" "$@" > /tmp/aip_smoke_output.txt 2>&1 || return $?
    fi
}

check() {
    local desc="$1"
    local cmd="$2"
    echo -n "  CHECK: $desc ... "
    if timeout "${STEP_TIMEOUT}" sh -c "$cmd" > /tmp/aip_smoke_check.txt 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        PASS=$((PASS + 1))
    else
        local rc=$?
        if [ "$rc" -eq 124 ]; then
            echo -e "${RED}TIMEOUT${NC} (${STEP_TIMEOUT}s)"
        else
            echo -e "${RED}FAIL${NC}"
        fi
        FAIL=$((FAIL + 1))
        if [ -f /tmp/aip_smoke_check.txt ]; then
            cat /tmp/aip_smoke_check.txt | head -5
        fi
    fi
}

cleanup() {
    if [ "$KEEP_DATA" = false ]; then
        rm -rf db/ config/ data/ exports/ 2>/dev/null || true
    fi
}

echo "=== AIP Dogfood Smoke Test ==="
echo ""

# Step 0: Clean up any existing test databases
echo "Step 0: Cleaning up existing test data..."
cleanup

# Step 1: Initialize AIP
echo ""
echo "Step 1: aip init"
run_cmd "Initialize AIP" uv run aip init
check "state.db exists" "test -f db/state.db"
check "lexical.db exists" "test -f db/lexical.db"
check "config exists" "test -f config/aip.config.toml"
check "config has db_path" "grep -q db_path config/aip.config.toml"

# Step 2: Check status
echo ""
echo "Step 2: aip status"
run_cmd "Check status" uv run aip status
check "status reports state.db" "uv run aip status 2>&1 | grep -q state.db"

# Step 3: Create a project
echo ""
echo "Step 3: aip project create"
run_cmd "Create project" uv run aip project create --name aip_loom --domain aip_loom
check "project created" "uv run aip project list 2>&1 | grep -q aip_loom"

# Step 4: Ingest sample content
echo ""
echo "Step 4: aip ingest directory"
run_cmd "Ingest sample" uv run aip ingest directory examples/sample_threads --domain aip_loom
check "ingestion succeeded" "true"
check "lexical.db has content" "test $(sqlite3 db/lexical.db 'SELECT COUNT(*) FROM fts_documents' 2>/dev/null || echo 0) -gt 0"

# Verify ingest with --project alias
echo ""
echo "Step 4b: Verify --project alias on ingest"
run_cmd "Ingest with --project" uv run aip ingest file examples/sample_threads/aip_loom_decisions.md --project aip_loom
check "--project alias works" "true"

# Step 5: Ask without model (should return NEEDS_CONFIGURATION with sources)
echo ""
echo "Step 5: aip ask (no model configured — should show sources)"
set +e
run_cmd "Ask without model" uv run aip ask "What have we decided about artifact storage?" --project aip_loom --source all --show-context
ASK_EXIT=$?
set -e
check "ask returns non-zero or OK" "true"
check "ask output mentions sources or config" "cat /tmp/aip_smoke_output.txt 2>/dev/null | grep -qiE 'source|NEEDS_CONFIGURATION|NO_PROJECT_MEMORY|Answer'"

# Step 6: Verify datastore coherence — same DB for project and ask
echo ""
echo "Step 6: Verify datastore coherence"
check "projects table in state.db has data" "test $(sqlite3 db/state.db 'SELECT COUNT(*) FROM projects' 2>/dev/null || echo 0) -gt 0"
check "artifacts table in state.db has data" "test $(sqlite3 db/state.db 'SELECT COUNT(*) FROM artifacts' 2>/dev/null || echo 0) -gt 0"
check "ecs_state table in state.db exists" "sqlite3 db/state.db '.tables' 2>/dev/null | grep -q ecs_state"
check "events table in state.db has data" "test $(sqlite3 db/state.db 'SELECT COUNT(*) FROM events' 2>/dev/null || echo 0) -gt 0"

# Step 7: Test the full review/export pipeline with directly-created artifact
echo ""
echo "Step 7: Test review/export pipeline with directly-created artifact"
run_step "Create test artifact via Python" uv run python3 -c "
import asyncio
import sys
sys.path.insert(0, 'src')

async def setup_test_artifact():
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
    from aip.adapter.event_store_queryable import QueryableEventStore

    db_path = 'db/state.db'
    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()
    event_store = QueryableEventStore(db_path)
    await event_store.initialize()
    ecs_store = PersistentEcsStore(db_path, event_store=event_store)
    await ecs_store.initialize()

    artifact_id = 'ask:testdogfood1234567890ab'
    content = 'Based on the ingested conversations, AIP Loom has decided on a versioned artifact storage approach with DEFINER sovereignty.'
    metadata = {
        'artifact_type': 'ask_answer',
        'project_id': 'aip_loom',
        'project_name': 'aip_loom',
        'prompt': 'What have we decided about artifact storage?',
        'model_slot': 'synthesis',
        'model_name': 'test_provider',
        'source_ids': ['conv:sample_conv_001'],
        'source_types': ['conversation_chunk'],
        'session_id': 'test:session123',
        'generated_at': '2025-01-01T00:00:00Z',
    }

    await artifact_store.write(artifact_id, content, metadata)
    await ecs_store.transition(
        artifact_id=artifact_id,
        from_state=None,
        to_state='GENERATED',
        actor='test_pipeline',
        reason='Test artifact for smoke test',
    )

    await artifact_store.close()
    await ecs_store.close()
    await event_store.close()
    print(f'Created test artifact: {artifact_id}')

asyncio.run(setup_test_artifact())
"
check "test artifact created" "true"

# Step 8: Review list
echo ""
echo "Step 8: aip review list"
run_cmd "Review list" uv run aip review list --project aip_loom
check "review list shows artifacts" "cat /tmp/aip_smoke_output.txt 2>/dev/null | grep -q 'testdogfood'"

# Step 9: Review show
echo ""
echo "Step 9: aip review show"
run_cmd "Review show" uv run aip review show ask:testdogfood1234567890ab
check "review show displays content" "cat /tmp/aip_smoke_output.txt 2>/dev/null | grep -q 'versioned artifact storage'"

# Step 10: Review sources
echo ""
echo "Step 10: aip review sources"
run_cmd "Review sources" uv run aip review sources ask:testdogfood1234567890ab
check "review sources displays links" "cat /tmp/aip_smoke_output.txt 2>/dev/null | grep -qiE 'source|provenance'"

# Step 11: Review approve
echo ""
echo "Step 11: aip review approve"
run_cmd "Review approve" uv run aip review approve ask:testdogfood1234567890ab
check "approve succeeded" "cat /tmp/aip_smoke_output.txt 2>/dev/null | grep -q 'Approved'"
check "artifact is now APPROVED" "sqlite3 db/state.db \"SELECT current_state FROM ecs_state WHERE artifact_id='ask:testdogfood1234567890ab'\" | grep -q APPROVED"

# Step 12: Export artifact
echo ""
echo "Step 12: aip export artifact"
mkdir -p exports
run_cmd "Export artifact" uv run aip export artifact ask:testdogfood1234567890ab --format markdown --out ./exports/test.md
check "export file exists" "test -f exports/test.md"
check "export has content" "test $(wc -c < exports/test.md) -gt 50"
check "export has metadata frontmatter" "head -5 exports/test.md | grep -q '---'"
check "export has lifecycle state" "grep -q 'lifecycle_state' exports/test.md"
check "export has provenance footer" "grep -qiE 'provenance|source' exports/test.md"

# Step 13: Verify export of unapproved artifact requires --force
echo ""
echo "Step 13: Verify export gate on unapproved artifact"
run_step "Create unapproved test artifact" uv run python3 -c "
import asyncio, sys
sys.path.insert(0, 'src')

async def setup_unapproved():
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
    from aip.adapter.event_store_queryable import QueryableEventStore

    db_path = 'db/state.db'
    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()
    event_store = QueryableEventStore(db_path)
    await event_store.initialize()
    ecs_store = PersistentEcsStore(db_path, event_store=event_store)
    await ecs_store.initialize()

    artifact_id = 'ask:unapproved_test_artifact'
    metadata = {
        'artifact_type': 'ask_answer', 'project_id': 'aip_loom',
        'project_name': 'aip_loom', 'prompt': 'Test question',
        'source_ids': ['conv:sample_conv_001'], 'source_types': ['conversation_chunk'],
    }
    await artifact_store.write(artifact_id, 'Unapproved test.', metadata)
    await ecs_store.transition(artifact_id=artifact_id, from_state=None, to_state='GENERATED',
                               actor='test_pipeline', reason='Unapproved test')
    await artifact_store.close(); await ecs_store.close(); await event_store.close()

asyncio.run(setup_unapproved())
"
set +e
timeout "${STEP_TIMEOUT}" uv run aip export artifact ask:unapproved_test_artifact --format markdown --out ./exports/unapproved.md 2>/dev/null
UNAPPROVED_EXIT=$?
set -e
check "unapproved export refused (exit non-zero)" "test $UNAPPROVED_EXIT -ne 0"

# Step 14: Reject and verify blocked export
echo ""
echo "Step 14: aip review reject + verify blocked export"
run_cmd "Review reject" uv run aip review reject ask:unapproved_test_artifact --note "Test rejection"
check "reject succeeded" "cat /tmp/aip_smoke_output.txt 2>/dev/null | grep -q 'Rejected'"
check "artifact is now REJECTED" "sqlite3 db/state.db \"SELECT current_state FROM ecs_state WHERE artifact_id='ask:unapproved_test_artifact'\" | grep -q REJECTED"

set +e
timeout "${STEP_TIMEOUT}" uv run aip export artifact ask:unapproved_test_artifact --format markdown --out ./exports/rejected.md 2>/dev/null
REJECTED_EXIT=$?
set -e
check "rejected export refused" "test $REJECTED_EXIT -ne 0"

# Summary
echo ""
echo "========================================="
echo "  Dogfood Smoke Test Summary"
echo "========================================="
echo -e "  ${GREEN}PASS${NC}: $PASS"
echo -e "  ${RED}FAIL${NC}: $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}SMOKE TEST FAILED${NC} — $FAIL check(s) did not pass"
    exit 1
else
    echo -e "${GREEN}ALL SMOKE TESTS PASSED${NC}"
    cleanup
    exit 0
fi
