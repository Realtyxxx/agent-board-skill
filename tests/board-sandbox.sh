#!/bin/bash
# tests/board-sandbox.sh — Contract, HTTP security, and Sandbox assertions for agent-board.
#
# Covers:
#   1. GET /api/board and /api/team against synthetic fixtures.
#   2. HTTP 405 Method Not Allowed on write attempts (POST, PUT, DELETE, PATCH).
#   3. HTTP 400 Bad Request on path traversal attempts.
#   4. HTTP 404 Not Found on unknown paths.
#   5. The sandbox bind plan never lists artifacts/.
#   6. Under macOS sandbox-exec (or Linux bwrap), artifacts/ is strictly unreadable
#      and blocked by the kernel / sandbox profile.

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
BOARD_DIR="$REPO_ROOT/board"
SERVE="$BOARD_DIR/serve.py"
LAUNCHER="$BOARD_DIR/run-sandboxed.sh"

TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/board-sandbox-test.XXXXXX")
TEST_ROOT=$(cd "$TEST_ROOT" && pwd -P)
TEAMS_ROOT="$TEST_ROOT/.teams"
TEAM_DIR="$TEAMS_ROOT/demo"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
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

skip() {
  printf 'skip - %s\n' "$1"
}

[ -f "$SERVE" ] || fail "missing serve.py: $SERVE"
[ -f "$LAUNCHER" ] || fail "missing run-sandboxed.sh: $LAUNCHER"

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

# Invalid receipt
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

# Secret artifact file that must NEVER be read
echo "SECRET_ARTIFACT_TOKEN_12345" > "$TEAM_DIR/artifacts/T5.md"
echo "SECRET_ARTIFACT_TOKEN_67890" > "$TEAM_DIR/artifacts/secret.md"

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

# Start server in background for HTTP API testing.
# --host 127.0.0.1 pinned explicitly: the server's own default is now
# 0.0.0.0 (LAN-visible), but this fixture is throwaway test data and the
# port-detection sed below matches on a literal 127.0.0.1 log line.
python3 "$SERVE" --teams-root "$TEAMS_ROOT" --host 127.0.0.1 --port 0 > "$TEST_ROOT/server.log" 2>&1 &
SERVER_PID=$!
PORT=""
for _ in $(seq 1 100); do
  PORT=$(sed -n 's#.*http://127\.0\.0\.1:\([0-9]*\).*#\1#p' "$TEST_ROOT/server.log" | head -n 1)
  [ -n "$PORT" ] && break
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 0.1
done
[ -n "$PORT" ] || fail "server did not report a port: $(cat "$TEST_ROOT/server.log")"

fetch() {
  curl -s -S --fail-with-body "$1"
}

BODY="$TEST_ROOT/response.json"
fetch "http://127.0.0.1:$PORT/api/board?board=demo" > "$BODY" ||
  fail "GET /api/board?board=demo failed"

assert_json() {
  local label="$1" expr="$2"
  python3 - "$BODY" "$expr" <<'EOF' || fail "$label: python assertion failed"
import json, sys
body = json.load(open(sys.argv[1]))
data = body
tasks = {t['id']: t for t in body['tasks']}
lanes = {l['id']: l for l in body['lanes']}
roster = {r['worker']: r for r in body.get('roster', [])}
expr = sys.argv[2]
if not eval(expr):
    print("Assertion failed for expression:", expr, file=sys.stderr)
    sys.exit(1)
EOF
  pass "$label"
}

# --- Column mapping assertions ---
assert_json "completed pass -> done" "tasks['T5']['column'] == 'done'"
assert_json "undispatched contract -> todo" "tasks['T7']['column'] == 'todo' and tasks['T7']['owner'] is None"
assert_json "blocked receipt -> blocked" "tasks['T4']['column'] == 'blocked'"
assert_json "in-flight worktree blocked -> blocked" "tasks['T8']['column'] == 'blocked'"

# --- Badges ---
assert_json "blocked receipt badge" "'receipt:blocked' in tasks['T4']['badges']"
assert_json "worktree blocked badge" "'worktree:blocked' in tasks['T8']['badges']"
assert_json "failed verdict badge" "'verdict:fail' in tasks['T5-verify']['badges']"
assert_json "failed verify routes predecessor to rework" "'rework' in tasks['T5']['badges']"

# --- Receipt whitelist & sanitization ---
assert_json "invalid receipt degrades to null" "tasks['T9']['receipt'] is None"
assert_json "invalid receipt recorded in warnings" "'invalid-receipt:T9' in data['warnings']"
if grep -q "SECRET_ARTIFACT_TOKEN" "$BODY"; then
  fail "Artifact token leaked into JSON response"
