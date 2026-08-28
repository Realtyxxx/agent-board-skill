#!/bin/bash
# tests/teams-adapter.sh — Comprehensive test suite for TeamsAdapter (.teams/)
# Tests:
#   1. TSV / markdown data parsing (board.tsv, flow.tsv, worktrees.tsv, agents.tsv, workers.tsv)
#   2. Receipt validation with DONE sentinel & whitelist sanitization
#   3. Worktree status and badge linkage
#   4. Rework loop detection from verify verdict fail
#   5. 3-tier lineage fallback (flow.tsv -> board.tsv dispatch order -> heuristic)
#   6. Unified JSON contract output with backward-compatible aliases
#   7. get_mount_paths exclusion of artifacts/

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
BOARD_DIR="$REPO_ROOT/board"

TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/teams-adapter-test.XXXXXX")
TEST_ROOT=$(cd "$TEST_ROOT" && pwd -P)
TEAMS_ROOT="$TEST_ROOT/.teams"
TEAM_DIR="$TEAMS_ROOT/demo"

cleanup() {
  rm -rf "$TEST_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'ok - %s\n' "$1"
}

mkdir -p "$TEAM_DIR/tasks" "$TEAM_DIR/receipts" "$TEAM_DIR/artifacts"

cat > "$TEAM_DIR/team-meta.env" <<'EOF'
TEAM_NAME=demo
TEAM_TASK=refund-path
TEAM_STATUS=active
TEAM_TMUX_SESSION=team-demo
EOF

printf '# scenario: feature-mr\n\nrules go here\n' > "$TEAM_DIR/mode.md"

printf '%s\t%s\n' \
  impl-a '%1' \
  reviewer '%2' \
  impl-b '%3' \
  newbie '%4' > "$TEAM_DIR/workers.tsv"

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  leader lead claude sess-lead "$TEST_ROOT" '%0' active \
  worker impl-a claude sess-a "$TEST_ROOT" '%1' active \
  worker reviewer codex sess-r "$TEST_ROOT" '%2' active \
  worker impl-b agy sess-b "$TEST_ROOT" '%3' active \
  worker newbie claude sess-n "$TEST_ROOT" '%4' active > "$TEAM_DIR/agents.tsv"

printf '%s\t%s\n' \
  T5 impl-a \
  T5-verify reviewer \
  T4 impl-a \
  T8 impl-b \
  T9 impl-a > "$TEAM_DIR/board.tsv"

printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
  impl-a '%1' - "$TEST_ROOT/wt-a" feat/refund working \
  impl-a '%1' '!41' "$TEST_ROOT/wt-a" feat/refund review \
  impl-b '%3' - "$TEST_ROOT/wt-b" feat/ledger blocked > "$TEAM_DIR/worktrees.tsv"

printf '%s\t%s\t%s\t%s\n' \
  1756366800 T5 impl-a - \
  1756366900 T5-verify reviewer T5 \
  1756366950 T4 impl-a - > "$TEAM_DIR/flow.tsv"

for id in T5 T5-verify T4 T8 T9 T7; do
  printf '# contract %s\n\nObjective: bounded outcome for %s\n' "$id" "$id" \
    > "$TEAM_DIR/tasks/$id.md"
done

write_receipt() {
  local id="$1" worker="$2" status="$3" artifact="$4" verdict="$5"
  local blocker="$6" next="$7"
  cat > "$TEAM_DIR/receipts/$id.md" <<EOF
task_id: $id
worker: $worker
status: $status
artifact: $artifact
verdict: $verdict
blocker: $blocker
next: $next
DONE $id
EOF
}

write_receipt T5 impl-a completed "artifacts/T5.md" pass none verify
write_receipt T5-verify reviewer completed "artifacts/T5-verify.md" fail none rework
write_receipt T4 impl-a blocked "artifacts/T4.md" pass T2 await_user

# Invalid receipt for T9
cat > "$TEAM_DIR/receipts/T9.md" <<'EOF'
task_id: T9
worker: impl-a
status: totally-bogus
verdict: pass
blocker: none
next: none
artifact: artifacts/T9.md
DONE T9
EOF

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