fi
pass "Artifact content was never leaked into response"

# --- HTTP Methods security (405 on write, 400 on traversal, 404 on unknown) ---
python3 - "$PORT" <<'EOF' || fail "HTTP security checks failed"
import sys, urllib.request, urllib.error
port = sys.argv[1]

def request_status(path, method="GET", data=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code

checks = [
    ("unknown path is 404", request_status("/random-path"), 404),
    ("traversal board query is 400", request_status("/api/board?board=../.."), 400),
    ("traversal team query is 400", request_status("/api/team?team=../.."), 400),
    ("POST method is 405", request_status("/api/board", method="POST", data=b"{}"), 405),
    ("PUT method is 405", request_status("/api/board", method="PUT", data=b"{}"), 405),
    ("DELETE method is 405", request_status("/api/board", method="DELETE"), 405),
    ("PATCH method is 405", request_status("/api/board", method="PATCH", data=b"{}"), 405),
]

for label, code, expected in checks:
    if code != expected:
        print(f"FAIL: {label} (got {code}, want {expected})", file=sys.stderr)
        sys.exit(1)
    print(f"ok - {label}")
EOF
pass "All HTTP endpoint security checks passed"

kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""

# --- Sandbox Bind Plan Check ---
PLAN=$("$LAUNCHER" --teams-root "$TEAMS_ROOT" --print-plan) || fail "run-sandboxed --print-plan failed"
printf '%s\n' "$PLAN" | grep -q "$TEAM_DIR/receipts" || fail "bind plan missing receipts/"
printf '%s\n' "$PLAN" | grep -q "$TEAM_DIR/board.tsv" || fail "bind plan missing board.tsv"
if printf '%s\n' "$PLAN" | grep -q '/artifacts'; then
  fail "bind plan exposes artifacts/"
fi
pass "Bind plan covers control files and never artifacts/"

# --- Sandbox Profile & Kernel Isolation (macOS sandbox-exec) ---
if ! command -v sandbox-exec >/dev/null 2>&1; then
  skip "sandbox-exec not available on this platform"
else
  PROFILE="$TEST_ROOT/board.sb"
  "$LAUNCHER" --teams-root "$TEAMS_ROOT" --print-profile > "$PROFILE" || fail "print-profile failed"
  grep -q '^(deny default)$' "$PROFILE" || fail "profile does not deny by default"
  if grep -q '/artifacts' "$PROFILE"; then
    fail "profile explicitly grants access to artifacts"
  fi
  pass "Seatbelt profile is deny-default and excludes artifacts/"

  # Kernel verification: cat of artifact file MUST be denied by macOS kernel
  if sandbox-exec -f "$PROFILE" /bin/cat "$TEAM_DIR/artifacts/secret.md" >/dev/null 2>&1; then
    fail "sandbox-exec permitted reading artifacts/secret.md"
  fi
  pass "Kernel sandbox-exec blocked access to artifacts/secret.md"

  # Control file must be readable
  sandbox-exec -f "$PROFILE" /bin/cat "$TEAM_DIR/board.tsv" >/dev/null 2>&1 || fail "board.tsv unreadable"
  pass "Control plane file board.tsv is readable under sandbox-exec"

  # Run live server under sandbox-exec (same --host 127.0.0.1 pin as above)
  SANDBOX_LOG="$TEST_ROOT/sandbox-live.log"
  "$LAUNCHER" --teams-root "$TEAMS_ROOT" --mode sandbox-exec --host 127.0.0.1 --port 0 > "$SANDBOX_LOG" 2>&1 &
  SERVER_PID=$!
  PORT_SB=""
  for _ in $(seq 1 150); do
    PORT_SB=$(sed -n 's#.*http://127\.0\.0\.1:\([0-9]*\).*#\1#p' "$SANDBOX_LOG" | head -n 1)
    [ -n "$PORT_SB" ] && break
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 0.1
  done
  [ -n "$PORT_SB" ] || fail "sandboxed server failed to start: $(cat "$SANDBOX_LOG")"

  fetch "http://127.0.0.1:$PORT_SB/api/board?board=demo" > "$BODY" || fail "sandboxed GET /api/board failed"
  assert_json "sandboxed server delivers correct payload" "tasks['T5']['column'] == 'done' and tasks['T4']['column'] == 'blocked'"

  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  SERVER_PID=""
  pass "Live sandboxed server execution verified"
fi

pass "board-sandbox.sh completed all tests successfully"