python3 - "$TEAMS_ROOT" <<'EOF'
import sys
import os
from board.adapters.teams import TeamsAdapter

teams_root = sys.argv[1]
adapter = TeamsAdapter(teams_root)

# ---------------------------------------------------------------------------
# Test 1: list_boards
# ---------------------------------------------------------------------------
boards = adapter.list_boards()
assert len(boards) == 1
assert boards[0]["name"] == "demo"
assert boards[0]["status"] == "active"
print("ok - 1. list_boards found demo")

# ---------------------------------------------------------------------------
# Test 2: load_board & contract validation
# ---------------------------------------------------------------------------
data = adapter.load_board("demo")
assert data["board"]["name"] == "demo"
assert data["board"]["title"] == "refund-path"
assert data["adapter"] == "tmux-agent-teams"

tasks = {t["id"]: t for t in data["tasks"]}
assert tasks["T5"]["column"] == "done"
assert tasks["T5-verify"]["column"] == "done"
assert tasks["T4"]["column"] == "blocked"
assert tasks["T8"]["column"] == "blocked"
assert tasks["T7"]["column"] == "todo"
assert tasks["T9"]["column"] == "blocked"
assert tasks["T9"]["receipt"] is None
assert "invalid-receipt:T9" in data["warnings"]
print("ok - 2. Column mapping and receipt validation verified")

# ---------------------------------------------------------------------------
# Test 3: Badges and controlled vocabulary
# ---------------------------------------------------------------------------
assert "receipt:blocked" in tasks["T4"]["badges"]
assert "worktree:blocked" in tasks["T8"]["badges"]
assert "verdict:fail" in tasks["T5-verify"]["badges"]
assert "rework" in tasks["T5"]["badges"]
print("ok - 3. Badges and rework propagation verified")

# ---------------------------------------------------------------------------
# Test 4: Roster & Worktree mapping
# ---------------------------------------------------------------------------
lanes = {l["id"]: l for l in data["lanes"]}
assert "impl-a" in lanes and "reviewer" in lanes and "newbie" in lanes
assert lanes["reviewer"]["runtime"] == "codex"
assert lanes["newbie"]["new"] is True
assert tasks["T5"]["ext"]["worktree"]["mr"] == "!41"
assert tasks["T5"]["ext"]["worktree"]["status"] == "review"
print("ok - 4. Roster and worktree mapping verified")

# ---------------------------------------------------------------------------
# Test 5: Lineage from flow.tsv
# ---------------------------------------------------------------------------
assert tasks["T5-verify"]["lineage"]["source"] == "flow"
chain = [n["task"] for n in tasks["T5-verify"]["lineage"]["chain"]]
assert chain == ["T5", "T5-verify"]
assert "heuristic-lineage" not in tasks["T5-verify"]["badges"]

assert tasks["T8"]["lineage"]["source"] == "heuristic"
assert "heuristic-lineage" in tasks["T8"]["badges"]
print("ok - 5. Lineage from flow.tsv and heuristic fallback verified")

# ---------------------------------------------------------------------------
# Test 6: Attention and activity feed
# ---------------------------------------------------------------------------
assert "T4" in data["attention"]
assert len(data["activity"]) >= 3
print("ok - 6. Attention list and activity feed verified")

# ---------------------------------------------------------------------------
# Test 7: get_mount_paths excludes artifacts
# ---------------------------------------------------------------------------
mount_paths = adapter.get_mount_paths()
for p in mount_paths:
    assert "/artifacts" not in p, f"artifacts found in mount_paths: {p}"
print("ok - 7. get_mount_paths excludes artifacts")

# ---------------------------------------------------------------------------
# Test 8: Aliases compatibility (team, roster, receipts_feed)
# ---------------------------------------------------------------------------
assert data["team"]["name"] == "demo"
assert len(data["roster"]) == len(data["lanes"])
assert len(data["receipts_feed"]) == len(data["activity"])
print("ok - 8. Backward-compatible aliases verified")

print("\nALL TEAMS ADAPTER TESTS PASSED CLEANLY.")
EOF

pass "teams-adapter.sh execution completed successfully"
